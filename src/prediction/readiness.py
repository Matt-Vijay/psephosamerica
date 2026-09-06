from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

from src.export.contracts import MemberProfilePayload, SourceAnchor
from src.ontology.contracts import OntologyMemberFeaturesPayload
from src.prediction.contracts import (
    PredictionBootstrapPayload,
    PredictionCommitteeContextPayload,
    PredictionCommitteeReadinessPayload,
    PredictionCommitteeReadinessRowPayload,
    PredictionContextArtifactRefsPayload,
    PredictionJurisdictionCapabilityPayload,
    PredictionMemberContextPayload,
    PredictionMemberReadinessPayload,
    PredictionReadinessCoveragePayload,
    PredictionReadinessIndexPayload,
    PredictionReadinessPayload,
    PredictionReadinessReasonPayload,
    PredictionReadinessStatus,
    PredictionSectorContextPayload,
    PredictionSectorReadinessPayload,
    PredictionSectorReadinessRowPayload,
    PredictionSegmentReadinessPayload,
    PredictionSegmentType,
    PredictionSourceContextPayload,
    PredictionSourceIndexPayload,
    PredictionSourceIndexRowPayload,
    PredictionTopologyPayload,
    prediction_source_context_path,
    prediction_source_key,
)
from src.prediction.source_anchors import (
    SourceAnchorIdentity,
    dedupe_prediction_source_anchors,
    source_anchor_identity,
)

_REASON_PRIORITY = {
    "missing_vote_history": 0,
    "missing_ontology_features": 1,
    "ontology_features_not_ready": 2,
}


def build_prediction_readiness(
    *,
    snapshot_id: str,
    snapshot_date: date,
    member_profiles: list[MemberProfilePayload],
    ontology_features: dict[str, OntologyMemberFeaturesPayload],
    vote_rows: list[dict[str, Any]],
) -> PredictionReadinessPayload:
    """Build a compact readiness report for source-backed vote prediction inputs."""
    votes_by_bioguide = _vote_rows_by_bioguide(vote_rows)
    members = [
        _member_readiness(profile, ontology_features.get(profile.bioguide_id), votes_by_bioguide)
        for profile in sorted(
            member_profiles, key=lambda item: (item.chamber, item.state, item.slug)
        )
    ]
    return PredictionReadinessPayload(
        snapshot_id=snapshot_id,
        snapshot_date=snapshot_date,
        member_count=len(members),
        ready_member_count=sum(1 for member in members if member.readiness_status == "ready"),
        partial_member_count=sum(1 for member in members if member.readiness_status == "partial"),
        blocked_member_count=sum(1 for member in members if member.readiness_status == "blocked"),
        vote_event_count=sum(_int_value(row.get("vote_event_count")) for row in vote_rows),
        vote_cast_count=sum(member.vote_count for member in members),
        coverage=_coverage_summary(members),
        jurisdictions=_jurisdiction_capabilities(member_profiles, vote_rows),
        segments=_segment_summaries(members),
        members=members,
    )


def _vote_rows_by_bioguide(vote_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    votes_by_bioguide: dict[str, dict[str, Any]] = {}
    for row in vote_rows:
        bioguide_id = str(row["bioguide_id"])
        current = votes_by_bioguide.setdefault(bioguide_id, {"bioguide_id": bioguide_id})
        for key in (
            "vote_count",
            "yea_count",
            "nay_count",
            "present_count",
            "not_voting_count",
        ):
            current[key] = _int_value(current.get(key)) + _int_value(row.get(key))
        latest_vote_date = row.get("latest_vote_date")
        if _is_later_vote_date(latest_vote_date, current.get("latest_vote_date")):
            current["latest_vote_date"] = latest_vote_date
    return votes_by_bioguide


def _is_later_vote_date(value: object, current: object) -> bool:
    if not isinstance(value, date):
        return False
    if not isinstance(current, date):
        return True
    return value > current


def _jurisdiction_capabilities(
    member_profiles: list[MemberProfilePayload],
    vote_rows: list[dict[str, Any]],
) -> list[PredictionJurisdictionCapabilityPayload]:
    required_source_roles = ["bills", "members", "source_anchors", "votes"]
    jurisdiction_ids = {
        str(row.get("jurisdiction_id") or "us_congress").strip()
        for row in vote_rows
        if str(row.get("jurisdiction_id") or "us_congress").strip()
    }
    jurisdiction_ids.add("us_congress")
    us_congress_bodies = sorted(
        {f"us_congress_{profile.chamber}" for profile in member_profiles if profile.chamber}
    )
    body_ids_by_jurisdiction: dict[str, set[str]] = {"us_congress": set(us_congress_bodies)}
    session_ids_by_jurisdiction: dict[str, set[str]] = {"us_congress": set()}
    for row in vote_rows:
        jurisdiction_id = str(row.get("jurisdiction_id") or "us_congress").strip()
        body_id = str(row.get("legislative_body_id") or "").strip()
        if jurisdiction_id and body_id:
            body_ids_by_jurisdiction.setdefault(jurisdiction_id, set()).add(body_id)
            session_id = str(row.get("legislative_session_id") or "").strip()
            if session_id:
                session_ids_by_jurisdiction.setdefault(jurisdiction_id, set()).add(
                    f"{body_id}:{session_id}"
                )

    return [
        PredictionJurisdictionCapabilityPayload(
            jurisdiction_id=jurisdiction_id,
            implementation_status=(
                "implemented" if jurisdiction_id == "us_congress" else "portable_contract"
            ),
            legislative_body_ids=sorted(body_ids_by_jurisdiction.get(jurisdiction_id, set())),
            legislative_session_ids=sorted(session_ids_by_jurisdiction.get(jurisdiction_id, set())),
            required_source_roles=required_source_roles,
        )
        for jurisdiction_id in sorted(jurisdiction_ids)
    ]


def build_prediction_readiness_index(
    readiness: PredictionReadinessPayload,
) -> PredictionReadinessIndexPayload:
    """Build a compact lookup index from a validated prediction readiness payload."""
    members_by_bioguide = {
        member.bioguide_id: member
        for member in sorted(readiness.members, key=lambda item: item.bioguide_id)
    }
    slug_to_bioguide = {
        member.slug: member.bioguide_id
        for member in sorted(readiness.members, key=lambda item: item.slug)
    }
    return PredictionReadinessIndexPayload(
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        member_count=len(members_by_bioguide),
        slug_to_bioguide=slug_to_bioguide,
        members_by_bioguide=members_by_bioguide,
    )


def build_prediction_member_context(
    member_readiness: PredictionMemberReadinessPayload,
    *,
    snapshot_id: str,
    snapshot_date: date,
    ontology_features: OntologyMemberFeaturesPayload | None,
    artifact_refs: PredictionContextArtifactRefsPayload | None = None,
    source_anchors: list[SourceAnchor] | None = None,
) -> PredictionMemberContextPayload:
    """Build one member's source-backed prediction input context."""
    reasons = list(member_readiness.readiness_reasons)
    if ontology_features is None and "missing_ontology_features" not in reasons:
        reasons.append("missing_ontology_features")
    elif (
        ontology_features is not None
        and ontology_features.readiness_status != "ready"
        and "ontology_features_not_ready" not in reasons
    ):
        reasons.append("ontology_features_not_ready")

    context_status: PredictionReadinessStatus
    if not reasons:
        context_status = "ready"
    elif member_readiness.readiness_status == "blocked" and ontology_features is None:
        context_status = "blocked"
    else:
        context_status = "partial"

    deduped_source_anchors = dedupe_prediction_source_anchors(source_anchors or [])
    source_keys = [_source_key(anchor) for anchor in deduped_source_anchors]
    return PredictionMemberContextPayload(
        snapshot_id=snapshot_id,
        snapshot_date=snapshot_date,
        member_bioguide_id=member_readiness.bioguide_id,
        member_readiness=member_readiness,
        ontology_features=ontology_features,
        context_status=context_status,
        context_reasons=reasons,
        artifact_refs=artifact_refs,
        source_anchors=deduped_source_anchors,
        source_keys=source_keys,
        source_context_paths=[_source_context_path(source_key) for source_key in source_keys],
    )


def build_prediction_bootstrap(
    readiness: PredictionReadinessPayload,
    *,
    readiness_path: str,
    index_path: str,
    topology_path: str | None = None,
    source_index_path: str,
    source_context_path_template: str,
    sector_readiness_path: str,
    sector_context_path_template: str,
    committee_readiness_path: str,
    committee_context_path_template: str,
    member_readiness_path_template: str,
    member_context_path_template: str,
) -> PredictionBootstrapPayload:
    """Build a small prediction artifact index for product clients."""
    return PredictionBootstrapPayload(
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        member_count=readiness.member_count,
        ready_member_count=readiness.ready_member_count,
        partial_member_count=readiness.partial_member_count,
        blocked_member_count=readiness.blocked_member_count,
        vote_event_count=readiness.vote_event_count,
        vote_cast_count=readiness.vote_cast_count,
        coverage=readiness.coverage,
        jurisdictions=readiness.jurisdictions,
        readiness_path=readiness_path,
        index_path=index_path,
        topology_path=topology_path,
        source_index_path=source_index_path,
        source_context_path_template=source_context_path_template,
        sector_readiness_path=sector_readiness_path,
        sector_context_path_template=sector_context_path_template,
        committee_readiness_path=committee_readiness_path,
        committee_context_path_template=committee_context_path_template,
        member_readiness_path_template=member_readiness_path_template,
        member_context_path_template=member_context_path_template,
    )


def build_prediction_sector_readiness(
    *,
    snapshot_id: str,
    snapshot_date: date,
    member_contexts: list[PredictionMemberContextPayload],
) -> PredictionSectorReadinessPayload:
    """Aggregate member prediction contexts into sector-level readiness."""
    sector_members: dict[str, dict[str, PredictionMemberContextPayload]] = {}
    sector_labels: dict[str, str | None] = {}
    sector_committee_edges: dict[str, int] = {}
    sector_holding_edges: dict[str, int] = {}
    sector_transaction_edges: dict[str, int] = {}
    sector_source_keys: dict[str, set[str]] = {}

    for context in member_contexts:
        if context.ontology_features is None:
            continue
        for exposure in context.ontology_features.sector_exposures:
            sector_members.setdefault(exposure.sector_id, {})[context.member_bioguide_id] = context
            sector_labels.setdefault(exposure.sector_id, exposure.label)
            sector_committee_edges[exposure.sector_id] = (
                sector_committee_edges.get(exposure.sector_id, 0)
                + exposure.committee_jurisdiction_edge_count
            )
            sector_holding_edges[exposure.sector_id] = (
                sector_holding_edges.get(exposure.sector_id, 0) + exposure.holding_edge_count
            )
            sector_transaction_edges[exposure.sector_id] = (
                sector_transaction_edges.get(exposure.sector_id, 0)
                + exposure.transaction_edge_count
            )
            sector_source_keys.setdefault(exposure.sector_id, set()).update(
                _context_source_keys(context)
            )

    rows = [
        _sector_readiness_row(
            sector_id,
            label=sector_labels.get(sector_id),
            members=list(members_by_id.values()),
            committee_jurisdiction_edge_count=sector_committee_edges.get(sector_id, 0),
            holding_edge_count=sector_holding_edges.get(sector_id, 0),
            transaction_edge_count=sector_transaction_edges.get(sector_id, 0),
            source_keys=sorted(sector_source_keys.get(sector_id, set())),
        )
        for sector_id, members_by_id in sorted(sector_members.items())
    ]
    return PredictionSectorReadinessPayload(
        snapshot_id=snapshot_id,
        snapshot_date=snapshot_date,
        sector_count=len(rows),
        sectors=rows,
    )


def build_prediction_sector_contexts(
    *,
    snapshot_id: str,
    snapshot_date: date,
    member_contexts: list[PredictionMemberContextPayload],
) -> list[PredictionSectorContextPayload]:
    """Build direct one-sector context artifacts for fast simulation lookup."""
    readiness = build_prediction_sector_readiness(
        snapshot_id=snapshot_id,
        snapshot_date=snapshot_date,
        member_contexts=member_contexts,
    )
    members_by_sector: dict[str, list[PredictionMemberContextPayload]] = {}
    for context in member_contexts:
        if context.ontology_features is None:
            continue
        for exposure in context.ontology_features.sector_exposures:
            members_by_sector.setdefault(exposure.sector_id, []).append(context)

    contexts: list[PredictionSectorContextPayload] = []
    for sector in readiness.sectors:
        contexts.append(
            PredictionSectorContextPayload(
                snapshot_id=snapshot_id,
                snapshot_date=snapshot_date,
                sector=sector,
                members=sorted(
                    members_by_sector.get(sector.sector_id, []),
                    key=lambda member: member.member_bioguide_id,
                ),
            )
        )
    return contexts


def build_prediction_source_index(
    *,
    snapshot_id: str,
    snapshot_date: date,
    member_contexts: list[PredictionMemberContextPayload],
) -> PredictionSourceIndexPayload:
    """Build a reverse source-to-prediction-context lookup index."""
    members_by_source: dict[_PredictionSourceIndexKey, set[str]] = {}
    sectors_by_source: dict[_PredictionSourceIndexKey, set[str]] = {}
    committees_by_source: dict[_PredictionSourceIndexKey, set[str]] = {}
    anchors_by_key: dict[_PredictionSourceIndexKey, SourceAnchor] = {}

    for context in member_contexts:
        sector_ids = (
            {exposure.sector_id for exposure in context.ontology_features.sector_exposures}
            if context.ontology_features is not None
            else set()
        )
        committee_ids = (
            {committee.node_id for committee in context.ontology_features.committees}
            if context.ontology_features is not None
            else set()
        )
        for anchor in context.source_anchors:
            key = _source_anchor_key(anchor)
            anchors_by_key[key] = anchor
            members_by_source.setdefault(key, set()).add(context.member_bioguide_id)
            sectors_by_source.setdefault(key, set()).update(sector_ids)
            committees_by_source.setdefault(key, set()).update(committee_ids)

    rows = [
        PredictionSourceIndexRowPayload(
            source_type=anchor.source_type,
            source_key=_source_key(anchor),
            source_id=anchor.source_id,
            jurisdiction_id=anchor.jurisdiction_id,
            legislative_body_id=anchor.legislative_body_id,
            legislative_session_id=anchor.legislative_session_id,
            url=anchor.url,
            label=anchor.label,
            source_context_path=f"prediction/source-context/{_source_key(anchor)}.json",
            member_count=len(members_by_source[key]),
            member_bioguide_ids=sorted(members_by_source[key]),
            sector_ids=sorted(sectors_by_source.get(key, set())),
            committee_ids=sorted(committees_by_source.get(key, set())),
        )
        for key, anchor in sorted(
            anchors_by_key.items(),
            key=lambda item: item[0],
        )
    ]
    rows.sort(key=lambda row: row.source_key)
    return PredictionSourceIndexPayload(
        snapshot_id=snapshot_id,
        snapshot_date=snapshot_date,
        source_count=len(rows),
        sources=rows,
    )


def build_prediction_source_contexts(
    *,
    snapshot_id: str,
    snapshot_date: date,
    member_contexts: list[PredictionMemberContextPayload],
) -> list[PredictionSourceContextPayload]:
    """Build direct one-source context artifacts for traceability lookup."""
    source_index = build_prediction_source_index(
        snapshot_id=snapshot_id,
        snapshot_date=snapshot_date,
        member_contexts=member_contexts,
    )
    members_by_source_key: dict[str, dict[str, PredictionMemberContextPayload]] = {}
    for context in member_contexts:
        for anchor in context.source_anchors:
            members_by_source_key.setdefault(_source_key(anchor), {})[
                context.member_bioguide_id
            ] = context

    return [
        PredictionSourceContextPayload(
            snapshot_id=snapshot_id,
            snapshot_date=snapshot_date,
            source=source,
            members=sorted(
                members_by_source_key.get(source.source_key, {}).values(),
                key=lambda member: member.member_bioguide_id,
            ),
        )
        for source in source_index.sources
    ]


def build_prediction_topology(
    *,
    readiness: PredictionReadinessPayload,
    source_index: PredictionSourceIndexPayload,
    sector_readiness: PredictionSectorReadinessPayload,
    committee_readiness: PredictionCommitteeReadinessPayload,
    readiness_path: str,
    index_path: str,
    source_index_path: str,
    sector_readiness_path: str,
    committee_readiness_path: str,
    member_readiness_path_template: str,
    member_context_path_template: str,
    source_context_path_template: str,
    sector_context_path_template: str,
    committee_context_path_template: str,
) -> PredictionTopologyPayload:
    """Build a discoverable topology for prediction artifact families."""
    jurisdiction_ids = [jurisdiction.jurisdiction_id for jurisdiction in readiness.jurisdictions]
    implemented_jurisdiction_ids = [
        jurisdiction.jurisdiction_id
        for jurisdiction in readiness.jurisdictions
        if jurisdiction.implementation_status == "implemented"
    ]
    portable_jurisdiction_ids = [
        jurisdiction.jurisdiction_id
        for jurisdiction in readiness.jurisdictions
        if jurisdiction.implementation_status == "portable_contract"
    ]
    legislative_body_ids = sorted(
        {
            f"{jurisdiction.jurisdiction_id}:{body_id}"
            for jurisdiction in readiness.jurisdictions
            for body_id in jurisdiction.legislative_body_ids
        }
    )
    legislative_session_ids = sorted(
        {
            f"{jurisdiction.jurisdiction_id}:{session_id}"
            for jurisdiction in readiness.jurisdictions
            for session_id in jurisdiction.legislative_session_ids
        }
    )
    return PredictionTopologyPayload(
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        member_count=readiness.member_count,
        sector_count=sector_readiness.sector_count,
        committee_count=committee_readiness.committee_count,
        source_count=source_index.source_count,
        jurisdiction_count=len(jurisdiction_ids),
        implemented_jurisdiction_count=len(implemented_jurisdiction_ids),
        portable_jurisdiction_count=len(portable_jurisdiction_ids),
        legislative_body_count=len(legislative_body_ids),
        legislative_session_count=len(legislative_session_ids),
        jurisdiction_ids=jurisdiction_ids,
        implemented_jurisdiction_ids=implemented_jurisdiction_ids,
        portable_jurisdiction_ids=portable_jurisdiction_ids,
        legislative_body_ids=legislative_body_ids,
        legislative_session_ids=legislative_session_ids,
        readiness_path=readiness_path,
        index_path=index_path,
        source_index_path=source_index_path,
        sector_readiness_path=sector_readiness_path,
        committee_readiness_path=committee_readiness_path,
        member_readiness_path_template=member_readiness_path_template,
        member_context_path_template=member_context_path_template,
        source_context_path_template=source_context_path_template,
        sector_context_path_template=sector_context_path_template,
        committee_context_path_template=committee_context_path_template,
    )


def build_prediction_committee_readiness(
    *,
    snapshot_id: str,
    snapshot_date: date,
    member_contexts: list[PredictionMemberContextPayload],
) -> PredictionCommitteeReadinessPayload:
    """Aggregate member prediction contexts into committee-level readiness."""
    committee_members: dict[str, dict[str, PredictionMemberContextPayload]] = {}
    committee_labels: dict[str, str | None] = {}
    committee_sector_ids: dict[str, set[str]] = {}
    committee_source_keys: dict[str, set[str]] = {}

    for context in member_contexts:
        if context.ontology_features is None:
            continue
        sector_ids = {exposure.sector_id for exposure in context.ontology_features.sector_exposures}
        for committee in context.ontology_features.committees:
            committee_members.setdefault(committee.node_id, {})[context.member_bioguide_id] = (
                context
            )
            committee_labels.setdefault(committee.node_id, committee.label)
            committee_sector_ids.setdefault(committee.node_id, set()).update(sector_ids)
            committee_source_keys.setdefault(committee.node_id, set()).update(
                _context_source_keys(context)
            )

    rows = [
        _committee_readiness_row(
            committee_id,
            label=committee_labels.get(committee_id),
            members=list(members_by_id.values()),
            sector_ids=sorted(committee_sector_ids.get(committee_id, set())),
            source_keys=sorted(committee_source_keys.get(committee_id, set())),
        )
        for committee_id, members_by_id in sorted(committee_members.items())
    ]
    return PredictionCommitteeReadinessPayload(
        snapshot_id=snapshot_id,
        snapshot_date=snapshot_date,
        committee_count=len(rows),
        committees=rows,
    )


def build_prediction_committee_contexts(
    *,
    snapshot_id: str,
    snapshot_date: date,
    member_contexts: list[PredictionMemberContextPayload],
) -> list[PredictionCommitteeContextPayload]:
    """Build direct one-committee context artifacts for fast simulation lookup."""
    readiness = build_prediction_committee_readiness(
        snapshot_id=snapshot_id,
        snapshot_date=snapshot_date,
        member_contexts=member_contexts,
    )
    members_by_committee: dict[str, list[PredictionMemberContextPayload]] = {}
    for context in member_contexts:
        if context.ontology_features is None:
            continue
        for committee in context.ontology_features.committees:
            members_by_committee.setdefault(committee.node_id, []).append(context)

    contexts: list[PredictionCommitteeContextPayload] = []
    for committee_row in readiness.committees:
        contexts.append(
            PredictionCommitteeContextPayload(
                snapshot_id=snapshot_id,
                snapshot_date=snapshot_date,
                committee=committee_row,
                members=sorted(
                    members_by_committee.get(committee_row.committee_id, []),
                    key=lambda member: member.member_bioguide_id,
                ),
            )
        )
    return contexts


def _committee_readiness_row(
    committee_id: str,
    *,
    label: str | None,
    members: list[PredictionMemberContextPayload],
    sector_ids: list[str],
    source_keys: list[str],
) -> PredictionCommitteeReadinessRowPayload:
    member_count = len(members)
    return PredictionCommitteeReadinessRowPayload(
        committee_id=committee_id,
        label=label,
        member_count=member_count,
        ready_member_count=sum(1 for member in members if member.context_status == "ready"),
        partial_member_count=sum(1 for member in members if member.context_status == "partial"),
        blocked_member_count=sum(1 for member in members if member.context_status == "blocked"),
        readiness_rate=_rate(
            sum(1 for member in members if member.context_status == "ready"),
            member_count,
        ),
        vote_coverage_rate=_rate(
            sum(1 for member in members if member.member_readiness.vote_count > 0),
            member_count,
        ),
        sector_ids=sector_ids,
        source_count=len(source_keys),
        source_keys=source_keys,
        source_context_paths=[_source_context_path(source_key) for source_key in source_keys],
        member_bioguide_ids=sorted(member.member_bioguide_id for member in members),
    )


def _sector_readiness_row(
    sector_id: str,
    *,
    label: str | None,
    members: list[PredictionMemberContextPayload],
    committee_jurisdiction_edge_count: int,
    holding_edge_count: int,
    transaction_edge_count: int,
    source_keys: list[str],
) -> PredictionSectorReadinessRowPayload:
    member_count = len(members)
    return PredictionSectorReadinessRowPayload(
        sector_id=sector_id,
        label=label,
        member_count=member_count,
        ready_member_count=sum(1 for member in members if member.context_status == "ready"),
        partial_member_count=sum(1 for member in members if member.context_status == "partial"),
        blocked_member_count=sum(1 for member in members if member.context_status == "blocked"),
        readiness_rate=_rate(
            sum(1 for member in members if member.context_status == "ready"),
            member_count,
        ),
        vote_coverage_rate=_rate(
            sum(1 for member in members if member.member_readiness.vote_count > 0),
            member_count,
        ),
        committee_jurisdiction_edge_count=committee_jurisdiction_edge_count,
        holding_edge_count=holding_edge_count,
        transaction_edge_count=transaction_edge_count,
        source_count=len(source_keys),
        source_keys=source_keys,
        source_context_paths=[_source_context_path(source_key) for source_key in source_keys],
        member_bioguide_ids=sorted(member.member_bioguide_id for member in members),
    )


def _member_readiness(
    profile: MemberProfilePayload,
    ontology_features: OntologyMemberFeaturesPayload | None,
    votes_by_bioguide: dict[str, dict[str, Any]],
) -> PredictionMemberReadinessPayload:
    vote_row = votes_by_bioguide.get(profile.bioguide_id, {})
    vote_count = _int_value(vote_row.get("vote_count"))
    yea_count = _int_value(vote_row.get("yea_count"))
    nay_count = _int_value(vote_row.get("nay_count"))
    present_count = _int_value(vote_row.get("present_count"))
    not_voting_count = _int_value(vote_row.get("not_voting_count"))
    ontology_status: PredictionReadinessStatus = (
        ontology_features.readiness_status if ontology_features is not None else "blocked"
    )
    reasons: list[str] = []
    if vote_count == 0:
        reasons.append("missing_vote_history")
    if ontology_features is None:
        reasons.append("missing_ontology_features")
    elif ontology_features.readiness_status != "ready":
        reasons.append("ontology_features_not_ready")

    readiness_status: PredictionReadinessStatus
    if not reasons:
        readiness_status = "ready"
    elif vote_count == 0 and ontology_features is None:
        readiness_status = "blocked"
    else:
        readiness_status = "partial"

    return PredictionMemberReadinessPayload(
        bioguide_id=profile.bioguide_id,
        slug=profile.slug,
        name=profile.name,
        chamber=profile.chamber,
        party=profile.party,
        state=profile.state,
        vote_count=vote_count,
        yea_count=yea_count,
        nay_count=nay_count,
        present_count=present_count,
        not_voting_count=not_voting_count,
        yea_rate=_rate(yea_count, vote_count),
        nay_rate=_rate(nay_count, vote_count),
        present_rate=_rate(present_count, vote_count),
        not_voting_rate=_rate(not_voting_count, vote_count),
        participation_rate=_rate(yea_count + nay_count + present_count, vote_count),
        latest_vote_date=vote_row.get("latest_vote_date"),
        ontology_readiness_status=ontology_status,
        ontology_edge_count=ontology_features.edge_count if ontology_features is not None else 0,
        readiness_status=readiness_status,
        readiness_reasons=reasons,
    )


def _int_value(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if type(value) is int:
        return value
    if isinstance(value, str | float | Decimal):
        return int(value)
    if value is None:
        return 0
    raise TypeError(f"cannot coerce value to int: {value!r}")


_PredictionSourceIndexKey = tuple[SourceAnchorIdentity, str, str]


def _source_anchor_key(anchor: SourceAnchor) -> _PredictionSourceIndexKey:
    return (source_anchor_identity(anchor), anchor.url or "", anchor.label)


def _source_key(anchor: SourceAnchor) -> str:
    return prediction_source_key(
        source_type=anchor.source_type,
        source_id=anchor.source_id,
        url=anchor.url,
        label=anchor.label,
        jurisdiction_id=anchor.jurisdiction_id,
        legislative_body_id=anchor.legislative_body_id,
        legislative_session_id=anchor.legislative_session_id,
    )


def _source_context_path(source_key: str) -> str:
    return prediction_source_context_path(source_key)


def _context_source_keys(context: PredictionMemberContextPayload) -> set[str]:
    return {_source_key(anchor) for anchor in context.source_anchors}


def _coverage_summary(
    members: list[PredictionMemberReadinessPayload],
) -> PredictionReadinessCoveragePayload:
    member_count = len(members)
    reason_counts: dict[str, int] = {}
    for member in members:
        for reason in member.readiness_reasons:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    return PredictionReadinessCoveragePayload(
        readiness_rate=_rate(
            sum(1 for member in members if member.readiness_status == "ready"),
            member_count,
        ),
        vote_coverage_rate=_rate(
            sum(1 for member in members if member.vote_count > 0),
            member_count,
        ),
        ontology_coverage_rate=_rate(
            sum(
                1
                for member in members
                if "missing_ontology_features" not in member.readiness_reasons
            ),
            member_count,
        ),
        average_votes_per_member=_average(
            sum(member.vote_count for member in members),
            member_count,
        ),
        average_ontology_edges_per_member=_average(
            sum(member.ontology_edge_count for member in members),
            member_count,
        ),
        readiness_reasons=[
            PredictionReadinessReasonPayload(reason=reason, member_count=count)
            for reason, count in sorted(
                reason_counts.items(),
                key=lambda item: (-item[1], _REASON_PRIORITY.get(item[0], 100), item[0]),
            )
        ],
    )


def _segment_summaries(
    members: list[PredictionMemberReadinessPayload],
) -> list[PredictionSegmentReadinessPayload]:
    segments: list[PredictionSegmentReadinessPayload] = []
    grouped_segments: tuple[
        tuple[PredictionSegmentType, dict[str, list[PredictionMemberReadinessPayload]]],
        ...,
    ] = (
        ("chamber", _group_by(members, lambda member: member.chamber)),
        ("party", _group_by(members, lambda member: member.party or "unknown")),
    )
    for segment_type, grouped_members in grouped_segments:
        for segment_key, segment_members in sorted(grouped_members.items()):
            segments.append(
                PredictionSegmentReadinessPayload(
                    segment_type=segment_type,
                    segment_key=segment_key,
                    member_count=len(segment_members),
                    ready_member_count=sum(
                        1 for member in segment_members if member.readiness_status == "ready"
                    ),
                    partial_member_count=sum(
                        1 for member in segment_members if member.readiness_status == "partial"
                    ),
                    blocked_member_count=sum(
                        1 for member in segment_members if member.readiness_status == "blocked"
                    ),
                    vote_coverage_rate=_rate(
                        sum(1 for member in segment_members if member.vote_count > 0),
                        len(segment_members),
                    ),
                    ontology_coverage_rate=_rate(
                        sum(
                            1
                            for member in segment_members
                            if "missing_ontology_features" not in member.readiness_reasons
                        ),
                        len(segment_members),
                    ),
                    readiness_rate=_rate(
                        sum(1 for member in segment_members if member.readiness_status == "ready"),
                        len(segment_members),
                    ),
                )
            )
    return segments


def _group_by(
    members: list[PredictionMemberReadinessPayload],
    key_fn: Callable[[PredictionMemberReadinessPayload], str],
) -> dict[str, list[PredictionMemberReadinessPayload]]:
    grouped: dict[str, list[PredictionMemberReadinessPayload]] = {}
    for member in members:
        grouped.setdefault(str(key_fn(member)), []).append(member)
    return grouped


def _rate(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _average(total: int, count: int) -> float:
    if count == 0:
        return 0.0
    return total / count
