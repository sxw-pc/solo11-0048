"""Canonical JSON report serialization."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from ..config import Contract
from ..sources import ReadResult, source_coverage


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical_json(value))


def strip_volatile(report: dict[str, Any]) -> dict[str, Any]:
    """Return a copy suitable for golden comparisons."""
    result = copy.deepcopy(report)
    result.pop("run_id", None)
    result.pop("generated_at", None)
    return result


def build_contract_coverage(read_result: ReadResult, contract: Contract) -> dict[str, Any]:
    """Shape the read-layer file x source routing genealogy for the report."""
    files = [
        {
            "origin": entry.origin,
            "candidates": list(entry.candidates),
            "selected": list(entry.selected),
            "status": entry.status,
            "reason": entry.reason,
        }
        for entry in read_result.routing
    ]
    sources = []
    for coverage in sorted(
        source_coverage(read_result.routing, contract), key=lambda item: item.name
    ):
        sources.append(
            {
                "name": coverage.name,
                "covered": coverage.covered,
                "matched_files": list(coverage.matched_files),
                "routed_files": list(coverage.routed_files),
                "routing_failures": list(coverage.routing_failures),
            }
        )
    uncovered = [source["name"] for source in sources if not source["covered"]]
    statuses = [entry["status"] for entry in files]
    return {
        "files": files,
        "sources": sources,
        "uncovered_sources": uncovered,
        "summary": {
            "files": len(files),
            "routed_files": statuses.count("routed"),
            "unmatched_files": statuses.count("unmatched"),
            "unrouted_files": statuses.count("unrouted"),
            "declared_sources": len(sources),
            "covered_sources": len(sources) - len(uncovered),
            "uncovered_sources": len(uncovered),
        },
    }
