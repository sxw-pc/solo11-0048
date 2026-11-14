"""Aggregation of abnormal points into stable time intervals."""

from __future__ import annotations

from .timeline import SamplePoint


def build_intervals(
    source: str,
    points: list[SamplePoint],
    point_kinds: list[set[str]],
    merge_gap_points: int,
) -> list[dict[str, object]]:
    """Merge abnormal point runs separated by at most N normal points."""
    abnormal = [index for index, kinds in enumerate(point_kinds) if kinds]
    if not abnormal:
        return []
    groups: list[list[int]] = []
    current = [abnormal[0]]
    for index in abnormal[1:]:
        if index - current[-1] - 1 <= merge_gap_points:
            current.append(index)
        else:
            groups.append(current)
            current = [index]
    groups.append(current)

    intervals: list[dict[str, object]] = []
    for group in groups:
        kinds = sorted({kind for index in group for kind in point_kinds[index]})
        first = points[group[0]]
        last = points[group[-1]]
        intervals.append(
            {
                "id": "",
                "source": source,
                "start_ts": first.ts_ms,
                "end_ts": last.ts_ms,
                "kinds": kinds,
                "point_count": len(group),
                "start_origin": first.origin,
                "start_lineno": first.lineno,
                "end_origin": last.origin,
                "end_lineno": last.lineno,
            }
        )
    intervals.sort(key=lambda item: (item["start_ts"], item["end_ts"], item["start_lineno"]))
    for ordinal, interval in enumerate(intervals, 1):
        interval["id"] = f"{source}#{ordinal:04d}"
    return intervals


def max_consecutive_abnormal(point_kinds: list[set[str]]) -> int:
    best = 0
    run = 0
    for kinds in point_kinds:
        if kinds:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best
