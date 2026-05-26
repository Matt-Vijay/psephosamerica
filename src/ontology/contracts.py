from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from src.export.contracts import SourceAnchor
from src.evidence.source_anchor_policy import (
    duplicate_source_anchor_keys,
    has_official_claim_source_anchor,
)

OntologyNodeType = Literal["member", "committee", "sector", "issuer"]
OntologyEdgeType = Literal[
    "member_committee_assignment",
    "committee_sector_jurisdiction",
    "member_sector_holding_exposure",
    "member_sector_transaction_exposure",
    "member_sector_contribution_exposure",
    "member_sector_statement_alignment",
    "member_sector_public_statement_alignment",
]


def _require_nonnegative_count_map(values: dict[str, int], *, field_name: str) -> None:
    if any(value < 0 for value in values.values()):
        raise ValueError(f"{field_name} values must be nonnegative")


def _reject_boolean_int(value: object, field_name: str) -> object:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _reject_boolean_count_map_values(
    value: object,
    *,
    field_name: str,
) -> object:
    if isinstance(value, dict) and any(isinstance(count, bool) for count in value.values()):
        raise ValueError(f"{field_name} values must be integers")
    return value


def _require_sorted_unique(values: list[str], *, field_name: str) -> None:
    if any(not value.strip() or value != value.strip() for value in values):
        raise ValueError(f"{field_name} must be sorted, unique, and nonblank")
    if values != sorted(set(values)):
        raise ValueError(f"{field_name} must be sorted and unique")


def _node_ref_keys(nodes: list["OntologyNodeRef"]) -> list[tuple[str, str]]:
    return [(node.node_type, node.node_id) for node in nodes]


class OntologyNodeRef(BaseModel):
    """Stable reference to one node in the congressional intelligence ontology."""

    node_type: OntologyNodeType
    node_id: str
    label: str | None = None

    @field_validator("node_id", mode="before")
    @classmethod
    def strip_node_id(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("label", mode="before")
    @classmethod
    def strip_optional_label(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @model_validator(mode="after")
    def node_id_is_not_blank(self) -> Self:
        if not self.node_id:
            raise ValueError("node_id must not be blank")
        return self


class OntologyEdgePayload(BaseModel):
    """Source-backed relationship used by scoring, prediction, and simulations."""

    edge_id: str
    edge_type: OntologyEdgeType
    subject: OntologyNodeRef
    object: OntologyNodeRef
    source_anchors: list[SourceAnchor] = Field(min_length=1)
    confidence: Literal["high", "medium", "low"] = "high"
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("edge_id", mode="before")
    @classmethod
    def strip_edge_id(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @model_validator(mode="after")
    def require_official_claim_source(self) -> Self:
        if not self.edge_id:
            raise ValueError("edge_id must not be blank")
        if not has_official_claim_source_anchor(self.source_anchors):
            raise ValueError("ontology edges require at least one official source anchor")
        duplicate_anchor_keys = duplicate_source_anchor_keys(self.source_anchors)
        if duplicate_anchor_keys:
            raise ValueError(
                "ontology edges reject duplicate source anchors: "
                f"{', '.join(duplicate_anchor_keys)}"
            )
        return self

    @model_validator(mode="after")
    def endpoints_match_edge_type(self) -> Self:
        expected_shapes = {
            "member_committee_assignment": ("member", "committee"),
            "committee_sector_jurisdiction": ("committee", "sector"),
            "member_sector_holding_exposure": ("member", "sector"),
            "member_sector_transaction_exposure": ("member", "sector"),
            "member_sector_contribution_exposure": ("member", "sector"),
            "member_sector_statement_alignment": ("member", "sector"),
            "member_sector_public_statement_alignment": ("member", "sector"),
        }
        expected_subject_type, expected_object_type = expected_shapes[self.edge_type]
        if (
            self.subject.node_type != expected_subject_type
            or self.object.node_type != expected_object_type
        ):
            raise ValueError(
                f"{self.edge_type} must connect {expected_subject_type} to {expected_object_type}"
            )
        return self


class OntologyGraphPayload(BaseModel):
    """Frontend-fast ontology slice for a published snapshot."""

    snapshot_id: str
    edge_count: int
    edges: list[OntologyEdgePayload] = Field(default_factory=list)

    @field_validator("edge_count", mode="before")
    @classmethod
    def edge_count_is_plain_int(cls, value: object, info: object) -> object:
        return _reject_boolean_int(value, getattr(info, "field_name", "count"))

    @model_validator(mode="after")
    def edge_count_matches_edges(self) -> Self:
        if self.edge_count != len(self.edges):
            raise ValueError("edge_count must match edges length")
        edge_ids = [edge.edge_id for edge in self.edges]
        if len(set(edge_ids)) != len(edge_ids):
            raise ValueError("ontology graph edge IDs must be unique")
        return self


class OntologyIndexPayload(BaseModel):
    """Compact ontology coverage/readiness index for fast routing and LLM lookup."""

    snapshot_id: str
    edge_count: int = Field(ge=0)
    node_count: int = Field(ge=0)
    member_count: int = Field(ge=0)
    edge_type_counts: dict[str, int] = Field(default_factory=dict)
    node_type_counts: dict[str, int] = Field(default_factory=dict)
    source_type_counts: dict[str, int] = Field(default_factory=dict)
    member_edge_counts: dict[str, int] = Field(default_factory=dict)
    available_member_graphs: list[str] = Field(default_factory=list)

    @field_validator("edge_count", "node_count", "member_count", mode="before")
    @classmethod
    def top_level_counts_are_plain_ints(cls, value: object, info: object) -> object:
        return _reject_boolean_int(value, getattr(info, "field_name", "count"))

    @field_validator(
        "edge_type_counts",
        "node_type_counts",
        "source_type_counts",
        "member_edge_counts",
        mode="before",
    )
    @classmethod
    def count_map_values_are_plain_ints(
        cls,
        value: object,
        info: object,
    ) -> object:
        return _reject_boolean_count_map_values(
            value,
            field_name=getattr(info, "field_name", "count_map"),
        )

    @model_validator(mode="after")
    def counts_match_index(self) -> Self:
        _require_nonnegative_count_map(self.edge_type_counts, field_name="edge_type_counts")
        _require_nonnegative_count_map(self.node_type_counts, field_name="node_type_counts")
        _require_nonnegative_count_map(
            self.source_type_counts,
            field_name="source_type_counts",
        )
        _require_nonnegative_count_map(
            self.member_edge_counts,
            field_name="member_edge_counts",
        )
        _require_sorted_unique(
            self.available_member_graphs,
            field_name="available_member_graphs",
        )
        if self.edge_count != sum(self.edge_type_counts.values()):
            raise ValueError("edge_count must match edge_type_counts total")
        if self.node_count != sum(self.node_type_counts.values()):
            raise ValueError("node_count must match node_type_counts total")
        if self.member_count != len(self.available_member_graphs):
            raise ValueError("member_count must match available_member_graphs length")
        if set(self.member_edge_counts) != set(self.available_member_graphs):
            raise ValueError("member_edge_counts must match available_member_graphs")
        return self


class OntologySectorExposurePayload(BaseModel):
    """Per-sector feature summary for one member ontology slice."""

    sector_id: str
    label: str | None = None
    committee_jurisdiction_edge_count: int = Field(default=0, ge=0)
    holding_edge_count: int = Field(default=0, ge=0)
    transaction_edge_count: int = Field(default=0, ge=0)
    contribution_edge_count: int = Field(default=0, ge=0)
    statement_edge_count: int = Field(default=0, ge=0)
    source_count: int = Field(default=0, ge=0)

    @field_validator("sector_id", mode="before")
    @classmethod
    def strip_sector_id(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator(
        "committee_jurisdiction_edge_count",
        "holding_edge_count",
        "transaction_edge_count",
        "contribution_edge_count",
        "statement_edge_count",
        "source_count",
        mode="before",
    )
    @classmethod
    def sector_counts_are_plain_ints(cls, value: object, info: object) -> object:
        return _reject_boolean_int(value, getattr(info, "field_name", "count"))

    @field_validator("label", mode="before")
    @classmethod
    def strip_optional_label(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @model_validator(mode="after")
    def sector_id_is_not_blank(self) -> Self:
        if not self.sector_id:
            raise ValueError("sector_id must not be blank")
        return self


class OntologyMemberFeaturesPayload(BaseModel):
    """Compact source-backed member features for prediction and LLM context lookup."""

    snapshot_id: str
    member_bioguide_id: str
    edge_count: int = Field(ge=0)
    source_count: int = Field(ge=0)
    edge_type_counts: dict[str, int] = Field(default_factory=dict)
    source_type_counts: dict[str, int] = Field(default_factory=dict)
    committees: list[OntologyNodeRef] = Field(default_factory=list)
    sector_exposures: list[OntologySectorExposurePayload] = Field(default_factory=list)
    readiness_status: Literal["ready", "partial", "blocked"]
    readiness_reasons: list[str] = Field(default_factory=list)

    @field_validator("edge_count", "source_count", mode="before")
    @classmethod
    def member_feature_counts_are_plain_ints(cls, value: object, info: object) -> object:
        return _reject_boolean_int(value, getattr(info, "field_name", "count"))

    @field_validator("edge_type_counts", "source_type_counts", mode="before")
    @classmethod
    def member_feature_count_map_values_are_plain_ints(
        cls,
        value: object,
        info: object,
    ) -> object:
        return _reject_boolean_count_map_values(
            value,
            field_name=getattr(info, "field_name", "count_map"),
        )

    @field_validator("member_bioguide_id", mode="before")
    @classmethod
    def strip_member_bioguide_id(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @model_validator(mode="after")
    def feature_counts_are_consistent(self) -> Self:
        if not self.member_bioguide_id:
            raise ValueError("member_bioguide_id must not be blank")
        _require_nonnegative_count_map(self.edge_type_counts, field_name="edge_type_counts")
        _require_nonnegative_count_map(
            self.source_type_counts,
            field_name="source_type_counts",
        )
        committee_keys = _node_ref_keys(self.committees)
        if committee_keys != sorted(set(committee_keys)):
            raise ValueError("committees must be sorted and unique")
        sector_ids = [sector.sector_id for sector in self.sector_exposures]
        if sector_ids != sorted(set(sector_ids)):
            raise ValueError("sector_exposures must be sorted and unique")
        if self.edge_count != sum(self.edge_type_counts.values()):
            raise ValueError("edge_count must match edge_type_counts total")
        if self.source_count != sum(self.source_type_counts.values()):
            raise ValueError("source_count must match source_type_counts total")
        if self.readiness_status == "ready" and self.readiness_reasons:
            raise ValueError("ready member features must not carry readiness reasons")
        if self.readiness_status != "ready" and not self.readiness_reasons:
            raise ValueError("non-ready member features require readiness reasons")
        return self


class OntologyMemberGraphPayload(BaseModel):
    """Frontend-fast ontology slice for one member in a published snapshot."""

    snapshot_id: str
    member_bioguide_id: str
    edge_count: int
    edges: list[OntologyEdgePayload] = Field(default_factory=list)

    @field_validator("edge_count", mode="before")
    @classmethod
    def edge_count_is_plain_int(cls, value: object, info: object) -> object:
        return _reject_boolean_int(value, getattr(info, "field_name", "count"))

    @field_validator("member_bioguide_id", mode="before")
    @classmethod
    def strip_member_bioguide_id(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @model_validator(mode="after")
    def member_slice_matches_edges(self) -> Self:
        if not self.member_bioguide_id:
            raise ValueError("member_bioguide_id must not be blank")
        if self.edge_count != len(self.edges):
            raise ValueError("edge_count must match edges length")
        edge_ids = [edge.edge_id for edge in self.edges]
        if len(set(edge_ids)) != len(edge_ids):
            raise ValueError("member ontology graph edge IDs must be unique")
        for edge in self.edges:
            member_ids = {
                node.node_id for node in (edge.subject, edge.object) if node.node_type == "member"
            }
            if member_ids and self.member_bioguide_id not in member_ids:
                raise ValueError("all member graph edges must match member_bioguide_id")
        if self.edges and not any(
            self.member_bioguide_id
            in {node.node_id for node in (edge.subject, edge.object) if node.node_type == "member"}
            for edge in self.edges
        ):
            raise ValueError("member_bioguide_id must match at least one member endpoint")
        return self
