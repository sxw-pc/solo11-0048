"""Unified source-attribution decisions shared by every entry point.

`Contract.route` / `Contract.decide` (config.py) and the CSV/JSONL readers
(sources/) both delegate to `decide_source`, so the same record can never be
attributed to different sources depending on which entry point computed it.

Every decision yields three things: the final source (or None), the candidate
sources considered at decision time, and a stable machine-readable reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from .config import SourceContract

# Stable rationale codes for RoutingDecision.reason.
REASON_EXACT_VALUE = "exact_value"  # label == match.value
REASON_NAME_MATCH = "name_match"  # label == source name
REASON_UNIQUE_GENERIC = "unique_generic"  # sole wildcard (match.value is null) fallback
REASON_UNIQUE_CANDIDATE = "unique_candidate"  # unlabeled record, single candidate
REASON_NO_CANDIDATES = "no_candidates"  # no contract source matched the file
REASON_UNRESOLVED_LABEL = "unresolved_label"  # label matched nothing, fallback not unique
REASON_MISSING_LABEL = "missing_label"  # no label and more than one candidate

RESOLVED_REASONS = (
    REASON_EXACT_VALUE,
    REASON_NAME_MATCH,
    REASON_UNIQUE_GENERIC,
    REASON_UNIQUE_CANDIDATE,
)


@dataclass(frozen=True)
class RoutingDecision:
    """Outcome of attributing one record to a contract source."""

    source: SourceContract | None
    candidates: tuple[SourceContract, ...]
    reason: str

    @property
    def source_name(self) -> str | None:
        return self.source.name if self.source is not None else None

    @property
    def candidate_names(self) -> tuple[str, ...]:
        return tuple(source.name for source in self.candidates)


def filename_matches(filename: str, pattern: str) -> bool:
    """A pattern matches when it globs the relative POSIX path or the basename."""
    name = filename.rsplit("/", 1)[-1]
    return fnmatchcase(filename, pattern) or fnmatchcase(name, pattern)


def match_candidates(
    sources: Sequence[SourceContract], filename: str
) -> tuple[SourceContract, ...]:
    """All sources whose `match.filename` accepts `filename`, in contract order."""
    return tuple(
        source for source in sources if filename_matches(filename, source.match_filename)
    )


def decide_source(
    candidates: Sequence[SourceContract], label: str | None
) -> RoutingDecision:
    """Attribute one record to exactly one source, or explain why not.

    Priority (ties within a tier resolve to the earliest contract declaration):

    1. exact `match.value == label` -> `exact_value`
    2. `name == label` -> `name_match`
    3. a single wildcard candidate (`match.value` is null) -> `unique_generic`
    4. unlabeled records route only to a single candidate -> `unique_candidate`

    Anything else is undecidable: `unresolved_label` when a label was given,
    `missing_label` when none was, `no_candidates` when the file matched nothing.
    """
    pool = tuple(candidates)
    if not pool:
        return RoutingDecision(None, pool, REASON_NO_CANDIDATES)
    if label:
        for source in pool:
            if source.match_value == label:
                return RoutingDecision(source, pool, REASON_EXACT_VALUE)
        for source in pool:
            if source.name == label:
                return RoutingDecision(source, pool, REASON_NAME_MATCH)
        generic = [source for source in pool if source.match_value is None]
        if len(generic) == 1:
            return RoutingDecision(generic[0], pool, REASON_UNIQUE_GENERIC)
        return RoutingDecision(None, pool, REASON_UNRESOLVED_LABEL)
    if len(pool) == 1:
        return RoutingDecision(pool[0], pool, REASON_UNIQUE_CANDIDATE)
    return RoutingDecision(None, pool, REASON_MISSING_LABEL)
