"""Canonical entities — the typed, provenance-anchored output of resolution.

:func:`build_canonical_entity` folds a :class:`~src.graph.entity_resolution.\
linker.ResolvedCluster` and its member source records into a
:class:`CanonicalEntity`: one stable ID, a chosen canonical display name, the
union of every source's external IDs, and a ``source_anchors``-equivalent list
(:class:`EntitySourceAnchor`) carrying each merged source's provenance. This is
the shape the Track A output contract emits downstream
(``canonical_*_id, ..., known_at, source_anchors[]``).

Time travel for free: pass ``as_of=t`` to project the entity using only the
sources knowable by ``t`` (``known_at <= t``). The display name, external IDs,
anchors, ``known_at``, and the content-addressed ``canonical_id`` are all
recomputed from the surviving membership, so the result is exactly what a
leakage-safe resolution at time ``t`` would have emitted for this cluster — the
blueprint's "replayable to any time-``t`` snapshot" at the entity level.

(Cluster *membership* still comes from the full resolution; a fully
time-consistent re-resolution at ``t`` is a separate, stateful slice.)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from src.graph.entity_resolution.ids import stable_id
from src.graph.entity_resolution.linker import ResolvedCluster
from src.graph.entity_resolution.names import normalize_name_token
from src.graph.entity_resolution.records import EntityType, ExternalId, SourceRecord
from src.graph.provenance import ProvenanceEnvelope


@dataclass(frozen=True)
class EntitySourceAnchor:
    """One merged source's contribution to a canonical entity, with provenance."""

    record_id: str
    source_system: str
    provenance: ProvenanceEnvelope


@dataclass(frozen=True)
class CanonicalEntity:
    """A resolved real-world entity with its full source-merge audit trail."""

    canonical_id: str
    entity_type: EntityType
    display_name: str
    external_ids: tuple[ExternalId, ...]
    anchors: tuple[EntitySourceAnchor, ...]

    @property
    def known_at(self) -> datetime:
        """Earliest instant any merged source made this entity knowable."""
        return min(anchor.provenance.known_at for anchor in self.anchors)

    @property
    def source_record_ids(self) -> tuple[str, ...]:
        return tuple(anchor.record_id for anchor in self.anchors)

    def known_as_of(self, cutoff: datetime) -> bool:
        """True once at least one source was knowable by ``cutoff``."""
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("cutoff must be timezone-aware")
        return self.known_at <= cutoff


def _completeness(record: SourceRecord) -> int:
    """How many distinct name parts a person record fills (for name choice)."""
    name = record.person_name()
    assert name is not None  # only called on person records, which always parse
    score = sum(bool(part) for part in (name.given, name.family, name.suffix))
    return score + len(name.middle)


def _canonical_display_name(records: list[SourceRecord], entity_type: EntityType) -> str:
    """Deterministically pick the canonical display name for the cluster.

    Persons prefer the most complete name; orgs prefer the longest spelled-out
    name. Both tie-break on the raw display string for determinism.
    """
    if entity_type == "person":
        # Prefer the most complete name, then natural "First Last" order over the
        # "Last, First" database form, then longer, then lexicographic.
        chosen = max(
            records,
            key=lambda r: (
                _completeness(r),
                "," not in r.display_name,
                len(r.display_name),
                r.display_name,
            ),
        )
    else:
        chosen = max(
            records, key=lambda r: (len(normalize_name_token(r.display_name)), r.display_name)
        )
    return chosen.display_name


def _merge_external_ids(records: list[SourceRecord]) -> tuple[ExternalId, ...]:
    merged: dict[str, ExternalId] = {}
    for record in records:
        for ext in record.external_ids:
            merged.setdefault(ext.canonical_key, ext)
    return tuple(sorted(merged.values(), key=lambda e: (e.system, e.value)))


def build_canonical_entity(
    cluster: ResolvedCluster,
    records_by_id: Mapping[str, SourceRecord],
    *,
    as_of: datetime | None = None,
) -> CanonicalEntity | None:
    """Materialize a cluster into a canonical entity, optionally as of a time.

    Returns ``None`` when ``as_of`` predates every member source (the entity was
    not yet knowable).
    """
    members = [records_by_id[record_id] for record_id in cluster.record_ids]
    if as_of is not None:
        members = [record for record in members if record.known_as_of(as_of)]
    if not members:
        return None

    entity_type = members[0].entity_type
    anchors = tuple(
        sorted(
            (
                EntitySourceAnchor(record.record_id, record.source_system, record.provenance)
                for record in members
            ),
            key=lambda anchor: anchor.record_id,
        )
    )
    canonical_id = stable_id(sorted(record.record_id for record in members), "ce")
    return CanonicalEntity(
        canonical_id=canonical_id,
        entity_type=entity_type,
        display_name=_canonical_display_name(members, entity_type),
        external_ids=_merge_external_ids(members),
        anchors=anchors,
    )
