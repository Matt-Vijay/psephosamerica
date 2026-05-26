from __future__ import annotations

import hashlib
from datetime import date
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from src.export.contracts import ExportContractModel, SourceAnchor
from src.evidence.source_anchor_policy import (
    duplicate_source_anchor_keys,
    has_official_claim_source_anchor,
    is_official_source_url,
)
from src.ontology.contracts import OntologyMemberFeaturesPayload

PredictionReadinessStatus = Literal["ready", "partial", "blocked"]
PredictionSegmentType = Literal["chamber", "party"]
PredictionJurisdictionImplementationStatus = Literal["implemented", "portable_contract"]


def prediction_source_key(
    *,
    source_type: str,
    source_id: str,
    url: str | None,
    label: str,
    jurisdiction_id: str | None = None,
    legislative_body_id: str | None = None,
    legislative_session_id: str | None = None,
) -> str:
    """Return the deterministic short key used for prediction source contexts."""
    legislative_context = (
        (jurisdiction_id or "", legislative_body_id or "", legislative_session_id or "")
        if source_type in {"legislative_bill", "legislative_vote"}
        else ("", "", "")
    )
    raw = "|".join((source_type, source_id, url or "", label, *legislative_context))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def prediction_source_context_path(source_key: str) -> str:
    return f"prediction/source-context/{source_key}.json"


_PREDICTION_STATIC_PATHS = frozenset(
    {
        "prediction/readiness.json",
        "prediction/index.json",
        "prediction/topology.json",
        "prediction/sources.json",
        "prediction/sectors.json",
        "prediction/committees.json",
    }
)

_PREDICTION_TEMPLATES = frozenset(
    {
        "prediction/members/{bioguide}.json",
        "prediction/member-context/{bioguide}.json",
        "prediction/source-context/{source_key}.json",
        "prediction/sector-context/{sector}.json",
        "prediction/committee-context/{committee}.json",
    }
)


def _require_known_prediction_path(value: str | None, *, field_name: str) -> None:
    if value is not None and value not in _PREDICTION_STATIC_PATHS:
        raise ValueError(f"{field_name} must be a canonical prediction artifact path")


def _require_known_prediction_template(value: str, *, field_name: str) -> None:
    if value not in _PREDICTION_TEMPLATES:
        raise ValueError(f"{field_name} must be a canonical prediction artifact template")


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _float_matches(actual: float, expected: float) -> bool:
    return abs(actual - expected) <= 1e-9


def _strip_optional_string(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


def _strip_string(value: object) -> object:
    if isinstance(value, str):
        return value.strip()
    return value


def _ensure_sorted_unique_nonblank(values: list[str], *, field_name: str) -> None:
    if any(not value.strip() or value != value.strip() for value in values):
        raise ValueError(f"{field_name} must be sorted, unique, and nonblank")
    if values != sorted(set(values)):
        raise ValueError(f"{field_name} must be sorted and unique")


def _ensure_unique_nonblank(values: list[str], *, field_name: str) -> None:
    if any(not value.strip() or value != value.strip() for value in values):
        raise ValueError(f"{field_name} must be unique and nonblank")
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} must be unique and nonblank")


def _require_nonblank_string(value: str, *, field_name: str) -> None:
    if not value:
        raise ValueError(f"{field_name} must be nonblank")


class PredictionReadinessReasonPayload(ExportContractModel):
    """Snapshot-level count for one readiness blocker reason."""

    reason: str
    member_count: int = Field(ge=0)

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, value: object) -> object:
        return _strip_string(value)

    @model_validator(mode="after")
    def reason_is_nonblank(self) -> Self:
        _require_nonblank_string(self.reason, field_name="reason")
        return self


class PredictionReadinessCoveragePayload(ExportContractModel):
    """Snapshot-level coverage summary for prediction input readiness."""

    readiness_rate: float = Field(ge=0.0, le=1.0)
    vote_coverage_rate: float = Field(ge=0.0, le=1.0)
    ontology_coverage_rate: float = Field(ge=0.0, le=1.0)
    average_votes_per_member: float = Field(ge=0.0)
    average_ontology_edges_per_member: float = Field(ge=0.0)
    readiness_reasons: list[PredictionReadinessReasonPayload] = Field(default_factory=list)

    @model_validator(mode="after")
    def readiness_reasons_are_unique(self) -> Self:
        reasons = [item.reason for item in self.readiness_reasons]
        if len(set(reasons)) != len(reasons):
            raise ValueError("readiness_reasons must be unique")
        return self


class PredictionMemberReadinessPayload(ExportContractModel):
    """Per-member readiness row for vote prediction inputs."""

    bioguide_id: str
    slug: str
    name: str
    chamber: str
    party: str | None = None
    state: str | None = None
    vote_count: int = Field(ge=0)
    yea_count: int = Field(default=0, ge=0)
    nay_count: int = Field(default=0, ge=0)
    present_count: int = Field(default=0, ge=0)
    not_voting_count: int = Field(default=0, ge=0)
    yea_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    nay_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    present_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    not_voting_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    participation_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    latest_vote_date: date | None = None
    ontology_readiness_status: PredictionReadinessStatus
    ontology_edge_count: int = Field(default=0, ge=0)
    readiness_status: PredictionReadinessStatus
    readiness_reasons: list[str] = Field(default_factory=list)

    @field_validator("bioguide_id", "slug", "name", "chamber", mode="before")
    @classmethod
    def strip_required_identity_text(cls, value: object) -> object:
        return _strip_string(value)

    @field_validator("party", "state", mode="before")
    @classmethod
    def strip_optional_identity_text(cls, value: object) -> object:
        return _strip_optional_string(value)

    @model_validator(mode="after")
    def identity_fields_are_nonblank(self) -> Self:
        for field_name in ("bioguide_id", "slug", "name", "chamber"):
            _require_nonblank_string(getattr(self, field_name), field_name=field_name)
        return self

    @model_validator(mode="after")
    def readiness_reason_shape(self) -> Self:
        _ensure_unique_nonblank(self.readiness_reasons, field_name="readiness_reasons")
        if self.readiness_status == "ready" and self.readiness_reasons:
            raise ValueError("ready prediction readiness rows must not carry reasons")
        if self.readiness_status != "ready" and not self.readiness_reasons:
            raise ValueError("non-ready prediction readiness rows require reasons")
        return self

    @model_validator(mode="after")
    def vote_counts_and_rates_match(self) -> Self:
        vote_total = self.yea_count + self.nay_count + self.present_count + self.not_voting_count
        if self.vote_count != vote_total:
            raise ValueError("vote_count must match vote option counts")
        expected_rates = {
            "yea_rate": _rate(self.yea_count, self.vote_count),
            "nay_rate": _rate(self.nay_count, self.vote_count),
            "present_rate": _rate(self.present_count, self.vote_count),
            "not_voting_rate": _rate(self.not_voting_count, self.vote_count),
            "participation_rate": _rate(
                self.yea_count + self.nay_count + self.present_count,
                self.vote_count,
            ),
        }
        for field_name, expected in expected_rates.items():
            actual = getattr(self, field_name)
            if not _float_matches(actual, expected):
                raise ValueError(f"{field_name} must match vote option counts")
        return self


class PredictionSegmentReadinessPayload(ExportContractModel):
    """Readiness summary for one member segment such as chamber or party."""

    segment_type: PredictionSegmentType
    segment_key: str
    member_count: int = Field(ge=0)
    ready_member_count: int = Field(ge=0)
    partial_member_count: int = Field(ge=0)
    blocked_member_count: int = Field(ge=0)
    vote_coverage_rate: float = Field(ge=0.0, le=1.0)
    ontology_coverage_rate: float = Field(ge=0.0, le=1.0)
    readiness_rate: float = Field(ge=0.0, le=1.0)

    @field_validator("segment_key", mode="before")
    @classmethod
    def strip_segment_key(cls, value: object) -> object:
        return _strip_string(value)

    @model_validator(mode="after")
    def segment_counts_match(self) -> Self:
        _require_nonblank_string(self.segment_key, field_name="segment_key")
        if (
            self.ready_member_count + self.partial_member_count + self.blocked_member_count
            != self.member_count
        ):
            raise ValueError("segment status counts must sum to member_count")
        if not _float_matches(
            self.readiness_rate,
            _rate(self.ready_member_count, self.member_count),
        ):
            raise ValueError("segment readiness_rate must match status counts")
        return self


class PredictionJurisdictionCapabilityPayload(ExportContractModel):
    """What a jurisdiction must provide to reuse the prediction pipeline."""

    jurisdiction_id: str
    implementation_status: PredictionJurisdictionImplementationStatus
    legislative_body_ids: list[str] = Field(default_factory=list)
    legislative_session_ids: list[str] = Field(default_factory=list)
    required_source_roles: list[str] = Field(default_factory=list)

    @field_validator("jurisdiction_id", mode="before")
    @classmethod
    def strip_jurisdiction_id(cls, value: object) -> object:
        return _strip_string(value)

    @model_validator(mode="after")
    def capability_shape(self) -> Self:
        _require_nonblank_string(self.jurisdiction_id, field_name="jurisdiction_id")
        _ensure_sorted_unique_nonblank(
            self.legislative_body_ids,
            field_name="legislative_body_ids",
        )
        _ensure_sorted_unique_nonblank(
            self.legislative_session_ids,
            field_name="legislative_session_ids",
        )
        _ensure_sorted_unique_nonblank(
            self.required_source_roles,
            field_name="required_source_roles",
        )
        if any(":" in body_id for body_id in self.legislative_body_ids):
            raise ValueError("legislative_body_ids must be local body ids")
        body_ids = set(self.legislative_body_ids)
        for session_id in self.legislative_session_ids:
            parts = session_id.split(":")
            if len(parts) != 2 or not parts[0] or not parts[1]:
                raise ValueError("legislative_session_ids must be body scoped")
            if parts[0] not in body_ids:
                raise ValueError("legislative_session_ids must reference body ids")
        return self


class PredictionReadinessPayload(ExportContractModel):
    """Snapshot-level readiness report for vote prediction and simulation inputs."""

    snapshot_id: str
    snapshot_date: date
    member_count: int = Field(ge=0)
    ready_member_count: int = Field(ge=0)
    partial_member_count: int = Field(ge=0)
    blocked_member_count: int = Field(ge=0)
    vote_event_count: int = Field(ge=0)
    vote_cast_count: int = Field(ge=0)
    coverage: PredictionReadinessCoveragePayload
    jurisdictions: list[PredictionJurisdictionCapabilityPayload] = Field(default_factory=list)
    segments: list[PredictionSegmentReadinessPayload] = Field(default_factory=list)
    members: list[PredictionMemberReadinessPayload] = Field(default_factory=list)

    @model_validator(mode="after")
    def counts_match_members(self) -> Self:
        if self.member_count != len(self.members):
            raise ValueError("member_count must match members length")
        status_counts = {"ready": 0, "partial": 0, "blocked": 0}
        reason_counts: dict[str, int] = {}
        for member in self.members:
            status_counts[member.readiness_status] += 1
            for reason in member.readiness_reasons:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
        if self.ready_member_count != status_counts["ready"]:
            raise ValueError("ready_member_count must match members")
        if self.partial_member_count != status_counts["partial"]:
            raise ValueError("partial_member_count must match members")
        if self.blocked_member_count != status_counts["blocked"]:
            raise ValueError("blocked_member_count must match members")
        if self.vote_cast_count != sum(member.vote_count for member in self.members):
            raise ValueError("vote_cast_count must match member vote totals")
        segment_keys = [(segment.segment_type, segment.segment_key) for segment in self.segments]
        if segment_keys != sorted(set(segment_keys)):
            raise ValueError("segments must be sorted and unique")
        jurisdiction_ids = [jurisdiction.jurisdiction_id for jurisdiction in self.jurisdictions]
        if jurisdiction_ids != sorted(set(jurisdiction_ids)):
            raise ValueError("jurisdictions must be sorted and unique")
        expected_coverage = {
            "readiness_rate": _rate(self.ready_member_count, self.member_count),
            "vote_coverage_rate": _rate(
                sum(1 for member in self.members if member.vote_count > 0),
                self.member_count,
            ),
            "ontology_coverage_rate": _rate(
                sum(
                    1
                    for member in self.members
                    if "missing_ontology_features" not in member.readiness_reasons
                ),
                self.member_count,
            ),
            "average_votes_per_member": _rate(self.vote_cast_count, self.member_count),
            "average_ontology_edges_per_member": _rate(
                sum(member.ontology_edge_count for member in self.members),
                self.member_count,
            ),
        }
        for field_name, expected in expected_coverage.items():
            actual = getattr(self.coverage, field_name)
            if not _float_matches(actual, expected):
                raise ValueError(f"coverage.{field_name} must match members")
        expected_reasons = {
            item.reason: item.member_count for item in self.coverage.readiness_reasons
        }
        if expected_reasons != reason_counts:
            raise ValueError("coverage.readiness_reasons must match member reasons")
        return self


class PredictionReadinessIndexPayload(ExportContractModel):
    """Hashtable-style index for fast per-member prediction readiness lookup."""

    snapshot_id: str
    snapshot_date: date
    member_count: int = Field(ge=0)
    slug_to_bioguide: dict[str, str] = Field(default_factory=dict)
    members_by_bioguide: dict[str, PredictionMemberReadinessPayload] = Field(default_factory=dict)

    @model_validator(mode="after")
    def counts_match_maps(self) -> Self:
        if self.member_count != len(self.members_by_bioguide):
            raise ValueError("member_count must match members_by_bioguide length")
        expected_member_keys = {member.bioguide_id for member in self.members_by_bioguide.values()}
        if set(self.members_by_bioguide) != expected_member_keys:
            raise ValueError("members_by_bioguide keys must match member bioguide IDs")
        expected_slugs = {member.slug for member in self.members_by_bioguide.values()}
        if set(self.slug_to_bioguide) != expected_slugs:
            raise ValueError("slug_to_bioguide keys must match indexed member slugs")
        for slug, bioguide_id in self.slug_to_bioguide.items():
            member = self.members_by_bioguide.get(bioguide_id)
            if member is None or member.slug != slug:
                raise ValueError("slug_to_bioguide must point at matching indexed members")
        return self


class PredictionContextArtifactRefsPayload(ExportContractModel):
    """Published artifact pointers needed to trace one prediction context."""

    member_profile_path: str
    member_page_path: str
    prediction_member_readiness_path: str
    prediction_member_context_path: str
    ontology_member_features_path: str | None = None
    ontology_member_graph_path: str | None = None


class PredictionMemberContextPayload(ExportContractModel):
    """One-member prediction input bundle for LLM and simulation contexts."""

    snapshot_id: str
    snapshot_date: date
    member_bioguide_id: str
    member_readiness: PredictionMemberReadinessPayload
    ontology_features: OntologyMemberFeaturesPayload | None = None
    context_status: PredictionReadinessStatus
    context_reasons: list[str] = Field(default_factory=list)
    artifact_refs: PredictionContextArtifactRefsPayload | None = None
    source_anchors: list[SourceAnchor] = Field(default_factory=list)
    source_keys: list[str] = Field(default_factory=list)
    source_context_paths: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def source_keys_match_anchors(self) -> Self:
        if len(self.source_keys) != len(self.source_anchors):
            raise ValueError("source_keys must match source_anchors length")
        if len(self.source_context_paths) != len(self.source_keys):
            raise ValueError("source_context_paths must match source_keys length")
        if len(set(self.source_keys)) != len(self.source_keys):
            raise ValueError("source_keys must be unique")
        duplicate_anchor_keys = duplicate_source_anchor_keys(self.source_anchors)
        if duplicate_anchor_keys:
            raise ValueError(
                "prediction contexts reject duplicate source anchors: "
                f"{', '.join(duplicate_anchor_keys)}"
            )
        expected_source_keys = [
            prediction_source_key(
                source_type=anchor.source_type,
                source_id=anchor.source_id,
                url=anchor.url,
                label=anchor.label,
                jurisdiction_id=anchor.jurisdiction_id,
                legislative_body_id=anchor.legislative_body_id,
                legislative_session_id=anchor.legislative_session_id,
            )
            for anchor in self.source_anchors
        ]
        if self.source_keys != expected_source_keys:
            raise ValueError("source_keys must match source_anchors")
        expected_context_paths = [
            prediction_source_context_path(source_key) for source_key in self.source_keys
        ]
        if self.source_context_paths != expected_context_paths:
            raise ValueError("source_context_paths must match source_keys")
        return self

    @model_validator(mode="after")
    def context_matches_member(self) -> Self:
        _ensure_unique_nonblank(self.context_reasons, field_name="context_reasons")
        if self.member_bioguide_id != self.member_readiness.bioguide_id:
            raise ValueError("member_bioguide_id must match member_readiness")
        if (
            self.ontology_features is not None
            and self.ontology_features.member_bioguide_id != self.member_bioguide_id
        ):
            raise ValueError("ontology_features must match member_bioguide_id")
        if self.context_status == "ready" and self.context_reasons:
            raise ValueError("ready prediction contexts must not carry reasons")
        if self.context_status != "ready" and not self.context_reasons:
            raise ValueError("non-ready prediction contexts require reasons")
        if self.context_status == "ready" and not self.source_anchors:
            raise ValueError("ready prediction contexts require source anchors")
        if self.context_status == "ready" and not has_official_claim_source_anchor(
            self.source_anchors
        ):
            raise ValueError("ready prediction contexts require an official source anchor")
        return self


class PredictionBootstrapPayload(ExportContractModel):
    """Small entrypoint for prediction/simulation frontend and LLM clients."""

    snapshot_id: str
    snapshot_date: date
    member_count: int = Field(ge=0)
    ready_member_count: int = Field(ge=0)
    partial_member_count: int = Field(ge=0)
    blocked_member_count: int = Field(ge=0)
    vote_event_count: int = Field(ge=0)
    vote_cast_count: int = Field(ge=0)
    coverage: PredictionReadinessCoveragePayload
    jurisdictions: list[PredictionJurisdictionCapabilityPayload] = Field(default_factory=list)
    readiness_path: str
    index_path: str
    topology_path: str | None = None
    source_index_path: str
    source_context_path_template: str
    sector_readiness_path: str
    sector_context_path_template: str
    committee_readiness_path: str
    committee_context_path_template: str
    member_readiness_path_template: str
    member_context_path_template: str

    @model_validator(mode="after")
    def paths_are_canonical(self) -> Self:
        _require_known_prediction_path(self.readiness_path, field_name="readiness_path")
        _require_known_prediction_path(self.index_path, field_name="index_path")
        _require_known_prediction_path(self.topology_path, field_name="topology_path")
        _require_known_prediction_path(self.source_index_path, field_name="source_index_path")
        _require_known_prediction_path(
            self.sector_readiness_path,
            field_name="sector_readiness_path",
        )
        _require_known_prediction_path(
            self.committee_readiness_path,
            field_name="committee_readiness_path",
        )
        _require_known_prediction_template(
            self.source_context_path_template,
            field_name="source_context_path_template",
        )
        _require_known_prediction_template(
            self.sector_context_path_template,
            field_name="sector_context_path_template",
        )
        _require_known_prediction_template(
            self.committee_context_path_template,
            field_name="committee_context_path_template",
        )
        _require_known_prediction_template(
            self.member_readiness_path_template,
            field_name="member_readiness_path_template",
        )
        _require_known_prediction_template(
            self.member_context_path_template,
            field_name="member_context_path_template",
        )
        jurisdiction_ids = [jurisdiction.jurisdiction_id for jurisdiction in self.jurisdictions]
        if jurisdiction_ids != sorted(set(jurisdiction_ids)):
            raise ValueError("jurisdictions must be sorted and unique")
        return self


class PredictionTopologyPayload(ExportContractModel):
    """Discoverable prediction artifact topology for frontend and LLM clients."""

    snapshot_id: str
    snapshot_date: date
    member_count: int = Field(ge=0)
    sector_count: int = Field(ge=0)
    committee_count: int = Field(ge=0)
    source_count: int = Field(ge=0)
    jurisdiction_count: int = Field(default=0, ge=0)
    implemented_jurisdiction_count: int = Field(default=0, ge=0)
    portable_jurisdiction_count: int = Field(default=0, ge=0)
    legislative_body_count: int = Field(default=0, ge=0)
    legislative_session_count: int = Field(default=0, ge=0)
    jurisdiction_ids: list[str] = Field(default_factory=list)
    implemented_jurisdiction_ids: list[str] = Field(default_factory=list)
    portable_jurisdiction_ids: list[str] = Field(default_factory=list)
    legislative_body_ids: list[str] = Field(default_factory=list)
    legislative_session_ids: list[str] = Field(default_factory=list)
    readiness_path: str
    index_path: str
    source_index_path: str
    sector_readiness_path: str
    committee_readiness_path: str
    member_readiness_path_template: str
    member_context_path_template: str
    source_context_path_template: str
    sector_context_path_template: str
    committee_context_path_template: str

    @model_validator(mode="after")
    def paths_are_canonical(self) -> Self:
        _require_known_prediction_path(self.readiness_path, field_name="readiness_path")
        _require_known_prediction_path(self.index_path, field_name="index_path")
        _require_known_prediction_path(self.source_index_path, field_name="source_index_path")
        _require_known_prediction_path(
            self.sector_readiness_path,
            field_name="sector_readiness_path",
        )
        _require_known_prediction_path(
            self.committee_readiness_path,
            field_name="committee_readiness_path",
        )
        _require_known_prediction_template(
            self.member_readiness_path_template,
            field_name="member_readiness_path_template",
        )
        _require_known_prediction_template(
            self.member_context_path_template,
            field_name="member_context_path_template",
        )
        _require_known_prediction_template(
            self.source_context_path_template,
            field_name="source_context_path_template",
        )
        _require_known_prediction_template(
            self.sector_context_path_template,
            field_name="sector_context_path_template",
        )
        _require_known_prediction_template(
            self.committee_context_path_template,
            field_name="committee_context_path_template",
        )
        if (
            self.implemented_jurisdiction_count + self.portable_jurisdiction_count
            != self.jurisdiction_count
        ):
            raise ValueError("jurisdiction status counts must sum to jurisdiction_count")
        for field_name in (
            "jurisdiction_ids",
            "implemented_jurisdiction_ids",
            "portable_jurisdiction_ids",
            "legislative_body_ids",
            "legislative_session_ids",
        ):
            _ensure_sorted_unique_nonblank(getattr(self, field_name), field_name=field_name)
        if self.jurisdiction_count != len(self.jurisdiction_ids):
            raise ValueError("jurisdiction_count must match jurisdiction_ids")
        if self.implemented_jurisdiction_count != len(self.implemented_jurisdiction_ids):
            raise ValueError(
                "implemented_jurisdiction_count must match implemented_jurisdiction_ids"
            )
        if self.portable_jurisdiction_count != len(self.portable_jurisdiction_ids):
            raise ValueError("portable_jurisdiction_count must match portable_jurisdiction_ids")
        if self.legislative_body_count != len(self.legislative_body_ids):
            raise ValueError("legislative_body_count must match legislative_body_ids")
        if self.legislative_session_count != len(self.legislative_session_ids):
            raise ValueError("legislative_session_count must match legislative_session_ids")
        if set(self.implemented_jurisdiction_ids) | set(self.portable_jurisdiction_ids) != set(
            self.jurisdiction_ids
        ):
            raise ValueError("jurisdiction status ids must cover jurisdiction_ids")
        if set(self.implemented_jurisdiction_ids) & set(self.portable_jurisdiction_ids):
            raise ValueError("jurisdiction status ids must not overlap")
        jurisdiction_ids = set(self.jurisdiction_ids)
        for body_id in self.legislative_body_ids:
            parts = body_id.split(":")
            if len(parts) != 2 or parts[0] not in jurisdiction_ids or not parts[1]:
                raise ValueError("legislative_body_ids must be jurisdiction scoped")
        body_ids = set(self.legislative_body_ids)
        for session_id in self.legislative_session_ids:
            parts = session_id.split(":")
            if (
                len(parts) != 3
                or parts[0] not in jurisdiction_ids
                or f"{parts[0]}:{parts[1]}" not in body_ids
                or not parts[2]
            ):
                raise ValueError("legislative_session_ids must be body scoped")
        return self


class PredictionSourceIndexRowPayload(ExportContractModel):
    """Reverse index row from one official source to prediction contexts it supports."""

    source_key: str
    source_type: str
    source_id: str
    jurisdiction_id: str | None = None
    legislative_body_id: str | None = None
    legislative_session_id: str | None = None
    url: str | None = None
    label: str
    source_context_path: str
    member_count: int = Field(ge=0)
    member_bioguide_ids: list[str] = Field(default_factory=list)
    sector_ids: list[str] = Field(default_factory=list)
    committee_ids: list[str] = Field(default_factory=list)

    @field_validator("source_key", "source_type", "source_id", "label", mode="before")
    @classmethod
    def strip_required_text(cls, value: object) -> object:
        return _strip_string(value)

    @field_validator("url", mode="before")
    @classmethod
    def strip_optional_url(cls, value: object) -> object:
        return _strip_optional_string(value)

    @field_validator(
        "jurisdiction_id", "legislative_body_id", "legislative_session_id", mode="before"
    )
    @classmethod
    def strip_optional_legislative_context(cls, value: object) -> object:
        return _strip_optional_string(value)

    @model_validator(mode="after")
    def source_member_count_matches(self) -> Self:
        for field_name in ("source_key", "source_type", "source_id", "label"):
            _require_nonblank_string(getattr(self, field_name), field_name=field_name)
        if self.source_type in {"legislative_bill", "legislative_vote"}:
            missing_context = [
                field_name
                for field_name in (
                    "jurisdiction_id",
                    "legislative_body_id",
                    "legislative_session_id",
                )
                if getattr(self, field_name) is None
            ]
            if missing_context:
                raise ValueError(
                    "prediction legislative source rows require context: "
                    f"{', '.join(missing_context)}"
                )
        _ensure_sorted_unique_nonblank(
            self.member_bioguide_ids,
            field_name="member_bioguide_ids",
        )
        _ensure_sorted_unique_nonblank(self.sector_ids, field_name="sector_ids")
        _ensure_sorted_unique_nonblank(self.committee_ids, field_name="committee_ids")
        if self.member_count != len(self.member_bioguide_ids):
            raise ValueError("source member_count must match member_bioguide_ids")
        if not is_official_source_url(self.source_type, self.url):
            raise ValueError("prediction source rows require official source URLs")
        expected_source_key = prediction_source_key(
            source_type=self.source_type,
            source_id=self.source_id,
            url=self.url,
            label=self.label,
            jurisdiction_id=self.jurisdiction_id,
            legislative_body_id=self.legislative_body_id,
            legislative_session_id=self.legislative_session_id,
        )
        if self.source_key != expected_source_key:
            raise ValueError("prediction source_key must match source identity")
        if self.source_context_path != prediction_source_context_path(self.source_key):
            raise ValueError("prediction source_context_path must match source_key")
        return self


class PredictionSourceIndexPayload(ExportContractModel):
    """Source-centric lookup index for tracing prediction inputs."""

    snapshot_id: str
    snapshot_date: date
    source_count: int = Field(ge=0)
    sources: list[PredictionSourceIndexRowPayload] = Field(default_factory=list)

    @model_validator(mode="after")
    def source_count_matches_rows(self) -> Self:
        if self.source_count != len(self.sources):
            raise ValueError("source_count must match sources length")
        source_keys = [source.source_key for source in self.sources]
        if source_keys != sorted(source_keys):
            raise ValueError("sources must be sorted by source_key")
        if len(set(source_keys)) != len(source_keys):
            raise ValueError("source keys must be unique")
        return self


class PredictionSourceContextPayload(ExportContractModel):
    """One-source prediction context bundle for source traceability clients."""

    snapshot_id: str
    snapshot_date: date
    source: PredictionSourceIndexRowPayload
    members: list[PredictionMemberContextPayload] = Field(default_factory=list)

    @model_validator(mode="after")
    def source_members_match_summary(self) -> Self:
        if self.source.member_count != len(self.members):
            raise ValueError("source member_count must match members length")
        member_ids = sorted(member.member_bioguide_id for member in self.members)
        if self.source.member_bioguide_ids != member_ids:
            raise ValueError("source member_bioguide_ids must match members")
        if any(self.source.source_key not in member.source_keys for member in self.members):
            raise ValueError("source context members must reference source_key")
        return self


class PredictionCommitteeReadinessRowPayload(ExportContractModel):
    """Prediction readiness summary for one congressional committee."""

    committee_id: str
    label: str | None = None
    member_count: int = Field(ge=0)
    ready_member_count: int = Field(ge=0)
    partial_member_count: int = Field(ge=0)
    blocked_member_count: int = Field(ge=0)
    readiness_rate: float = Field(ge=0.0, le=1.0)
    vote_coverage_rate: float = Field(ge=0.0, le=1.0)
    sector_ids: list[str] = Field(default_factory=list)
    source_count: int = Field(default=0, ge=0)
    source_keys: list[str] = Field(default_factory=list)
    source_context_paths: list[str] = Field(default_factory=list)
    member_bioguide_ids: list[str] = Field(default_factory=list)

    @field_validator("committee_id", mode="before")
    @classmethod
    def strip_committee_id(cls, value: object) -> object:
        return _strip_string(value)

    @field_validator("label", mode="before")
    @classmethod
    def strip_committee_label(cls, value: object) -> object:
        return _strip_optional_string(value)

    @model_validator(mode="after")
    def committee_counts_match(self) -> Self:
        _require_nonblank_string(self.committee_id, field_name="committee_id")
        if (
            self.ready_member_count + self.partial_member_count + self.blocked_member_count
            != self.member_count
        ):
            raise ValueError("committee status counts must sum to member_count")
        if self.member_count != len(self.member_bioguide_ids):
            raise ValueError("member_count must match member_bioguide_ids")
        _ensure_sorted_unique_nonblank(
            self.member_bioguide_ids,
            field_name="member_bioguide_ids",
        )
        _ensure_sorted_unique_nonblank(self.sector_ids, field_name="sector_ids")
        _ensure_sorted_unique_nonblank(self.source_keys, field_name="source_keys")
        if self.source_count != len(self.source_keys):
            raise ValueError("source_count must match source_keys")
        if len(self.source_context_paths) != len(self.source_keys):
            raise ValueError("source_context_paths must match source_keys")
        if not _float_matches(
            self.readiness_rate,
            _rate(self.ready_member_count, self.member_count),
        ):
            raise ValueError("committee readiness_rate must match status counts")
        expected_source_context_paths = [
            prediction_source_context_path(source_key) for source_key in self.source_keys
        ]
        if self.source_context_paths != expected_source_context_paths:
            raise ValueError("committee source_context_paths must match source_keys")
        return self


class PredictionCommitteeReadinessPayload(ExportContractModel):
    """Committee-level readiness index for lobbying and simulation routing."""

    snapshot_id: str
    snapshot_date: date
    committee_count: int = Field(ge=0)
    committees: list[PredictionCommitteeReadinessRowPayload] = Field(default_factory=list)

    @model_validator(mode="after")
    def committee_count_matches_rows(self) -> Self:
        if self.committee_count != len(self.committees):
            raise ValueError("committee_count must match committees length")
        committee_ids = [committee.committee_id for committee in self.committees]
        if committee_ids != sorted(set(committee_ids)):
            raise ValueError("committees must be sorted by committee_id")
        return self


class PredictionCommitteeContextPayload(ExportContractModel):
    """One-committee prediction context bundle for lobbying and simulation clients."""

    snapshot_id: str
    snapshot_date: date
    committee: PredictionCommitteeReadinessRowPayload
    members: list[PredictionMemberContextPayload] = Field(default_factory=list)

    @model_validator(mode="after")
    def committee_members_match_summary(self) -> Self:
        if self.committee.member_count != len(self.members):
            raise ValueError("committee member_count must match members length")
        member_ids = sorted(member.member_bioguide_id for member in self.members)
        if self.committee.member_bioguide_ids != member_ids:
            raise ValueError("committee member_bioguide_ids must match members")
        for member in self.members:
            committee_ids = (
                {committee.node_id for committee in member.ontology_features.committees}
                if member.ontology_features is not None
                else set()
            )
            if self.committee.committee_id not in committee_ids:
                raise ValueError("committee context members must include committee")
        return self


class PredictionSectorReadinessRowPayload(ExportContractModel):
    """Prediction readiness summary for one ontology sector."""

    sector_id: str
    label: str | None = None
    member_count: int = Field(ge=0)
    ready_member_count: int = Field(ge=0)
    partial_member_count: int = Field(ge=0)
    blocked_member_count: int = Field(ge=0)
    readiness_rate: float = Field(ge=0.0, le=1.0)
    vote_coverage_rate: float = Field(ge=0.0, le=1.0)
    committee_jurisdiction_edge_count: int = Field(ge=0)
    holding_edge_count: int = Field(ge=0)
    transaction_edge_count: int = Field(ge=0)
    source_count: int = Field(default=0, ge=0)
    source_keys: list[str] = Field(default_factory=list)
    source_context_paths: list[str] = Field(default_factory=list)
    member_bioguide_ids: list[str] = Field(default_factory=list)

    @field_validator("sector_id", mode="before")
    @classmethod
    def strip_sector_id(cls, value: object) -> object:
        return _strip_string(value)

    @field_validator("label", mode="before")
    @classmethod
    def strip_sector_label(cls, value: object) -> object:
        return _strip_optional_string(value)

    @model_validator(mode="after")
    def sector_counts_match(self) -> Self:
        _require_nonblank_string(self.sector_id, field_name="sector_id")
        if (
            self.ready_member_count + self.partial_member_count + self.blocked_member_count
            != self.member_count
        ):
            raise ValueError("sector status counts must sum to member_count")
        if self.member_count != len(self.member_bioguide_ids):
            raise ValueError("member_count must match member_bioguide_ids")
        _ensure_sorted_unique_nonblank(
            self.member_bioguide_ids,
            field_name="member_bioguide_ids",
        )
        _ensure_sorted_unique_nonblank(self.source_keys, field_name="source_keys")
        if self.source_count != len(self.source_keys):
            raise ValueError("source_count must match source_keys")
        if len(self.source_context_paths) != len(self.source_keys):
            raise ValueError("source_context_paths must match source_keys")
        if not _float_matches(
            self.readiness_rate,
            _rate(self.ready_member_count, self.member_count),
        ):
            raise ValueError("sector readiness_rate must match status counts")
        expected_source_context_paths = [
            prediction_source_context_path(source_key) for source_key in self.source_keys
        ]
        if self.source_context_paths != expected_source_context_paths:
            raise ValueError("sector source_context_paths must match source_keys")
        return self


class PredictionSectorReadinessPayload(ExportContractModel):
    """Sector-level readiness index for lobbying and simulation surfaces."""

    snapshot_id: str
    snapshot_date: date
    sector_count: int = Field(ge=0)
    sectors: list[PredictionSectorReadinessRowPayload] = Field(default_factory=list)

    @model_validator(mode="after")
    def sector_count_matches_rows(self) -> Self:
        if self.sector_count != len(self.sectors):
            raise ValueError("sector_count must match sectors length")
        sector_ids = [sector.sector_id for sector in self.sectors]
        if sector_ids != sorted(set(sector_ids)):
            raise ValueError("sectors must be sorted by sector_id")
        return self


class PredictionSectorContextPayload(ExportContractModel):
    """One-sector prediction context bundle for lobbying and simulation clients."""

    snapshot_id: str
    snapshot_date: date
    sector: PredictionSectorReadinessRowPayload
    members: list[PredictionMemberContextPayload] = Field(default_factory=list)

    @model_validator(mode="after")
    def sector_members_match_summary(self) -> Self:
        if self.sector.member_count != len(self.members):
            raise ValueError("sector member_count must match members length")
        member_ids = sorted(member.member_bioguide_id for member in self.members)
        if self.sector.member_bioguide_ids != member_ids:
            raise ValueError("sector member_bioguide_ids must match members")
        for member in self.members:
            sector_ids = (
                {exposure.sector_id for exposure in member.ontology_features.sector_exposures}
                if member.ontology_features is not None
                else set()
            )
            if self.sector.sector_id not in sector_ids:
                raise ValueError("sector context members must include sector exposure")
        return self
