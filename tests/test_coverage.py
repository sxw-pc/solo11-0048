from __future__ import annotations

from pathlib import Path

from jitterlens.engine import analyze
from jitterlens.report.jsonreport import canonical_json, strip_volatile
from jitterlens.report.markdown import render_markdown
from jitterlens.sources import read_inputs, source_coverage

from tests.conftest import DEFAULT_CONTRACT_PATH, FIXTURE_DIR, write_contract

TWO_SOURCE_CONTRACT = """
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

GHOST_CONTRACT = """
version: 1
sources:
  - name: alpha
    match:
      filename: "alpha.csv"
      value: alpha
  - name: ghost
    match:
      filename: "ghost-*.csv"
      value: ghost
"""


def _coverage(report: dict) -> dict:
    return report["contract_coverage"]


def test_unmatched_file_genealogy(tmp_path: Path) -> None:
    contract_path = write_contract(tmp_path, GHOST_CONTRACT)
    (tmp_path / "notes.csv").write_text("ts,source\n1,alpha\n", encoding="utf-8")
    from jitterlens.config import load_contract

    contract = load_contract(contract_path)
    result = read_inputs(tmp_path, contract)
    assert len(result.routing) == 1
    entry = result.routing[0]
    assert entry.origin == "notes.csv"
    assert entry.status == "unmatched"
    assert entry.candidates == ()
    assert entry.selected == ()
    assert entry.reason == "no contract source matches file"
    coverage = {item.name: item for item in source_coverage(result.routing, contract)}
    assert coverage["alpha"].matched_files == ()
    assert not coverage["alpha"].covered
    assert coverage["alpha"].routing_failures == ()


def test_ambiguous_file_unrouted_and_counted_once(tmp_path: Path) -> None:
    contract_path = write_contract(tmp_path, TWO_SOURCE_CONTRACT)
    (tmp_path / "mixed.csv").write_text("ts,v\n1000,x\n", encoding="utf-8")
    _, report, _ = analyze(contract_path, tmp_path)
    coverage = _coverage(report)
    (entry,) = coverage["files"]
    assert entry["status"] == "unrouted"
    assert entry["candidates"] == ["alpha", "beta"]
    assert entry["selected"] == []
    assert "no source column" in entry["reason"]
    # The file is attributed to every candidate source but counted once.
    assert coverage["summary"]["unrouted_files"] == 1
    assert coverage["uncovered_sources"] == ["alpha", "beta"]
    for source in coverage["sources"]:
        assert source["matched_files"] == ["mixed.csv"]
        assert source["routed_files"] == []
        assert source["routing_failures"] == ["mixed.csv"]
    # Existing behavior is preserved: a single E_READ_004 issue, no points.
    assert report["summary"]["parse_errors"] == 1
    assert [item["code"] for item in report["parse_errors"]] == ["E_READ_004"]
    assert report["summary"]["valid_points"] == 0


def test_unclaimed_label_unrouted(tmp_path: Path) -> None:
    text = """
version: 1
sources:
  - name: alpha
    match:
      filename: "alpha.csv"
      value: alpha
"""
    contract_path = write_contract(tmp_path, text)
    (tmp_path / "alpha.csv").write_text(
        "ts,source\n1000,ghost\n1001,ghost\n", encoding="utf-8"
    )
    _, report, _ = analyze(contract_path, tmp_path)
    coverage = _coverage(report)
    (entry,) = coverage["files"]
    assert entry["status"] == "unrouted"
    assert entry["candidates"] == ["alpha"]
    assert entry["reason"] == "cannot route source label 'ghost' for file"
    (source,) = coverage["sources"]
    assert source["name"] == "alpha"
    assert source["matched_files"] == ["alpha.csv"]
    assert source["routing_failures"] == ["alpha.csv"]
    assert coverage["uncovered_sources"] == ["alpha"]


def test_mixed_file_selects_each_source(tmp_path: Path) -> None:
    contract_path = write_contract(tmp_path, TWO_SOURCE_CONTRACT)
    (tmp_path / "mixed.csv").write_text(
        "ts,source,v\n1000,alpha,x\n1500,beta,y\n1250,alpha,z\n", encoding="utf-8"
    )
    _, report, _ = analyze(contract_path, tmp_path)
    coverage = _coverage(report)
    (entry,) = coverage["files"]
    assert entry["status"] == "routed"
    assert entry["selected"] == ["alpha", "beta"]
    assert entry["reason"] is None
    assert coverage["uncovered_sources"] == []
    for source in coverage["sources"]:
        assert source["covered"]
        assert source["routed_files"] == ["mixed.csv"]
        assert source["routing_failures"] == []


def test_declared_but_never_hit_source_is_listed(tmp_path: Path) -> None:
    contract_path = write_contract(tmp_path, GHOST_CONTRACT)
    (tmp_path / "alpha.csv").write_text("ts,source\n1000,alpha\n", encoding="utf-8")
    _, report, _ = analyze(contract_path, tmp_path)
    coverage = _coverage(report)
    assert coverage["uncovered_sources"] == ["ghost"]
    ghost = next(item for item in coverage["sources"] if item["name"] == "ghost")
    assert ghost["matched_files"] == []
    assert ghost["routing_failures"] == []
    # The source still appears in the regular sources section.
    assert "ghost" in [source["name"] for source in report["sources"]]
    text = render_markdown(report)
    assert "## Contract coverage" in text
    assert "`ghost`" in text
    assert "alpha.csv" in text


def test_coverage_section_is_deterministic(tmp_path: Path) -> None:
    contract_path = write_contract(tmp_path, TWO_SOURCE_CONTRACT)
    (tmp_path / "mixed.csv").write_text("ts,v\n1000,x\n", encoding="utf-8")
    _, first, _ = analyze(contract_path, tmp_path)
    _, second, _ = analyze(contract_path, tmp_path)
    assert canonical_json(strip_volatile(first)) == canonical_json(strip_volatile(second))


def test_demo_fixtures_are_fully_covered() -> None:
    _, report, _ = analyze(DEFAULT_CONTRACT_PATH, FIXTURE_DIR)
    coverage = _coverage(report)
    assert coverage["summary"]["routed_files"] == 3
    assert coverage["summary"]["unmatched_files"] == 0
    assert coverage["summary"]["unrouted_files"] == 0
    assert coverage["uncovered_sources"] == []
    assert [entry["status"] for entry in coverage["files"]] == ["routed"] * 3
