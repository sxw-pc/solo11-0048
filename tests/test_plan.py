from __future__ import annotations

from fractions import Fraction
from types import SimpleNamespace

from jitterlens.core.plan import build_plans
from jitterlens.core.timeline import SamplePoint


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
    document = build_plans(
        [_analysis(_base_interval(["gap"]))],
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
    document = build_plans(
        [_analysis(_base_interval(["gap"], start=0, end=3000))],
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
