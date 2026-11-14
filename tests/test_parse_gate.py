"""Tests for the dual-parser semantic gate (jitterlens/yamlgate.py)."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from jitterlens import yamlgate, yamlmini
from jitterlens.cli import main
from jitterlens.config import load_contract, parse_yaml_text
from jitterlens.engine import analyze
from jitterlens.errors import JitterLensError
from jitterlens.report.jsonreport import canonical_json, strip_volatile

from tests.conftest import (
    DEFAULT_CONTRACT_PATH,
    FIXTURE_DIR,
    ROOT,
    STRICT_CONTRACT_PATH,
    write_contract,
)


def _outcome(func, *args):
    """Run func and capture the result as a comparable tuple."""
    try:
        return ("ok", func(*args))
    except JitterLensError as exc:
        return ("error", exc.code, exc.line, exc.message)


def _install_fake_pyyaml(monkeypatch, *, safe_load_result=None, safe_load_exc=None):
    """Make the gate see a PyYAML stand-in with a doctored safe_load."""
    yaml = pytest.importorskip("yaml")
    fake = types.SimpleNamespace(
        YAMLError=yaml.YAMLError,
        MappingNode=yaml.MappingNode,
        SequenceNode=yaml.SequenceNode,
        compose=yaml.compose,
    )
    if safe_load_exc is not None:
        def _raise(text):
            raise safe_load_exc
        fake.safe_load = _raise
    else:
        fake.safe_load = lambda text: safe_load_result
    monkeypatch.setattr(yamlgate, "_import_pyyaml", lambda: fake)


SUBSET_CASES = [
    "version: 1\n",
    "a: 0\nb: -5\nc: +5\nd: 1000\n",
    "a: 0.5\nb: .5\nc: 1.\nd: 1.5e+3\ne: -2.5e-3\nf: 00.5\n",
    "a: true\nb: False\nc: TRUE\nd: false\n",
    "a: null\nb: ~\nc:\n",
    'a: "quoted # not comment"\nb: \'it\'\'s\'\n',
    "a: plain string\nb: mock-sensor-a\nc: UTC\n",
    'list: [1, 2.5, true, null, "x"]\nnested: [[1, 2], [3]]\n',
    'sources:\n  - name: a\n    match:\n      filename: "*.csv"\n  - name: b\n    period_ms: 500\n',
    "# only a comment\n",
    "",
]


@pytest.mark.parametrize("text", SUBSET_CASES)
def test_subset_documents_parse_without_pyyaml(text, monkeypatch):
    expected = yamlmini.loads(text)
    monkeypatch.setitem(sys.modules, "yaml", None)
    assert parse_yaml_text(text) == expected


@pytest.mark.parametrize("text", SUBSET_CASES)
def test_subset_documents_parse_identically_with_pyyaml(text, monkeypatch):
    pytest.importorskip("yaml")
    expected = yamlmini.loads(text)
    with monkeypatch.context() as m:
        m.setitem(sys.modules, "yaml", None)
        without = _outcome(parse_yaml_text, text)
    assert without == ("ok", expected)
    assert _outcome(parse_yaml_text, text) == without


SUPERSET_CASES = [
    ("period_ms: 0100\n", 1, "leading zero"),
    ("period_ms: -0100\n", 1, "leading zero"),
    ("period_ms: 09\n", 1, "leading zero"),
    ("monotonic: yes\n", 1, "true/false"),
    ("monotonic: Off\n", 1, "true/false"),
    ("period_ms: 0x1F\n", 1, "hex/binary"),
    ("period_ms: 0b101\n", 1, "hex/binary"),
    ("period_ms: 1_000\n", 1, "underscore"),
    ("period_ms: 1:30\n", 1, "sexagesimal"),
    ("jitter_rel: .inf\n", 1, "non-finite"),
    ("jitter_rel: .NaN\n", 1, "non-finite"),
    ("generated_at: 2025-01-01\n", 1, "timestamp"),
    ("jitter_rel: 1.5e3\n", 1, "explicit sign"),
    ("jitter_rel: +.5\n", 1, "integer part"),
    ("match: &a x\n", 1, "indicator"),
    ("match: *a\n", 1, "indicator"),
    ("match: !t x\n", 1, "indicator"),
    ("match: |\n", 1, "indicator"),
    ("match: >\n", 1, "indicator"),
    ("match: {filename: x}\n", 1, "indicator"),
    ("merge: <<\n", 1, "merge"),
    ("value: =\n", 1, "merge"),
    ("buckets: [1, {x: 2}]\n", 1, "indicator"),
    ("buckets: [1, x: 2]\n", 1, "':'"),
    ("name: b: c\n", 1, "':'"),
    ("name: -\n", 1, "quote"),
    ("name: ?\n", 1, "quote"),
    ("name: ,\n", 1, "flow"),
    ("name: ]\n", 1, "flow"),
    ("yes: 1\n", 1, "true/false"),
    ("0100: 1\n", 1, "leading zero"),
    ("---\nversion: 1\n", 1, "---"),
    ("version: 1\n...\n", 2, "..."),
    ("%YAML 1.2\nversion: 1\n", 1, "directives"),
    ("buckets: [0100]\n", 1, "leading zero"),
    ("- 0100\n", 1, "leading zero"),
]


@pytest.mark.parametrize(("text", "line", "fragment"), SUPERSET_CASES)
def test_superset_constructs_rejected_without_pyyaml(text, line, fragment, monkeypatch):
    monkeypatch.setitem(sys.modules, "yaml", None)
    outcome = _outcome(parse_yaml_text, text)
    assert outcome[0] == "error"
    assert outcome[1] == "E_CTR_001"
    assert outcome[2] == line
    assert fragment in outcome[3]


@pytest.mark.parametrize(("text", "line", "fragment"), SUPERSET_CASES)
def test_superset_constructs_rejected_identically_with_pyyaml(text, line, fragment, monkeypatch):
    pytest.importorskip("yaml")
    with monkeypatch.context() as m:
        m.setitem(sys.modules, "yaml", None)
        without = _outcome(parse_yaml_text, text)
    assert without[0] == "error"
    assert without[1] == "E_CTR_001"
    assert without[2] == line
    assert fragment in without[3]
    assert _outcome(parse_yaml_text, text) == without


def test_octal_contract_rejected_identically_with_and_without_pyyaml(tmp_path, monkeypatch):
    path = write_contract(
        tmp_path,
        "version: 1\nsources:\n  - name: a\n    period_ms: 0100\n",
    )
    with monkeypatch.context() as m:
        m.setitem(sys.modules, "yaml", None)
        without = _outcome(load_contract, path)
    assert without[0] == "error"
    assert without[1] == "E_CTR_001"
    assert without[2] == 4
    assert "leading zero" in without[3]
    pytest.importorskip("yaml")
    assert _outcome(load_contract, path) == without


def test_shipped_contracts_parse_identically_on_both_paths(monkeypatch):
    for path in (DEFAULT_CONTRACT_PATH, STRICT_CONTRACT_PATH):
        text = path.read_text(encoding="utf-8")
        expected = yamlmini.loads(text)
        with monkeypatch.context() as m:
            m.setitem(sys.modules, "yaml", None)
            assert parse_yaml_text(text) == expected
            contract_without = load_contract(path)
        pytest.importorskip("yaml")
        assert parse_yaml_text(text) == expected
        assert load_contract(path) == contract_without


def test_divergence_reports_field_path_values_and_line(monkeypatch):
    _install_fake_pyyaml(
        monkeypatch,
        safe_load_result={"sources": [{"name": "a", "period_ms": 64}]},
    )
    with pytest.raises(JitterLensError) as excinfo:
        parse_yaml_text("sources:\n  - name: a\n    period_ms: 100\n")
    exc = excinfo.value
    assert exc.code == "E_CTR_006"
    assert exc.line == 3
    assert "sources[0].period_ms" in exc.message
    assert "yamlmini=100 (int)" in exc.message
    assert "pyyaml=64 (int)" in exc.message


def test_divergence_distinguishes_bool_from_int(monkeypatch):
    _install_fake_pyyaml(monkeypatch, safe_load_result={"flag": 1})
    with pytest.raises(JitterLensError) as excinfo:
        parse_yaml_text("flag: true\n")
    assert excinfo.value.code == "E_CTR_006"
    assert "yamlmini=True (bool)" in excinfo.value.message
    assert "pyyaml=1 (int)" in excinfo.value.message


def test_divergence_reports_mapping_key_mismatch(monkeypatch):
    _install_fake_pyyaml(monkeypatch, safe_load_result={True: 1})
    with pytest.raises(JitterLensError) as excinfo:
        parse_yaml_text("flag: 1\n")
    assert excinfo.value.code == "E_CTR_006"
    assert "mapping keys differ" in excinfo.value.message


def test_divergence_reports_sequence_length_mismatch(monkeypatch):
    _install_fake_pyyaml(monkeypatch, safe_load_result={"buckets": [1, 2, 3]})
    with pytest.raises(JitterLensError) as excinfo:
        parse_yaml_text("buckets: [1, 2]\n")
    assert excinfo.value.code == "E_CTR_006"
    assert "sequence length differs" in excinfo.value.message


def test_divergence_when_pyyaml_rejects_accepted_document(monkeypatch):
    yaml = pytest.importorskip("yaml")
    _install_fake_pyyaml(monkeypatch, safe_load_exc=yaml.YAMLError("boom"))
    with pytest.raises(JitterLensError) as excinfo:
        parse_yaml_text("version: 1\n")
    assert excinfo.value.code == "E_CTR_006"
    assert "PyYAML rejected it" in excinfo.value.message


def test_node_line_map_tracks_field_paths():
    yaml = pytest.importorskip("yaml")
    lines = yamlgate.node_line_map(yaml, "a:\n  b: 1\nlist:\n  - x\n  - 2\n")
    assert lines[("a", "b")] == 2
    assert lines[("list", 0)] == 4
    assert lines[("list", 1)] == 5


def test_parse_falls_back_to_yamlmini_without_pyyaml(monkeypatch):
    monkeypatch.setitem(sys.modules, "yaml", None)
    text = DEFAULT_CONTRACT_PATH.read_text(encoding="utf-8")
    assert parse_yaml_text(text) == yamlmini.loads(text)


def test_demo_warns_and_exits_zero_without_pyyaml(monkeypatch, tmp_path, capsys):
    monkeypatch.setitem(sys.modules, "yaml", None)
    code = main(
        [
            "demo",
            "--fixtures-dir",
            str(tmp_path / "fixtures"),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )
    assert code == 0
    assert "verdict=warn" in capsys.readouterr().out


def test_golden_report_unchanged_without_pyyaml(monkeypatch):
    monkeypatch.setitem(sys.modules, "yaml", None)
    _, report, _ = analyze(DEFAULT_CONTRACT_PATH, FIXTURE_DIR)
    golden = (ROOT / "fixtures" / "golden" / "default_report.json").read_text(encoding="utf-8")
    assert canonical_json(strip_volatile(report)) == golden
