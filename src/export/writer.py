"""Snapshot writer: serialize payloads, compute paths, plan files.  No I/O."""

from __future__ import annotations

import json
from datetime import date
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from src.identity.current_member_lookup import build_current_member_lookup
from src.ontology.agent_tools import build_ontology_agent_tool_manifest
from src.ontology.artifact_paths import ontology_frontend_artifact_paths
from src.ontology.contracts import (
    OntologyEdgePayload,
    OntologyGraphPayload,
    OntologyMemberGraphPayload,
)
from src.ontology.frontend_contracts import (
    build_ontology_frontend_contract,
    build_ontology_typescript_client,
    build_ontology_typescript_declarations,
)
from src.ontology.index import build_ontology_index
from src.ontology.member_features import build_member_feature_slices, slice_member_ontology_edges
from src.ontology.static_schema import build_ontology_frontend_index, build_ontology_static_schema
from src.prediction.contracts import (
    PredictionContextArtifactRefsPayload,
    PredictionMemberReadinessPayload,
    PredictionReadinessPayload,
)
from src.prediction.readiness import (
    build_prediction_bootstrap,
    build_prediction_committee_contexts,
    build_prediction_committee_readiness,
    build_prediction_member_context,
    build_prediction_readiness_index,
    build_prediction_sector_contexts,
    build_prediction_sector_readiness,
    build_prediction_source_contexts,
    build_prediction_source_index,
    build_prediction_topology,
)

from .builders import build_manifest, sha256_hex
from .contracts import (
    EvidenceCardPayload,
    MemberHistoryPayload,
    MemberProfilePayload,
    SourceAnchor,
    ZipFeedPayload,
)
from src.evidence.source_anchor_policy import validate_evidence_card_policy

if TYPE_CHECKING:
    from src.api.contracts import MemberPagePayload


# ── Serialisation ──────────────────────────────────────────────────────────────


def serialize_payload(payload: BaseModel) -> bytes:
    # sort_keys ensures byte-for-byte reproducibility across Python versions
    raw: dict[str, Any] = payload.model_dump(mode="json", by_alias=True)
    return json.dumps(raw, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")


# ── Output path helpers ────────────────────────────────────────────────────────


def member_path(slug: str) -> str:
    return f"members/{slug}.json"


def member_page_payload_path(slug: str) -> str:
    return f"member-pages/{slug}.json"


def zip_path(zip_code: str) -> str:
    return f"zip/{zip_code}.json"


def zip_entry_path(zip_code: str) -> str:
    return f"zip-entry/{zip_code}.json"


def evidence_path(evidence_card_id: str) -> str:
    return f"evidence/{evidence_card_id}.json"


def homepage_bootstrap_path() -> str:
    return "homepage/bootstrap.json"


def ontology_edges_path() -> str:
    return ontology_frontend_artifact_paths()["global_edges"]


def ontology_agent_tools_path() -> str:
    return ontology_frontend_artifact_paths()["agent_tools"]


def ontology_schema_path() -> str:
    return ontology_frontend_artifact_paths()["schema"]


def ontology_index_path() -> str:
    return ontology_frontend_artifact_paths()["index"]


def ontology_frontend_index_path() -> str:
    return ontology_frontend_artifact_paths()["frontend_index"]


def ontology_frontend_contract_path() -> str:
    return ontology_frontend_artifact_paths()["frontend_contract"]


def ontology_frontend_types_path() -> str:
    return ontology_frontend_artifact_paths()["frontend_types"]


def ontology_frontend_client_path() -> str:
    return ontology_frontend_artifact_paths()["frontend_client"]


def ontology_member_edges_path(member_bioguide_id: str) -> str:
    if not member_bioguide_id or not member_bioguide_id.replace("-", "").isalnum():
        raise ValueError(f"Invalid ontology member id: {member_bioguide_id!r}")
    return ontology_frontend_artifact_paths()["member_graph"].replace(
        "{member_bioguide_id}", member_bioguide_id
    )


def ontology_member_features_path(member_bioguide_id: str) -> str:
    if not member_bioguide_id or not member_bioguide_id.replace("-", "").isalnum():
        raise ValueError(f"Invalid ontology member id: {member_bioguide_id!r}")
    return ontology_frontend_artifact_paths()["member_features"].replace(
        "{member_bioguide_id}", member_bioguide_id
    )


def member_history_path(slug: str) -> str:
    return f"history/members/{slug}.json"


def member_change_summary_path(slug: str) -> str:
    return f"history/member-changes/{slug}.json"


def member_history_chart_path(slug: str) -> str:
    return f"history/member-charts/{slug}.json"


def member_history_page_path(slug: str) -> str:
    return f"history/member-pages/{slug}.json"


def member_history_coverage_path(slug: str) -> str:
    return f"history/member-coverage/{slug}.json"


def member_history_coverage_index_path() -> str:
    return "history/member-coverage/index.json"


def member_timeline_index_path(slug: str) -> str:
    return f"history/member-timelines/{slug}/index.json"


def member_timeline_page_path(slug: str, page: int) -> str:
    return f"history/member-timelines/{slug}/pages/{page}.json"


def member_timeline_year_path(slug: str, year: int) -> str:
    return f"history/member-timelines/{slug}/years/{year}.json"


def member_timeline_dimension_path(slug: str, dimension: str) -> str:
    return f"history/member-timelines/{slug}/dimensions/{dimension}.json"


def history_event_path(event_id: str) -> str:
    return f"history/events/{event_id}.json"


def history_event_page_path(event_id: str) -> str:
    return f"history/event-pages/{event_id}.json"


def member_preset_compare_path(slug: str, preset_key: str) -> str:
    return f"history/member-preset-compares/{slug}/{preset_key}.json"


def member_trend_summary_path(slug: str) -> str:
    return f"history/member-trends/{slug}.json"


def snapshot_index_path() -> str:
    return "history/snapshot-index.json"


def history_bootstrap_path() -> str:
    return "history/bootstrap.json"


def history_coverage_path() -> str:
    return "history/coverage.json"


def snapshot_preset_compare_path(preset_key: str) -> str:
    return f"history/snapshot-preset-compares/{preset_key}.json"


def movement_window_path(name: str = "latest", *, dimension: str | None = None) -> str:
    if dimension is None:
        return f"history/movement/{name}.json"
    return f"history/movement/dimensions/{dimension}/{name}.json"


def history_preset_range_path(preset_key: str) -> str:
    return f"history/preset-ranges/{preset_key}.json"


def current_member_lookup_path() -> str:
    return "identity/current-member-lookup.json"


def prediction_readiness_path() -> str:
    return "prediction/readiness.json"


def prediction_bootstrap_path() -> str:
    return "prediction/bootstrap.json"


def prediction_topology_path() -> str:
    return "prediction/topology.json"


def prediction_readiness_index_path() -> str:
    return "prediction/index.json"


def prediction_source_index_path() -> str:
    return "prediction/sources.json"


def prediction_source_context_path(source_key: str) -> str:
    if not source_key or not source_key.isalnum():
        raise ValueError(f"Invalid prediction source key: {source_key!r}")
    return f"prediction/source-context/{source_key}.json"


def prediction_sector_readiness_path() -> str:
    return "prediction/sectors.json"


def prediction_sector_context_path(sector_id: str) -> str:
    if not sector_id or not sector_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError(f"Invalid prediction sector id: {sector_id!r}")
    return f"prediction/sector-context/{sector_id}.json"


def prediction_committee_readiness_path() -> str:
    return "prediction/committees.json"


def prediction_committee_context_path(committee_id: str) -> str:
    if not committee_id or not committee_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError(f"Invalid prediction committee id: {committee_id!r}")
    return f"prediction/committee-context/{committee_id}.json"


def prediction_member_readiness_path(member_bioguide_id: str) -> str:
    if not member_bioguide_id or not member_bioguide_id.replace("-", "").isalnum():
        raise ValueError(f"Invalid prediction member id: {member_bioguide_id!r}")
    return f"prediction/members/{member_bioguide_id}.json"


def prediction_member_context_path(member_bioguide_id: str) -> str:
    if not member_bioguide_id or not member_bioguide_id.replace("-", "").isalnum():
        raise ValueError(f"Invalid prediction member id: {member_bioguide_id!r}")
    return f"prediction/member-context/{member_bioguide_id}.json"


def manifest_path(snapshot_id: str) -> str:
    return f"snapshots/{snapshot_id}/manifest.json"


def _is_manifest_path(path: str) -> bool:
    pure = PurePosixPath(path)
    return (
        len(pure.parts) == 3 and pure.parts[0] == "snapshots" and pure.parts[2] == "manifest.json"
    )


# ── PlannedFile ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PlannedFile:
    """An artifact queued for writing.  Content is pre-computed and hashed.

    Use :meth:`from_bytes` rather than constructing directly so that
    ``sha256`` and ``size_bytes`` are always derived from ``content``.
    """

    path: str
    content: bytes
    sha256: str
    size_bytes: int

    @classmethod
    def from_bytes(cls, path: str, content: bytes) -> PlannedFile:
        return cls(
            path=path,
            content=content,
            sha256=sha256_hex(content),
            size_bytes=len(content),
        )


def finalize_publish_plan(snapshot_id: str, files: list[PlannedFile]) -> list[PlannedFile]:
    manifest_file_path = manifest_path(snapshot_id)
    manifest_paths = {planned.path for planned in files if _is_manifest_path(planned.path)}

    if not manifest_paths:
        return list(files)

    if manifest_paths != {manifest_file_path}:
        unexpected = ", ".join(sorted(manifest_paths))
        raise ValueError(
            f"publish plan must contain exactly one canonical manifest path: {unexpected}"
        )

    non_manifest_files = [planned for planned in files if planned.path != manifest_file_path]
    manifest = build_manifest(
        snapshot_id=snapshot_id,
        file_entries=[
            {"path": planned.path, "sha256": planned.sha256, "size_bytes": planned.size_bytes}
            for planned in non_manifest_files
        ],
    )
    finalized_manifest = PlannedFile.from_bytes(
        manifest_file_path,
        serialize_payload(manifest),
    )
    return non_manifest_files + [finalized_manifest]


# ── Snapshot plan ──────────────────────────────────────────────────────────────


def plan_snapshot(
    snapshot_id: str,
    member_profiles: list[MemberProfilePayload],
    zip_feeds: list[ZipFeedPayload],
    evidence_cards: list[EvidenceCardPayload],
    *,
    member_histories: list[MemberHistoryPayload] | None = None,
    ontology_edges: list[OntologyEdgePayload] | None = None,
    current_member_lookup_file: PlannedFile | None = None,
    prediction_readiness: PredictionReadinessPayload | None = None,
    snapshot_date: date | None = None,
) -> list[PlannedFile]:
    # Manifest is always last so callers can stream data files first.
    # It covers only the data files, not itself.
    planned: list[PlannedFile] = []
    evidence_cards_by_id = {card.evidence_card_id: card for card in evidence_cards}
    prediction_readiness_by_bioguide = (
        {member.bioguide_id: member for member in prediction_readiness.members}
        if prediction_readiness is not None
        else {}
    )
    ontology_member_features_by_bioguide = (
        build_member_feature_slices(snapshot_id, ontology_edges)
        if ontology_edges is not None
        else {}
    )
    ontology_member_edges_by_bioguide = (
        slice_member_ontology_edges(ontology_edges) if ontology_edges is not None else {}
    )

    for profile in member_profiles:
        planned.append(
            PlannedFile.from_bytes(member_path(profile.slug), serialize_payload(profile))
        )
        planned.append(
            PlannedFile.from_bytes(
                member_page_payload_path(profile.slug),
                serialize_payload(
                    build_member_page_payload(
                        profile,
                        evidence_cards_by_id,
                        prediction_readiness=prediction_readiness_by_bioguide.get(
                            profile.bioguide_id
                        ),
                    )
                ),
            )
        )

    for feed in zip_feeds:
        planned.append(PlannedFile.from_bytes(zip_path(feed.zip_code), serialize_payload(feed)))

    for card in evidence_cards:
        validate_evidence_card_policy(card)
        planned.append(
            PlannedFile.from_bytes(evidence_path(card.evidence_card_id), serialize_payload(card))
        )

    if ontology_edges is not None:
        planned.append(
            PlannedFile.from_bytes(
                ontology_agent_tools_path(),
                serialize_payload(build_ontology_agent_tool_manifest()),
            )
        )
        planned.append(
            PlannedFile.from_bytes(
                ontology_schema_path(),
                serialize_payload(build_ontology_static_schema()),
            )
        )
        planned.append(
            PlannedFile.from_bytes(
                ontology_frontend_contract_path(),
                serialize_payload(build_ontology_frontend_contract()),
            )
        )
        planned.append(
            PlannedFile.from_bytes(
                ontology_frontend_types_path(),
                build_ontology_typescript_declarations().encode("utf-8"),
            )
        )
        planned.append(
            PlannedFile.from_bytes(
                ontology_frontend_client_path(),
                build_ontology_typescript_client().encode("utf-8"),
            )
        )
        planned.append(
            PlannedFile.from_bytes(
                ontology_index_path(),
                serialize_payload(build_ontology_index(snapshot_id, ontology_edges)),
            )
        )
        planned.append(
            PlannedFile.from_bytes(
                ontology_frontend_index_path(),
                serialize_payload(
                    build_ontology_frontend_index(
                        snapshot_id=snapshot_id,
                        edges=ontology_edges,
                    )
                ),
            )
        )
        planned.append(
            PlannedFile.from_bytes(
                ontology_edges_path(),
                serialize_payload(
                    OntologyGraphPayload(
                        snapshot_id=snapshot_id,
                        edge_count=len(ontology_edges),
                        edges=ontology_edges,
                    )
                ),
            )
        )
        for member_bioguide_id, edges in ontology_member_edges_by_bioguide.items():
            planned.append(
                PlannedFile.from_bytes(
                    ontology_member_edges_path(member_bioguide_id),
                    serialize_payload(
                        OntologyMemberGraphPayload(
                            snapshot_id=snapshot_id,
                            member_bioguide_id=member_bioguide_id,
                            edge_count=len(edges),
                            edges=edges,
                        )
                    ),
                )
            )
        for member_bioguide_id, features in ontology_member_features_by_bioguide.items():
            planned.append(
                PlannedFile.from_bytes(
                    ontology_member_features_path(member_bioguide_id),
                    serialize_payload(features),
                )
            )

    for history in member_histories or []:
        planned.append(
            PlannedFile.from_bytes(member_history_path(history.slug), serialize_payload(history))
        )

    if prediction_readiness is not None:
        member_contexts = [
            build_prediction_member_context(
                member,
                snapshot_id=prediction_readiness.snapshot_id,
                snapshot_date=prediction_readiness.snapshot_date,
                ontology_features=ontology_member_features_by_bioguide.get(member.bioguide_id),
                artifact_refs=_prediction_member_artifact_refs(
                    member,
                    has_ontology_features=member.bioguide_id
                    in ontology_member_features_by_bioguide,
                ),
                source_anchors=_prediction_member_source_anchors(
                    ontology_member_edges_by_bioguide.get(member.bioguide_id, [])
                ),
            )
            for member in prediction_readiness.members
        ]
        prediction_source_index = build_prediction_source_index(
            snapshot_id=prediction_readiness.snapshot_id,
            snapshot_date=prediction_readiness.snapshot_date,
            member_contexts=member_contexts,
        )
        prediction_sector_readiness = build_prediction_sector_readiness(
            snapshot_id=prediction_readiness.snapshot_id,
            snapshot_date=prediction_readiness.snapshot_date,
            member_contexts=member_contexts,
        )
        prediction_committee_readiness = build_prediction_committee_readiness(
            snapshot_id=prediction_readiness.snapshot_id,
            snapshot_date=prediction_readiness.snapshot_date,
            member_contexts=member_contexts,
        )
        planned.append(
            PlannedFile.from_bytes(
                prediction_bootstrap_path(),
                serialize_payload(
                    build_prediction_bootstrap(
                        prediction_readiness,
                        readiness_path=prediction_readiness_path(),
                        index_path=prediction_readiness_index_path(),
                        topology_path=prediction_topology_path(),
                        source_index_path=prediction_source_index_path(),
                        source_context_path_template="prediction/source-context/{source_key}.json",
                        sector_readiness_path=prediction_sector_readiness_path(),
                        sector_context_path_template="prediction/sector-context/{sector}.json",
                        committee_readiness_path=prediction_committee_readiness_path(),
                        committee_context_path_template=(
                            "prediction/committee-context/{committee}.json"
                        ),
                        member_readiness_path_template="prediction/members/{bioguide}.json",
                        member_context_path_template="prediction/member-context/{bioguide}.json",
                    )
                ),
            )
        )
        for source_context in build_prediction_source_contexts(
            snapshot_id=prediction_readiness.snapshot_id,
            snapshot_date=prediction_readiness.snapshot_date,
            member_contexts=member_contexts,
        ):
            planned.append(
                PlannedFile.from_bytes(
                    prediction_source_context_path(source_context.source.source_key),
                    serialize_payload(source_context),
                )
            )
        planned.append(
            PlannedFile.from_bytes(
                prediction_readiness_path(),
                serialize_payload(prediction_readiness),
            )
        )
        planned.append(
            PlannedFile.from_bytes(
                prediction_readiness_index_path(),
                serialize_payload(build_prediction_readiness_index(prediction_readiness)),
            )
        )
        planned.append(
            PlannedFile.from_bytes(
                prediction_source_index_path(),
                serialize_payload(prediction_source_index),
            )
        )
        planned.append(
            PlannedFile.from_bytes(
                prediction_topology_path(),
                serialize_payload(
                    build_prediction_topology(
                        readiness=prediction_readiness,
                        source_index=prediction_source_index,
                        sector_readiness=prediction_sector_readiness,
                        committee_readiness=prediction_committee_readiness,
                        readiness_path=prediction_readiness_path(),
                        index_path=prediction_readiness_index_path(),
                        source_index_path=prediction_source_index_path(),
                        sector_readiness_path=prediction_sector_readiness_path(),
                        committee_readiness_path=prediction_committee_readiness_path(),
                        member_readiness_path_template="prediction/members/{bioguide}.json",
                        member_context_path_template="prediction/member-context/{bioguide}.json",
                        source_context_path_template=(
                            "prediction/source-context/{source_key}.json"
                        ),
                        sector_context_path_template="prediction/sector-context/{sector}.json",
                        committee_context_path_template=(
                            "prediction/committee-context/{committee}.json"
                        ),
                    )
                ),
            )
        )
        planned.append(
            PlannedFile.from_bytes(
                prediction_sector_readiness_path(),
                serialize_payload(prediction_sector_readiness),
            )
        )
        planned.append(
            PlannedFile.from_bytes(
                prediction_committee_readiness_path(),
                serialize_payload(prediction_committee_readiness),
            )
        )
        for sector_context in build_prediction_sector_contexts(
            snapshot_id=prediction_readiness.snapshot_id,
            snapshot_date=prediction_readiness.snapshot_date,
            member_contexts=member_contexts,
        ):
            planned.append(
                PlannedFile.from_bytes(
                    prediction_sector_context_path(sector_context.sector.sector_id),
                    serialize_payload(sector_context),
                )
            )
        for committee_context in build_prediction_committee_contexts(
            snapshot_id=prediction_readiness.snapshot_id,
            snapshot_date=prediction_readiness.snapshot_date,
            member_contexts=member_contexts,
        ):
            planned.append(
                PlannedFile.from_bytes(
                    prediction_committee_context_path(committee_context.committee.committee_id),
                    serialize_payload(committee_context),
                )
            )
        for member, member_context in zip(
            prediction_readiness.members,
            member_contexts,
            strict=True,
        ):
            planned.append(
                PlannedFile.from_bytes(
                    prediction_member_readiness_path(member.bioguide_id),
                    serialize_payload(member),
                )
            )
            planned.append(
                PlannedFile.from_bytes(
                    prediction_member_context_path(member.bioguide_id),
                    serialize_payload(member_context),
                )
            )

    lookup_file = current_member_lookup_file or PlannedFile.from_bytes(
        current_member_lookup_path(),
        serialize_payload(
            build_current_member_lookup(
                member_profiles,
                snapshot_date=_resolve_lookup_snapshot_date(
                    snapshot_id, member_profiles, snapshot_date
                ),
            )
        ),
    )
    planned.append(lookup_file)

    manifest = build_manifest(
        snapshot_id=snapshot_id,
        file_entries=[
            {"path": f.path, "sha256": f.sha256, "size_bytes": f.size_bytes} for f in planned
        ],
    )
    planned.append(PlannedFile.from_bytes(manifest_path(snapshot_id), serialize_payload(manifest)))

    return planned


def _resolve_member_page_cards(
    evidence_card_ids: list[str],
    evidence_cards_by_id: dict[str, EvidenceCardPayload],
    *,
    limit: int | None = None,
) -> list[EvidenceCardPayload]:
    cards: list[EvidenceCardPayload] = []
    seen_ids: set[str] = set()
    for evidence_card_id in evidence_card_ids:
        if evidence_card_id in seen_ids:
            continue
        card = evidence_cards_by_id.get(evidence_card_id)
        if card is None:
            continue
        seen_ids.add(evidence_card_id)
        cards.append(card)
        if limit is not None and len(cards) >= limit:
            break
    return cards


def build_member_page_payload(
    profile: MemberProfilePayload,
    evidence_cards_by_id: dict[str, EvidenceCardPayload],
    *,
    prediction_readiness: PredictionMemberReadinessPayload | None = None,
    top_cards_limit: int | None = None,
    recent_cards_limit: int | None = None,
) -> MemberPagePayload:
    from src.api.contracts import MemberPagePayload

    top_evidence_cards = _resolve_member_page_cards(
        profile.top_evidence_card_ids,
        evidence_cards_by_id,
        limit=top_cards_limit,
    )
    recent_evidence_cards = _resolve_member_page_cards(
        [fire.evidence_card_id for fire in profile.recent_rule_fires if fire.evidence_card_id],
        evidence_cards_by_id,
        limit=recent_cards_limit,
    )
    return MemberPagePayload(
        profile=profile,
        top_evidence_cards=top_evidence_cards,
        recent_evidence_cards=recent_evidence_cards,
        prediction_readiness=prediction_readiness,
    )


def _prediction_member_artifact_refs(
    member: PredictionMemberReadinessPayload,
    *,
    has_ontology_features: bool,
) -> PredictionContextArtifactRefsPayload:
    return PredictionContextArtifactRefsPayload(
        member_profile_path=member_path(member.slug),
        member_page_path=member_page_payload_path(member.slug),
        prediction_member_readiness_path=prediction_member_readiness_path(member.bioguide_id),
        prediction_member_context_path=prediction_member_context_path(member.bioguide_id),
        ontology_member_features_path=(
            ontology_member_features_path(member.bioguide_id) if has_ontology_features else None
        ),
        ontology_member_graph_path=(
            ontology_member_edges_path(member.bioguide_id) if has_ontology_features else None
        ),
    )


def _prediction_member_source_anchors(
    edges: list[OntologyEdgePayload],
) -> list[SourceAnchor]:
    anchors: list[SourceAnchor] = []
    for edge in edges:
        anchors.extend(edge.source_anchors)
    return anchors


def _resolve_lookup_snapshot_date(
    snapshot_id: str,
    member_profiles: list[MemberProfilePayload],
    snapshot_date: date | None,
) -> date:
    if snapshot_date is not None:
        return snapshot_date
    if member_profiles:
        return member_profiles[0].snapshot_date
    return date.fromisoformat(snapshot_id)
