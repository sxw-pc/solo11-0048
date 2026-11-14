"""Input readers and shared source-routing helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path

from ..config import Contract, SourceContract, TimestampSpec
from ..errors import JitterLensError

# Timestamp normalization contract (single rule set for every input format):
# parsing keeps full precision as an exact rational number of milliseconds;
# one operator then rejects out-of-range values and rounds half up.
ROUNDING_POLICY = "half_up"
MIN_EPOCH_MS = 0
MAX_EPOCH_MS = 2**63 - 1  # report/ledger timestamps are signed 64-bit integers


@dataclass(frozen=True)
class RoundingTrace:
    """Audit record left when a timestamp carried sub-millisecond precision."""

    raw: str
    exact_ms: str
    direction: str  # "up" or "down"


@dataclass(frozen=True)
class RawPoint:
    ts_ms: int
    source: str
    origin: str
    lineno: int
    unknown_fields: tuple[tuple[str, str], ...] = ()
    rounding: RoundingTrace | None = None


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


@dataclass(frozen=True)
class TimestampNormalization:
    """Exact parse result plus the rounded integer millisecond value."""

    ts_ms: int
    exact_ms: Fraction
    direction: str | None  # None when already integral, else "up"/"down"

    def trace(self, raw: object) -> RoundingTrace | None:
        """Return the audit record for this parse, or None when unrounded."""
        if self.direction is None:
            return None
        return RoundingTrace(
            raw=str(raw).strip(),
            exact_ms=format_exact_ms(self.exact_ms),
            direction=self.direction,
        )


SUPPORTED_SUFFIXES = {".csv", ".jsonl"}
_UNIT_MS = {"s": Fraction(1000), "ms": Fraction(1), "us": Fraction(1, 1000)}
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


def format_exact_ms(value: Fraction) -> str:
    """Render an exact rational millisecond value as a plain decimal string."""
    numerator = value.numerator
    denominator = value.denominator
    sign = "-" if numerator < 0 else ""
    numerator = abs(numerator)
    twos = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    fives = 0
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:  # unreachable from Decimal/ISO inputs
        return f"{sign}{numerator}/{value.denominator}"
    places = max(twos, fives)
    scaled = numerator * 2 ** (places - twos) * 5 ** (places - fives)
    digits = str(scaled).rjust(places + 1, "0")
    if not places:
        return f"{sign}{digits}"
    return f"{sign}{digits[:-places]}.{digits[-places:]}"


def normalize_millis(exact_ms: Fraction) -> tuple[int, str | None]:
    """Collapse an exact rational millisecond value to integer Unix ms.

    Single normalization operator shared by every timestamp path. Rejection
    contract: the exact value must satisfy MIN_EPOCH_MS <= value <=
    MAX_EPOCH_MS, checked before rounding; anything else raises ValueError.
    Accepted values are rounded half up, i.e. floor(exact + 1/2), which is
    unambiguous because every accepted value is non-negative. Returns
    (ts_ms, direction) where direction is None when the exact value was
    already an integer, else "up"/"down" for the rounding move.
    """
    if exact_ms < MIN_EPOCH_MS:
        raise ValueError(
            f"timestamp below minimum {MIN_EPOCH_MS} ms: {format_exact_ms(exact_ms)}"
        )
    if exact_ms > MAX_EPOCH_MS:
        raise ValueError(
            f"timestamp above maximum {MAX_EPOCH_MS} ms: {format_exact_ms(exact_ms)}"
        )
    denominator = exact_ms.denominator
    rounded = (2 * exact_ms.numerator + denominator) // (2 * denominator)
    scaled = rounded * denominator
    if scaled == exact_ms.numerator:
        return rounded, None
    return rounded, "up" if scaled > exact_ms.numerator else "down"


def _epoch_millis(text: str, spec: TimestampSpec) -> Fraction:
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"invalid numeric timestamp: {text}") from exc
    if not value.is_finite():
        raise ValueError("timestamp must be finite")
    # Magnitude gate: a raw decimal exponent outside this window is out of
    # range in any unit (>= 1e25 raw exceeds MAX_EPOCH_MS even in us) or
    # carries precision far below anything integer ms can express. The gate
    # raises the same contract violations as normalize_millis while keeping
    # the exact rational pipeline bounded for absurd exponents like 1e9.
    adjusted = value.adjusted()
    if adjusted > 24 or (value < 0 and adjusted < -30):
        rule, bound = (
            ("below minimum", MIN_EPOCH_MS) if value < 0 else ("above maximum", MAX_EPOCH_MS)
        )
        raise ValueError(f"timestamp {rule} {bound} ms: {text}")
    if adjusted < -30:
        raise ValueError(f"timestamp precision out of range: {text}")
    return Fraction(value) * _UNIT_MS[spec.unit]


def _iso_millis(text: str) -> Fraction:
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid ISO-8601 timestamp: {text}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delta = parsed.astimezone(timezone.utc) - _EPOCH
    total_us = (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
    return Fraction(total_us, 1000)


def parse_timestamp_detail(raw: object, spec: TimestampSpec) -> TimestampNormalization:
    """Normalize an epoch or ISO-8601 timestamp to integer Unix milliseconds.

    Every format is parsed into an exact rational number of milliseconds
    (Decimal/Fraction only, never float) and then collapsed by the shared
    normalize_millis operator, so equivalent representations of the same
    physical instant always land on the same integer millisecond.
    """
    if raw is None or isinstance(raw, bool):
        raise ValueError("missing or boolean timestamp")
    text = str(raw).strip()
    if not text:
        raise ValueError("empty timestamp")
    if spec.format == "iso8601":
        exact = _iso_millis(text)
    else:
        exact = _epoch_millis(text, spec)
    ts_ms, direction = normalize_millis(exact)
    return TimestampNormalization(ts_ms=ts_ms, exact_ms=exact, direction=direction)


def parse_timestamp(raw: object, spec: TimestampSpec) -> int:
    """Convert an epoch or ISO-8601 timestamp to integer Unix milliseconds."""
    return parse_timestamp_detail(raw, spec).ts_ms


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
