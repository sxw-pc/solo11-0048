"""Unified source attribution: one decision for contract routing and readers."""

from __future__ import annotations

from pathlib import Path

from jitterlens.config import load_contract
from jitterlens.engine import analyze, write_outputs
from jitterlens.ledger import query_ledger
from jitterlens.routing import (
    REASON_EXACT_VALUE,
    REASON_MISSING_LABEL,
    REASON_NAME_MATCH,
    REASON_NO_CANDIDATES,
    REASON_UNIQUE_CANDIDATE,
    REASON_UNIQUE_GENERIC,
    REASON_UNRESOLVED_LABEL,
    decide_source,
)
from jitterlens.sources import read_inputs, select_source

from tests.conftest import FIXTURE_DIR, write_contract

OVERLAP_CONTRACT = """
version: 1
generated_at: "2025-01-01T00:00:00Z"
sources:
  - name: alpha
    match:
      filename: "*.csv"
      value: shared
  - name: shared
    match:
      filename: "*.csv"
  - name: gamma
    match:
      filename: "*.csv"
      value: gamma
"""

TWO_EXACT_CONTRACT = """
version: 1
generated_at: "2025-01-01T00:00:00Z"
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


def test_decision_priority_and_reasons(tmp_path: Path) -> None:
    contract = load_contract(
        write_contract(tmp_path, OVERLAP_CONTRACT)
    )
    # An exact match.value hit beats a source whose name equals the label.
    decision = contract.decide("mixed.csv", "shared")
    assert decision.source_name == "alpha"
    assert decision.reason == REASON_EXACT_VALUE
    assert decision.candidate_names == ("alpha", "shared", "gamma")
    # A label equal only to a source name routes by name.
    decision = contract.decide("mixed.csv", "alpha")
    assert decision.source_name == "alpha"
    assert decision.reason == REASON_NAME_MATCH
    # A sole wildcard candidate is the fallback for otherwise unmatched labels.
    decision = contract.decide("mixed.csv", "other")
    assert decision.source_name == "shared"
    assert decision.reason == REASON_UNIQUE_GENERIC
    # No label with several candidates is undecidable.
    decision = contract.decide("mixed.csv", None)
    assert decision.source is None
    assert decision.reason == REASON_MISSING_LABEL
    # A file no source matches has no candidates at all.
    decision = contract.decide("notes.txt", "shared")
    assert decision.source is None
    assert decision.candidates == ()
    assert decision.reason == REASON_NO_CANDIDATES
    assert contract.route("notes.txt", "shared") is None


def test_fallback_not_unique_is_unresolved(tmp_path: Path) -> None:
    text = """
version: 1
sources:
  - name: g1
    match:
      filename: "*.csv"
  - name: g2
    match:
      filename: "*.csv"
"""
    contract = load_contract(
        write_contract(tmp_path, text)
    )
    decision = contract.decide("x.csv", "other")
    assert decision.source is None
    assert decision.reason == REASON_UNRESOLVED_LABEL
    assert contract.route("x.csv", "other") is None


def test_unlabeled_single_candidate(default_contract) -> None:
    decision = default_contract.decide("mock-sensor-a.csv", None)
    assert decision.source_name == "mock-sensor-a"
    assert decision.reason == REASON_UNIQUE_CANDIDATE
    decision = default_contract.decide("mock-sensor-a.csv", "mock-sensor-a")
    assert decision.reason == REASON_EXACT_VALUE


def test_select_source_matches_decide_source(default_contract) -> None:
    candidates = default_contract.sources
    for label in ("mock-sensor-a", "mock-sensor-c", "ghost", None):
        assert select_source(candidates, label) is decide_source(candidates, label).source


def test_readers_and_contract_route_agree(tmp_path: Path) -> None:
    contract = load_contract(
        write_contract(tmp_path, TWO_EXACT_CONTRACT)
    )
    rows = [("1000", "alpha"), ("1500", "beta"), ("1250", "alpha")]
    (tmp_path / "mixed.csv").write_text(
        "ts,source,v\n" + "".join(f"{ts},{label},x\n" for ts, label in rows),
        encoding="utf-8",
    )
    result = read_inputs(tmp_path, contract)
    assert result.issues == []
    for point, (_, label) in zip(result.points, rows):
        decision = contract.decide(point.origin, label)
        assert point.routing == decision
        assert point.source == decision.source_name
        assert contract.route(point.origin, label) is decision.source


def test_subdirectory_file_routes_like_the_reader(tmp_path: Path, default_contract) -> None:
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "mock-sensor-a.csv").write_text(
        "ts,source\n1000,mock-sensor-a\n2000,mock-sensor-a\n", encoding="utf-8"
    )
    result = read_inputs(tmp_path, default_contract)
    assert [point.source for point in result.points] == ["mock-sensor-a", "mock-sensor-a"]
    origin = "sub/mock-sensor-a.csv"
    decision = default_contract.decide(origin, "mock-sensor-a")
    assert decision.source_name == "mock-sensor-a"
    assert decision.reason == REASON_EXACT_VALUE
    assert all(point.routing == decision for point in result.points)


def test_default_fixtures_keep_attribution(default_contract) -> None:
    result = read_inputs(FIXTURE_DIR, default_contract)
    assert len(result.points) == 581
    for point in result.points:
        assert point.routing is not None
        assert point.routing.source_name == point.source
        assert point.routing.candidate_names == (point.source,)
        assert point.routing.reason == REASON_EXACT_VALUE
    assert sorted(issue.code for issue in result.issues) == [
        "E_READ_002",
        "E_READ_002",
        "E_READ_003",
    ]


def test_unresolvable_rows_are_reported_not_dropped(tmp_path: Path) -> None:
    contract_path = write_contract(tmp_path, TWO_EXACT_CONTRACT)
    (tmp_path / "mixed.csv").write_text(
        "ts,source,v\n"
        "1000,alpha,x\n"
        "1500,beta,y\n"
        "1250,ghost,z\n"
        "1750,,q\n",
        encoding="utf-8",
    )
    _, report, plans = analyze(contract_path, tmp_path)
    # Decidable rows keep their attribution; ambiguous rows never become points.
    summary = report["summary"]
    assert summary["valid_points"] == 2
    assert summary["parse_errors"] == 2
    errors = report["parse_errors"]
    assert [entry["code"] for entry in errors] == ["E_READ_005", "E_READ_005"]
    assert [entry["lineno"] for entry in errors] == [4, 5]
    assert "reason=unresolved_label" in errors[0]["message"]
    assert "reason=missing_label" in errors[1]["message"]
    assert "candidates=[alpha,beta]" in errors[0]["message"]
    # Issues stay attributable for stats: first candidate in contract order.
    assert {entry["source"] for entry in errors} == {"alpha"}
    artifact = write_outputs(tmp_path / "out", report, plans)
    rows = query_ledger(artifact.ledger_path, "parse_errors")["rows"]
    assert [row["code"] for row in rows] == ["E_READ_005", "E_READ_005"]
    assert {row["origin"] for row in rows} == {"mixed.csv"}


def test_overlap_priority_end_to_end(tmp_path: Path) -> None:
    contract = load_contract(
        write_contract(tmp_path, OVERLAP_CONTRACT)
    )
    (tmp_path / "mixed.csv").write_text(
        "ts,source,v\n"
        "1000,shared,x\n"
        "1500,gamma,y\n"
        "1250,other,z\n"
        "1750,alpha,w\n",
        encoding="utf-8",
    )
    result = read_inputs(tmp_path, contract)
    assert result.issues == []
    assert [point.source for point in result.points] == ["alpha", "gamma", "shared", "alpha"]
    assert [point.routing.reason for point in result.points] == [
        REASON_EXACT_VALUE,
        REASON_EXACT_VALUE,
        REASON_UNIQUE_GENERIC,
        REASON_NAME_MATCH,
    ]


def test_jsonl_unresolvable_label(tmp_path: Path) -> None:
    text = TWO_EXACT_CONTRACT.replace("*.csv", "*.jsonl")
    contract = load_contract(
        write_contract(tmp_path, text)
    )
    (tmp_path / "mixed.jsonl").write_text(
        '{"source":"alpha","ts":1000}\n'
        '{"source":"beta","ts":2000}\n'
        '{"source":"ghost","ts":3000}\n',
        encoding="utf-8",
    )
    result = read_inputs(tmp_path, contract)
    assert [point.source for point in result.points] == ["alpha", "beta"]
    assert [issue.code for issue in result.issues] == ["E_READ_005"]
    assert result.issues[0].lineno == 3
    decision = contract.decide("mixed.jsonl", "ghost")
    assert decision.source is None
    assert decision.reason == REASON_UNRESOLVED_LABEL
