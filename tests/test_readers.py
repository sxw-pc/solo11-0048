from __future__ import annotations

from pathlib import Path

import pytest

from jitterlens.config import load_contract
from jitterlens.errors import JitterLensError
from jitterlens.sources import parse_timestamp, read_inputs

from tests.conftest import (
    DEFAULT_CONTRACT_PATH,
    ROOT,
    make_source,
    single_contract,
    write_contract,
)


def test_parse_timestamp_units_and_iso() -> None:
    contract = load_contract(DEFAULT_CONTRACT_PATH)
    spec = contract.sources[0].timestamp
    assert parse_timestamp(1735689600123, spec) == 1735689600123
    seconds = spec.__class__("ts", "s", "epoch", "UTC")
    micros = spec.__class__("ts", "us", "epoch", "UTC")
    iso = spec.__class__("ts", "ms", "iso8601", "UTC")
    assert parse_timestamp("1735689600", seconds) == 1735689600000
    assert parse_timestamp("1735689600123456", micros) == 1735689600123
    assert parse_timestamp("2025-01-01T00:00:00Z", iso) == 1735689600000
    assert parse_timestamp("2025-01-01T01:00:00+01:00", iso) == 1735689600000
    with pytest.raises(ValueError):
        parse_timestamp(True, spec)
    with pytest.raises(ValueError):
        parse_timestamp("not-a-time", spec)


def test_csv_and_jsonl_readers(tmp_path: Path, default_contract, source_a) -> None:
    (tmp_path / "mock-sensor-a.csv").write_text(
        "ts,source,value\n1000,mock-sensor-a,1\n1002,mock-sensor-a,2\n"
        "bad,mock-sensor-a,3\n\n",
        encoding="utf-8",
    )
    b = default_contract.by_name("mock-sensor-b")
    (tmp_path / "mock-sensor-b.jsonl").write_text(
        '{"ts":2000000,"source":"mock-sensor-b"}\n'
        '{"ts":2500000,"source":"mock-sensor-b","extra":1}\n'
        "not json\n"
        "\n",
        encoding="utf-8",
    )
    result = read_inputs(tmp_path, default_contract)
    assert [point.ts_ms for point in result.points if point.source == source_a.name] == [1000, 1002]
    assert [point.ts_ms for point in result.points if point.source == b.name] == [2000, 2500]
    assert [issue.code for issue in result.issues] == [
        "E_READ_003",
        "E_READ_002",
        "E_READ_002",
        "E_READ_002",
    ]
    assert [issue.lineno for issue in result.issues] == [4, 5, 3, 4]
    assert sum(len(point.unknown_fields) for point in result.points) == 3
    assert result.files == ["mock-sensor-a.csv", "mock-sensor-b.jsonl"]


def test_source_column_routing(tmp_path: Path) -> None:
    text = """
version: 1
generated_at: "2025-01-01T00:00:00Z"
defaults:
  period_ms: 1000
  source_column: source
sources:
  - name: alpha
    match:
      filename: "*.csv"
      value: alpha
  - name: beta
    match:
      filename: "*.csv"
      value: beta
    period_ms: 500
"""
    contract = load_contract(write_contract(tmp_path, text))
    (tmp_path / "mixed.csv").write_text(
        "ts,source,v\n1000,alpha,x\n1500,beta,y\n1250,alpha,z\n", encoding="utf-8"
    )
    result = read_inputs(tmp_path, contract)
    assert [(point.ts_ms, point.source) for point in result.points] == [
        (1000, "alpha"),
        (1500, "beta"),
        (1250, "alpha"),
    ]
    assert result.issues == []


def test_ambiguous_file_requires_source_column(tmp_path: Path) -> None:
    text = """
version: 1
sources:
  - name: alpha
    match:
      filename: "*.csv"
      value: alpha
  - name: beta
    match:
      filename: "*.csv"
      value: beta
"""
    contract = load_contract(write_contract(tmp_path, text))
    (tmp_path / "mixed.csv").write_text("ts,v\n1000,x\n", encoding="utf-8")
    result = read_inputs(tmp_path, contract)
    assert result.points == []
    assert result.issues[0].code == "E_READ_004"


def test_unmatched_file_and_unknown_label(tmp_path: Path, source_a) -> None:
    (tmp_path / "unknown.csv").write_text("ts\n1\n", encoding="utf-8")
    contract = single_contract(load_contract(ROOT / "contracts" / "sensors_default.yaml"), source_a)
    result = read_inputs(tmp_path, contract)
    assert result.points == []
    assert result.issues[0].source == "<unmatched>"
    assert result.issues[0].code == "E_READ_004"

    (tmp_path / "unknown.csv").unlink()
    (tmp_path / "mock-sensor-a.csv").write_text("ts,source\n1,other\n", encoding="utf-8")
    result = read_inputs(tmp_path, contract)
    assert result.issues[0].code == "E_READ_005"


def test_jsonl_bad_shapes_and_missing_ts(tmp_path: Path, default_contract) -> None:
    b = default_contract.by_name("mock-sensor-b")
    contract = single_contract(default_contract, b)
    (tmp_path / "mock-sensor-b.jsonl").write_text(
        "[1,2]\n"
        '{"source":"mock-sensor-b"}\n'
        '{"source":"unrouted","ts":1}\n',
        encoding="utf-8",
    )
    result = read_inputs(tmp_path, contract)
    assert [issue.code for issue in result.issues] == [
        "E_READ_002",
        "E_READ_003",
        "E_READ_005",
    ]


def test_missing_input_directory_and_files(tmp_path: Path, default_contract) -> None:
    with pytest.raises(JitterLensError) as excinfo:
        read_inputs(tmp_path / "missing", default_contract)
    assert excinfo.value.code == "E_READ_001"
    (tmp_path / "note.txt").write_text("ignored", encoding="utf-8")
    with pytest.raises(JitterLensError) as excinfo:
        read_inputs(tmp_path, default_contract)
    assert excinfo.value.code == "E_READ_001"


def test_missing_timestamp_column_and_bad_width(tmp_path: Path, default_contract, source_a) -> None:
    contract = single_contract(default_contract, source_a)
    (tmp_path / "mock-sensor-a.csv").write_text(
        "ts,source\n1000,mock-sensor-a\n1001\n", encoding="utf-8"
    )
    result = read_inputs(tmp_path, contract)
    assert result.points[0].ts_ms == 1000
    assert result.issues[0].code == "E_READ_002"
    (tmp_path / "mock-sensor-a.csv").write_text(
        "stamp,source\n1,mock-sensor-a\n", encoding="utf-8"
    )
    result = read_inputs(tmp_path, contract)
    assert result.issues[0].code == "E_READ_004"


def test_multiple_files_are_sorted(tmp_path: Path, default_contract, source_a) -> None:
    wildcard = make_source(source_a, match_filename="*.csv")
    contract = single_contract(default_contract, wildcard)
    (tmp_path / "z.csv").write_text("ts,source\n3000,mock-sensor-a\n", encoding="utf-8")
    (tmp_path / "a.csv").write_text("ts,source\n1000,mock-sensor-a\n", encoding="utf-8")
    result = read_inputs(tmp_path, contract)
    assert result.files == ["a.csv", "z.csv"]
    assert [point.origin for point in result.points] == ["a.csv", "z.csv"]
