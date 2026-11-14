"""JSON Lines sampling-log reader."""

from __future__ import annotations

import json
from pathlib import Path

from ..config import SourceContract
from . import ParseIssue, RawPoint, parse_timestamp, select_source


def _issue(source: str, origin: str, lineno: int, code: str, message: str) -> ParseIssue:
    return ParseIssue(source=source, origin=origin, lineno=lineno, code=code, message=message)


def read_jsonl(
    path: Path,
    origin: str,
    candidates: list[SourceContract],
) -> tuple[list[RawPoint], list[ParseIssue]]:
    points: list[RawPoint] = []
    issues: list[ParseIssue] = []
    default_source = candidates[0].name
    source_fields = {source.source_field for source in candidates}
    timestamp_columns = {source.timestamp.column for source in candidates}

    try:
        handle = path.open("r", encoding="utf-8-sig")
    except OSError as exc:
        return [], [_issue(default_source, origin, 0, "E_READ_001", f"cannot open JSONL: {exc}")]

    with handle:
        for lineno, raw_line in enumerate(handle, 1):
            line = raw_line.strip()
            if not line:
                issues.append(_issue(default_source, origin, lineno, "E_READ_002", "empty line"))
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                issues.append(
                    _issue(default_source, origin, lineno, "E_READ_002", f"invalid JSON: {exc.msg}")
                )
                continue
            if not isinstance(record, dict):
                issues.append(
                    _issue(default_source, origin, lineno, "E_READ_002", "JSONL record must be an object")
                )
                continue

            label = None
            for field in sorted(source_fields):
                value = record.get(field)
                if isinstance(value, str) and value.strip():
                    label = value.strip()
                    break
            chosen = select_source(candidates, label)
            if chosen is None:
                issues.append(
                    _issue(
                        default_source,
                        origin,
                        lineno,
                        "E_READ_004",
                        f"cannot route source label {label!r} for file",
                    )
                )
                continue
            ts_column = chosen.timestamp.column
            if ts_column not in record:
                issues.append(
                    _issue(
                        chosen.name,
                        origin,
                        lineno,
                        "E_READ_003",
                        f"missing timestamp field {ts_column!r}",
                    )
                )
                continue
            try:
                ts_ms = parse_timestamp(record[ts_column], chosen.timestamp)
            except ValueError as exc:
                issues.append(_issue(chosen.name, origin, lineno, "E_READ_003", str(exc)))
                continue

            known = set(source_fields) | set(timestamp_columns)
            unknown = tuple(
                sorted(
                    (
                        str(key),
                        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    )
                    for key, value in record.items()
                    if key not in known
                )
            )
            points.append(
                RawPoint(
                    ts_ms=ts_ms,
                    source=chosen.name,
                    origin=origin,
                    lineno=lineno,
                    unknown_fields=unknown,
                )
            )
    return points, issues
