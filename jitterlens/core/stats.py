"""Per-source timing statistics, rule counts and verdict grading."""

from __future__ import annotations

from collections.abc import Iterator
from statistics import median
from typing import Any

from ..config import SourceContract
from ..sources import ParseIssue
from .classify import Classification
from .intervals import max_consecutive_abnormal
from .timeline import SourceTimeline

VERDICT_ORDER = {"ok": 0, "warn": 1, "fail": 2}


def _number(value: float) -> int | float:
    rounded = round(value, 3)
    if float(rounded).is_integer():
        return int(rounded)
    return rounded


def _ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)


def missing_points_between(start_ts_ms: int, end_ts_ms: int, period_ms: int) -> int:
    """Missing-point count inside one gap bounded by two sampled endpoints.

    Unified gap semantics shared by ``missing_estimate`` (report.json) and
    ``expected_missing`` (plans.json) so a plan can be audited back against
    the statistics on the same interval:

    * Both endpoints are treated as already sampled; only strictly interior
      nominal grid positions ``start_ts_ms + k * period_ms`` (``k >= 1``)
      that still lie before ``end_ts_ms`` count as missing.
    * Exact multiple: a span of exactly ``k`` periods misses ``k - 1``
      points, because the right endpoint lands on the grid and is sampled.
    * Non-multiple: ``floor(span / period_ms)`` points are missing; the
      leftover sub-period remainder is absorbed by the right endpoint and
      never counts as an extra missing point.

    Both cases are the single integer identity ``ceil(span / period) - 1 ==
    (span - 1) // period`` for ``span > 0``; a non-positive span has no
    interior grid positions and yields 0. Pure integer arithmetic.
    """
    span = end_ts_ms - start_ts_ms
    if span <= 0:
        return 0
    return (span - 1) // period_ms


def missing_grid_timestamps(start_ts_ms: int, end_ts_ms: int, period_ms: int) -> Iterator[int]:
    """Yield the interior grid positions counted by :func:`missing_points_between`.

    Same endpoint semantics: positions are ``start_ts_ms + k * period_ms``
    for ``k >= 1`` while strictly smaller than ``end_ts_ms``. The number of
    yielded timestamps always equals ``missing_points_between(...)``.
    """
    cursor = start_ts_ms + period_ms
    while cursor < end_ts_ms:
        yield cursor
        cursor += period_ms


def histogram(pairs: list[Any], buckets: tuple[int, ...]) -> list[dict[str, int | None]]:
    counts = [0 for _ in range(len(buckets) + 1)]
    for pair in pairs:
        if pair.kind == "duplicate":
            continue
        placed = False
        for index, upper in enumerate(buckets):
            if pair.abs_dev_ms <= upper:
                counts[index] += 1
                placed = True
                break
        if not placed:
            counts[-1] += 1
    rows: list[dict[str, int | None]] = []
    for index, upper in enumerate(buckets):
        rows.append({"le_ms": upper, "count": counts[index]})
    rows.append({"le_ms": None, "count": counts[-1]})
    return rows


def _verdict(
    counts: dict[str, int],
    gap_ratio: float,
    jitter_ratio: float,
    parse_ratio: float,
    source: SourceContract,
) -> tuple[str, list[str]]:
    fail_reasons: list[str] = []
    warn_reasons: list[str] = []
    if counts["records"] == 0:
        fail_reasons.append("no_records")
    if counts["valid_points"] == 0:
        fail_reasons.append("no_valid_points")
    if counts["monotonic"] > 0:
        fail_reasons.append("monotonic_violation")
    if gap_ratio > source.max_gap_ratio:
        fail_reasons.append("gap_ratio_exceeded")
    if counts["parse_errors"] > 0 and parse_ratio > source.max_parse_error_ratio:
        fail_reasons.append("parse_error_ratio_exceeded")
    if jitter_ratio > source.fail_jitter_ratio:
        fail_reasons.append("jitter_ratio_exceeded")
    if fail_reasons:
        return "fail", fail_reasons
    if counts["abnormal_points"] > 0:
        warn_reasons.append("abnormal_points")
    if counts["parse_errors"] > 0:
        warn_reasons.append("parse_errors")
    if counts["duplicate"] > 0:
        warn_reasons.append("duplicates")
    if counts["clock_drift"] > 0:
        warn_reasons.append("clock_drift")
    if warn_reasons:
        return "warn", warn_reasons
    return "ok", []


def build_source_stats(
    source: SourceContract,
    timeline: SourceTimeline,
    classification: Classification,
    issues: list[ParseIssue],
    intervals: list[dict[str, object]],
    unknown_field_count: int,
) -> dict[str, object]:
    points = timeline.points
    pairs = classification.pairs
    kind_counts = {
        kind: classification.count_kind(kind)
        for kind in (
            "duplicate",
            "monotonic",
            "out_of_order",
            "jitter",
            "gap",
            "clock_drift",
        )
    }
    on_time_pairs = sum(1 for pair in pairs if pair.kind == "on_time")
    jitter_pairs = sum(1 for pair in pairs if pair.kind == "jitter")
    nonduplicate_pairs = [pair for pair in pairs if pair.kind != "duplicate"]
    gap_pairs = [pair for pair in pairs if pair.kind == "gap"]
    # Unified semantics: both endpoints of a gap pair are sampled; count the
    # interior nominal grid positions. Identical to what fill plans expect
    # for the same pairs, so plans reconcile with this estimate.
    missing = sum(
        missing_points_between(
            points[pair.prev_index].ts_ms,
            points[pair.cur_index].ts_ms,
            source.period_ms,
        )
        for pair in gap_pairs
    )
    unique_points = len({point.ts_ms for point in points})
    records = len(points) + len(issues)
    gap_ratio = _ratio(missing, unique_points + missing)
    jitter_ratio = _ratio(jitter_pairs, len(nonduplicate_pairs))
    parse_ratio = _ratio(len(issues), records)
    abnormal_points = sum(1 for kinds in classification.point_kinds if kinds)
    counts = {
        "records": records,
        "valid_points": len(points),
        "unique_points": unique_points,
        "abnormal_points": abnormal_points,
        "parse_errors": len(issues),
        "intervals": len(intervals),
        "duplicate": kind_counts["duplicate"],
        "monotonic": kind_counts["monotonic"],
        "out_of_order": kind_counts["out_of_order"],
        "jitter": kind_counts["jitter"],
        "gap": kind_counts["gap"],
        "clock_drift": kind_counts["clock_drift"],
    }
    verdict, reasons = _verdict(counts, gap_ratio, jitter_ratio, parse_ratio, source)
    deltas = [pair.delta_ms for pair in pairs]
    timing: dict[str, int | float | None] = {
        "delta_count": len(deltas),
        "mean_delta_ms": _number(sum(deltas) / len(deltas)) if deltas else None,
        "median_delta_ms": _number(float(median(deltas))) if deltas else None,
        "max_delta_ms": max(deltas) if deltas else None,
        "min_delta_ms": min(deltas) if deltas else None,
    }
    diagnostics = [
        {
            "kind": flag.kind,
            "ts_ms": flag.ts_ms,
            "origin": flag.origin,
            "lineno": flag.lineno,
        }
        for flag in sorted(timeline.flags, key=lambda flag: (flag.origin, flag.lineno, flag.kind))
    ]
    rule_hits = {
        "on_time": on_time_pairs,
        "jitter": kind_counts["jitter"],
        "gap": kind_counts["gap"],
        "clock_drift": kind_counts["clock_drift"],
        "duplicate": kind_counts["duplicate"],
        "monotonic": kind_counts["monotonic"],
        "out_of_order": kind_counts["out_of_order"],
        "parse_error": len(issues),
    }
    return {
        "name": source.name,
        "verdict": verdict,
        "verdict_reasons": reasons,
        "counts": counts,
        "timing": timing,
        "gap_ratio": gap_ratio,
        "jitter_ratio": jitter_ratio,
        "parse_error_ratio": parse_ratio,
        "missing_estimate": missing,
        "max_consecutive_abnormal": max_consecutive_abnormal(classification.point_kinds),
        "histogram": histogram(pairs, source.histogram_buckets),
        "rule_hits": rule_hits,
        "diagnostics": diagnostics,
        "parse_errors": [
            {
                "origin": issue.origin,
                "lineno": issue.lineno,
                "code": issue.code,
                "message": issue.message,
            }
            for issue in sorted(issues, key=lambda issue: (issue.origin, issue.lineno, issue.code))
        ],
        "unknown_field_count": unknown_field_count,
        "contract": {
            "period_ms": source.period_ms,
            "jitter_abs_ms": source.jitter_abs_ms,
            "jitter_rel": source.jitter_rel_float,
            "max_gap_points": source.max_gap_points,
            "max_gap_ratio": source.max_gap_ratio,
            "monotonic": source.monotonic,
            "duplicate_policy": source.duplicate_policy,
        },
    }
