"""Tests for duplicate_policy=allow: concurrent samples as first-class input."""

from __future__ import annotations

from fractions import Fraction
from types import SimpleNamespace

from jitterlens.core.classify import classify_timeline
from jitterlens.core.intervals import build_intervals
from jitterlens.core.plan import build_plans
from jitterlens.core.stats import build_source_stats, histogram
from jitterlens.core.timeline import build_timelines
from jitterlens.engine import analyze
from jitterlens.report.jsonreport import canonical_json, strip_volatile
from jitterlens.sources import RawPoint

from tests.conftest import (
    DEFAULT_CONTRACT_PATH,
    FIXTURE_DIR,
    ROOT,
    make_source,
    single_contract,
)

ALLOW_CONTRACT_PATH = ROOT / "contracts" / "sensors_allow.yaml"


def _allow(source, **changes):
    return make_source(source, duplicate_policy="allow", **changes)


def _pipeline(times, source, default_contract):
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
    stats = build_source_stats(source, timeline, classification, [], intervals, 0)
    return timeline, classification, intervals, stats


def test_allow_keeps_concurrent_points_unflagged(default_contract, source_a) -> None:
    source = _allow(source_a)
    timeline, _, _, _ = _pipeline([1000, 1000, 2000], source, default_contract)
    assert [point.ts_ms for point in timeline.points] == [1000, 1000, 2000]
    assert timeline.flags == []
    assert timeline.concurrent_point_indices() == [0, 1]
    triple, _, _, _ = _pipeline([5, 5, 5], source, default_contract)
    assert triple.concurrent_point_indices() == [0, 1, 2]


def test_allow_zero_delta_pair_is_concurrent(default_contract, source_a) -> None:
    source = _allow(source_a)
    _, classification, _, _ = _pipeline([0, 0, 1000], source, default_contract)
    assert [pair.kind for pair in classification.pairs] == ["concurrent", "on_time"]
    pair = classification.pairs[0]
    assert pair.delta_ms == 0
    assert pair.abs_dev_ms == 0
    assert pair.rel_dev == Fraction(0)
    assert classification.point_kinds == [set(), set(), set()]
    assert len(classification.pairs) == 2


def test_allow_concurrent_pair_escapes_jitter_and_gap(default_contract, source_a) -> None:
    source = _allow(source_a, jitter_abs_ms=0, jitter_rel=Fraction(0))
    _, classification, _, _ = _pipeline([0, 0, 1000], source, default_contract)
    kinds = [pair.kind for pair in classification.pairs]
    assert kinds == ["concurrent", "on_time"]
    assert all(not kinds_set for kinds_set in classification.point_kinds)


def test_mark_zero_delta_pair_is_still_duplicate(default_contract, source_a) -> None:
    source = make_source(source_a, duplicate_policy="mark")
    _, classification, _, _ = _pipeline([0, 0, 1000], source, default_contract)
    pair = classification.pairs[0]
    assert pair.kind == "duplicate"
    assert pair.abs_dev_ms == source.period_ms
    assert pair.rel_dev == Fraction(1)
    assert classification.point_kinds[1] == {"duplicate"}


def test_histogram_skips_concurrent_pairs() -> None:
    pairs = [
        SimpleNamespace(kind="on_time", abs_dev_ms=5),
        SimpleNamespace(kind="concurrent", abs_dev_ms=0),
        SimpleNamespace(kind="duplicate", abs_dev_ms=1000),
    ]
    assert histogram(pairs, (5, 10)) == [
        {"le_ms": 5, "count": 1},
        {"le_ms": 10, "count": 0},
        {"le_ms": None, "count": 0},
    ]


def test_allow_jitter_ratio_excludes_concurrent(default_contract, source_a) -> None:
    source = _allow(source_a)
    *_, stats = _pipeline([0, 0, 1000, 2000, 3026], source, default_contract)
    assert stats["rule_hits"]["on_time"] == 2
    assert stats["counts"]["jitter"] == 2  # both endpoints of one jitter pair
    assert stats["jitter_ratio"] == 0.333333  # 1 / 3 measured pairs, not 1 / 4
    assert stats["timing"]["delta_count"] == 4  # zero-delta pair stays counted
    assert stats["timing"]["min_delta_ms"] == 0


def test_allow_counts_and_rule_hits_trace_concurrency(default_contract, source_a) -> None:
    source = _allow(source_a)
    *_, intervals, stats = _pipeline([0, 0, 1000], source, default_contract)
    assert stats["verdict"] == "ok"
    assert stats["counts"]["valid_points"] == 3
    assert stats["counts"]["unique_points"] == 2  # same-ts records: one dedup key
    assert stats["counts"]["concurrent_pairs"] == 1
    assert stats["counts"]["concurrent_points"] == 2
    assert stats["counts"]["duplicate"] == 0
    assert stats["counts"]["abnormal_points"] == 0
    assert stats["rule_hits"]["concurrent"] == 1
    assert intervals == []


def test_allow_gap_ratio_uses_distinct_instants(default_contract, source_a) -> None:
    source = _allow(source_a)
    *_, stats = _pipeline([0, 0, 4000], source, default_contract)
    assert stats["missing_estimate"] == 3
    assert stats["gap_ratio"] == 0.6  # 3 / (2 unique instants + 3), not 3 / 6
    assert stats["counts"]["gap"] == 2  # gap pair flags both endpoints


def test_allow_concurrent_pairs_not_in_drift_windows(default_contract, source_a) -> None:
    source = _allow(source_a, drift_window=4, drift_threshold_rel=Fraction(0))
    times: list[int] = []
    for index in range(12):
        times.append(index * 1000)
        if index % 3 == 0:
            times.append(index * 1000)
    _, classification, _, _ = _pipeline(times, source, default_contract)
    assert classification.drift_ranges == []
    assert all("clock_drift" not in kinds for kinds in classification.point_kinds)


def test_concurrency_only_source_generates_no_plans(default_contract, source_a) -> None:
    source = _allow(source_a)
    timeline, classification, intervals, stats = _pipeline(
        [0, 0, 1000, 2000, 3000], source, default_contract
    )
    assert stats["counts"]["abnormal_points"] == 0
    document = build_plans(
        [
            {
                "source": source,
                "timeline": timeline,
                "classification": classification,
                "intervals": intervals,
            }
        ],
        "jl-test",
        "sha",
        "2025-01-01T00:00:00Z",
    )
    assert document["plans"] == []
    assert document["summary"] == {"fill": 0, "downsample": 0, "truncated": 0}


def test_mark_stats_have_no_concurrent_fields(default_contract, source_a) -> None:
    source = make_source(source_a, duplicate_policy="mark")
    *_, stats = _pipeline([0, 0, 1000], source, default_contract)
    assert stats["counts"]["duplicate"] == 1
    assert "concurrent_pairs" not in stats["counts"]
    assert "concurrent_points" not in stats["counts"]
    assert "concurrent" not in stats["rule_hits"]


def test_allow_contract_report_traces() -> None:
    _, report, _ = analyze(ALLOW_CONTRACT_PATH, FIXTURE_DIR)
    sources = {source["name"]: source for source in report["sources"]}
    sensor_b = sources["mock-sensor-b"]
    assert sensor_b["contract"]["duplicate_policy"] == "allow"
    assert sensor_b["counts"]["duplicate"] == 0
    assert sensor_b["counts"]["concurrent_pairs"] == 1
    assert sensor_b["counts"]["concurrent_points"] == 2
    assert sensor_b["rule_hits"]["concurrent"] == 1
    assert "duplicates" not in sensor_b["verdict_reasons"]
    delta_count = sensor_b["timing"]["delta_count"]
    assert delta_count == sensor_b["counts"]["valid_points"] - 1
    measured = delta_count - sensor_b["counts"]["concurrent_pairs"]
    assert sum(row["count"] for row in sensor_b["histogram"]) == measured
    for name in ("mock-sensor-a", "mock-sensor-c"):
        assert "concurrent" not in sources[name]["rule_hits"]
        assert "concurrent_pairs" not in sources[name]["counts"]
    assert "concurrent" not in report["summary"]["rule_hits"]


def test_allow_contract_leaves_mark_sources_untouched() -> None:
    _, allow_report, _ = analyze(ALLOW_CONTRACT_PATH, FIXTURE_DIR)
    _, mark_report, _ = analyze(DEFAULT_CONTRACT_PATH, FIXTURE_DIR)
    for name in ("mock-sensor-a", "mock-sensor-c"):
        allow_source = next(s for s in allow_report["sources"] if s["name"] == name)
        mark_source = next(s for s in mark_report["sources"] if s["name"] == name)
        assert allow_source == mark_source


def test_allow_contract_is_deterministic() -> None:
    _, first, _ = analyze(ALLOW_CONTRACT_PATH, FIXTURE_DIR)
    _, second, _ = analyze(ALLOW_CONTRACT_PATH, FIXTURE_DIR)
    assert canonical_json(strip_volatile(first)) == canonical_json(strip_volatile(second))
