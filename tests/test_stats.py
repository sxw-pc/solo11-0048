from __future__ import annotations

from types import SimpleNamespace

from jitterlens.core.classify import classify_timeline
from jitterlens.core.intervals import build_intervals
from jitterlens.core.stats import (
    FAIL_RULES,
    RULES,
    WARN_RULES,
    build_source_stats,
    evaluate_verdict,
    histogram,
    verdict_metrics,
)
from jitterlens.core.timeline import build_timelines
from jitterlens.sources import ParseIssue, RawPoint

from tests.conftest import make_source, single_contract


def _metrics(**overrides):
    counts = {
        "records": 10,
        "valid_points": 10,
        "abnormal_points": 0,
        "parse_errors": 0,
        "monotonic": 0,
        "duplicate": 0,
        "clock_drift": 0,
    }
    derived = ("gap_ratio", "jitter_ratio", "parse_error_ratio")
    counts.update(
        {key: value for key, value in overrides.items() if key not in derived}
    )
    return verdict_metrics(
        counts,
        overrides.get("gap_ratio", 0.0),
        overrides.get("jitter_ratio", 0.0),
        overrides.get("parse_error_ratio", 0.0),
    )


def _stats(times, source, default_contract, issues=(), unknown=0):
    contract = single_contract(default_contract, source)
    raw = [
        RawPoint(ts, source.name, "x.csv", index + 2, ())
        for index, ts in enumerate(times)
    ]
    timeline = build_timelines(raw, contract)[source.name]
    classification = classify_timeline(timeline, source)
    intervals = build_intervals(
        source.name, timeline.points, classification.point_kinds, source.merge_gap_points
    )
    return build_source_stats(
        source, timeline, classification, list(issues), intervals, unknown
    )


def test_histogram_bucket_boundaries() -> None:
    pairs = [
        SimpleNamespace(kind="on_time", abs_dev_ms=5),
        SimpleNamespace(kind="jitter", abs_dev_ms=6),
        SimpleNamespace(kind="jitter", abs_dev_ms=10),
        SimpleNamespace(kind="gap", abs_dev_ms=26),
        SimpleNamespace(kind="duplicate", abs_dev_ms=1000),
    ]
    assert histogram(pairs, (5, 10, 25)) == [
        {"le_ms": 5, "count": 1},
        {"le_ms": 10, "count": 2},
        {"le_ms": 25, "count": 0},
        {"le_ms": None, "count": 1},
    ]


def test_ok_source(default_contract, source_a) -> None:
    stats = _stats([0, 1000, 2000], source_a, default_contract, unknown=4)
    assert stats["verdict"] == "ok"
    assert stats["counts"]["valid_points"] == 3
    assert stats["counts"]["abnormal_points"] == 0
    assert stats["timing"]["median_delta_ms"] == 1000
    assert stats["rule_hits"]["on_time"] == 2
    assert stats["unknown_field_count"] == 4
    assert stats["max_consecutive_abnormal"] == 0


def test_warn_on_jitter_and_parse(default_contract, source_a) -> None:
    times = [0, 1026, 2052] + [3052 + index * 1000 for index in range(8)]
    jittered = _stats(times, source_a, default_contract)
    assert jittered["verdict"] == "warn"
    assert jittered["rule_hits"]["jitter"] == 3
    normal_times = [index * 1000 for index in range(20)]
    issue = ParseIssue(source_a.name, "x.csv", 99, "E_READ_002", "bad")
    with_parse = _stats(normal_times, source_a, default_contract, issues=[issue])
    assert with_parse["verdict"] == "warn"
    assert with_parse["counts"]["parse_errors"] == 1
    assert with_parse["rule_hits"]["parse_error"] == 1


def test_fail_on_monotonic_violation(default_contract, source_a) -> None:
    stats = _stats([2000, 1000], source_a, default_contract)
    assert stats["verdict"] == "fail"
    assert "monotonic_violation" in stats["verdict_reasons"]
    assert stats["diagnostics"][0]["lineno"] == 3
    assert stats["counts"]["out_of_order"] == 1


def test_fail_on_gap_ratio(default_contract, source_a) -> None:
    strict_gap = make_source(source_a, max_gap_ratio=0.0, max_gap_points=3)
    stats = _stats([0, 4000], strict_gap, default_contract)
    assert stats["verdict"] == "fail"
    assert stats["gap_ratio"] == 0.6
    assert stats["missing_estimate"] == 3
    assert "gap_ratio_exceeded" in stats["verdict_reasons"]


def test_fail_on_parse_ratio(default_contract, source_a) -> None:
    issue = ParseIssue(source_a.name, "x.csv", 2, "E_READ_002", "bad")
    stats = _stats([0, 1000], source_a, default_contract, issues=[issue])
    assert stats["verdict"] == "fail"
    assert "parse_error_ratio_exceeded" in stats["verdict_reasons"]


def test_empty_source_fails(default_contract, source_a) -> None:
    stats = _stats([], source_a, default_contract)
    assert stats["verdict"] == "fail"
    assert stats["verdict_reasons"] == ["no_records", "no_valid_points"]
    assert stats["timing"]["mean_delta_ms"] is None


def test_rule_ids_and_reason_order_are_stable() -> None:
    assert [rule.reason for rule in FAIL_RULES] == [
        "no_records",
        "no_valid_points",
        "monotonic_violation",
        "gap_ratio_exceeded",
        "parse_error_ratio_exceeded",
        "jitter_ratio_exceeded",
    ]
    assert [rule.reason for rule in WARN_RULES] == [
        "abnormal_points",
        "parse_errors",
        "duplicates",
        "clock_drift",
    ]
    assert RULES == FAIL_RULES + WARN_RULES


def test_each_rule_declares_metrics_and_operator() -> None:
    by_reason = {rule.reason: rule for rule in RULES}
    assert by_reason["no_records"].metrics == ("records",)
    assert by_reason["no_valid_points"].metrics == ("valid_points",)
    assert by_reason["monotonic_violation"].metrics == ("monotonic",)
    assert by_reason["gap_ratio_exceeded"].metrics == ("gap_ratio",)
    assert by_reason["parse_error_ratio_exceeded"].metrics == (
        "parse_errors",
        "parse_error_ratio",
    )
    assert by_reason["jitter_ratio_exceeded"].metrics == ("jitter_ratio",)
    assert by_reason["abnormal_points"].metrics == ("abnormal_points",)
    assert by_reason["parse_errors"].metrics == ("parse_errors",)
    assert by_reason["duplicates"].metrics == ("duplicate",)
    assert by_reason["clock_drift"].metrics == ("clock_drift",)
    for rule in RULES:
        assert all(check.op in ("eq", "gt") for check in rule.comparisons)
        assert rule.severity in ("fail", "warn")


def test_fail_precedes_warn_regardless_of_table_order(default_contract, source_a) -> None:
    metrics = _metrics(abnormal_points=1, monotonic=1)
    assert evaluate_verdict(metrics, source_a) == ("fail", ["monotonic_violation"])


def test_verdict_is_ok_with_no_rule_hits(default_contract, source_a) -> None:
    assert evaluate_verdict(_metrics(), source_a) == ("ok", [])


def test_gap_ratio_boundary_is_strict(default_contract, source_a) -> None:
    at_limit = _metrics(gap_ratio=source_a.max_gap_ratio)
    assert evaluate_verdict(at_limit, source_a) == ("ok", [])
    above = _metrics(gap_ratio=source_a.max_gap_ratio + 0.000001)
    assert evaluate_verdict(above, source_a) == ("fail", ["gap_ratio_exceeded"])


def test_jitter_ratio_boundary_is_strict(default_contract, source_a) -> None:
    at_limit = _metrics(jitter_ratio=source_a.fail_jitter_ratio)
    assert evaluate_verdict(at_limit, source_a) == ("ok", [])
    above = _metrics(jitter_ratio=source_a.fail_jitter_ratio + 0.000001)
    assert evaluate_verdict(above, source_a) == ("fail", ["jitter_ratio_exceeded"])


def test_parse_error_rule_requires_both_conditions(default_contract, source_a) -> None:
    # A high parse ratio with zero parse errors cannot trigger the fail rule.
    assert evaluate_verdict(
        _metrics(parse_errors=0, parse_error_ratio=0.9), source_a
    ) == ("ok", [])
    # Parse errors alone (ratio at the limit) remain a warn.
    assert evaluate_verdict(
        _metrics(parse_errors=2, parse_error_ratio=source_a.max_parse_error_ratio), source_a
    ) == ("warn", ["parse_errors"])


def test_fail_reasons_keep_table_order(default_contract, source_a) -> None:
    metrics = _metrics(records=0, valid_points=0, monotonic=1)
    assert evaluate_verdict(metrics, source_a) == (
        "fail",
        ["no_records", "no_valid_points", "monotonic_violation"],
    )


def test_warn_reasons_keep_table_order(default_contract, source_a) -> None:
    metrics = _metrics(
        abnormal_points=1, parse_errors=1, duplicate=1, clock_drift=1
    )
    assert evaluate_verdict(metrics, source_a) == (
        "warn",
        ["abnormal_points", "parse_errors", "duplicates", "clock_drift"],
    )

