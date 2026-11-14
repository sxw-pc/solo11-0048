"""Input readers and shared source-routing helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from ..config import Contract, SourceContract, TimestampSpec
from ..errors import JitterLensError


@dataclass(frozen=True)
class RawPoint:
    ts_ms: int
    source: str
    origin: str
    lineno: int
    unknown_fields: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ParseIssue:
    source: str
    origin: str
    lineno: int
    code: str
    message: str


@dataclass
class ReadResult:
    points: list[RawPoint] = field(default_factory=list)
    issues: list[ParseIssue] = field(default_factory=list)
    files: list[str] = field(default_factory=list)


SUPPORTED_SUFFIXES = {".csv", ".jsonl"}
_UNIT_MS = {"s": Decimal(1000), "ms": Decimal(1), "us": Decimal("0.001")}
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def select_source(candidates: list[SourceContract], label: str | None) -> SourceContract | None:
    """Route one record to exactly one contract source."""
    if label:
        for source in candidates:
            if source.match_value == label:
                return source
        for source in candidates:
            if source.name == label:
                return source
        generic = [source for source in candidates if source.match_value is None]
        if len(generic) == 1:
            return generic[0]
        return None
    if len(candidates) == 1:
        return candidates[0]
    return None


def parse_timestamp(raw: object, spec: TimestampSpec) -> int:
    """Convert an epoch or ISO-8601 timestamp to integer Unix milliseconds."""
    if raw is None or isinstance(raw, bool):
        raise ValueError("missing or boolean timestamp")
    if spec.format == "iso8601":
        text = str(raw).strip()
        if not text:
            raise ValueError("empty timestamp")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"invalid ISO-8601 timestamp: {text}") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        delta = parsed.astimezone(timezone.utc) - _EPOCH
        return (
            delta.days * 86_400_000
            + delta.seconds * 1000
            + delta.microseconds // 1000
        )
    text = str(raw).strip()
    if not text:
        raise ValueError("empty timestamp")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"invalid numeric timestamp: {text}") from exc
    if not value.is_finite():
        raise ValueError("timestamp must be finite")
    millis = value * _UNIT_MS[spec.unit]
    return int(millis.to_integral_value(rounding=ROUND_HALF_UP))


def _header_columns(candidates: list[SourceContract]) -> set[str]:
    recognized: set[str] = set()
    for source in candidates:
        recognized.add(source.source_column)
        recognized.add(source.timestamp.column)
    return recognized


def _reader_issue(source: str, origin: str, lineno: int, code: str, message: str) -> ParseIssue:
    return ParseIssue(source=source, origin=origin, lineno=lineno, code=code, message=message)


def read_inputs(
    input_dir: str | Path,
    contract: Contract,
) -> ReadResult:
    """Read every CSV/JSONL file below input_dir in a stable order."""
    root = Path(input_dir)
    if not root.is_dir():
        raise JitterLensError("E_READ_001", f"input directory not found: {root}")
    paths = sorted(
        (path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    if not paths:
        raise JitterLensError("E_READ_001", f"no CSV/JSONL inputs below: {root}")
    result = ReadResult()
    from .csv_reader import read_csv
    from .jsonl_reader import read_jsonl

    for path in paths:
        origin = path.relative_to(root).as_posix()
        candidates = [
            source
            for source in contract.sources
            if _filename_matches(origin, source.match_filename)
        ]
        result.files.append(origin)
        if not candidates:
            result.issues.append(
                _reader_issue("<unmatched>", origin, 0, "E_READ_004", "no contract source matches file")
            )
            continue
        reader = read_csv if path.suffix.lower() == ".csv" else read_jsonl
        try:
            points, issues = reader(path, origin, candidates)
        except OSError as exc:
            result.issues.append(
                _reader_issue(candidates[0].name, origin, 0, "E_READ_001", f"cannot read file: {exc}")
            )
            continue
        result.points.extend(points)
        result.issues.extend(issues)
    return result


def _filename_matches(filename: str, pattern: str) -> bool:
    from fnmatch import fnmatchcase

    name = filename.rsplit("/", 1)[-1]
    return fnmatchcase(filename, pattern) or fnmatchcase(name, pattern)
