"""Declarative sampling contract loading and validation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from . import yamlmini
from .errors import JitterLensError
from .routing import RoutingDecision, decide_source, match_candidates

DEFAULT_GENERATED_AT = "1970-01-01T00:00:00Z"

@dataclass(frozen=True)
class TimestampSpec:
    column: str
    unit: str
    format: str
    tz: str

@dataclass(frozen=True)
class SourceContract:
    name: str
    match_filename: str
    match_value: str | None
    match_column: str
    timestamp: TimestampSpec
    source_column: str
    source_field: str
    period_ms: int
    jitter_abs_ms: int
    jitter_rel: Fraction
    max_gap_points: int
    max_gap_ratio: float
    fail_jitter_ratio: float
    max_parse_error_ratio: float
    monotonic: bool
    duplicate_policy: str
    merge_gap_points: int
    drift_window: int
    drift_threshold_rel: Fraction
    downsample_threshold: Fraction
    histogram_buckets: tuple[int, ...]

    @property
    def jitter_rel_float(self) -> float:
        return round(float(self.jitter_rel), 9)

    @property
    def drift_threshold_float(self) -> float:
        return round(float(self.drift_threshold_rel), 9)

@dataclass(frozen=True)
class Contract:
    version: int
    generated_at: str
    name: str
    sha256: str
    sources: tuple[SourceContract, ...]

    def by_name(self, name: str) -> SourceContract:
        for source in self.sources:
            if source.name == name:
                return source
        raise JitterLensError("E_CTR_002", f"unknown source in contract: {name}")

    def decide(self, filename: str, label: str | None) -> RoutingDecision:
        """Attribute one record of `filename` with candidates and rationale.

        Uses the same unified decision as the input readers
        (`jitterlens.routing.decide_source`), so both entry points always
        agree on the final source for the same record.
        """
        return decide_source(match_candidates(self.sources, filename), label)

    def route(self, filename: str, label: str | None) -> SourceContract | None:
        """Final source for one record, or None when attribution is undecidable."""
        return self.decide(filename, label).source

_DEFAULTS: dict[str, Any] = {
    "period_ms": 1000,
    "jitter_abs_ms": 25,
    "jitter_rel": 0.02,
    "max_gap_points": 3,
    "max_gap_ratio": 0.05,
    "fail_jitter_ratio": 0.25,
    "max_parse_error_ratio": 0.05,
    "monotonic": True,
    "duplicate_policy": "mark",
    "merge_gap_points": 2,
    "drift_window": 64,
    "drift_threshold_rel": 0.01,
    "downsample_threshold": 0.5,
    "histogram_buckets": [5, 25, 50, 100, 250, 1000],
    "source_column": "source",
    "source_field": "source",
    "timestamp": {"column": "ts", "unit": "ms", "format": "epoch", "tz": "UTC"},
}


def _fail(field: str, message: str) -> JitterLensError:
    return JitterLensError("E_CTR_002", f"{field}: {message}")


def _type_fail(field: str, expected: str) -> JitterLensError:
    return JitterLensError("E_CTR_003", f"{field} must be {expected}")


def _require(value: Any, field: str) -> Any:
    if value is None:
        raise JitterLensError("E_CTR_002", f"{field}: required field is missing")
    return value


def _as_mapping(value: Any, field: str) -> dict[str, Any]:
    value = _require(value, field)
    if not isinstance(value, dict):
        raise _type_fail(field, "a mapping")
    return value


def _as_str(value: Any, field: str, nonempty: bool = True) -> str:
    value = _require(value, field)
    if not isinstance(value, str):
        raise _type_fail(field, "a string")
    if nonempty and not value.strip():
        raise _type_fail(field, "a non-empty string")
    return value


def _as_int(value: Any, field: str, minimum: int | None = None) -> int:
    value = _require(value, field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _type_fail(field, "an integer")
    if minimum is not None and value < minimum:
        raise _fail(field, f"must be >= {minimum}")
    return value


def _as_bool(value: Any, field: str) -> bool:
    value = _require(value, field)
    if not isinstance(value, bool):
        raise _type_fail(field, "a boolean")
    return value


def _as_ratio(value: Any, field: str) -> float:
    value = _require(value, field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _type_fail(field, "a number")
    number = float(value)
    if number < 0:
        raise _fail(field, "must be >= 0")
    if number > 1:
        raise _fail(field, "must be <= 1")
    return round(number, 9)


def _as_fraction(value: Any, field: str) -> Fraction:
    value = _require(value, field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _type_fail(field, "a number")
    if value < 0:
        raise _fail(field, "must be >= 0")
    return Fraction(str(value)).limit_denominator(1_000_000)


def _load_with_pyyaml(text: str) -> object | None:
    try:
        import yaml  # type: ignore
    except ImportError:
        return None
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark is not None else None
        message = getattr(exc, "problem", None) or str(exc)
        raise JitterLensError("E_CTR_001", f"YAML parse failed: {message}", line=line) from exc


def parse_yaml_text(text: str) -> object:
    """Use PyYAML when available, otherwise the bundled subset parser."""
    parsed = _load_with_pyyaml(text)
    if parsed is None:
        return yamlmini.loads(text)
    return parsed


def _timestamp_spec(value: Any, field: str) -> TimestampSpec:
    mapping = _as_mapping(value, field)
    column = _as_str(mapping.get("column", "ts"), f"{field}.column")
    unit = _as_str(mapping.get("unit", "ms"), f"{field}.unit")
    fmt = _as_str(mapping.get("format", "epoch"), f"{field}.format")
    tz = _as_str(mapping.get("tz", "UTC"), f"{field}.tz")
    if unit not in ("ms", "us", "s"):
        raise _fail(f"{field}.unit", "must be one of ms, us, s")
    if fmt not in ("epoch", "iso8601"):
        raise _fail(f"{field}.format", "must be one of epoch, iso8601")
    if tz.upper() != "UTC":
        raise _fail(f"{field}.tz", "only UTC is supported")
    return TimestampSpec(column, unit, fmt, "UTC")


def _source_from(raw: Any, defaults: dict[str, Any], index: int) -> SourceContract:
    mapping = _as_mapping(raw, f"sources[{index}]")
    name = _as_str(mapping.get("name"), f"sources[{index}].name")
    merged = dict(defaults)
    for key, value in mapping.items():
        if key == "timestamp" and isinstance(value, dict):
            merged["timestamp"] = {**defaults.get("timestamp", {}), **value}
        elif key not in ("name", "match"):
            merged[key] = value
    match = _as_mapping(mapping.get("match", {}), f"{name}.match")
    timestamp = _timestamp_spec(merged.get("timestamp"), f"{name}.timestamp")
    buckets_raw = merged.get("histogram_buckets")
    if not isinstance(buckets_raw, list) or not buckets_raw:
        raise _type_fail(f"{name}.histogram_buckets", "a non-empty list")
    buckets: list[int] = []
    for pos, item in enumerate(buckets_raw):
        bucket = _as_int(item, f"{name}.histogram_buckets[{pos}]", 0)
        if bucket <= 0:
            raise _fail(f"{name}.histogram_buckets", "buckets must be positive")
        if buckets and bucket <= buckets[-1]:
            raise _fail(f"{name}.histogram_buckets", "buckets must be strictly increasing")
        buckets.append(bucket)
    duplicate_policy = _as_str(merged.get("duplicate_policy"), f"{name}.duplicate_policy")
    if duplicate_policy not in ("mark", "allow"):
        raise _fail(f"{name}.duplicate_policy", "must be one of mark, allow")
    period = _as_int(merged.get("period_ms"), f"{name}.period_ms", 1)
    max_gap_points = _as_int(merged.get("max_gap_points"), f"{name}.max_gap_points", 0)
    drift_window = _as_int(merged.get("drift_window"), f"{name}.drift_window", 4)
    return SourceContract(
        name=name,
        match_filename=_as_str(match.get("filename", "*"), f"{name}.match.filename"),
        match_value=match.get("value") if match.get("value") is None else _as_str(match.get("value"), f"{name}.match.value"),
        match_column=_as_str(match.get("column", "source"), f"{name}.match.column"),
        timestamp=timestamp,
        source_column=_as_str(merged.get("source_column"), f"{name}.source_column"),
        source_field=_as_str(merged.get("source_field"), f"{name}.source_field"),
        period_ms=period,
        jitter_abs_ms=_as_int(merged.get("jitter_abs_ms"), f"{name}.jitter_abs_ms", 0),
        jitter_rel=_as_fraction(merged.get("jitter_rel"), f"{name}.jitter_rel"),
        max_gap_points=max_gap_points,
        max_gap_ratio=_as_ratio(merged.get("max_gap_ratio"), f"{name}.max_gap_ratio"),
        fail_jitter_ratio=_as_ratio(merged.get("fail_jitter_ratio"), f"{name}.fail_jitter_ratio"),
        max_parse_error_ratio=_as_ratio(merged.get("max_parse_error_ratio"), f"{name}.max_parse_error_ratio"),
        monotonic=_as_bool(merged.get("monotonic"), f"{name}.monotonic"),
        duplicate_policy=duplicate_policy,
        merge_gap_points=_as_int(merged.get("merge_gap_points"), f"{name}.merge_gap_points", 0),
        drift_window=drift_window,
        drift_threshold_rel=_as_fraction(merged.get("drift_threshold_rel"), f"{name}.drift_threshold_rel"),
        downsample_threshold=_as_fraction(merged.get("downsample_threshold"), f"{name}.downsample_threshold"),
        histogram_buckets=tuple(buckets),
    )


def load_contract(path: str | Path) -> Contract:
    contract_path = Path(path)
    try:
        raw_bytes = contract_path.read_bytes()
    except OSError as exc:
        raise JitterLensError("E_CTR_001", f"cannot read contract: {exc}", origin=str(contract_path)) from exc
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise JitterLensError("E_CTR_001", "contract must be UTF-8", origin=str(contract_path)) from exc
    data = parse_yaml_text(text)
    top = _as_mapping(data, "contract")
    version = _as_int(top.get("version"), "version")
    if version != 1:
        raise JitterLensError("E_CTR_005", f"unsupported contract version: {version}")
    generated_at = _as_str(top.get("generated_at", DEFAULT_GENERATED_AT), "generated_at")
    defaults = dict(_DEFAULTS)
    defaults.update(_as_mapping(top.get("defaults", {}), "defaults"))
    raw_sources = top.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise _type_fail("sources", "a non-empty list")
    sources = tuple(_source_from(item, defaults, pos) for pos, item in enumerate(raw_sources))
    names: set[str] = set()
    for source in sources:
        if source.name in names:
            raise JitterLensError("E_CTR_004", f"duplicate source name: {source.name}")
        names.add(source.name)
    digest = hashlib.sha256(raw_bytes).hexdigest()
    return Contract(version, generated_at, contract_path.name, digest, sources)
