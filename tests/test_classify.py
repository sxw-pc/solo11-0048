from __future__ import annotations

from fractions import Fraction

from jitterlens.core.classify import classify_timeline, is_on_time
from jitterlens.core.timeline import build_timelines
from jitterlens.sources import RawPoint

from tests.conftest import make_source, single_contract


def _classify(times: list[int], source, default_contract):
    contract = single_contract(default_contract, source)
    raw = [
        RawPoint(ts, source.name, "x.csv", index + 2, ()) for index, ts in enumerate(times)
    ]
    timeline = build_timelines(raw, contract)[source.name]
    return classify_timeline(timeline, source)


def test_absolute_boundary_is_inclusive(default_contract, source_a) -> None:
    result = _classify([0, 1025], source_a, default_contract)
    assert result.pairs[0].kind == "on_time"
    assert result.point_kinds == [set(), set()]
    later = _classify([0, 1026], source_a, default_contract)
    assert later.pairs[0].kind == "jitter"
    assert later.point_kinds[0] == {"jitter"}
    assert later.point_kinds[1] == {"jitter"}


def test_relative_boundary_is_inclusive(default_contract, source_a) -> None:
    source = make_source(source_a, jitter_abs_ms=0, jitter_rel=Fraction(1, 50))
    assert is_on_time(20, 1000, source) is True
    assert is_on_time(21, 1000, source) is False
    assert _classify([0, 980], source, default_contract).pairs[0].kind == "on_time"
    assert _classify([0, 979], source, default_contract).pairs[0].kind == "jitter"


def test_gap_threshold_is_strict(default_contract, source_a) -> None:
    source = make_source(source_a, max_gap_points=3)
    at_limit = _classify([0, 3000], source, default_contract)
    assert at_limit.pairs[0].kind == "jitter"
    beyond = _classify([0, 3001], source, default_contract)
    assert beyond.pairs[0].kind == "gap"
    assert beyond.point_kinds[0] == {"gap"}
    assert beyond.point_kinds[1] == {"gap"}


def test_gap_wins_over_loose_tolerance(default_contract, source_a) -> None:
    source = make_source(source_a, jitter_abs_ms=5000, max_gap_points=3)
    result = _classify([0, 4000], source, default_contract)
    assert result.pairs[0].kind == "gap"


def test_duplicate_pair_is_not_on_time(default_contract, source_a) -> None:
    source = make_source(source_a, duplicate_policy="mark")
    result = _classify([0, 0, 1000], source, default_contract)
    assert [pair.kind for pair in result.pairs] == ["duplicate", "on_time"]
    assert result.point_kinds[0] == set()
    assert result.point_kinds[1] == {"duplicate"}
    assert result.point_kinds[2] == set()


def test_clock_drift_window(default_contract, source_a) -> None:
    source = make_source(
        source_a,
        jitter_abs_ms=2000,
        max_gap_points=10,
        drift_window=32,
        drift_threshold_rel=Fraction(1, 100),
    )
    timestamps = [0]
    for index in range(200):
        timestamps.append(timestamps[-1] + 1000 + index * 2)
    result = _classify(timestamps, source, default_contract)
    assert result.drift_ranges
    assert any("clock_drift" in kinds for kinds in result.point_kinds)
    first = result.drift_ranges[0]
    assert first["end_ts"] > first["start_ts"]
    assert first["median_change_ms"] > 0


def test_flat_series_has_no_drift(default_contract, source_a) -> None:
    source = make_source(source_a, drift_window=32, drift_threshold_rel=Fraction(0))
    result = _classify([index * 1000 for index in range(200)], source, default_contract)
    assert result.drift_ranges == []
    assert all(pair.kind == "on_time" for pair in result.pairs)
