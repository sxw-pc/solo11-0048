"""Interval classification, tolerance checks and clock-drift detection."""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from statistics import median_low

from ..config import SourceContract
from .timeline import SamplePoint, SourceTimeline

#: Pair kinds that describe a real measured interval between two instants.
#: Zero-delta pairs (``duplicate`` under mark, ``concurrent`` under allow)
#: stay in the delta sequence but are never measured intervals.
MEASURED_KINDS = ("on_time", "jitter", "gap")


@dataclass(frozen=True)
class PairClassification:
    prev_index: int
    cur_index: int
    delta_ms: int
    abs_dev_ms: int
    kind: str
    rel_dev: Fraction


@dataclass
class Classification:
    pairs: list[PairClassification] = field(default_factory=list)
    point_kinds: list[set[str]] = field(default_factory=list)
    drift_ranges: list[dict[str, int]] = field(default_factory=list)

    def count_kind(self, kind: str) -> int:
        return sum(1 for kinds in self.point_kinds if kind in kinds)


def is_on_time(abs_dev: int, period: int, source: SourceContract) -> bool:
    """Absolute or relative deviation within tolerance (inclusive)."""
    if abs_dev <= source.jitter_abs_ms:
        return True
    return Fraction(abs_dev, period) <= source.jitter_rel


def classify_timeline(timeline: SourceTimeline, source: SourceContract) -> Classification:
    point_kinds: list[set[str]] = [set() for _ in timeline.points]
    for flag in timeline.flags:
        point_kinds[flag.sorted_index].add(flag.kind)

    pairs: list[PairClassification] = []
    gap_limit = source.max_gap_points * source.period_ms
    for index in range(1, len(timeline.points)):
        previous = timeline.points[index - 1]
        current = timeline.points[index]
        delta = current.ts_ms - previous.ts_ms
        if delta == 0:
            if source.duplicate_policy == "allow":
                # Concurrent samples at one instant: the pair stays in the
                # delta sequence but is not a measured interval, so it gets
                # no deviation and never falls into jitter/gap.
                kind, abs_dev, rel_dev = "concurrent", 0, Fraction(0)
            else:
                kind, abs_dev, rel_dev = "duplicate", source.period_ms, Fraction(1)
            pairs.append(
                PairClassification(
                    prev_index=index - 1,
                    cur_index=index,
                    delta_ms=0,
                    abs_dev_ms=abs_dev,
                    kind=kind,
                    rel_dev=rel_dev,
                )
            )
            continue
        abs_dev = abs(delta - source.period_ms)
        rel_dev = Fraction(abs_dev, source.period_ms)
        if delta > gap_limit:
            kind = "gap"
        elif is_on_time(abs_dev, source.period_ms, source):
            kind = "on_time"
        else:
            kind = "jitter"
        pairs.append(
            PairClassification(
                prev_index=index - 1,
                cur_index=index,
                delta_ms=delta,
                abs_dev_ms=abs_dev,
                kind=kind,
                rel_dev=rel_dev,
            )
        )
        if kind in ("jitter", "gap"):
            point_kinds[index - 1].add(kind)
            point_kinds[index].add(kind)

    drift_ranges = _detect_drift(pairs, point_kinds, source, timeline.points)
    return Classification(pairs=pairs, point_kinds=point_kinds, drift_ranges=drift_ranges)


def _detect_drift(
    pairs: list[PairClassification],
    point_kinds: list[set[str]],
    source: SourceContract,
    points: list[SamplePoint],
) -> list[dict[str, int]]:
    positive = [pair for pair in pairs if pair.kind in MEASURED_KINDS]
    window = source.drift_window
    if len(positive) < window * 2:
        return []
    step = max(1, window // 2)
    windows: list[dict[str, int]] = []
    for start in range(0, len(positive) - window + 1, step):
        chunk = positive[start : start + window]
        windows.append(
            {
                "start_index": chunk[0].prev_index,
                "end_index": chunk[-1].cur_index,
                "median": int(median_low(pair.delta_ms for pair in chunk)),
            }
        )
    if len(windows) < 3:
        return []

    result: list[dict[str, int]] = []
    low = 0
    while low < len(windows) - 1:
        sign = windows[low + 1]["median"] - windows[low]["median"]
        if sign == 0:
            low += 1
            continue
        sign = 1 if sign > 0 else -1
        high = low + 1
        while high + 1 < len(windows):
            nxt = windows[high + 1]["median"] - windows[high]["median"]
            if nxt == 0 or (1 if nxt > 0 else -1) != sign:
                break
            high += 1
        change = abs(windows[high]["median"] - windows[low]["median"])
        if high - low >= 2 and change * source.drift_threshold_rel.denominator > (
            source.period_ms * source.drift_threshold_rel.numerator
        ):
            start_index = windows[low]["start_index"]
            end_index = windows[high]["end_index"]
            for index in range(start_index, end_index + 1):
                point_kinds[index].add("clock_drift")
            result.append(
                {
                    "start_index": start_index,
                    "end_index": end_index,
                    "start_ts": points[start_index].ts_ms,
                    "end_ts": points[end_index].ts_ms,
                    "window_count": high - low + 1,
                    "median_change_ms": change,
                }
            )
        low = high if high > low else low + 1
    return result
