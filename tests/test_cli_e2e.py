from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from jitterlens.cli import main

from tests.conftest import FIXTURE_DIR, ROOT, STRICT_CONTRACT_PATH


def _demo_args(tmp_path: Path) -> list[str]:
    return [
        "demo",
        "--fixtures-dir",
        str(tmp_path / "fixtures"),
        "--out-dir",
        str(tmp_path / "out"),
    ]


def test_demo_writes_all_artifacts(tmp_path: Path, capsys) -> None:
    assert main(_demo_args(tmp_path)) == 0
    output = capsys.readouterr()
    assert "verdict=warn" in output.out
    assert "generated 3 fixtures" in output.out
    out = tmp_path / "out"
    for name in ("report.json", "report.md", "report.html", "plans.json", "ledger.sqlite"):
        assert (out / name).is_file(), name
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["summary"]["verdict"] == "warn"
    assert report["summary"]["plans"]["fill"] == 3


def test_demo_rerun_appends_ledger(tmp_path: Path) -> None:
    args = _demo_args(tmp_path)
    assert main(args) == 0
    assert main(args) == 0
    result = main(["query", "runs", "--db", str(tmp_path / "out" / "ledger.sqlite")])
    assert result == 0


def test_query_alias_and_subcommand(tmp_path: Path, capsys) -> None:
    assert main(_demo_args(tmp_path)) == 0
    capsys.readouterr()
    db = tmp_path / "out" / "ledger.sqlite"
    assert main(["--query", "runs", "--db", str(db)]) == 0
    alias = json.loads(capsys.readouterr().out)
    assert alias["table"] == "runs"
    assert alias["count"] == 1
    assert main(["query", "sources", "--db", str(db), "--limit", "2"]) == 0
    subcommand = json.loads(capsys.readouterr().out)
    assert subcommand["count"] == 2


def test_run_exit_codes_and_no_fail_exit(tmp_path: Path) -> None:
    assert main(_demo_args(tmp_path)) == 0
    fixtures = tmp_path / "fixtures"
    base = [
        "run",
        "--contract",
        str(STRICT_CONTRACT_PATH),
        "--input-dir",
        str(fixtures),
        "--out-dir",
        str(tmp_path / "strict"),
    ]
    assert main(base) == 2
    assert main(base + ["--no-fail-exit"]) == 0
    assert main(
        [
            "run",
            "--contract",
            str(STRICT_CONTRACT_PATH),
            "--input-dir",
            str(fixtures),
            "--out-dir",
            str(tmp_path / "strict2"),
            "--no-fail-exit",
        ]
    ) == 0


def test_contract_error_exit_code(tmp_path: Path, capsys) -> None:
    broken = tmp_path / "broken.yaml"
    broken.write_text("version: [1\n", encoding="utf-8")
    code = main(
        [
            "run",
            "--contract",
            str(broken),
            "--input-dir",
            str(FIXTURE_DIR),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )
    assert code == 1
    assert "E_CTR_001" in capsys.readouterr().err


def test_demo_is_offline(monkeypatch, tmp_path: Path) -> None:
    import socket

    def blocked(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    assert main(_demo_args(tmp_path)) == 0


def test_module_entry_point(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "jitterlens",
            "demo",
            "--fixtures-dir",
            str(tmp_path / "fixtures"),
            "--out-dir",
            str(tmp_path / "out"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "verdict=warn" in result.stdout
    assert (tmp_path / "out" / "report.json").is_file()


def test_gen_fixtures_command(tmp_path: Path) -> None:
    assert main(["gen-fixtures", "--out-dir", str(tmp_path / "fixtures")]) == 0
    assert len(list((tmp_path / "fixtures").glob("mock-*"))) == 3
