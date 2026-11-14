from __future__ import annotations

import sys
from pathlib import Path

import pytest

from jitterlens import yamlmini
from jitterlens.config import load_contract, parse_yaml_text
from jitterlens.errors import JitterLensError

from tests.conftest import DEFAULT_CONTRACT_PATH


def test_nested_mapping_and_sequence() -> None:
    parsed = yamlmini.loads(
        """
name: sample
enabled: true
count: 3
ratio: 0.25
missing: null
items:
  - one
  - 2
  - false
sources:
  - name: alpha
    match:
      filename: "*.csv"
    buckets: [1, 2, 5]
  - name: beta
    match:
      filename: "*.jsonl"
"""
    )
    assert parsed["name"] == "sample"
    assert parsed["enabled"] is True
    assert parsed["count"] == 3
    assert parsed["ratio"] == 0.25
    assert parsed["missing"] is None
    assert parsed["items"] == ["one", 2, False]
    assert parsed["sources"][0]["match"]["filename"] == "*.csv"
    assert parsed["sources"][0]["buckets"] == [1, 2, 5]
    assert parsed["sources"][1]["name"] == "beta"


def test_comments_quotes_and_colons() -> None:
    parsed = yamlmini.loads(
        """
# leading comment
stamp: "2025-01-01T00:00:00Z"  # trailing comment
hash: 'a#b'
plain: a#b
url_like: "scheme://local/resource"
"""
    )
    assert parsed["stamp"] == "2025-01-01T00:00:00Z"
    assert parsed["hash"] == "a#b"
    assert parsed["plain"] == "a#b"
    assert parsed["url_like"] == "scheme://local/resource"


def test_empty_document() -> None:
    assert yamlmini.loads("") is None
    assert yamlmini.loads("# only a comment\n") is None


@pytest.mark.parametrize(
    ("text", "line"),
    [
        ("a: 1\nb: 2\n  c: 3\n", 3),
        ("a: 1\n   b: 2\n", 2),
        ("a: [1, 2\n", 1),
        ("a: 'unclosed\n", 1),
        ("a: 1\na: 2\n", 2),
        ("- one\n two\n", 2),
        ("\ta: 1\n", 1),
    ],
)
def test_parser_errors_have_lines(text: str, line: int) -> None:
    with pytest.raises(JitterLensError) as excinfo:
        yamlmini.loads(text)
    assert excinfo.value.code == "E_CTR_001"
    assert excinfo.value.line == line


def test_inline_list_error() -> None:
    with pytest.raises(JitterLensError) as excinfo:
        yamlmini.loads("a: [1, 2]]\n")
    assert excinfo.value.code == "E_CTR_001"


def test_pyyaml_equivalence_for_contracts() -> None:
    yaml = pytest.importorskip("yaml")
    for path in [DEFAULT_CONTRACT_PATH, Path("contracts/sensors_strict.yaml")]:
        text = path.read_text(encoding="utf-8")
        assert yamlmini.loads(text) == yaml.safe_load(text)


def test_fallback_parser_contract_semantics(monkeypatch: pytest.MonkeyPatch) -> None:
    text = DEFAULT_CONTRACT_PATH.read_text(encoding="utf-8")
    expected = yamlmini.loads(text)
    monkeypatch.setitem(sys.modules, "yaml", None)
    assert parse_yaml_text(text) == expected
    contract = load_contract(DEFAULT_CONTRACT_PATH)
    assert [source.name for source in contract.sources] == [
        "mock-sensor-a",
        "mock-sensor-b",
        "mock-sensor-c",
    ]


def test_pyyaml_path_used_when_available() -> None:
    pytest.importorskip("yaml")
    assert parse_yaml_text("value: 4\n") == {"value": 4}


def test_missing_file_error(tmp_path: Path) -> None:
    with pytest.raises(JitterLensError) as excinfo:
        yamlmini.load_file(str(tmp_path / "missing.yaml"))
    assert excinfo.value.code == "E_CTR_001"


def _contract_text(extra: str = "", sources: str | None = None) -> str:
    body = sources or """
sources:
  - name: sample
    match:
      filename: "*.csv"
    period_ms: 1000
"""
    return "version: 1\ngenerated_at: \"2025-01-01T00:00:00Z\"\n" + extra + body


def test_contract_missing_required_field(tmp_path: Path) -> None:
    from tests.conftest import write_contract

    path = write_contract(tmp_path, 'generated_at: "x"\nsources:\n  - name: a\n')
    with pytest.raises(JitterLensError) as excinfo:
        load_contract(path)
    assert excinfo.value.code == "E_CTR_002"


def test_contract_bad_types_and_ranges(tmp_path: Path) -> None:
    from tests.conftest import write_contract

    cases = [
        ("version: 1\nsources:\n  - name: a\n    period_ms: nope\n", "E_CTR_003"),
        ("version: 1\nsources:\n  - name: a\n    period_ms: 0\n", "E_CTR_002"),
        ("version: 1\nsources:\n  - name: a\n    jitter_rel: -0.1\n", "E_CTR_002"),
        ("version: 1\nsources:\n  - name: a\n    histogram_buckets: [10, 5]\n", "E_CTR_002"),
        ("version: 1\nsources:\n  - name: a\n    timestamp:\n      tz: LOCAL\n", "E_CTR_002"),
    ]
    for index, (text, code) in enumerate(cases):
        path = write_contract(tmp_path, text, f"bad-{index}.yaml")
        with pytest.raises(JitterLensError) as excinfo:
            load_contract(path)
        assert excinfo.value.code == code, text


def test_contract_version_and_duplicate_source(tmp_path: Path) -> None:
    from tests.conftest import write_contract

    version2 = write_contract(tmp_path, _contract_text(), "v2.yaml").read_text()
    path = write_contract(tmp_path, version2.replace("version: 1", "version: 2"), "v2b.yaml")
    with pytest.raises(JitterLensError) as excinfo:
        load_contract(path)
    assert excinfo.value.code == "E_CTR_005"
    duplicate = """
sources:
  - name: same
    period_ms: 1000
  - name: same
    period_ms: 500
"""
    path = write_contract(tmp_path, _contract_text(sources=duplicate), "dup.yaml")
    with pytest.raises(JitterLensError) as excinfo:
        load_contract(path)
    assert excinfo.value.code == "E_CTR_004"


def test_contract_bad_yaml_code(tmp_path: Path) -> None:
    from tests.conftest import write_contract

    path = write_contract(tmp_path, "version: [1\n", "broken.yaml")
    with pytest.raises(JitterLensError) as excinfo:
        load_contract(path)
    assert excinfo.value.code == "E_CTR_001"
