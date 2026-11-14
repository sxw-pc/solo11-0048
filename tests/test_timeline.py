from __future__ import annotations

from jitterlens.core.timeline import build_timelines
from jitterlens.sources import RawPoint

from tests.conftest import make_source, single_contract


def _raw(ts: int, line: int, origin: str = "x.csv", source: str = "mock-sensor-a") -> RawPoint:
    return RawPoint(ts, source, origin, line, ())


def test_ordering_and_structural_flags(default_contract, source_a) -> None:
    contract = single_contract(default_contract, source_a)
    raw = [_raw(1000, 2), _raw(2000, 3), _raw(1500, 4), _raw(2500, 5), _raw(2000, 6)]
    timeline = build_timelines(raw, contract)[source_a.name]
    assert [point.ts_ms for point in timeline.points] == [1000, 1500, 2000, 2000, 2500]
    assert [(point.origin, point.lineno) for point in timeline.points] == [
        ("x.csv", 2),
        ("x.csv", 4),
        ("x.csv", 3),
        ("x.csv", 6),
        ("x.csv", 5),
    ]
    flags = {(flag.origin, flag.lineno, flag.kind) for flag in timeline.flags}
    assert flags == {
        ("x.csv", 4, "monotonic"),
        ("x.csv", 4, "out_of_order"),
        ("x.csv", 6, "duplicate"),
        ("x.csv", 6, "monotonic"),
        ("x.csv", 6, "out_of_order"),
    }
    assert timeline.structural_counts() == {"duplicate": 1, "monotonic": 2, "out_of_order": 2}


def test_monotonic_not_required_still_marks_out_of_order(default_contract, source_a) -> None:
    relaxed = make_source(source_a, monotonic=False)
    contract = single_contract(default_contract, relaxed)
    timeline = build_timelines([_raw(1000, 1), _raw(900, 2)], contract)[relaxed.name]
    kinds = {flag.kind for flag in timeline.flags}
    assert kinds == {"out_of_order"}


def test_duplicate_policy_allow(default_contract, source_a) -> None:
    allowed = make_source(source_a, duplicate_policy="allow")
    contract = single_contract(default_contract, allowed)
    timeline = build_timelines([_raw(1000, 1), _raw(1000, 2)], contract)[allowed.name]
    assert timeline.flags == []


def test_stable_sort_across_files(default_contract, source_a) -> None:
    contract = single_contract(default_contract, source_a)
    raw = [_raw(1000, 2, "z.csv"), _raw(1000, 1, "a.csv"), _raw(1000, 1, "z.csv")]
    timeline = build_timelines(raw, contract)[source_a.name]
    assert [(point.origin, point.lineno) for point in timeline.points] == [
        ("a.csv", 1),
        ("z.csv", 1),
        ("z.csv", 2),
    ]
    duplicate_locations = sorted(
        (flag.origin, flag.lineno)
        for flag in timeline.flags
        if flag.kind == "duplicate"
    )
    assert duplicate_locations == [("z.csv", 1), ("z.csv", 2)]


def test_undeclared_source_is_kept_but_not_in_main_maps(default_contract, source_a) -> None:
    contract = single_contract(default_contract, source_a)
    timelines = build_timelines([_raw(1000, 1, source="other")], contract)
    assert set(timelines) == {source_a.name, "other"}
    assert timelines["other"].points[0].source == "other"
    assert timelines[source_a.name].points == []
