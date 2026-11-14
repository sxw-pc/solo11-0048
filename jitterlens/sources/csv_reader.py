"""CSV sampling-log reader."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Sequence

from ..config import SourceContract
from ..routing import decide_source
from . import ParseIssue, RawPoint, _header_columns, attribution_issue, parse_timestamp


def _issue(source: str, origin: str, lineno: int, code: str, message: str) -> ParseIssue:
    return ParseIssue(source=source, origin=origin, lineno=lineno, code=code, message=message)


def read_csv(
    path: Path,
    origin: str,
    candidates: Sequence[SourceContract],
) -> tuple[list[RawPoint], list[ParseIssue]]:
    points: list[RawPoint] = []
    issues: list[ParseIssue] = []
    default_source = candidates[0].name
    header: list[str] | None = None
    header_line = 0
    source_column: str | None = None
    recognized: set[str] = set()

    try:
        handle = path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        return [], [_issue(default_source, origin, 0, "E_READ_001", f"cannot open CSV: {exc}")]

    with handle:
        reader = csv.reader(handle)
        try:
            for row in reader:
                lineno = reader.line_num
                if not row or all(not str(cell).strip() for cell in row):
                    issues.append(_issue(default_source, origin, lineno, "E_READ_002", "empty line"))
                    continue
                if header is None:
                    header = [str(cell).strip() for cell in row]
                    header_line = lineno
                    if any(not name for name in header):
                        issues.append(
                            _issue(default_source, origin, lineno, "E_READ_004", "empty CSV header name")
                        )
                        header = None
                        continue
                    if len(set(header)) != len(header):
                        issues.append(
                            _issue(default_source, origin, lineno, "E_READ_004", "duplicate CSV header name")
                        )
                        header = None
                        continue
                    recognized = _header_columns(candidates)
                    for source in candidates:
                        if source.source_column in header:
                            source_column = source.source_column
                            break
                    if len(candidates) > 1 and source_column is None:
                        issues.append(
                            _issue(
                                default_source,
                                origin,
                                lineno,
                                "E_READ_004",
                                "multiple sources match file but no source column is present",
                            )
                        )
                        return [], issues
                    continue

                if len(row) != len(header):
                    label = None
                    if source_column is not None:
                        index = header.index(source_column)
                        if index < len(row):
                            label = row[index].strip() or None
                    decision = decide_source(candidates, label)
                    issues.append(
                        _issue(
                            decision.source_name or default_source,
                            origin,
                            lineno,
                            "E_READ_002",
                            f"field count {len(row)} does not match header count {len(header)}",
                        )
                    )
                    continue

                record = {name: row[index] for index, name in enumerate(header)}
                label = None
                if source_column is not None:
                    label = record.get(source_column, "").strip() or None
                decision = decide_source(candidates, label)
                chosen = decision.source
                if chosen is None:
                    issues.append(attribution_issue(default_source, origin, lineno, label, decision))
                    continue
                ts_column = chosen.timestamp.column
                if ts_column not in record:
                    issues.append(
                        _issue(
                            chosen.name,
                            origin,
                            lineno,
                            "E_READ_004",
                            f"timestamp column {ts_column!r} is missing",
                        )
                    )
                    continue
                try:
                    ts_ms = parse_timestamp(record.get(ts_column), chosen.timestamp)
                except ValueError as exc:
                    issues.append(_issue(chosen.name, origin, lineno, "E_READ_003", str(exc)))
                    continue
                unknown = tuple(
                    sorted((name, record[name]) for name in header if name not in recognized)
                )
                points.append(
                    RawPoint(
                        ts_ms=ts_ms,
                        source=chosen.name,
                        origin=origin,
                        lineno=lineno,
                        unknown_fields=unknown,
                        routing=decision,
                    )
                )
        except csv.Error as exc:
            issues.append(
                _issue(default_source, origin, reader.line_num, "E_READ_002", f"CSV parse error: {exc}")
            )

    if header is None and not issues:
        issues.append(_issue(default_source, origin, 0, "E_READ_004", "CSV file has no header"))
    elif header is None and not any(issue.lineno == header_line for issue in issues):
        issues.append(_issue(default_source, origin, 0, "E_READ_004", "CSV file has no usable header"))
    return points, issues
