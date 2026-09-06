"""Change-data-capture deltas for the entity-resolution output feed.

The output contract is emitted continuously *and* as a delta feed "when facts
change" (the blueprint). :func:`diff_outputs` compares two snapshots of
:class:`~src.graph.contracts.EntityResolutionOutput` rows — keyed by their
*stable* canonical ID (see
:mod:`src.graph.entity_resolution.assignment`) — and emits one
:class:`EntityDelta` per entity that was created, removed, or changed. Unchanged
entities produce nothing, so the feed carries only real movement.

Each delta is field-level and serializable: which external IDs and source
records were added or removed, and any display-name or enrichment-status
transition — enough for a downstream consumer to update incrementally and to
audit what moved.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from src.export.contracts import ExportContractModel
from src.graph.contracts import EntityResolutionOutput

ChangeType = Literal["created", "updated", "removed"]


class EntityDelta(ExportContractModel):
    """One entity's change between two output snapshots."""

    canonical_id: str = Field(min_length=1)
    change_type: ChangeType
    added_external_ids: list[str] = Field(default_factory=list)
    removed_external_ids: list[str] = Field(default_factory=list)
    added_source_record_ids: list[str] = Field(default_factory=list)
    removed_source_record_ids: list[str] = Field(default_factory=list)
    old_display_name: str | None = None
    new_display_name: str | None = None
    old_enrichment_status: str | None = None
    new_enrichment_status: str | None = None
    enrichment_changed: bool = False

    def _has_any_change(self) -> bool:
        return bool(
            self.added_external_ids
            or self.removed_external_ids
            or self.added_source_record_ids
            or self.removed_source_record_ids
            or self.old_display_name != self.new_display_name
            or self.old_enrichment_status != self.new_enrichment_status
            or self.enrichment_changed
        )

    @model_validator(mode="after")
    def _updated_must_change_something(self) -> Self:
        if self.change_type == "updated" and not self._has_any_change():
            raise ValueError("an 'updated' delta must carry at least one change")
        return self


def _dossier_substance(dossier: dict[str, Any] | None) -> dict[str, Any] | None:
    """The dossier with the ``as_of`` snapshot timestamp dropped.

    ``as_of`` advances every regeneration tick; it is snapshot metadata, not a
    fact. Excluding it means a clock-only advance (no new facts) does not churn
    every entity through the CDC feed — only real content changes do.
    """
    if dossier is None:
        return None
    return {key: value for key, value in dossier.items() if key != "as_of"}


def _record_ids(output: EntityResolutionOutput) -> list[str]:
    return [anchor.record_id for anchor in output.source_anchors]


def _created(canonical_id: str, output: EntityResolutionOutput) -> EntityDelta:
    return EntityDelta(
        canonical_id=canonical_id,
        change_type="created",
        added_external_ids=list(output.external_ids),
        added_source_record_ids=_record_ids(output),
        new_display_name=output.display_name,
        new_enrichment_status=output.enrichment_status,
    )


def _removed(canonical_id: str, output: EntityResolutionOutput) -> EntityDelta:
    return EntityDelta(
        canonical_id=canonical_id,
        change_type="removed",
        removed_external_ids=list(output.external_ids),
        removed_source_record_ids=_record_ids(output),
        old_display_name=output.display_name,
        old_enrichment_status=output.enrichment_status,
    )


def _updated(
    canonical_id: str,
    prev: EntityResolutionOutput,
    curr: EntityResolutionOutput,
) -> EntityDelta | None:
    prev_ext, curr_ext = set(prev.external_ids), set(curr.external_ids)
    prev_rec, curr_rec = set(_record_ids(prev)), set(_record_ids(curr))
    added_ext = sorted(curr_ext - prev_ext)
    removed_ext = sorted(prev_ext - curr_ext)
    added_rec = sorted(curr_rec - prev_rec)
    removed_rec = sorted(prev_rec - curr_rec)
    name_changed = prev.display_name != curr.display_name
    status_changed = prev.enrichment_status != curr.enrichment_status
    enrichment_changed = (
        _dossier_substance(prev.dossier_json) != _dossier_substance(curr.dossier_json)
        or prev.dossier_embedding != curr.dossier_embedding
        or prev.structural_embedding != curr.structural_embedding
        or prev.semantic_embedding != curr.semantic_embedding
    )
    if not (
        added_ext
        or removed_ext
        or added_rec
        or removed_rec
        or name_changed
        or status_changed
        or enrichment_changed
    ):
        return None
    return EntityDelta(
        canonical_id=canonical_id,
        change_type="updated",
        added_external_ids=added_ext,
        removed_external_ids=removed_ext,
        added_source_record_ids=added_rec,
        removed_source_record_ids=removed_rec,
        old_display_name=prev.display_name if name_changed else None,
        new_display_name=curr.display_name if name_changed else None,
        old_enrichment_status=prev.enrichment_status if status_changed else None,
        new_enrichment_status=curr.enrichment_status if status_changed else None,
        enrichment_changed=enrichment_changed,
    )


def diff_outputs(
    prev: Mapping[str, EntityResolutionOutput],
    curr: Mapping[str, EntityResolutionOutput],
) -> list[EntityDelta]:
    """Emit one delta per created / removed / changed entity, keyed by canonical ID."""
    deltas: list[EntityDelta] = []
    for canonical_id in sorted(set(prev) | set(curr)):
        before, after = prev.get(canonical_id), curr.get(canonical_id)
        if before is None:
            assert after is not None
            deltas.append(_created(canonical_id, after))
        elif after is None:
            deltas.append(_removed(canonical_id, before))
        else:
            updated = _updated(canonical_id, before, after)
            if updated is not None:
                deltas.append(updated)
    return deltas
