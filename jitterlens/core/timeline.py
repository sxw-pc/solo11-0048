"""Timeline normalization and structural timestamp diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Contract, SourceContract
from ..sources import RawPoint


@dataclass(frozen=True)
class SamplePoint:
    ts_ms: int
    source: str
    origin: str
    lineno: int
    input_seq: int
    unknown_fields: tuple[tuple[str, str], ...] = ()

    def sort_key(self) -> tuple[int, str, int, int]:
        return (self.ts_ms, self.origin, self.lineno, self.input_seq)


@dataclass(frozen=True)
class PointFlag:
    kind: str
    ts_ms: int
    origin: str
    lineno: int
    sorted_index: int


@dataclass
class SourceTimeline:
    source: str
    points: list[SamplePoint] = field(default_factory=list)
    flags: list[PointFlag] = field(default_factory=list)

    @property
    def point_count(self) -> int:
        return len(self.points)

    def structural_counts(self) -> dict[str, int]:
        counts = {"duplicate": 0, "monotonic": 0, "out_of_order": 0}
        for flag in self.flags:
            counts[flag.kind] = counts.get(flag.kind, 0) + 1
        return counts


def build_timelines(raw_points: list[RawPoint], contract: Contract) -> dict[str, SourceTimeline]:
    """Group points per contract source and flag rollbacks, duplicates and disorder."""
    timeline_map = {source.name: SourceTimeline(source.name) for source in contract.sources}
    seq = 0
    for raw in raw_points:
        point = SamplePoint(
            ts_ms=raw.ts_ms,
            source=raw.source,
            origin=raw.origin,
            lineno=raw.lineno,
            input_seq=seq,
            unknown_fields=raw.unknown_fields,
        )
        seq += 1
        timeline_map.setdefault(raw.source, SourceTimeline(raw.source)).points.append(point)

    for source in contract.sources:
        timeline = timeline_map[source.name]
        timeline.points.sort(key=SamplePoint.sort_key)
        timeline.flags = _structural_flags(timeline.points, source)
    return timeline_map


def _structural_flags(points: list[SamplePoint], source: SourceContract) -> list[PointFlag]:
    if not points:
        return []
    by_seq = {point.input_seq: index for index, point in enumerate(points)}
    flags: dict[tuple[int, str], PointFlag] = {}

    def add(point: SamplePoint, kind: str) -> None:
        index = by_seq[point.input_seq]
        flags[(index, kind)] = PointFlag(
            kind=kind,
            ts_ms=point.ts_ms,
            origin=point.origin,
            lineno=point.lineno,
            sorted_index=index,
        )

    input_order = sorted(points, key=lambda point: point.input_seq)
    max_ts: int | None = None
    for point in input_order:
        if max_ts is not None and point.ts_ms < max_ts:
            add(point, "out_of_order")
            if source.monotonic:
                add(point, "monotonic")
        max_ts = point.ts_ms if max_ts is None else max(max_ts, point.ts_ms)

    seen: set[int] = set()
    for point in points:
        if point.ts_ms in seen and source.duplicate_policy == "mark":
            add(point, "duplicate")
        seen.add(point.ts_ms)

    return sorted(flags.values(), key=lambda flag: (flag.sorted_index, flag.kind))
