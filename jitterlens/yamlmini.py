"""A deliberately small YAML subset parser.

Supported: block mappings, block sequences, inline scalar lists, quoted
strings, integers, floats, booleans, null and comments.  Not supported:
anchors, aliases, tags, block scalars, multi-line strings and flow mappings.
The subset is sufficient for the contract files shipped with this project.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .errors import JitterLensError

_INT_RE = re.compile(r"^[+-]?[0-9]+$")
_FLOAT_RE = re.compile(r"^[+-]?(?:[0-9]+\.[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$")

@dataclass(frozen=True)
class _Line:
    indent: int
    content: str
    lineno: int

def _fail(lineno: int, message: str) -> JitterLensError:
    return JitterLensError("E_CTR_001", message, line=lineno)

def _strip_comment(text: str, lineno: int) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if quote == '"' and char == "\\":
            escaped = True
            continue
        if char in ("'", '"'):
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            continue
        if char == "#" and quote is None and (index == 0 or text[index - 1].isspace()):
            return text[:index]
    if quote is not None:
        raise _fail(lineno, "unterminated quoted scalar")
    return text

def _tokenize(text: str) -> list[_Line]:
    result: list[_Line] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        raw = raw.rstrip("\r\n")
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise _fail(lineno, "tab indentation is not supported")
        content = _strip_comment(raw, lineno).rstrip()
        if not content.strip():
            continue
        indent = len(content) - len(content.lstrip(" "))
        result.append(_Line(indent, content[indent:], lineno))
    return result

def _split_key(text: str, lineno: int) -> tuple[str, str]:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if quote == '"' and char == "\\":
            escaped = True
            continue
        if char in ("'", '"'):
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            continue
        if char == ":" and quote is None:
            if index + 1 == len(text) or text[index + 1] in (" ", "\t"):
                key = text[:index].strip()
                if not key:
                    raise _fail(lineno, "empty mapping key")
                return key, text[index + 1 :].strip()
    raise _fail(lineno, "expected 'key: value'")


def _split_inline_items(text: str, lineno: int) -> list[str]:
    items: list[str] = []
    start = 0
    quote: str | None = None
    depth = 0
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if quote == '"' and char == "\\":
            escaped = True
            continue
        if char in ("'", '"'):
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            continue
        if quote is not None:
            continue
        if char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char == "," and depth == 0:
            items.append(text[start:index].strip())
            start = index + 1
    if quote is not None or depth != 0:
        raise _fail(lineno, "unbalanced inline list")
    tail = text[start:].strip()
    if tail:
        items.append(tail)
    return items


def _parse_scalar(text: str, lineno: int) -> object:
    value = text.strip()
    if not value:
        return None
    if value.startswith('"'):
        if not value.endswith('"') or len(value) < 2:
            raise _fail(lineno, "unterminated double-quoted scalar")
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise _fail(lineno, f"bad double-quoted scalar: {exc.msg}") from exc
    if value.startswith("'"):
        if not value.endswith("'") or len(value) < 2:
            raise _fail(lineno, "unterminated single-quoted scalar")
        return value[1:-1].replace("''", "'")
    if value.startswith("["):
        if not value.endswith("]"):
            raise _fail(lineno, "unterminated inline list")
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(item, lineno) for item in _split_inline_items(inner, lineno)]
    lowered = value.lower()
    if lowered in ("null", "~"):
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if _INT_RE.fullmatch(value):
        return int(value)
    if _FLOAT_RE.fullmatch(value):
        return float(value)
    return value


def _looks_like_mapping(text: str) -> bool:
    try:
        _split_key(text, 1)
    except JitterLensError:
        return False
    return True


class _Parser:
    def __init__(self, lines: list[_Line]) -> None:
        self.lines = lines
        self.index = 0

    def peek(self) -> _Line | None:
        if self.index >= len(self.lines):
            return None
        return self.lines[self.index]

    def parse(self) -> object:
        if not self.lines:
            return None
        first = self.lines[0]
        if first.indent != 0:
            raise _fail(first.lineno, "top-level content must not be indented")
        value = self.parse_block(0)
        if self.index != len(self.lines):
            extra = self.lines[self.index]
            raise _fail(extra.lineno, "unexpected trailing content")
        return value

    def parse_block(self, indent: int) -> object:
        line = self.peek()
        if line is None:
            return None
        if line.indent != indent:
            raise _fail(line.lineno, "inconsistent indentation")
        if line.content == "-" or line.content.startswith("- "):
            return self.parse_sequence(indent)
        return self.parse_mapping(indent)

    def parse_sequence(self, indent: int) -> list[object]:
        result: list[object] = []
        while True:
            line = self.peek()
            if line is None or line.indent != indent:
                break
            if not (line.content == "-" or line.content.startswith("- ")):
                break
            rest = line.content[1:].strip()
            self.index += 1
            if not rest:
                nxt = self.peek()
                if nxt is not None and nxt.indent > indent:
                    result.append(self.parse_block(nxt.indent))
                else:
                    result.append(None)
                continue
            if _looks_like_mapping(rest):
                item: dict[str, object] = {}
                self._parse_mapping_entry(item, rest, indent + 2, line.lineno, indent)
                while True:
                    nxt = self.peek()
                    if nxt is None or nxt.indent != indent + 2:
                        break
                    if nxt.content == "-" or nxt.content.startswith("- "):
                        break
                    self.index += 1
                    self._parse_mapping_entry(item, nxt.content, indent + 4, nxt.lineno, indent + 2)
                result.append(item)
            else:
                result.append(_parse_scalar(rest, line.lineno))
                nxt = self.peek()
                if nxt is not None and nxt.indent > indent:
                    raise _fail(nxt.lineno, "scalar sequence item cannot have children")
        nxt = self.peek()
        if nxt is not None and nxt.indent > indent:
            raise _fail(nxt.lineno, "unexpected indentation")
        return result

    def _parse_mapping_entry(
        self,
        target: dict[str, object],
        text: str,
        child_indent: int,
        lineno: int,
        parent_indent: int,
    ) -> None:
        key, raw = _split_key(text, lineno)
        key = _parse_scalar(key, lineno)
        if not isinstance(key, str) or not key:
            raise _fail(lineno, "mapping keys must be non-empty strings")
        if key in target:
            raise _fail(lineno, f"duplicate key: {key}")
        if raw:
            target[key] = _parse_scalar(raw, lineno)
            return
        nxt = self.peek()
        if nxt is not None and nxt.indent > parent_indent:
            target[key] = self.parse_block(nxt.indent)
        else:
            target[key] = None

    def parse_mapping(self, indent: int) -> dict[str, object]:
        result: dict[str, object] = {}
        while True:
            line = self.peek()
            if line is None or line.indent != indent:
                break
            if line.content == "-" or line.content.startswith("- "):
                break
            self.index += 1
            self._parse_mapping_entry(result, line.content, indent + 2, line.lineno, indent)
        nxt = self.peek()
        if nxt is not None and nxt.indent > indent:
            raise _fail(nxt.lineno, "unexpected indentation")
        return result


def loads(text: str) -> object:
    """Parse a YAML subset and return Python data.

    Raises JitterLensError(E_CTR_001) with a one-based line number on failure.
    """
    if not isinstance(text, str):
        raise JitterLensError("E_CTR_001", "YAML input must be text")
    try:
        return _Parser(_tokenize(text)).parse()
    except JitterLensError:
        raise
    except Exception as exc:  # defensive: never leak parser internals
        raise JitterLensError("E_CTR_001", f"YAML parse failed: {exc}") from exc


def load_file(path: str) -> object:
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            return loads(handle.read())
    except OSError as exc:
        raise JitterLensError("E_CTR_001", f"cannot read YAML: {exc}", origin=path) from exc
