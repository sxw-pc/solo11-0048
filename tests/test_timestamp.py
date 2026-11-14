"""Unified exact-rational timestamp normalization tests."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

from jitterlens.config import TimestampSpec
from jitterlens.engine import analyze
from jitterlens.sources import (
    MAX_EPOCH_MS,
    MIN_EPOCH_MS,
    ROUNDING_POLICY,
    format_exact_ms,
    normalize_millis,
    parse_timestamp,
    parse_timestamp_detail,
    read_inputs,
)

from tests.conftest import (
    DEFAULT_CONTRACT_PATH,
    FIXTURE_DIR,
    make_source,
    single_contract,
    write_contract,
)

MS = TimestampSpec("ts", "ms", "epoch", "UTC")
S = TimestampSpec("ts", "s", "epoch", "UTC")
US = TimestampSpec("ts", "us", "epoch", "UTC")
ISO = TimestampSpec("ts", "ms", "iso8601", "UTC")


def test_equivalent_representations_converge_on_same_millisecond() -> None:
    # The physical instant 1735689600.0005 s == 1735689600000.5 ms exactly.
    expected = 1735689600001
    assert parse_timestamp("1735689600.0005", S) == expected
    assert parse_timestamp("1735689600000.5", MS) == expected
    assert parse_timestamp("1735689600000500", US) == expected
    assert parse_timestamp("2025-01-01T00:00:00.000500Z", ISO) == expected
    assert parse_timestamp("2025-01-01T01:00:00.000500+01:00", ISO) == expected
    # A sub-half instant rounds down identically on every path.
    below = 1735689600000
    assert parse_timestamp("1735689600.0004", S) == below
    assert parse_timestamp("1735689600000.4", MS) == below
    assert parse_timestamp("1735689600000400", US) == below
    assert parse_timestamp("2025-01-01T00:00:00.000400Z", ISO) == below
    # Integral instants agree across units and formats.
    assert parse_timestamp("1735689600", S) == below
    assert parse_timestamp("1735689600000", MS) == below
    assert parse_timestamp("1735689600000000", US) == below
    assert parse_timestamp("2025-01-01T00:00:00Z", ISO) == below


def test_half_up_rounding_contract() -> None:
    assert ROUNDING_POLICY == "half_up"
    assert parse_timestamp("0.4", MS) == 0
    assert parse_timestamp("0.5", MS) == 1
    assert parse_timestamp("0.6", MS) == 1
    assert parse_timestamp("1.5", MS) == 2
    assert parse_timestamp("2.5", MS) == 3  # half goes up, never to even
    assert parse_timestamp("500", US) == 1
    assert parse_timestamp("1500", US) == 2
    assert parse_timestamp("0.0005", S) == 1
    assert parse_timestamp_detail("0.4", MS).direction == "down"
    assert parse_timestamp_detail("0.5", MS).direction == "up"
    assert parse_timestamp_detail("0.6", MS).direction == "up"
    assert parse_timestamp_detail("2", MS).direction is None
    assert parse_timestamp_detail("2025-01-01T00:00:00.000500Z", ISO).direction == "up"


def test_negative_timestamps_rejected_uniformly() -> None:
    rejected = [
        ("-1", MS),
        ("-0.4", MS),  # would round to 0, still a negative instant
        ("-1", US),
        ("-0.001", S),
        ("1969-12-31T23:59:59.999Z", ISO),
        ("1969-12-31T23:59:59.999500Z", ISO),
    ]
    for raw, spec in rejected:
        with pytest.raises(ValueError, match="below minimum"):
            parse_timestamp(raw, spec)
    with pytest.raises(ValueError, match="below minimum"):
        normalize_millis(Fraction(-1, 1000))
    assert normalize_millis(Fraction(0)) == (0, None)
    assert parse_timestamp("0", MS) == 0
    assert parse_timestamp("1970-01-01T00:00:00Z", ISO) == 0


def test_out_of_range_timestamps_rejected_uniformly() -> None:
    assert MIN_EPOCH_MS == 0
    assert MAX_EPOCH_MS == 2**63 - 1  # signed 64-bit ledger bound
    assert parse_timestamp(str(MAX_EPOCH_MS), MS) == MAX_EPOCH_MS
    assert parse_timestamp(str(MAX_EPOCH_MS * 1000), US) == MAX_EPOCH_MS
    assert parse_timestamp("9223372036854775.807", S) == MAX_EPOCH_MS
    with pytest.raises(ValueError, match="above maximum"):
        parse_timestamp(str(MAX_EPOCH_MS + 1), MS)
    with pytest.raises(ValueError, match="above maximum"):
        parse_timestamp(str((MAX_EPOCH_MS + 1) * 1000), US)
    with pytest.raises(ValueError, match="above maximum"):
        parse_timestamp("9223372036854775.808", S)
    with pytest.raises(ValueError, match="above maximum"):
        parse_timestamp("1e30", MS)
    # The range contract applies to the exact value, before rounding.
    with pytest.raises(ValueError, match="above maximum"):
        normalize_millis(Fraction(MAX_EPOCH_MS) + Fraction(2, 5))
    assert normalize_millis(Fraction(MAX_EPOCH_MS) - Fraction(2, 5)) == (
        MAX_EPOCH_MS,
        "up",
    )


def test_format_exact_ms() -> None:
    assert format_exact_ms(Fraction(0)) == "0"
    assert format_exact_ms(Fraction(1, 2)) == "0.5"
    assert format_exact_ms(Fraction(2001, 2)) == "1000.5"
    assert format_exact_ms(Fraction(1, 1000)) == "0.001"
    assert format_exact_ms(Fraction(17356896000005, 10)) == "1735689600000.5"
    assert format_exact_ms(Fraction(123456789012345678, 10**18)) == "0.123456789012345678"
    assert format_exact_ms(Fraction(-1, 2)) == "-0.5"


def test_readers_attach_rounding_trace(tmp_path: Path, default_contract, source_a) -> None:
    csv_source = make_source(source_a, match_filename="*.csv")
    contract = single_contract(default_contract, csv_source)
    (tmp_path / "mock-sensor-a.csv").write_text(
        "ts,source\n1000.4,mock-sensor-a\n2000.5,mock-sensor-a\n3000,mock-sensor-a\n",
        encoding="utf-8",
    )
    result = read_inputs(tmp_path, contract)
    assert [point.ts_ms for point in result.points] == [1000, 2001, 3000]
    down, up, exact = result.points
    assert down.rounding is not None
    assert (down.rounding.raw, down.rounding.exact_ms, down.rounding.direction) == (
        "1000.4",
        "1000.4",
        "down",
    )
    assert up.rounding is not None
    assert (up.rounding.raw, up.rounding.exact_ms, up.rounding.direction) == (
        "2000.5",
        "2000.5",
        "up",
    )
    assert exact.rounding is None

    b = default_contract.by_name("mock-sensor-b")  # us epoch source
    jsonl_contract = single_contract(default_contract, make_source(b, match_filename="*.jsonl"))
    (tmp_path / "mock-sensor-a.csv").unlink()
    (tmp_path / "mock-sensor-b.jsonl").write_text(
        '{"ts":1500,"source":"mock-sensor-b"}\n{"ts":2000,"source":"mock-sensor-b"}\n',
        encoding="utf-8",
    )
    result = read_inputs(tmp_path, jsonl_contract)
    assert [point.ts_ms for point in result.points] == [2, 2]
    assert result.points[0].rounding is not None
    assert result.points[0].rounding.exact_ms == "1.5"
    assert result.points[0].rounding.direction == "up"
    assert result.points[1].rounding is None


def test_rejected_timestamps_become_parse_issues(
    tmp_path: Path, default_contract, source_a
) -> None:
    contract = single_contract(default_contract, make_source(source_a, match_filename="*.csv"))
    (tmp_path / "mock-sensor-a.csv").write_text(
        "ts,source\n-1,mock-sensor-a\n99999999999999999999,mock-sensor-a\n1000,mock-sensor-a\n",
        encoding="utf-8",
    )
    result = read_inputs(tmp_path, contract)
    assert [point.ts_ms for point in result.points] == [1000]
    assert [issue.code for issue in result.issues] == ["E_READ_003", "E_READ_003"]
    assert "below minimum" in result.issues[0].message
    assert "above maximum" in result.issues[1].message


def test_rounding_events_are_visible_in_source_report(tmp_path: Path) -> None:
    contract_path = write_contract(
        tmp_path,
        """
version: 1
generated_at: "2025-01-01T00:00:00Z"
sources:
  - name: mock-sub-ms
    match:
      filename: "*.csv"
""",
    )
    (tmp_path / "sub.csv").write_text("ts\n1000.4\n2000.5\n3000\n", encoding="utf-8")
    _, report, _ = analyze(contract_path, tmp_path)
    rounding = report["sources"][0]["rounding"]
    assert rounding == {
        "policy": "half_up",
        "event_count": 2,
        "events": [
            {
                "origin": "sub.csv",
                "lineno": 2,
                "raw": "1000.4",
                "exact_ms": "1000.4",
                "ts_ms": 1000,
                "direction": "down",
            },
            {
                "origin": "sub.csv",
                "lineno": 3,
                "raw": "2000.5",
                "exact_ms": "2000.5",
                "ts_ms": 2001,
                "direction": "up",
            },
        ],
    }


def test_default_fixtures_produce_no_rounding_events() -> None:
    _, report, _ = analyze(DEFAULT_CONTRACT_PATH, FIXTURE_DIR)
    assert report["summary"]["verdict"] == "warn"
    for source in report["sources"]:
        assert source["rounding"] == {"policy": "half_up", "event_count": 0, "events": []}
