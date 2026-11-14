"""End-to-end analysis pipeline and output materialization."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Contract, SourceContract, load_contract
from .core.classify import classify_timeline
from .core.intervals import build_intervals
from .core.plan import build_plans
from .core.stats import VERDICT_ORDER, build_source_stats
from .core.timeline import build_timelines
from .errors import JitterLensError
from .ledger import record_run
from .report.html_report import write_html
from .report.jsonreport import write_json
from .report.markdown import write_markdown
from .sources import ParseIssue, ReadResult, read_inputs

SCHEMA_VERSION = 1


@dataclass
class RunArtifact:
    report: dict[str, Any]
    plans: dict[str, Any]
    out_dir: Path
    ledger_path: Path

    @property
    def exit_code(self) -> int:
        return 2 if self.report["summary"]["verdict"] == "fail" else 0


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest(input_dir: Path) -> dict[str, Any]:
    files = sorted(
        (
            path
            for path in input_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in (".csv", ".jsonl")
        ),
        key=lambda path: path.relative_to(input_dir).as_posix(),
    )
    entries = []
    for path in files:
        relative = path.relative_to(input_dir).as_posix()
        entries.append(
            {
                "path": relative,
                "sha256": _sha256_file(path),
                "size": path.stat().st_size,
                "format": path.suffix.lower().lstrip("."),
            }
        )
    payload = "".join(
        f"{item['path']}\0{item['sha256']}\0{item['size']}\n" for item in entries
    )
    return {
        "files": entries,
        "manifest_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }


def _run_id(contract_sha: str, input_sha: str) -> str:
    seed = f"schema={SCHEMA_VERSION}\ncontract={contract_sha}\ninput={input_sha}\n"
    return "jl-" + hashlib.sha256(seed.encode("ascii")).hexdigest()[:16]


def _global_verdict(source_reports: list[dict[str, Any]], unmatched: bool) -> str:
    verdict = "ok"
    for source in source_reports:
        if VERDICT_ORDER[str(source["verdict"])] > VERDICT_ORDER[verdict]:
            verdict = str(source["verdict"])
    if unmatched and verdict == "ok":
        verdict = "warn"
    return verdict


def _issues_for_source(issues: list[ParseIssue], name: str) -> list[ParseIssue]:
    return [issue for issue in issues if issue.source == name]


def analyze(
    contract_path: str | Path,
    input_dir: str | Path,
    generated_at: str | None = None,
) -> tuple[Contract, dict[str, Any], dict[str, Any]]:
    contract = load_contract(contract_path)
    input_root = Path(input_dir)
    if not input_root.is_dir():
        raise JitterLensError("E_READ_001", f"input directory not found: {input_root}")
    manifest = _manifest(input_root)
    read_result: ReadResult = read_inputs(input_root, contract)
    timelines = build_timelines(read_result.points, contract)
    analyses: list[dict[str, Any]] = []
    source_reports: list[dict[str, Any]] = []
    all_intervals: list[dict[str, Any]] = []
    rule_hits = {
        "on_time": 0,
        "jitter": 0,
        "gap": 0,
        "clock_drift": 0,
        "duplicate": 0,
        "monotonic": 0,
        "out_of_order": 0,
        "parse_error": 0,
    }
    for source in sorted(contract.sources, key=lambda item: item.name):
        timeline = timelines[source.name]
        issues = _issues_for_source(read_result.issues, source.name)
        classification = classify_timeline(timeline, source)
        intervals = build_intervals(
            source.name, timeline.points, classification.point_kinds, source.merge_gap_points
        )
        unknown_count = sum(len(point.unknown_fields) for point in timeline.points)
        stats = build_source_stats(
            source, timeline, classification, issues, intervals, unknown_count
        )
        source_reports.append(stats)
        all_intervals.extend(intervals)
        for key in rule_hits:
            rule_hits[key] += int(stats["rule_hits"][key])
        analyses.append(
            {
                "source": source,
                "timeline": timeline,
                "classification": classification,
                "intervals": intervals,
                "stats": stats,
            }
        )
    all_intervals.sort(
        key=lambda item: (item["source"], item["start_ts"], item["end_ts"], item["start_lineno"])
    )
    run_id = _run_id(contract.sha256, manifest["manifest_sha256"])
    at = generated_at or contract.generated_at
    plans_document = build_plans(analyses, run_id, contract.sha256, at)
    unmatched = any(issue.source not in {source.name for source in contract.sources} for issue in read_result.issues)
    parse_errors = [
        {
            "source": issue.source,
            "origin": issue.origin,
            "lineno": issue.lineno,
            "code": issue.code,
            "message": issue.message,
        }
        for issue in sorted(
            read_result.issues,
            key=lambda issue: (issue.origin, issue.lineno, issue.source, issue.code),
        )
    ]
    source_reports.sort(key=lambda item: item["name"])
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "generated_at": at,
        "contract": {
            "name": contract.name,
            "version": contract.version,
            "sha256": contract.sha256,
        },
        "inputs": manifest,
        "summary": {
            "verdict": _global_verdict(source_reports, unmatched),
            "sources": len(source_reports),
            "valid_points": sum(int(source["counts"]["valid_points"]) for source in source_reports),
            "parse_errors": len(read_result.issues),
            "abnormal_intervals": len(all_intervals),
            "rule_hits": rule_hits,
            "plans": plans_document["summary"],
            "unknown_fields": sum(
                int(source["unknown_field_count"]) for source in source_reports
            ),
        },
        "sources": source_reports,
        "intervals": all_intervals,
        "parse_errors": parse_errors,
    }
    return contract, report, plans_document


def write_outputs(
    out_dir: str | Path,
    report: dict[str, Any],
    plans_document: dict[str, Any],
    with_html: bool = False,
    ledger_path: str | Path | None = None,
) -> RunArtifact:
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    write_json(target / "report.json", report)
    write_markdown(target / "report.md", report)
    if with_html:
        write_html(target / "report.html", report)
    write_json(target / "plans.json", plans_document)
    ledger = Path(ledger_path) if ledger_path is not None else target / "ledger.sqlite"
    record_run(ledger, report, plans_document, "plans.json")
    return RunArtifact(report=report, plans=plans_document, out_dir=target, ledger_path=ledger)


def run(
    contract_path: str | Path,
    input_dir: str | Path,
    out_dir: str | Path,
    generated_at: str | None = None,
    with_html: bool = False,
    ledger_path: str | Path | None = None,
) -> RunArtifact:
    _, report, plans_document = analyze(contract_path, input_dir, generated_at)
    return write_outputs(out_dir, report, plans_document, with_html, ledger_path)
