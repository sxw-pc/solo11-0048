from __future__ import annotations

import copy

from jitterlens.core.intervals import (
    build_intervals,
    cross_source_merge_metric,
    max_consecutive_abnormal,
)
from jitterlens.core.timeline import SamplePoint


def _points(count: int) -> list[SamplePoint]:
    return [
        SamplePoint(ts_ms=1000 + index * 1000, source="s", origin="x.csv", lineno=index + 2, input_seq=index)
        for index in range(count)
    ]


def test_merge_gap_points_threshold() -> None:
    points = _points(6)
    kinds = [set() for _ in points]
    kinds[0].add("jitter")
    kinds[2].add("gap")
    kinds[3].add("duplicate")
    merged = build_intervals("s", points, kinds, merge_gap_points=1)
    assert len(merged) == 1
    assert merged[0]["start_ts"] == 1000
    assert merged[0]["end_ts"] == 4000
    assert merged[0]["kinds"] == ["duplicate", "gap", "jitter"]
    assert merged[0]["point_count"] == 3

    separate = build_intervals("s", points, kinds, merge_gap_points=0)
    assert len(separate) == 2
    assert [interval["kinds"] for interval in separate] == [["jitter"], ["duplicate", "gap"]]


def test_interval_locations_and_ids() -> None:
    points = _points(4)
    kinds = [set() for _ in points]
    kinds[1].add("clock_drift")
    kinds[3].add("gap")
    intervals = build_intervals("source-x", points, kinds, merge_gap_points=0)
    assert [interval["id"] for interval in intervals] == ["source-x#0001", "source-x#0002"]
    assert intervals[0] == {
        "id": "source-x#0001",
        "source": "source-x",
        "start_ts": 2000,
        "end_ts": 2000,
        "kinds": ["clock_drift"],
        "point_count": 1,
        "start_origin": "x.csv",
        "start_lineno": 3,
        "end_origin": "x.csv",
        "end_lineno": 3,
    }
    assert intervals[1]["start_lineno"] == 5


def test_no_abnormal_points_and_max_run() -> None:
    points = _points(3)
    assert build_intervals("s", points, [set(), set(), set()], 2) == []
    kinds = [{"jitter"}, {"jitter"}, set(), {"gap"}, {"gap"}, set()]
    assert max_consecutive_abnormal(kinds) == 2
    assert max_consecutive_abnormal([]) == 0


def _interval(source: str, ordinal: int, start: int, end: int) -> dict[str, object]:
    return {
        "id": f"{source}#{ordinal:04d}",
        "source": source,
        "start_ts": start,
        "end_ts": end,
        "kinds": ["gap"],
        "point_count": 1,
        "start_origin": "x.csv",
        "start_lineno": 1,
        "end_origin": "x.csv",
        "end_lineno": 1,
    }


def test_metric_clusters_cross_source_overlaps() -> None:
    intervals = [
        _interval("b", 1, 1300, 1400),
        _interval("a", 1, 1000, 1300),
        _interval("c", 1, 1350, 1450),
        _interval("a", 2, 5000, 5100),
    ]
    metric = cross_source_merge_metric(intervals)
    assert metric["overlap_semantics"] == "closed"
    assert metric["intervals_total"] == 4
    assert metric["family_count"] == 2
    assert metric["merged_interval_count"] == 2
    assert metric["reducible_intervals"] == 2
    assert metric["multi_source_family_count"] == 1
    assert metric["cross_source_pair_overlaps"] == 2
    family = metric["families"][0]
    assert family["interval_ids"] == ["a#0001", "b#0001", "c#0001"]
    assert family["sources"] == ["a", "b", "c"]
    assert family["interval_count"] == 3
    assert family["source_count"] == 3
    # closed-interval pairs: a#0001 touches b#0001 at 1300, b#0001 overlaps
    # c#0001; a#0001 and c#0001 do not overlap directly.
    assert family["cross_source_pair_overlaps"] == 2
    assert metric["families"][1]["interval_ids"] == ["a#0002"]


def test_metric_closed_interval_boundary_and_same_timestamp() -> None:
    touching = cross_source_merge_metric(
        [_interval("a", 1, 100, 200), _interval("b", 1, 200, 300)]
    )
    assert touching["family_count"] == 1
    assert touching["cross_source_pair_overlaps"] == 1
    disjoint = cross_source_merge_metric(
        [_interval("a", 1, 100, 200), _interval("b", 1, 201, 300)]
    )
    assert disjoint["family_count"] == 2
    assert disjoint["reducible_intervals"] == 0
    same_ts = cross_source_merge_metric(
        [_interval("a", 1, 500, 500), _interval("b", 1, 500, 500)]
    )
    assert same_ts["family_count"] == 1
    assert same_ts["multi_source_family_count"] == 1
    assert cross_source_merge_metric([])["family_count"] == 0


def test_metric_is_order_independent_and_never_mutates() -> None:
    intervals = [
        _interval("b", 2, 900, 950),
        _interval("a", 1, 100, 200),
        _interval("b", 1, 150, 250),
    ]
    snapshot = copy.deepcopy(intervals)
    forward = cross_source_merge_metric(intervals)
    reverse = cross_source_merge_metric(list(reversed(intervals)))
    assert intervals == snapshot
    assert forward == reverse
    assert forward["families"][0]["interval_ids"] == ["a#0001", "b#0001"]
    assert forward["families"][1]["interval_ids"] == ["b#0002"]


def test_metric_does_not_change_per_source_merging() -> None:
    points = _points(5)
    kinds = [set() for _ in points]
    kinds[0].add("jitter")
    kinds[2].add("gap")
    merged = build_intervals("s", points, kinds, merge_gap_points=1)
    snapshot = copy.deepcopy(merged)
    cross_source_merge_metric(merged)
    assert merged == snapshot
    assert build_intervals("s", points, kinds, merge_gap_points=1) == snapshot
    assert [interval["id"] for interval in merged] == ["s#0001"]
