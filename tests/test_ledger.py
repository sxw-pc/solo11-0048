from __future__ import annotations

from pathlib import Path

import pytest

from jitterlens.engine import analyze
from jitterlens.errors import JitterLensError
from jitterlens.ledger import query_ledger, record_run

from tests.conftest import DEFAULT_CONTRACT_PATH, FIXTURE_DIR


def _run_data():
    _, report, plans = analyze(DEFAULT_CONTRACT_PATH, FIXTURE_DIR)
    return report, plans


def test_ledger_appends_and_keeps_history(tmp_path: Path) -> None:
    report, plans = _run_data()
    db = tmp_path / "ledger.sqlite"
    first = record_run(db, report, plans, "plans.json")
    second = record_run(db, report, plans, "plans.json")
    assert first != second
    runs = query_ledger(db, "runs")
    assert runs["count"] == 2
    assert [row["pk"] for row in runs["rows"]] == [first, second]
    assert runs["rows"][0]["run_id"] == runs["rows"][1]["run_id"]
    assert runs["rows"][0]["verdict"] == "warn"
    assert query_ledger(db, "sources")["count"] == 6
    assert query_ledger(db, "intervals")["count"] == 14
    assert query_ledger(db, "parse_errors")["count"] == 6
    assert query_ledger(db, "plans")["count"] == 8
    plan_rows = query_ledger(db, "plans")["rows"]
    assert {row["kind"] for row in plan_rows} == {"fill", "downsample"}
    assert any(row["truncated"] == 1 for row in plan_rows)


def test_query_limit_and_unknown_table(tmp_path: Path) -> None:
    report, plans = _run_data()
    db = tmp_path / "ledger.sqlite"
    record_run(db, report, plans, "plans.json")
    limited = query_ledger(db, "sources", limit=2)
    assert limited["count"] == 2
    with pytest.raises(JitterLensError) as excinfo:
        query_ledger(db, "sqlite_master")
    assert excinfo.value.code == "E_LEDGER_002"


def test_missing_ledger(tmp_path: Path) -> None:
    with pytest.raises(JitterLensError) as excinfo:
        query_ledger(tmp_path / "missing.sqlite", "runs")
    assert excinfo.value.code == "E_LEDGER_001"
