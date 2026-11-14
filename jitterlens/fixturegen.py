"""Deterministic synthetic sampling fixtures (no randomness, no clock reads)."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_MS = 1_735_689_600_000  # 2025-01-01T00:00:00Z
_BASE_DT = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _csv_line(cells: list[object]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(cells)
    return buffer.getvalue().rstrip("\n")


def _sensor_a() -> str:
    offsets = [-8, -5, -2, 0, 3, 6, 8, 4, 1, -1, -4, -7, -3, 2, 5, 7]
    lines = ["ts,source,value"]
    for index in range(240):
        if index == 50:
            lines.append(_csv_line(["bad-ts", "mock-sensor-a", 0]))
        if index == 90:
            lines.append("")
        offset = 800 if index == 60 else offsets[index % len(offsets)]
        shift = 5000 if index >= 120 else 0
        ts_ms = BASE_MS + index * 1000 + offset + shift
        value = 20 + (index % 7)
        lines.append(_csv_line([ts_ms, "mock-sensor-a", value]))
    return "\n".join(lines) + "\n"


def _sensor_b() -> str:
    offsets = [-3, 0, 3, 1, -1, 2, -2, 3, 0, -3, 1, 2]
    lines: list[str] = []
    for index in range(180):
        if index == 100:
            lines.append('{"source":"mock-sensor-b","ts":not-a-number}')
        if index == 70:
            duplicate_shift = 2000 if index >= 40 else 0
            duplicate_ts = (
                BASE_MS + 500_000 + index * 500 + offsets[index % len(offsets)] + duplicate_shift
            )
            lines.append(
                json.dumps(
                    {"source": "mock-sensor-b", "ts": duplicate_ts * 1000},
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        offset = offsets[index % len(offsets)]
        shift = (2000 if index >= 40 else 0) + (1500 if index >= 120 else 0)
        ts_us = (BASE_MS + 500_000 + index * 500 + offset + shift) * 1000
        record: dict[str, object] = {"source": "mock-sensor-b", "ts": ts_us}
        if index % 10 == 0:
            record["quality"] = "nominal"
        lines.append(json.dumps(record, sort_keys=True, separators=(",", ":")))
    return "\n".join(lines) + "\n"


def _sensor_c() -> str:
    lines = ["ts,source"]
    timestamps = [BASE_MS]
    for index in range(159):
        delta = 2000 + (index * 48) // 159
        timestamps.append(timestamps[-1] + delta)
    for ts_ms in timestamps:
        moment = _BASE_DT + timedelta(milliseconds=ts_ms - BASE_MS)
        text = moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        lines.append(_csv_line([text, "mock-sensor-c"]))
    return "\n".join(lines) + "\n"


def generate_fixtures(directory: str | Path) -> list[Path]:
    """Write the three canonical fixture files and return their paths."""
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    outputs = {
        "mock-sensor-a.csv": _sensor_a(),
        "mock-sensor-b.jsonl": _sensor_b(),
        "mock-sensor-c.csv": _sensor_c(),
    }
    written: list[Path] = []
    for name in sorted(outputs):
        path = root / name
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(outputs[name])
        written.append(path)
    return written


def fixture_paths(directory: str | Path) -> list[Path]:
    return sorted(Path(directory).glob("mock-*"))
