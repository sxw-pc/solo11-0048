"""Input readers and shared source-routing helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Sequence

from ..config import Contract, SourceContract, TimestampSpec
from ..errors import JitterLensError
from ..routing import RoutingDecision, decide_source, match_candidates


@dataclass(frozen=True)
class RawPoint:
    ts_ms: int
    source: str
    origin: str
    lineno: int
    unknown_fields: tuple[tuple[str, str], ...] = ()
    routing: RoutingDecision | None = None


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


def select_source(
    candidates: Sequence[SourceContract], label: str | None
) -> SourceContract | None:
    """Route one record to exactly one contract source.

    Compatibility wrapper around `jitterlens.routing.decide_source`; new code
    should use `decide_source` directly to also get candidates and rationale.
    """
    return decide_source(candidates, label).source


def attribution_issue(
    source: str,
    origin: str,
    lineno: int,
    label: str | None,
    decision: RoutingDecision,
) -> ParseIssue:
    """Build the E_READ_005 issue for a record whose attribution is undecidable.

    The record is reported (never silently dropped) with the candidate list and
    the decision reason so the conflict can be audited from report and ledger.
    """
    names = ",".join(decision.candidate_names)
    return ParseIssue(
        source=source,
        origin=origin,
        lineno=lineno,
        code="E_READ_005",
        message=(
            "cannot attribute record to a unique source: "
            f"label={label!r} candidates=[{names}] reason={decision.reason}"
        ),
    )


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


def _header_columns(candidates: Sequence[SourceContract]) -> set[str]:
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
        candidates = match_candidates(contract.sources, origin)
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
