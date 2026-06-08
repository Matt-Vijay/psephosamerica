"""Materialize resolved persons into stable-ID-keyed output rows.

The integration glue between the entity-resolution pipeline and the published
contract: :func:`materialize_person_nodes` resolves a batch of source records,
assigns *persistent* canonical IDs (carrying them forward from a prior run), and
emits one :class:`~src.graph.contracts.EntityResolutionOutput` per canonical
entity — published under the persistent ID rather than the content-addressed
cluster ID.

That persistent ID is the stable key everything downstream relies on: the graph
nodes, the edges that reference them, and the CDC delta feed
(:func:`src.graph.cdc.diff_outputs`), which can only report "this entity changed"
if the same entity keeps the same key across runs.
"""

from __future__ import annotations

from collections.abc import Iterable

from src.graph.contracts import EntityResolutionOutput, build_entity_resolution_output
from src.graph.entity_resolution.assignment import CanonicalAssignment, assign_canonical_ids
from src.graph.entity_resolution.canonical import build_canonical_entity
from src.graph.entity_resolution.linker import resolve
from src.graph.entity_resolution.records import SourceRecord


def materialize_person_nodes(
    records: Iterable[SourceRecord],
    prior_assignment: CanonicalAssignment | None = None,
) -> tuple[list[EntityResolutionOutput], CanonicalAssignment]:
    """Resolve records, assign persistent IDs, and emit stable-keyed output rows.

    Returns the output rows (sorted by canonical ID) and the new assignment to
    thread into the next run so IDs stay stable.
    """
    record_list = list(records)
    result = resolve(record_list)
    assignment = assign_canonical_ids(result.clusters, prior_assignment)
    by_id = {record.record_id: record for record in record_list}

    nodes: list[EntityResolutionOutput] = []
    for cluster in result.clusters:
        entity = build_canonical_entity(cluster, by_id)
        assert entity is not None  # clusters always have at least one member
        persistent_id = assignment.by_record[cluster.record_ids[0]]
        nodes.append(build_entity_resolution_output(entity, canonical_id=persistent_id))

    nodes.sort(key=lambda node: node.canonical_id)
    return nodes, assignment
