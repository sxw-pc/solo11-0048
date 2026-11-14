"""Per-source timing statistics, rule counts and verdict grading."""

from __future__ import annotations

import operator
from dataclasses import dataclass
from enum import Enum
from statistics import median
from typing import Any, Callable, Mapping

from ..config import SourceContract
from ..sources import ParseIssue
from .classify import Classification
from .intervals import max_consecutive_abnormal
from .timeline import SourceTimeline

VERDICT_ORDER = {"ok": 0, "warn": 1, "fail": 2}
FAIL = "fail"
WARN = "warn"


class Op(str, Enum):
    """Comparison operators a rule may declare."""

    EQ = "eq"
    GT = "gt"


_OPERATORS: dict[Op, Callable[[int | float, int | float], bool]] = {
    Op.EQ: operator.eq,
    Op.GT: operator.gt,
}


@dataclass(frozen=True)
class Limit:
    """A threshold resolved from the source contract when a rule is evaluated."""

    field: str


@dataclass(frozen=True)
class Comparison:
    """One declarative check over a metric fed into rule evaluation."""

    metric: str
    op: Op
    threshold: int | float | Limit

    def holds(self, metrics: Mapping[str, int | float], source: SourceContract) -> bool:
        threshold = self.threshold
        if isinstance(threshold, Limit):
            threshold = getattr(source, threshold.field)
        return _OPERATORS[self.op](metrics[self.metric], threshold)


@dataclass(frozen=True)
class Rule:
    """A reason code produced when all of its comparisons hold."""

    reason: str
    severity: str
    comparisons: tuple[Comparison, ...]

    def matches(self, metrics: Mapping[str, int | float], source: SourceContract) -> bool:
        return all(check.holds(metrics, source) for check in self.comparisons)

    @property
    def metrics(self) -> tuple[str, ...]:
        return tuple(check.metric for check in self.comparisons)


FAIL_RULES: tuple[Rule, ...] = (
    Rule("no_records", FAIL, (Comparison("records", Op.EQ, 0),)),
    Rule("no_valid_points", FAIL, (Comparison("valid_points", Op.EQ, 0),)),
    Rule("monotonic_violation", FAIL, (Comparison("monotonic", Op.GT, 0),)),
    Rule(
        "gap_ratio_exceeded",
        FAIL,
        (Comparison("gap_ratio", Op.GT, Limit("max_gap_ratio")),),
    ),
    Rule(
        "parse_error_ratio_exceeded",
        FAIL,
        (
            Comparison("parse_errors", Op.GT, 0),
            Comparison("parse_error_ratio", Op.GT, Limit("max_parse_error_ratio")),
        ),
    ),
    Rule(
        "jitter_ratio_exceeded",
        FAIL,
        (Comparison("jitter_ratio", Op.GT, Limit("fail_jitter_ratio")),),
    ),
)

# Warn rules keep their historical order; reasons are always reported in table order.
WARN_RULES: tuple[Rule, ...] = (
    Rule("abnormal_points", WARN, (Comparison("abnormal_points", Op.GT, 0),)),
    Rule("parse_errors", WARN, (Comparison("parse_errors", Op.GT, 0),)),
    Rule("duplicates", WARN, (Comparison("duplicate", Op.GT, 0),)),
    Rule("clock_drift", WARN, (Comparison("clock_drift", Op.GT, 0),)),
)

RULES = FAIL_RULES + WARN_RULES


def verdict_metrics(
    counts: Mapping[str, int],
    gap_ratio: float,
    jitter_ratio: float,
    parse_ratio: float,
) -> dict[str, int | float]:
    """Flatten counts fields and derived ratios into one rule-evaluation namespace."""
    metrics: dict[str, int | float] = dict(counts)
    metrics["gap_ratio"] = gap_ratio
    metrics["jitter_ratio"] = jitter_ratio
    metrics["parse_error_ratio"] = parse_ratio
    return metrics


def evaluate_verdict(
    metrics: Mapping[str, int | float],
    source: SourceContract,
    rules: tuple[Rule, ...] = RULES,
) -> tuple[str, list[str]]:
    """Grade a source: fail rules take precedence over warn rules in table order."""
    for severity in (FAIL, WARN):
        reasons = [
            rule.reason
            for rule in rules
            if rule.severity == severity and rule.matches(metrics, source)
        ]
        if reasons:
            return severity, reasons
    return "ok", []


def _number(value: float) -> int | float:
    rounded = round(value, 3)
    if float(rounded).is_integer():
        return int(rounded)
    return rounded


def _ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)


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
    missing = sum(
        max(0, (pair.delta_ms + source.period_ms - 1) // source.period_ms - 1)
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
    verdict, reasons = evaluate_verdict(
        verdict_metrics(counts, gap_ratio, jitter_ratio, parse_ratio), source
    )
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
