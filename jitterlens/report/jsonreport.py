"""Canonical JSON report serialization."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical_json(value))


def strip_volatile(report: dict[str, Any]) -> dict[str, Any]:
    """Return a copy suitable for golden comparisons."""
    result = copy.deepcopy(report)
    result.pop("run_id", None)
    result.pop("generated_at", None)
    return result
