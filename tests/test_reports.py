from __future__ import annotations

import copy
import json
from pathlib import Path

from jitterlens.engine import analyze
from jitterlens.report.html_report import render_html, write_html
from jitterlens.report.jsonreport import canonical_json, strip_volatile, write_json
from jitterlens.report.markdown import render_markdown, write_markdown

from tests.conftest import DEFAULT_CONTRACT_PATH, FIXTURE_DIR


def _report() -> dict:
    _, report, _ = analyze(DEFAULT_CONTRACT_PATH, FIXTURE_DIR)
    return report


def test_json_report_structure(tmp_path: Path) -> None:
    report = _report()
    assert report["schema_version"] == 1
    assert report["run_id"].startswith("jl-")
    assert report["contract"]["name"] == "sensors_default.yaml"
    assert report["inputs"]["files"]
    assert report["summary"]["sources"] == 3
    target = tmp_path / "report.json"
    write_json(target, report)
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded == report
    assert canonical_json(strip_volatile(report)).endswith("\n")
    assert "generated_at" not in strip_volatile(report)
    assert "run_id" not in strip_volatile(report)


def test_markdown_report_is_offline_and_readable(tmp_path: Path) -> None:
    report = _report()
    text = render_markdown(report)
    assert text.startswith("# Sampling Jitter Report")
    assert "## Source summary" in text
    assert "mock-sensor-a" in text
    assert "## Jitter histograms" in text
    assert "## Top abnormal intervals" in text
    assert "http://" not in text
    assert "https://" not in text
    target = tmp_path / "report.md"
    write_markdown(target, report)
    assert target.read_text(encoding="utf-8") == text


def test_html_report_is_self_contained() -> None:
    report = _report()
    text = render_html(report)
    assert text.startswith("<!DOCTYPE html>")
    assert "<style>" in text
    assert "<script" not in text.lower()
    assert "<link" not in text.lower()
    assert "<img" not in text.lower()
    assert "http://" not in text
    assert "https://" not in text
    assert "mock-sensor-b" in text
    assert text.endswith("</html>\n")


def test_html_escapes_dynamic_text(tmp_path: Path) -> None:
    report = copy.deepcopy(_report())
    report["sources"][0]["name"] = '<script>alert("x")</script>'
    text = render_html(report)
    assert "<script>alert" not in text
    assert "&lt;script&gt;" in text
    target = tmp_path / "nested" / "report.html"
    write_html(target, report)
    assert target.is_file()


def test_markdown_escapes_pipes() -> None:
    report = copy.deepcopy(_report())
    report["sources"][0]["name"] = "source|name"
    assert "source\\|name" in render_markdown(report)
