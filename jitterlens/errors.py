"""Stable, user-facing error types for jitterlens."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class JitterLensError(Exception):
    """An error carrying a stable code and an optional source location."""

    code: str
    message: str
    line: int | None = None
    origin: str | None = None

    def __str__(self) -> str:
        location = ""
        if self.origin:
            location = f" [{self.origin}]"
        if self.line is not None:
            location += f" line {self.line}"
        return f"{self.code}{location}: {self.message}"

    def as_dict(self) -> dict[str, object]:
        data: dict[str, object] = {"code": self.code, "message": self.message}
        if self.line is not None:
            data["line"] = self.line
        if self.origin is not None:
            data["origin"] = self.origin
        return data
