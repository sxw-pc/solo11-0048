from __future__ import annotations

import hashlib
import socket
from pathlib import Path

from jitterlens.engine import analyze
from jitterlens.fixturegen import generate_fixtures
from jitterlens.report.jsonreport import canonical_json, strip_volatile

from tests.conftest import DEFAULT_CONTRACT_PATH, FIXTURE_DIR, ROOT


def _tree_digest(directory: Path) -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.iterdir())
        if path.is_file()
    }


def test_fixture_generation_is_byte_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    generate_fixtures(first)
    generate_fixtures(second)
    assert _tree_digest(first) == _tree_digest(second)


def test_reports_are_byte_deterministic_and_golden_matches(tmp_path: Path) -> None:
    _, first, first_plans = analyze(DEFAULT_CONTRACT_PATH, FIXTURE_DIR)
    _, second, second_plans = analyze(DEFAULT_CONTRACT_PATH, FIXTURE_DIR)
    assert canonical_json(strip_volatile(first)) == canonical_json(strip_volatile(second))
    assert canonical_json(first_plans) == canonical_json(second_plans)
    assert first["run_id"] == second["run_id"]
    assert first["generated_at"] == "2025-01-01T00:00:00Z"
    golden = (ROOT / "fixtures" / "golden" / "default_report.json").read_text(encoding="utf-8")
    assert canonical_json(strip_volatile(first)) == golden


def test_inputs_are_never_modified() -> None:
    before = _tree_digest(FIXTURE_DIR)
    analyze(DEFAULT_CONTRACT_PATH, FIXTURE_DIR)
    after = _tree_digest(FIXTURE_DIR)
    assert before == after


def test_analysis_does_not_use_network(monkeypatch) -> None:
    def blocked(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    _, report, _ = analyze(DEFAULT_CONTRACT_PATH, FIXTURE_DIR)
    assert report["summary"]["sources"] == 3


def test_source_and_test_files_respect_size_limits() -> None:
    roots = [ROOT / "jitterlens", ROOT / "tests", ROOT / "scripts"]
    files = [
        path
        for root in roots
        for path in root.rglob("*.py")
        if path.is_file() and "__pycache__" not in path.parts
    ]
    assert files
    counts = {path: len(path.read_text(encoding="utf-8").splitlines()) for path in files}
    too_long = {path: count for path, count in counts.items() if count > 300}
    assert too_long == {}
    total = sum(counts.values())
    assert 2500 <= total <= 4500, f"implementation + tests line total is {total}"


def test_core_schema_has_no_float_drift_in_timestamps() -> None:
    _, report, _ = analyze(DEFAULT_CONTRACT_PATH, FIXTURE_DIR)
    for interval in report["intervals"]:
        assert isinstance(interval["start_ts"], int)
        assert isinstance(interval["end_ts"], int)
    for source in report["sources"]:
        assert source["jitter_ratio"] >= 0
        assert source["gap_ratio"] >= 0
