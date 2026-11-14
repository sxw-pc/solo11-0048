"""Semantic gate for contract YAML parsing.

Contract files are written in a deliberately small YAML subset.  Two parsers
can turn a contract into Python data: the bundled ``yamlmini`` (always
available) and PyYAML's ``safe_load`` (optional dependency).  Stock YAML
semantics make the same text mean different things in the two parsers:
``0100`` is decimal 100 to yamlmini but octal 64 to PyYAML, ``yes`` is a
string to yamlmini but a boolean to PyYAML, and PyYAML additionally accepts
anchors, aliases, tags, block scalars and flow mappings that yamlmini cannot
represent.  Left unchecked, the loaded contract (``period_ms`` and friends)
would depend on which parser happens to be installed.

This module is the single definition of the allowed subset:

* ``check_bare_scalar`` / ``check_line`` reject superset constructs with
  ``E_CTR_001`` and a source line, so every environment refuses the same
  text in the same way (quoted scalars stay available as an escape hatch);
* ``gated_loads`` parses with yamlmini first and, when PyYAML is installed,
  requires its result to be exactly identical.  Any disagreement raises
  ``E_CTR_006`` with the field path, both values and the source line
  instead of silently picking one side.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from .errors import JitterLensError

# --- superset forms that stock YAML resolves differently from the subset ---

_BOOL_11_RE = re.compile(r"^(?:yes|no|on|off)$", re.IGNORECASE)
_LEADING_ZERO_INT_RE = re.compile(r"^[+-]?0[0-9]+$")
_HEX_BIN_INT_RE = re.compile(r"^[+-]?0[xXbB][0-9a-fA-F_]+$")
_UNDERSCORE_NUM_RE = re.compile(r"^[+-]?[0-9][0-9_]*(?:\.[0-9_]*)?(?:[eE][+-]?[0-9]+)?$")
_SEXAGESIMAL_RE = re.compile(r"^[+-]?[0-9][0-9_]*(?::[0-5]?[0-9])+(?:\.[0-9_]*)?$")
_INF_NAN_RE = re.compile(r"^[+-]?\.(?:inf|nan)$", re.IGNORECASE)
_TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}(?:[Tt ].*)?$")
_UNSIGNED_EXPONENT_RE = re.compile(r"^[+-]?(?:[0-9]+\.[0-9]*|\.[0-9]+)[eE][0-9]")
_SIGNED_FRACTION_RE = re.compile(r"^[+-]\.[0-9]")
_INDICATOR_RE = re.compile(r"^[&*!|>{}%@`]")
_FLOW_ONLY_CHARS = (",", "[", "]", "{", "}")


def _fail(lineno: int, message: str) -> JitterLensError:
    return JitterLensError("E_CTR_001", message, line=lineno)


def check_bare_scalar(value: str, lineno: int) -> None:
    """Reject a bare (unquoted) scalar whose meaning is parser-dependent."""
    if _BOOL_11_RE.match(value):
        raise _fail(lineno, f"YAML 1.1 boolean {value!r} is not supported; use true/false")
    if _LEADING_ZERO_INT_RE.match(value):
        raise _fail(lineno, f"integer {value!r} has a leading zero and is ambiguous across parsers")
    if _HEX_BIN_INT_RE.match(value):
        raise _fail(lineno, f"hex/binary integer {value!r} is not supported; use decimal")
    if "_" in value and _UNDERSCORE_NUM_RE.match(value):
        raise _fail(lineno, f"underscores in numbers are not supported: {value!r}")
    if _SEXAGESIMAL_RE.match(value):
        raise _fail(lineno, f"sexagesimal number {value!r} is not supported")
    if _INF_NAN_RE.match(value):
        raise _fail(lineno, f"non-finite float {value!r} is not supported")
    if _TIMESTAMP_RE.match(value):
        raise _fail(lineno, f"bare timestamp {value!r} is not supported; quote it")
    if _UNSIGNED_EXPONENT_RE.match(value):
        raise _fail(lineno, f"float exponent in {value!r} needs an explicit sign")
    if _SIGNED_FRACTION_RE.match(value):
        raise _fail(lineno, f"fraction {value!r} needs an integer part, e.g. +0.5")
    if value in ("<<", "="):
        raise _fail(lineno, f"YAML merge/value key {value!r} is not supported")
    if _INDICATOR_RE.match(value):
        raise _fail(lineno, f"scalar starts with unsupported YAML indicator {value[0]!r}")
    if value == "-" or value.startswith("- "):
        raise _fail(lineno, f"plain scalar {value!r} cannot start with '-'; quote it")
    if value == "?" or value.startswith("? "):
        raise _fail(lineno, f"plain scalar {value!r} cannot start with '?'; quote it")
    if ": " in value or value.endswith(":"):
        raise _fail(lineno, f"plain scalar {value!r} cannot contain ':'; quote it")
    if value in _FLOW_ONLY_CHARS:
        raise _fail(lineno, f"flow indicator {value!r} is not supported here")


def check_line(content: str, lineno: int) -> None:
    """Reject document markers and directives on a comment-stripped line."""
    if content == "---" or content.startswith("--- "):
        raise _fail(lineno, "document marker '---' is not supported")
    if content == "..." or content.startswith("... "):
        raise _fail(lineno, "document marker '...' is not supported")
    if content.startswith("%"):
        raise _fail(lineno, "YAML directives are not supported")


# --- dual-parse equivalence check -------------------------------------------


def _format_path(path: tuple[Any, ...]) -> str:
    if not path:
        return "<root>"
    text = ""
    for part in path:
        if isinstance(part, int):
            text += f"[{part}]"
        else:
            text += ("." if text else "") + str(part)
    return text


def _divergence(path: tuple[Any, ...], detail: str, lines: dict) -> JitterLensError:
    return JitterLensError(
        "E_CTR_006",
        f"parser divergence at {_format_path(path)}: {detail}",
        line=lines.get(path),
    )


def _walk(reference: Any, other: Any, path: tuple[Any, ...], lines: dict) -> None:
    if isinstance(reference, dict) and isinstance(other, dict):
        if set(reference) != set(other):
            raise _divergence(
                path,
                f"mapping keys differ: yamlmini={sorted(map(str, reference))} "
                f"vs pyyaml={sorted(map(str, other))}",
                lines,
            )
        for key in reference:
            _walk(reference[key], other[key], path + (key,), lines)
        return
    if isinstance(reference, list) and isinstance(other, list):
        if len(reference) != len(other):
            raise _divergence(
                path,
                f"sequence length differs: yamlmini={len(reference)} vs pyyaml={len(other)}",
                lines,
            )
        for index, (ref_item, other_item) in enumerate(zip(reference, other)):
            _walk(ref_item, other_item, path + (index,), lines)
        return
    if type(reference) is type(other) and reference == other:
        return
    raise _divergence(
        path,
        f"yamlmini={reference!r} ({type(reference).__name__}) "
        f"vs pyyaml={other!r} ({type(other).__name__})",
        lines,
    )


def assert_equivalent(reference: Any, other: Any, lines: dict) -> None:
    """Raise E_CTR_006 unless both parse results are exactly equal."""
    _walk(reference, other, (), lines)


def node_line_map(yaml: Any, text: str) -> dict:
    """Map field paths to one-based source lines using PyYAML node marks."""
    try:
        root = yaml.compose(text)
    except Exception:
        return {}
    lines: dict = {}

    def walk(node: Any, path: tuple[Any, ...]) -> None:
        lines[path] = node.start_mark.line + 1
        if isinstance(node, yaml.MappingNode):
            for key_node, value_node in node.value:
                walk(value_node, path + (key_node.value,))
        elif isinstance(node, yaml.SequenceNode):
            for index, item in enumerate(node.value):
                walk(item, path + (index,))

    if root is not None:
        walk(root, ())
    return lines


def _import_pyyaml() -> Any | None:
    try:
        import yaml  # type: ignore
    except ImportError:
        return None
    return yaml


def gated_loads(text: str, mini_loads: Callable[[str], Any]) -> Any:
    """Parse *text* with the bundled parser and cross-check with PyYAML.

    The bundled parser defines the subset: it either returns the canonical
    structure or raises E_CTR_001.  When PyYAML is installed its result must
    be identical; disagreement raises E_CTR_006 with path, values and line.
    """
    reference = mini_loads(text)
    yaml = _import_pyyaml()
    if yaml is None:
        return reference
    try:
        other = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark is not None else None
        detail = getattr(exc, "problem", None) or str(exc)
        raise JitterLensError(
            "E_CTR_006",
            "parser divergence at <root>: bundled parser accepted the document, "
            f"PyYAML rejected it: {detail}",
            line=line,
        ) from exc
    assert_equivalent(reference, other, node_line_map(yaml, text))
    return reference
