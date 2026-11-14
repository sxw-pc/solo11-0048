from __future__ import annotations

import copy
import json
from pathlib import Path

from jitterlens.engine import analyze
from jitterlens.report.html_report import render_html, write_html
from jitterlens.report.jsonreport import canonical_json, strip_volatile, write_json
from jitterlens.report.markdown import render_markdown, write_markdown

from tests.conftest import DEFAULT_CONTRACT_PATH, FIXTURE_DIR, ROOT

HISTORICAL_INTERVAL_IDS = [
    "mock-sensor-a#0001",
    "mock-sensor-a#0002",
    "mock-sensor-b#0001",
    "mock-sensor-b#0002",
    "mock-sensor-b#0003",
    "mock-sensor-c#0001",
    "mock-sensor-c#0002",
]


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


def test_merge_metric_section_is_present_and_deterministic() -> None:
    report = _report()
    metric = report["merge_metric"]
    assert metric["overlap_semantics"] == "closed"
    assert metric["intervals_total"] == len(report["intervals"]) == 7
    assert metric["family_count"] == 5
    assert metric["multi_source_family_count"] == 1
    assert metric["reducible_intervals"] == 2
    assert metric["merged_interval_count"] == metric["family_count"]
    assert metric["cross_source_pair_overlaps"] == 2
    multi = [f for f in metric["families"] if f["source_count"] > 1]
    assert len(multi) == 1
    assert multi[0]["sources"] == ["mock-sensor-a", "mock-sensor-c"]
    assert multi[0]["interval_ids"] == [
        "mock-sensor-c#0001",
        "mock-sensor-a#0001",
        "mock-sensor-a#0002",
    ]
    assert _report()["merge_metric"] == metric


def test_merge_metric_does_not_drift_historical_intervals() -> None:
    report = _report()
    assert [interval["id"] for interval in report["intervals"]] == HISTORICAL_INTERVAL_IDS
    golden = json.loads(
        (ROOT / "fixtures" / "golden" / "default_report.json").read_text(encoding="utf-8")
    )
    assert report["intervals"] == golden["intervals"]
    assert report["summary"]["abnormal_intervals"] == len(HISTORICAL_INTERVAL_IDS)
    per_source: dict[str, int] = {}
    for interval in report["intervals"]:
        per_source[interval["source"]] = per_source.get(interval["source"], 0) + 1
    assert per_source == {"mock-sensor-a": 2, "mock-sensor-b": 3, "mock-sensor-c": 2}
