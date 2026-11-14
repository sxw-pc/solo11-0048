"""Human-readable Markdown report (no external references)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_cell(value) for value in row) + " |")
    if not rows:
        lines.append("| " + " | ".join("_none_" for _ in headers) + " |")
    return lines


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    contract = report["contract"]
    inputs = report["inputs"]
    lines = [
        "# Sampling Jitter Report",
        "",
        f"- Verdict: **{summary['verdict']}**",
        f"- Run ID: `{report['run_id']}`",
        f"- Generated at: `{report['generated_at']}`",
        f"- Contract: `{contract['name']}` (v{contract['version']}, sha256 `{contract['sha256'][:16]}...`)",
        f"- Input manifest sha256: `{inputs['manifest_sha256']}`",
        f"- Sources: {summary['sources']}; valid points: {summary['valid_points']}; "
        f"parse errors: {summary['parse_errors']}; intervals: {summary['abnormal_intervals']}",
        "",
        "## Source summary",
        "",
    ]
    rows = []
    for source in report["sources"]:
        counts = source["counts"]
        rows.append(
            [
                source["name"],
                source["verdict"],
                counts["valid_points"],
                counts["parse_errors"],
                counts["jitter"],
                counts["gap"],
                counts["clock_drift"],
                counts["intervals"],
                source["gap_ratio"],
                source["jitter_ratio"],
            ]
        )
    lines.extend(
        _table(
            [
                "Source",
                "Verdict",
                "Points",
                "Parse errors",
                "Jitter",
                "Gap",
                "Drift",
                "Intervals",
                "Gap ratio",
                "Jitter ratio",
            ],
            rows,
        )
    )
    lines.extend(["", "## Contract coverage", ""])
    coverage = report["contract_coverage"]
    cov_summary = coverage["summary"]
    uncovered = coverage["uncovered_sources"]
    lines.extend(
        [
            f"- Files: {cov_summary['files']} "
            f"({cov_summary['routed_files']} routed, {cov_summary['unmatched_files']} unmatched, "
            f"{cov_summary['unrouted_files']} unrouted)",
            f"- Sources: {cov_summary['declared_sources']} declared, "
            f"{cov_summary['covered_sources']} covered, "
            f"{cov_summary['uncovered_sources']} never routed",
            "- Never-routed sources: "
            + (", ".join(f"`{name}`" for name in uncovered) if uncovered else "_none_"),
            "",
            "### File routing",
            "",
        ]
    )
    file_rows = [
        [
            entry["origin"],
            entry["status"],
            ", ".join(entry["candidates"]) or "—",
            ", ".join(entry["selected"]) or "—",
            entry["reason"] or "—",
        ]
        for entry in coverage["files"]
    ]
    lines.extend(_table(["File", "Status", "Candidates", "Selected", "Reason"], file_rows))
    lines.extend(["", "### Source coverage", ""])
    source_rows = [
        [
            source["name"],
            "yes" if source["covered"] else "no",
            ", ".join(source["matched_files"]) or "—",
            ", ".join(source["routed_files"]) or "—",
            ", ".join(source["routing_failures"]) or "—",
        ]
        for source in coverage["sources"]
    ]
    lines.extend(
        _table(
            ["Source", "Covered", "Matched files", "Routed files", "Routing failures"],
            source_rows,
        )
    )
    lines.extend(["", "## Timing detail", ""])
    timing_rows = []
    for source in report["sources"]:
        timing = source["timing"]
        timing_rows.append(
            [
                source["name"],
                timing["mean_delta_ms"],
                timing["median_delta_ms"],
                timing["max_delta_ms"],
                source["missing_estimate"],
                source["max_consecutive_abnormal"],
            ]
        )
    lines.extend(
        _table(
            ["Source", "Mean delta ms", "Median delta ms", "Max delta ms", "Missing est.", "Max run"],
            timing_rows,
        )
    )
    lines.extend(["", "## Jitter histograms", ""])
    for source in report["sources"]:
        lines.extend([f"### {source['name']}", ""])
        histogram_rows = []
        for bucket in source["histogram"]:
            upper = "overflow" if bucket["le_ms"] is None else bucket["le_ms"]
            histogram_rows.append([upper, bucket["count"]])
        lines.extend(_table(["Absolute deviation <= ms", "Count"], histogram_rows))
        lines.append("")
    lines.extend(["## Top abnormal intervals", ""])
    interval_rows = []
    for interval in report["intervals"][:20]:
        interval_rows.append(
            [
                interval["id"],
                interval["source"],
                interval["start_ts"],
                interval["end_ts"],
                ",".join(interval["kinds"]),
                interval["point_count"],
                f"{interval['start_origin']}:{interval['start_lineno']}",
            ]
        )
    lines.extend(
        _table(
            ["ID", "Source", "Start ts", "End ts", "Kinds", "Points", "Location"],
            interval_rows,
        )
    )
    lines.extend(["", "## Parse errors", ""])
    parse_rows = [
        [item["source"], f"{item['origin']}:{item['lineno']}", item["code"], item["message"]]
        for item in report["parse_errors"][:50]
    ]
    lines.extend(_table(["Source", "Location", "Code", "Message"], parse_rows))
    lines.extend(
        [
            "",
            "## Plans",
            "",
            f"- Fill plans: {summary['plans']['fill']}",
            f"- Downsample plans: {summary['plans']['downsample']}",
            f"- Truncated plans: {summary['plans']['truncated']}",
            "- Plans are advisory only; source files are never modified.",
            "",
        ]
    )
    return "\n".join(lines)


def write_markdown(path: str | Path, report: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(render_markdown(report))
