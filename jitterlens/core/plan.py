"""Deterministic fill and downsample plans (plans are never written back).

Fill plans share the missing-point semantics of
:func:`jitterlens.core.stats.missing_points_between`: a gap pair's two
endpoints are sampled, and ``expected_missing`` counts the interior nominal
grid positions of every gap pair contained in the interval. Summing
``expected_missing`` over a source's fill plans therefore reproduces the
source's ``missing_estimate`` from report.json. ``truncated`` marks exactly
the plans where ``expected_missing`` exceeds the number of emitted
``insert_ts`` candidates (capped by ``max_gap_points``).
"""

from __future__ import annotations

from typing import Any

from .stats import missing_grid_timestamps, missing_points_between


def build_plans(
    analyses: list[dict[str, Any]],
    run_id: str,
    contract_sha256: str,
    generated_at: str,
) -> dict[str, object]:
    plans: list[dict[str, object]] = []
    for analysis in analyses:
        source = analysis["source"]
        timeline = analysis["timeline"]
        classification = analysis["classification"]
        for interval in analysis["intervals"]:
            kinds = interval["kinds"]
            if "gap" in kinds:
                spans = _gap_spans(classification, timeline, interval)
                plans.append(_fill_plan(source, interval, spans))
            if "jitter" in kinds and _has_excessive_jitter(
                classification, timeline, interval, source.downsample_threshold
            ):
                plans.append(_downsample_plan(source, timeline, interval))
    for ordinal, plan in enumerate(plans, 1):
        plan["id"] = f"plan-{ordinal:04d}"
    summary = {
        "fill": sum(1 for plan in plans if plan["kind"] == "fill"),
        "downsample": sum(1 for plan in plans if plan["kind"] == "downsample"),
        "truncated": sum(1 for plan in plans if bool(plan["truncated"])),
    }
    return {
        "schema_version": 1,
        "run_id": run_id,
        "generated_at": generated_at,
        "contract_sha256": contract_sha256,
        "summary": summary,
        "plans": plans,
    }


def _gap_spans(
    classification: Any,
    timeline: Any,
    interval: dict[str, object],
) -> list[tuple[int, int]]:
    """Timestamp spans of the gap pairs contained in one interval.

    Both endpoints of a gap pair are abnormal points, so each pair belongs
    to exactly one interval; filtering by the interval bounds attributes
    every gap pair to its own interval exactly once.
    """
    start = int(interval["start_ts"])
    end = int(interval["end_ts"])
    spans: list[tuple[int, int]] = []
    for pair in classification.pairs:
        if pair.kind != "gap":
            continue
        prev_ts = timeline.points[pair.prev_index].ts_ms
        cur_ts = timeline.points[pair.cur_index].ts_ms
        if start <= prev_ts and cur_ts <= end:
            spans.append((prev_ts, cur_ts))
    return spans


def _fill_plan(
    source: Any,
    interval: dict[str, object],
    gap_spans: list[tuple[int, int]],
) -> dict[str, object]:
    start = int(interval["start_ts"])
    end = int(interval["end_ts"])
    period = source.period_ms
    # Same computation as stats.missing_estimate, summed over the gap pairs
    # of this interval: interior grid positions between sampled endpoints.
    expected = sum(
        missing_points_between(span_start, span_end, period)
        for span_start, span_end in gap_spans
    )
    capacity = source.max_gap_points
    insert_ts: list[int] = []
    for span_start, span_end in gap_spans:
        for ts_ms in missing_grid_timestamps(span_start, span_end, period):
            if len(insert_ts) >= capacity:
                break
            insert_ts.append(ts_ms)
        if len(insert_ts) >= capacity:
            break
    return {
        "id": "",
        "kind": "fill",
        "source": source.name,
        "start_ts": start,
        "end_ts": end,
        "period_ms": period,
        "reason": "gap_interval",
        "expected_missing": expected,
        "insert_ts": insert_ts,
        "truncated": expected > len(insert_ts),
    }


def _has_excessive_jitter(
    classification: Any,
    timeline: Any,
    interval: dict[str, object],
    threshold: Any,
) -> bool:
    start = int(interval["start_ts"])
    end = int(interval["end_ts"])
    for pair in classification.pairs:
        if pair.kind != "jitter" or pair.rel_dev <= threshold:
            continue
        previous = timeline.points[pair.prev_index]
        current = timeline.points[pair.cur_index]
        if previous.ts_ms >= start and current.ts_ms <= end:
            return True
    return False


def _downsample_plan(source: Any, timeline: Any, interval: dict[str, object]) -> dict[str, object]:
    start = int(interval["start_ts"])
    end = int(interval["end_ts"])
    relevant = [point for point in timeline.points if start <= point.ts_ms <= end]
    keep_ts = [point.ts_ms for index, point in enumerate(relevant) if index % 2 == 0]
    return {
        "id": "",
        "kind": "downsample",
        "source": source.name,
        "start_ts": start,
        "end_ts": end,
        "period_ms": source.period_ms,
        "reason": "jitter_exceeds_downsample_threshold",
        "downsample_threshold": round(float(source.downsample_threshold), 9),
        "keep_every": 2,
        "considered_points": len(relevant),
        "keep_ts": keep_ts,
        "truncated": False,
    }
