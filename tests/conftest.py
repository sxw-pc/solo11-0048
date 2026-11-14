"""Shared test helpers."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from jitterlens.config import Contract, SourceContract, load_contract
from jitterlens.fixturegen import generate_fixtures

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONTRACT_PATH = ROOT / "contracts" / "sensors_default.yaml"
STRICT_CONTRACT_PATH = ROOT / "contracts" / "sensors_strict.yaml"
FIXTURE_DIR = ROOT / "fixtures" / "generated"


@pytest.fixture(scope="session", autouse=True)
def ensure_fixtures() -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    generate_fixtures(FIXTURE_DIR)


@pytest.fixture(scope="session")
def default_contract() -> Contract:
    return load_contract(DEFAULT_CONTRACT_PATH)


@pytest.fixture(scope="session")
def source_a(default_contract: Contract) -> SourceContract:
    return default_contract.by_name("mock-sensor-a")


def make_source(base: SourceContract, **changes: object) -> SourceContract:
    return replace(base, **changes)  # type: ignore[arg-type]


def single_contract(contract: Contract, source: SourceContract) -> Contract:
    return replace(contract, sources=(source,))


def write_contract(tmp_path: Path, text: str, name: str = "contract.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8", newline="\n")
    return path
