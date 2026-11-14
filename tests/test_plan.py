from __future__ import annotations

from fractions import Fraction
from types import SimpleNamespace

from jitterlens.core.classify import classify_timeline
from jitterlens.core.intervals import build_intervals
from jitterlens.core.plan import build_plans
from jitterlens.core.stats import build_source_stats, missing_points_between
from jitterlens.core.timeline import SamplePoint, build_timelines
from jitterlens.sources import RawPoint

from tests.conftest import make_source, single_contract


def _gap_pair(prev_index, cur_index):
    return SimpleNamespace(kind="gap", prev_index=prev_index, cur_index=cur_index)


def _points(timestamps):
    return [
        SamplePoint(ts, "mock-source", "x.csv", index + 2, index)
        for index, ts in enumerate(timestamps)
    ]


def _analysis(interval, source=None, points=None, pairs=None):
    source = source or SimpleNamespace(
        name="mock-source",
        period_ms=1000,
        max_gap_points=3,
        downsample_threshold=Fraction(1, 2),
    )
    timeline = SimpleNamespace(
        points=points
        or [
            SamplePoint(1000 + index * 1000, "mock-source", "x.csv", index + 2, index)
            for index in range(6)
        ]
    )
    classification = SimpleNamespace(pairs=pairs or [])
    return {
        "source": source,
        "timeline": timeline,
        "classification": classification,
        "intervals": [interval],
    }


def _base_interval(kinds, start=0, end=6000):
    return {
        "id": "x#0001",
        "source": "mock-source",
        "start_ts": start,
        "end_ts": end,
        "kinds": kinds,
        "point_count": 2,
        "start_origin": "x.csv",
        "start_lineno": 2,
        "end_origin": "x.csv",
        "end_lineno": 3,
    }


def test_fill_plan_truncation_and_metadata() -> None:
    analysis = _analysis(
        _base_interval(["gap"], start=0, end=6000),
        points=_points([0, 6000]),
        pairs=[_gap_pair(0, 1)],
    )
    document = build_plans(
        [analysis],
        run_id="jl-test",
        contract_sha256="abc",
        generated_at="2025-01-01T00:00:00Z",
    )
    assert document["run_id"] == "jl-test"
    assert document["contract_sha256"] == "abc"
    assert document["summary"] == {"fill": 1, "downsample": 0, "truncated": 1}
    plan = document["plans"][0]
    assert plan["id"] == "plan-0001"
    assert plan["kind"] == "fill"
    assert plan["expected_missing"] == 5
    assert plan["insert_ts"] == [1000, 2000, 3000]
    assert plan["truncated"] is True


def test_fill_plan_exact_capacity_not_truncated() -> None:
    analysis = _analysis(
        _base_interval(["gap"], start=0, end=3000),
        points=_points([0, 3000]),
        pairs=[_gap_pair(0, 1)],
    )
    document = build_plans(
        [analysis],
        "jl",
        "abc",
        "t",
    )
    plan = document["plans"][0]
    assert plan["expected_missing"] == 2
    assert plan["insert_ts"] == [1000, 2000]
    assert plan["truncated"] is False


def test_downsample_selection_is_deterministic() -> None:
    points = [
        SamplePoint(1000 + index * 500, "mock-source", "x.csv", index + 2, index)
        for index in range(5)
    ]
    pair = SimpleNamespace(kind="jitter", rel_dev=Fraction(3, 4), prev_index=0, cur_index=1)
    analysis = _analysis(
        _base_interval(["jitter"], start=1000, end=3000),
        points=points,
        pairs=[pair],
    )
    first = build_plans([analysis], "jl", "abc", "t")
    second = build_plans([analysis], "jl", "abc", "t")
    assert first == second
    plan = first["plans"][0]
    assert plan["kind"] == "downsample"
    assert plan["keep_every"] == 2
    assert plan["keep_ts"] == [1000, 2000, 3000]
    assert plan["considered_points"] == 5
    assert plan["truncated"] is False


def test_jitter_below_downsample_threshold_is_ignored() -> None:
    points = [
        SamplePoint(1000 + index * 500, "mock-source", "x.csv", index + 2, index)
        for index in range(5)
    ]
    pair = SimpleNamespace(kind="jitter", rel_dev=Fraction(1, 4), prev_index=0, cur_index=1)
    document = build_plans(
        [_analysis(_base_interval(["jitter"], 1000, 3000), points=points, pairs=[pair])],
        "jl",
        "abc",
        "t",
    )
    assert document["plans"] == []
    assert document["summary"] == {"fill": 0, "downsample": 0, "truncated": 0}


def test_fill_plan_counts_only_gap_pairs_in_merged_interval() -> None:
    # Interval merges a gap pair with a jitter pair: the plan must estimate
    # only the gap pair's interior grid points, never the already-sampled
    # interior points of the merged span.
    points = _points([0, 4000, 4500, 5500])
    pairs = [
        _gap_pair(0, 1),
        SimpleNamespace(kind="jitter", rel_dev=Fraction(1, 2), prev_index=1, cur_index=2),
    ]
    interval = _base_interval(["gap", "jitter"], start=0, end=4500)
    document = build_plans([_analysis(interval, points=points, pairs=pairs)], "jl", "abc", "t")
    assert document["summary"] == {"fill": 1, "downsample": 0, "truncated": 0}
    plan = document["plans"][0]
    assert plan["kind"] == "fill"
    assert plan["expected_missing"] == 3
    assert plan["insert_ts"] == [1000, 2000, 3000]
    assert plan["truncated"] is False
    sampled = {point.ts_ms for point in points}
    assert not set(plan["insert_ts"]) & sampled


def test_fill_plan_sums_gap_pairs_within_one_interval() -> None:
    points = _points([0, 4000, 5000, 9000])
    pairs = [_gap_pair(0, 1), _gap_pair(2, 3)]
    interval = _base_interval(["gap"], start=0, end=9000)
    document = build_plans([_analysis(interval, points=points, pairs=pairs)], "jl", "abc", "t")
    plan = document["plans"][0]
    assert plan["expected_missing"] == 6
    assert plan["insert_ts"] == [1000, 2000, 3000]
    assert plan["truncated"] is True
    assert plan["truncated"] == (plan["expected_missing"] > len(plan["insert_ts"]))


def test_fill_plans_reconcile_with_report_missing_estimate(default_contract, source_a) -> None:
    # Truncated fill over a two-gap interval: plans.json must explain the
    # missing_estimate of report.json on the same interval.
    source = make_source(source_a, max_gap_points=3, merge_gap_points=2)
    times = [0, 4000, 5000, 9000, 10000, 11000]
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
    analysis = {
        "source": source,
        "timeline": timeline,
        "classification": classification,
        "intervals": intervals,
    }
    document = build_plans([analysis], "jl", "abc", "t")
    fills = [plan for plan in document["plans"] if plan["kind"] == "fill"]
    assert len(fills) == 1
    plan = fills[0]
    assert stats["missing_estimate"] == 6
    assert plan["expected_missing"] == stats["missing_estimate"]
    assert sum(item["expected_missing"] for item in fills) == stats["missing_estimate"]
    # Truncation stays readable: truncated iff the cap dropped candidates.
    assert plan["insert_ts"] == [1000, 2000, 3000]
    assert len(plan["insert_ts"]) == source.max_gap_points
    assert plan["truncated"] is True
    assert plan["truncated"] == (plan["expected_missing"] > len(plan["insert_ts"]))
    assert document["summary"]["truncated"] == 1
    # Every emitted candidate is a grid position the stats layer also counts.
    per_pair = [
        (pair.prev_index, pair.cur_index)
        for pair in classification.pairs
        if pair.kind == "gap"
    ]
    grid: list[int] = []
    for prev_index, cur_index in per_pair:
        prev_ts = timeline.points[prev_index].ts_ms
        cur_ts = timeline.points[cur_index].ts_ms
        cursor = prev_ts + source.period_ms
        while cursor < cur_ts:
            grid.append(cursor)
            cursor += source.period_ms
    assert plan["expected_missing"] == len(grid)
    assert plan["insert_ts"] == grid[: source.max_gap_points]
    assert plan["expected_missing"] == sum(
        missing_points_between(timeline.points[prev].ts_ms, timeline.points[cur].ts_ms, source.period_ms)
        for prev, cur in per_pair
    )
