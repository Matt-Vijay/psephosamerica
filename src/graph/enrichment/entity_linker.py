"""Rule-based NER + entity-linking of names in text to canonical Person IDs.

Court opinions, LDA filings, and news articles name officials in free text.
:func:`extract_person_names` pulls candidate person mentions (title-prefixed or
bare ``First [M.] Last``); :class:`EntityLinker` resolves each against a roster
of canonical persons using the same name comparison the resolver uses
(:mod:`src.graph.entity_resolution.names`): a mention links only when it matches
**exactly one** roster person (family equal + given compatible), so ambiguous
mentions are dropped rather than mislinked — precision over recall.

No model download, no credential; the deterministic substrate a Splink /
bi-encoder matcher would sit above. :func:`link_precision_recall` reports P/R/F1
on a labeled sample.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from src.graph.entity_resolution.names import PersonName, given_names_compatible

_TITLES = (
    "Representative|Rep|Senator|Sen|Congressman|Congresswoman|Honorable|Hon|Judge|"
    "Justice|Governor|Gov|Mayor|Councilmember|Councilman|Councilwoman|Commissioner|"
    "Secretary|President|Mr|Ms|Mrs|Dr"
)
_NAME = r"[A-Z][a-zA-Z'’-]+(?:\s+[A-Z]\.?)?(?:\s+[A-Z][a-zA-Z'’-]+){1,2}"
_MENTION_RE = re.compile(rf"(?:\b(?:{_TITLES})\.?\s+)?({_NAME})")


def extract_person_names(text: str) -> list[str]:
    """Extract candidate person-name mentions from free text (order-preserving, deduped)."""
    seen: dict[str, None] = {}
    for match in _MENTION_RE.finditer(text):
        mention = " ".join(match.group(1).split())
        seen.setdefault(mention, None)
    return list(seen)


@dataclass(frozen=True)
class RosterEntry:
    """One canonical person in the linking roster."""

    canonical_id: str
    name: PersonName


def roster_from_names(named: Mapping[str, str]) -> list[RosterEntry]:
    """Build a roster from ``{canonical_id: display_name}``."""
    return [RosterEntry(cid, PersonName.parse(name)) for cid, name in named.items()]


class EntityLinker:
    """Links person-name mentions to a canonical ID when the match is unambiguous."""

    def __init__(self, roster: Iterable[RosterEntry]) -> None:
        self._roster = list(roster)

    def _candidates(self, mention: PersonName) -> list[str]:
        if not mention.family:
            return []
        out: list[str] = []
        for entry in self._roster:
            if entry.name.family != mention.family:
                continue
            if (
                mention.given
                and entry.name.given
                and not given_names_compatible(mention.given, entry.name.given)
            ):
                continue
            out.append(entry.canonical_id)
        return out

    def link(self, mention: str) -> str | None:
        """The canonical ID for ``mention`` iff exactly one roster person matches."""
        candidates = set(self._candidates(PersonName.parse(mention)))
        return next(iter(candidates)) if len(candidates) == 1 else None

    def link_text(self, text: str) -> list[tuple[str, str]]:
        """All ``(mention, canonical_id)`` links found in ``text``."""
        links: list[tuple[str, str]] = []
        for mention in extract_person_names(text):
            canonical_id = self.link(mention)
            if canonical_id is not None:
                links.append((mention, canonical_id))
        return links


def link_precision_recall(
    linker: EntityLinker,
    labeled: Sequence[tuple[str, set[str]]],
) -> dict[str, float]:
    """Precision / recall / F1 of the linker over ``(text, expected canonical IDs)``."""
    tp = fp = fn = 0
    for text, expected in labeled:
        got = {canonical_id for _, canonical_id in linker.link_text(text)}
        tp += len(got & expected)
        fp += len(got - expected)
        fn += len(expected - got)
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "tp": float(tp),
        "fp": float(fp),
        "fn": float(fn),
    }
