from __future__ import annotations

from jitterlens.core.intervals import build_intervals, max_consecutive_abnormal
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
