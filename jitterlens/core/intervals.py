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


def _family_summary(members: list[dict[str, object]]) -> dict[str, object]:
    sources = sorted({str(member["source"]) for member in members})
    pair_overlaps = 0
    for index, left in enumerate(members):
        for right in members[index + 1 :]:
            if left["source"] == right["source"]:
                continue
            if int(left["start_ts"]) <= int(right["end_ts"]) and int(
                right["start_ts"]
            ) <= int(left["end_ts"]):
                pair_overlaps += 1
    return {
        "start_ts": int(members[0]["start_ts"]),
        "end_ts": max(int(member["end_ts"]) for member in members),
        "interval_count": len(members),
        "source_count": len(sources),
        "sources": sources,
        "interval_ids": [str(member["id"]) for member in members],
        "cross_source_pair_overlaps": pair_overlaps,
    }


def cross_source_merge_metric(intervals: list[dict[str, object]]) -> dict[str, object]:
    """Cluster intervals into overlap families, ignoring source boundaries.

    Side-band metric only: the input list is re-sorted internally and never
    mutated, so per-source interval counts and ``source#序号`` ids cannot drift.
    Overlap uses closed-interval semantics: ``[start_ts, end_ts]`` ranges that
    share at least one timestamp overlap, so intervals merely touching at an
    endpoint (``a.end_ts == b.start_ts``) fall into the same family, as do
    zero-length intervals from several sources at one timestamp. A family is
    the transitive closure of pairwise overlaps along the time axis.
    """
    ordered = sorted(
        intervals,
        key=lambda item: (
            int(item["start_ts"]),
            int(item["end_ts"]),
            str(item["source"]),
            str(item["id"]),
        ),
    )
    families: list[dict[str, object]] = []
    current: list[dict[str, object]] = []
    current_end: int | None = None
    for interval in ordered:
        start = int(interval["start_ts"])
        end = int(interval["end_ts"])
        if current and current_end is not None and start > current_end:
            families.append(_family_summary(current))
            current = []
            current_end = None
        current.append(interval)
        current_end = end if current_end is None else max(current_end, end)
    if current:
        families.append(_family_summary(current))
    return {
        "overlap_semantics": "closed",
        "intervals_total": len(ordered),
        "family_count": len(families),
        "multi_source_family_count": sum(
            1 for family in families if int(family["source_count"]) > 1
        ),
        "merged_interval_count": len(families),
        "reducible_intervals": len(ordered) - len(families),
        "cross_source_pair_overlaps": sum(
            int(family["cross_source_pair_overlaps"]) for family in families
        ),
        "families": families,
    }


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
