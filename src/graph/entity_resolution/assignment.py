"""Persistent canonical-ID assignment across resolution runs.

A cluster's content-addressed ``canonical_id`` (from the linker) changes every
time its membership changes — useless as the *stable* public ID the definition
of done requires ("every official ... a stable canonical ID"). This module
carries an entity's ID forward across runs:

* **growth** — a record joins an existing entity: the entity keeps its ID;
* **merge** — two prior entities become one: the smaller prior ID wins and the
  other is reported as ``superseded`` (the audit trail of the collapse);
* **split** — one prior entity becomes two: exactly one side keeps the ID
  (deterministically, by cluster order) and the other is minted fresh.

A brand-new entity is seeded with its content-addressed ``canonical_id`` so the
first assignment is itself deterministic and replayable. The whole function is
pure: given the same clusters and prior assignment, the result is identical.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from pydantic import BaseModel, ConfigDict

from src.graph.entity_resolution.linker import ResolvedCluster


class CanonicalAssignment(BaseModel):
    """A persistent mapping of source-record IDs to stable canonical entity IDs."""

    model_config = ConfigDict(frozen=True)

    by_record: Mapping[str, str]
    superseded_ids: frozenset[str] = frozenset()

    @property
    def canonical_ids(self) -> frozenset[str]:
        """The set of distinct stable canonical IDs currently in use."""
        return frozenset(self.by_record.values())


def assign_canonical_ids(
    clusters: Iterable[ResolvedCluster],
    prior: CanonicalAssignment | None = None,
) -> CanonicalAssignment:
    """Assign stable canonical IDs to clusters, preserving prior IDs where possible."""
    prior_map: Mapping[str, str] = prior.by_record if prior is not None else {}

    by_record: dict[str, str] = {}
    claimed: set[str] = set()
    # Deterministic order so merges/splits resolve identically run to run.
    for cluster in sorted(clusters, key=lambda c: c.record_ids):
        candidates = sorted(
            {
                prior_map[record_id]
                for record_id in cluster.record_ids
                if record_id in prior_map and prior_map[record_id] not in claimed
            }
        )
        chosen = candidates[0] if candidates else cluster.canonical_id
        claimed.add(chosen)
        for record_id in cluster.record_ids:
            by_record[record_id] = chosen

    superseded = frozenset(prior_map.values()) - frozenset(by_record.values())
    return CanonicalAssignment(by_record=by_record, superseded_ids=superseded)
