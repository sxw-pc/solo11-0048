"""Deterministic fill and downsample plans (plans are never written back)."""

from __future__ import annotations

from typing import Any


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
                plans.append(_fill_plan(source, interval))
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


def _fill_plan(source: Any, interval: dict[str, object]) -> dict[str, object]:
    start = int(interval["start_ts"])
    end = int(interval["end_ts"])
    period = source.period_ms
    expected = max(0, (end - start - 1) // period) if end > start else 0
    capacity = source.max_gap_points
    insert_ts: list[int] = []
    cursor = start + period
    while cursor < end and len(insert_ts) < capacity:
        insert_ts.append(cursor)
        cursor += period
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
