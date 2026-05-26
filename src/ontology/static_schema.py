from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from src.export.contracts import ExportContractModel
from src.ontology.contracts import OntologyEdgePayload
from src.ontology.versions import (
    ONTOLOGY_FRONTEND_INDEX_VERSION,
    ONTOLOGY_STATIC_SCHEMA_VERSION,
)

OntologyCardinality = Literal["one_to_one", "one_to_many", "many_to_one", "many_to_many"]


class OntologyPropertyDefinitionPayload(ExportContractModel):
    """Static property metadata for one ontology object type."""

    name: str
    value_type: str
    required: bool = False
    description: str


class OntologyObjectTypeDefinitionPayload(ExportContractModel):
    """Palantir-style object type definition for public OpenPact artifacts."""

    object_type: str
    label: str
    primary_key: str
    source_backed: bool
    properties: list[OntologyPropertyDefinitionPayload] = Field(default_factory=list)
    description: str
    frontend_path_template: str | None = None

    @model_validator(mode="after")
    def properties_are_sorted_and_unique(self) -> Self:
        property_names = [prop.name for prop in self.properties]
        if property_names != sorted(property_names):
            raise ValueError("object type properties must be sorted by name")
        if len(set(property_names)) != len(property_names):
            raise ValueError("object type properties must be unique")
        return self


class OntologyLinkTypeDefinitionPayload(ExportContractModel):
    """Relationship definition between two ontology object types."""

    link_type: str
    subject_type: str
    object_type: str
    cardinality: OntologyCardinality
    source_required: bool
    availability_date_attributes: list[str] = Field(default_factory=list)
    description: str

    @model_validator(mode="after")
    def availability_attributes_are_sorted_unique(self) -> Self:
        if self.availability_date_attributes != sorted(set(self.availability_date_attributes)):
            raise ValueError("availability_date_attributes must be sorted and unique")
        return self


class OntologyActionTypeDefinitionPayload(ExportContractModel):
    """Typed action definition for prediction, simulation, and audit workflows."""

    action_type: str
    label: str
    input_object_types: list[str] = Field(default_factory=list)
    output_object_types: list[str] = Field(default_factory=list)
    source_required: bool
    description: str

    @model_validator(mode="after")
    def object_lists_are_sorted_unique(self) -> Self:
        if self.input_object_types != sorted(set(self.input_object_types)):
            raise ValueError("input_object_types must be sorted and unique")
        if self.output_object_types != sorted(set(self.output_object_types)):
            raise ValueError("output_object_types must be sorted and unique")
        return self


class OntologyStaticSchemaPayload(ExportContractModel):
    """Deterministic object/link/action catalog for frontend and LLM tooling."""

    schema_version: str
    object_types: list[OntologyObjectTypeDefinitionPayload] = Field(default_factory=list)
    link_types: list[OntologyLinkTypeDefinitionPayload] = Field(default_factory=list)
    action_types: list[OntologyActionTypeDefinitionPayload] = Field(default_factory=list)

    @model_validator(mode="after")
    def schema_is_sorted_unique_and_closed(self) -> Self:
        object_type_ids = [item.object_type for item in self.object_types]
        link_type_ids = [item.link_type for item in self.link_types]
        action_type_ids = [item.action_type for item in self.action_types]
        if object_type_ids != sorted(object_type_ids) or len(set(object_type_ids)) != len(
            object_type_ids
        ):
            raise ValueError("object_types must be sorted and unique")
        if link_type_ids != sorted(link_type_ids) or len(set(link_type_ids)) != len(link_type_ids):
            raise ValueError("link_types must be sorted and unique")
        if action_type_ids != sorted(action_type_ids) or len(set(action_type_ids)) != len(
            action_type_ids
        ):
            raise ValueError("action_types must be sorted and unique")
        known_object_types = set(object_type_ids)
        for link_type in self.link_types:
            if link_type.subject_type not in known_object_types:
                raise ValueError("link type references unknown object type")
            if link_type.object_type not in known_object_types:
                raise ValueError("link type references unknown object type")
        for action_type in self.action_types:
            for object_type in [*action_type.input_object_types, *action_type.output_object_types]:
                if object_type not in known_object_types:
                    raise ValueError("action type references unknown object type")
        return self


class OntologyFrontendLookupPayload(ExportContractModel):
    """Small lookup row for one object in a static ontology index."""

    object_id: str
    edge_ids: list[str] = Field(default_factory=list)
    linked_object_ids_by_type: dict[str, list[str]] = Field(default_factory=dict)
    source_keys: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def lookup_lists_are_sorted_unique(self) -> Self:
        if self.edge_ids != sorted(set(self.edge_ids)):
            raise ValueError("edge_ids must be sorted and unique")
        if self.source_keys != sorted(set(self.source_keys)):
            raise ValueError("source_keys must be sorted and unique")
        for object_ids in self.linked_object_ids_by_type.values():
            if object_ids != sorted(set(object_ids)):
                raise ValueError("linked object IDs must be sorted and unique")
        return self


class OntologyFrontendSourceLookupPayload(ExportContractModel):
    """Reverse lookup from one source anchor to the ontology edges it backs."""

    source_key: str
    source_type: str
    source_id: str
    edge_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def edge_ids_are_sorted_unique(self) -> Self:
        if self.edge_ids != sorted(set(self.edge_ids)):
            raise ValueError("edge_ids must be sorted and unique")
        return self


class OntologyFrontendIndexPayload(ExportContractModel):
    """Website-fast lookup tables derived from source-backed ontology edges."""

    schema_version: str = ONTOLOGY_FRONTEND_INDEX_VERSION
    snapshot_id: str
    edge_count: int = Field(ge=0)
    object_type_counts: dict[str, int] = Field(default_factory=dict)
    link_type_counts: dict[str, int] = Field(default_factory=dict)
    source_type_counts: dict[str, int] = Field(default_factory=dict)
    member_ids: list[str] = Field(default_factory=list)
    committee_ids: list[str] = Field(default_factory=list)
    sector_ids: list[str] = Field(default_factory=list)
    issuer_ids: list[str] = Field(default_factory=list)
    source_keys: list[str] = Field(default_factory=list)
    by_member: dict[str, OntologyFrontendLookupPayload] = Field(default_factory=dict)
    by_committee: dict[str, OntologyFrontendLookupPayload] = Field(default_factory=dict)
    by_sector: dict[str, OntologyFrontendLookupPayload] = Field(default_factory=dict)
    by_issuer: dict[str, OntologyFrontendLookupPayload] = Field(default_factory=dict)
    by_link_type: dict[str, list[str]] = Field(default_factory=dict)
    by_source_key: dict[str, OntologyFrontendSourceLookupPayload] = Field(default_factory=dict)

    @model_validator(mode="after")
    def index_counts_are_consistent(self) -> Self:
        if self.schema_version != ONTOLOGY_FRONTEND_INDEX_VERSION:
            raise ValueError("schema_version must match frontend index contract")
        if self.edge_count != sum(self.link_type_counts.values()):
            raise ValueError("edge_count must match link_type_counts total")
        if self.member_ids != sorted(self.by_member):
            raise ValueError("member_ids must match by_member keys")
        if self.committee_ids != sorted(self.by_committee):
            raise ValueError("committee_ids must match by_committee keys")
        if self.sector_ids != sorted(self.by_sector):
            raise ValueError("sector_ids must match by_sector keys")
        if self.issuer_ids != sorted(self.by_issuer):
            raise ValueError("issuer_ids must match by_issuer keys")
        if self.source_keys != sorted(set(self.source_keys)):
            raise ValueError("source_keys must be sorted and unique")
        if set(self.source_keys) != set(self.by_source_key):
            raise ValueError("source_keys must match by_source_key keys")
        if set(self.link_type_counts) != set(self.by_link_type):
            raise ValueError("link_type_counts must match by_link_type keys")
        lookup_by_object_type = {
            "committee": self.by_committee,
            "issuer": self.by_issuer,
            "member": self.by_member,
            "sector": self.by_sector,
        }
        if self.object_type_counts != {
            object_type: len(lookup)
            for object_type, lookup in lookup_by_object_type.items()
            if lookup
        }:
            raise ValueError("object_type_counts must match lookup table sizes")
        link_edge_ids: list[str] = []
        for link_type, edge_ids in self.by_link_type.items():
            if edge_ids != sorted(set(edge_ids)):
                raise ValueError("by_link_type edge IDs must be sorted and unique")
            if self.link_type_counts[link_type] != len(edge_ids):
                raise ValueError("link_type_counts must match by_link_type edge IDs")
            link_edge_ids.extend(edge_ids)
        if len(set(link_edge_ids)) != len(link_edge_ids) or len(link_edge_ids) != self.edge_count:
            raise ValueError("by_link_type edge IDs must cover each edge exactly once")
        known_edge_ids = set(link_edge_ids)
        known_source_keys = set(self.source_keys)
        for lookup in lookup_by_object_type.values():
            for object_id, lookup_row in lookup.items():
                if lookup_row.object_id != object_id:
                    raise ValueError("lookup object_id must match lookup key")
                if not set(lookup_row.edge_ids).issubset(known_edge_ids):
                    raise ValueError("lookup edge_ids must reference known edge IDs")
                if not set(lookup_row.source_keys).issubset(known_source_keys):
                    raise ValueError("lookup source_keys must reference known source keys")
        for source_key, source_row in self.by_source_key.items():
            if source_row.source_key != source_key:
                raise ValueError("source lookup source_key must match lookup key")
            if _source_key(source_row.source_type, source_row.source_id) != source_key:
                raise ValueError("source lookup key must match source_type and source_id")
            if not set(source_row.edge_ids).issubset(known_edge_ids):
                raise ValueError("source lookup edge_ids must reference known edge IDs")
        return self


def build_ontology_static_schema(
    *,
    schema_version: str = ONTOLOGY_STATIC_SCHEMA_VERSION,
) -> OntologyStaticSchemaPayload:
    """Build the deterministic OpenPact ontology object/link/action contract."""
    return OntologyStaticSchemaPayload(
        schema_version=schema_version,
        object_types=sorted(_object_types(), key=lambda item: item.object_type),
        link_types=sorted(_link_types(), key=lambda item: item.link_type),
        action_types=sorted(_action_types(), key=lambda item: item.action_type),
    )


def build_ontology_frontend_index(
    *,
    snapshot_id: str,
    edges: list[OntologyEdgePayload],
) -> OntologyFrontendIndexPayload:
    """Build hash-table style lookup sets so clients avoid request-time graph traversal."""
    object_counts: dict[str, int] = {}
    link_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    seen_objects: set[tuple[str, str]] = set()
    source_keys: set[str] = set()
    source_lookup: dict[str, dict[str, str | set[str]]] = {}
    edge_ids_by_link_type: dict[str, set[str]] = {}
    lookup_sets: dict[str, dict[str, _LookupSets]] = {
        "member": {},
        "committee": {},
        "sector": {},
        "issuer": {},
    }

    for edge in edges:
        link_counts[edge.edge_type] = link_counts.get(edge.edge_type, 0) + 1
        edge_ids_by_link_type.setdefault(edge.edge_type, set()).add(edge.edge_id)
        edge_source_keys: set[str] = set()
        for anchor in edge.source_anchors:
            source_counts[anchor.source_type] = source_counts.get(anchor.source_type, 0) + 1
            key = _source_key(anchor.source_type, anchor.source_id)
            source_keys.add(key)
            edge_source_keys.add(key)
            source_row = source_lookup.setdefault(
                key,
                {
                    "source_type": anchor.source_type,
                    "source_id": anchor.source_id,
                    "edge_ids": set(),
                },
            )
            edge_ids = source_row["edge_ids"]
            if isinstance(edge_ids, set):
                edge_ids.add(edge.edge_id)
        subject = edge.subject
        object_ = edge.object
        for node, linked_node in ((subject, object_), (object_, subject)):
            object_key = (node.node_type, node.node_id)
            if object_key not in seen_objects:
                seen_objects.add(object_key)
                object_counts[node.node_type] = object_counts.get(node.node_type, 0) + 1
            type_lookup = lookup_sets.get(node.node_type)
            if type_lookup is None:
                continue
            lookup = type_lookup.setdefault(node.node_id, _LookupSets(object_id=node.node_id))
            lookup.edge_ids.add(edge.edge_id)
            lookup.source_keys.update(edge_source_keys)
            lookup.linked_object_ids_by_type.setdefault(linked_node.node_type, set()).add(
                linked_node.node_id
            )

    return OntologyFrontendIndexPayload(
        schema_version=ONTOLOGY_FRONTEND_INDEX_VERSION,
        snapshot_id=snapshot_id,
        edge_count=len(edges),
        object_type_counts=dict(sorted(object_counts.items())),
        link_type_counts=dict(sorted(link_counts.items())),
        source_type_counts=dict(sorted(source_counts.items())),
        member_ids=sorted(lookup_sets["member"]),
        committee_ids=sorted(lookup_sets["committee"]),
        sector_ids=sorted(lookup_sets["sector"]),
        issuer_ids=sorted(lookup_sets["issuer"]),
        source_keys=sorted(source_keys),
        by_member=_materialize_lookup(lookup_sets["member"]),
        by_committee=_materialize_lookup(lookup_sets["committee"]),
        by_sector=_materialize_lookup(lookup_sets["sector"]),
        by_issuer=_materialize_lookup(lookup_sets["issuer"]),
        by_link_type={
            link_type: sorted(edge_ids)
            for link_type, edge_ids in sorted(edge_ids_by_link_type.items())
        },
        by_source_key=_materialize_source_lookup(source_lookup),
    )


class _LookupSets:
    def __init__(self, *, object_id: str) -> None:
        self.object_id = object_id
        self.edge_ids: set[str] = set()
        self.linked_object_ids_by_type: dict[str, set[str]] = {}
        self.source_keys: set[str] = set()


def _materialize_lookup(
    rows: dict[str, _LookupSets],
) -> dict[str, OntologyFrontendLookupPayload]:
    return {
        object_id: OntologyFrontendLookupPayload(
            object_id=lookup.object_id,
            edge_ids=sorted(lookup.edge_ids),
            linked_object_ids_by_type={
                object_type: sorted(object_ids)
                for object_type, object_ids in sorted(lookup.linked_object_ids_by_type.items())
            },
            source_keys=sorted(lookup.source_keys),
        )
        for object_id, lookup in sorted(rows.items())
    }


def _materialize_source_lookup(
    rows: dict[str, dict[str, str | set[str]]],
) -> dict[str, OntologyFrontendSourceLookupPayload]:
    materialized: dict[str, OntologyFrontendSourceLookupPayload] = {}
    for source_key, row in sorted(rows.items()):
        edge_ids = row["edge_ids"]
        materialized[source_key] = OntologyFrontendSourceLookupPayload(
            source_key=source_key,
            source_type=str(row["source_type"]),
            source_id=str(row["source_id"]),
            edge_ids=sorted(edge_ids) if isinstance(edge_ids, set) else [],
        )
    return materialized


def _source_key(source_type: str, source_id: str) -> str:
    return f"{source_type}:{source_id}"


def _property(
    name: str, value_type: str, description: str, *, required: bool = False
) -> OntologyPropertyDefinitionPayload:
    return OntologyPropertyDefinitionPayload(
        name=name,
        value_type=value_type,
        required=required,
        description=description,
    )


def _object(
    object_type: str,
    label: str,
    primary_key: str,
    *,
    source_backed: bool,
    properties: list[OntologyPropertyDefinitionPayload],
    description: str,
    frontend_path_template: str | None = None,
) -> OntologyObjectTypeDefinitionPayload:
    return OntologyObjectTypeDefinitionPayload(
        object_type=object_type,
        label=label,
        primary_key=primary_key,
        source_backed=source_backed,
        properties=sorted(properties, key=lambda item: item.name),
        description=description,
        frontend_path_template=frontend_path_template,
    )


def _link(
    link_type: str,
    subject_type: str,
    object_type: str,
    *,
    cardinality: OntologyCardinality = "many_to_many",
    source_required: bool = True,
    availability_date_attributes: list[str] | None = None,
    description: str,
) -> OntologyLinkTypeDefinitionPayload:
    return OntologyLinkTypeDefinitionPayload(
        link_type=link_type,
        subject_type=subject_type,
        object_type=object_type,
        cardinality=cardinality,
        source_required=source_required,
        availability_date_attributes=sorted(availability_date_attributes or []),
        description=description,
    )


def _action(
    action_type: str,
    label: str,
    *,
    input_object_types: list[str],
    output_object_types: list[str],
    source_required: bool,
    description: str,
) -> OntologyActionTypeDefinitionPayload:
    return OntologyActionTypeDefinitionPayload(
        action_type=action_type,
        label=label,
        input_object_types=sorted(input_object_types),
        output_object_types=sorted(output_object_types),
        source_required=source_required,
        description=description,
    )


def _object_types() -> list[OntologyObjectTypeDefinitionPayload]:
    return [
        _object(
            "bill",
            "Bill",
            "bill_key",
            source_backed=True,
            properties=[
                _property(
                    "bill_key", "string", "Canonical congress/type/number key.", required=True
                ),
                _property("congress", "integer", "Congress number.", required=True),
                _property("introduced_date", "date", "Official introduction date."),
                _property("title", "string", "Official bill title."),
            ],
            description="A source-backed legislative proposal.",
            frontend_path_template="bills/{bill_key}.json",
        ),
        _object(
            "committee",
            "Committee",
            "committee_id",
            source_backed=True,
            properties=[
                _property("chamber", "string", "House, Senate, or joint chamber."),
                _property("committee_id", "string", "Canonical committee code.", required=True),
                _property("name", "string", "Committee display name."),
            ],
            description="A congressional committee or subcommittee.",
            frontend_path_template="committees/{committee_id}.json",
        ),
        _object(
            "disclosure_transaction",
            "Disclosure Transaction",
            "transaction_id",
            source_backed=True,
            properties=[
                _property("amount_max", "decimal", "Upper bound of disclosed transaction value."),
                _property("amount_min", "decimal", "Lower bound of disclosed transaction value."),
                _property("issuer_name", "string", "Issuer name as disclosed."),
                _property("transaction_date", "date", "Disclosed transaction date."),
                _property("transaction_id", "string", "Canonical transaction ID.", required=True),
            ],
            description="A stock, asset, or other disclosed member transaction.",
        ),
        _object(
            "donor",
            "Donor",
            "donor_id",
            source_backed=True,
            properties=[
                _property("donor_id", "string", "Canonical donor ID.", required=True),
                _property("donor_name", "string", "Donor display name."),
                _property("donor_type", "string", "Individual, committee, PAC, or organization."),
            ],
            description="A source-backed political contribution donor.",
        ),
        _object(
            "issuer",
            "Issuer",
            "issuer_id",
            source_backed=True,
            properties=[
                _property("issuer_id", "string", "Canonical issuer ID.", required=True),
                _property("name", "string", "Issuer display name."),
                _property("ticker", "string", "Ticker symbol when available."),
            ],
            description="A disclosed company, fund, or asset issuer.",
        ),
        _object(
            "member",
            "Member",
            "bioguide_id",
            source_backed=True,
            properties=[
                _property("bioguide_id", "string", "Official Bioguide identifier.", required=True),
                _property("chamber", "string", "Current chamber."),
                _property("party", "string", "Current party."),
                _property("slug", "string", "Stable website slug."),
                _property("state", "string", "State abbreviation."),
            ],
            description="A current or historical member of Congress.",
            frontend_path_template="members/{bioguide_id}.json",
        ),
        _object(
            "pac",
            "PAC",
            "fec_committee_id",
            source_backed=True,
            properties=[
                _property("fec_committee_id", "string", "FEC committee ID.", required=True),
                _property("name", "string", "PAC or committee name."),
                _property("party", "string", "Associated party when available."),
            ],
            description="A political action committee or FEC committee.",
        ),
        _object(
            "sector",
            "Sector",
            "sector_id",
            source_backed=False,
            properties=[
                _property("label", "string", "Human-readable sector label."),
                _property("sector_id", "string", "Canonical sector ID.", required=True),
            ],
            description="A normalized policy or industry sector used for prediction features.",
            frontend_path_template="sectors/{sector_id}.json",
        ),
        _object(
            "source_artifact",
            "Source Artifact",
            "source_key",
            source_backed=True,
            properties=[
                _property("label", "string", "Display label for the source."),
                _property(
                    "source_key", "string", "Stable source_type/source_id key.", required=True
                ),
                _property("source_type", "string", "Source type namespace.", required=True),
                _property("url", "url", "Public source URL when available."),
            ],
            description="The public or canonical record backing an ontology claim.",
        ),
        _object(
            "statement",
            "Public Statement",
            "statement_id",
            source_backed=True,
            properties=[
                _property("excerpt", "string", "Extracted or summarized statement excerpt."),
                _property("statement_date", "date", "Publication date."),
                _property("statement_id", "string", "Canonical statement ID.", required=True),
                _property("title", "string", "Statement title."),
            ],
            description="A source-backed public statement by a member or office.",
        ),
        _object(
            "vote_event",
            "Vote Event",
            "vote_event_id",
            source_backed=True,
            properties=[
                _property("chamber", "string", "Vote chamber.", required=True),
                _property("question", "string", "Official vote question."),
                _property("vote_date", "date", "Vote date.", required=True),
                _property("vote_event_id", "integer", "Canonical vote event ID.", required=True),
            ],
            description="A source-backed roll-call vote event.",
            frontend_path_template="votes/{vote_event_id}.json",
        ),
        _object(
            "vote_prediction",
            "Vote Prediction",
            "prediction_id",
            source_backed=True,
            properties=[
                _property("cutoff_date", "date", "Knowledge cutoff used for the prediction."),
                _property(
                    "prediction_id", "string", "Stable prediction artifact ID.", required=True
                ),
                _property("probability_yes", "float", "Predicted yes probability."),
            ],
            description="A cutoff-safe prediction with feature/source provenance.",
        ),
    ]


def _link_types() -> list[OntologyLinkTypeDefinitionPayload]:
    return [
        _link(
            "bill_cosponsor",
            "member",
            "bill",
            availability_date_attributes=["cosponsored_date"],
            description="Member cosponsored a bill.",
        ),
        _link(
            "bill_sponsor",
            "member",
            "bill",
            cardinality="one_to_many",
            availability_date_attributes=["sponsored_date"],
            description="Member sponsored a bill.",
        ),
        _link(
            "bill_vote_event",
            "bill",
            "vote_event",
            cardinality="one_to_many",
            availability_date_attributes=["vote_date"],
            description="Bill was considered in a roll-call vote.",
        ),
        _link(
            "committee_sector_jurisdiction",
            "committee",
            "sector",
            availability_date_attributes=["start_date"],
            description="Committee jurisdiction maps to a sector.",
        ),
        _link(
            "donor_sector_alignment",
            "donor",
            "sector",
            availability_date_attributes=["contribution_date"],
            description="Donor or PAC contribution activity maps to a sector.",
        ),
        _link(
            "issuer_sector_classification",
            "issuer",
            "sector",
            source_required=False,
            description="Issuer normalization maps an issuer to a sector.",
        ),
        _link(
            "member_committee_assignment",
            "member",
            "committee",
            availability_date_attributes=["start_date"],
            description="Member served on a committee.",
        ),
        _link(
            "member_disclosure_transaction",
            "member",
            "disclosure_transaction",
            availability_date_attributes=["transaction_date"],
            description="Member reported a financial disclosure transaction.",
        ),
        _link(
            "member_public_statement",
            "member",
            "statement",
            cardinality="one_to_many",
            availability_date_attributes=["statement_date"],
            description="Member made a source-backed public statement.",
        ),
        _link(
            "member_sector_contribution_exposure",
            "member",
            "sector",
            availability_date_attributes=["contribution_date"],
            description="Member has contribution exposure to a sector.",
        ),
        _link(
            "member_sector_holding_exposure",
            "member",
            "sector",
            availability_date_attributes=["filing_date"],
            description="Member has holding exposure to a sector.",
        ),
        _link(
            "member_sector_public_statement_alignment",
            "member",
            "sector",
            availability_date_attributes=["statement_date"],
            description="Member public statement aligns with a sector.",
        ),
        _link(
            "member_sector_statement_alignment",
            "member",
            "sector",
            availability_date_attributes=["statement_date"],
            description="Legacy statement-to-sector alignment edge.",
        ),
        _link(
            "member_sector_transaction_exposure",
            "member",
            "sector",
            availability_date_attributes=["transaction_date"],
            description="Member has transaction exposure to a sector.",
        ),
        _link(
            "member_vote_cast",
            "member",
            "vote_event",
            availability_date_attributes=["vote_date"],
            description="Member cast a vote in a roll-call vote event.",
        ),
        _link(
            "statement_sector_alignment",
            "statement",
            "sector",
            availability_date_attributes=["statement_date"],
            description="Public statement semantically aligns with a sector.",
        ),
        _link(
            "vote_prediction_target",
            "vote_prediction",
            "vote_event",
            availability_date_attributes=["cutoff_date"],
            description="Prediction targets a vote event.",
        ),
    ]


def _action_types() -> list[OntologyActionTypeDefinitionPayload]:
    return [
        _action(
            "audit_claim_sources",
            "Audit Claim Sources",
            input_object_types=["source_artifact"],
            output_object_types=["source_artifact"],
            source_required=True,
            description="Verify whether a claim is backed by dereferenceable official sources.",
        ),
        _action(
            "explain_member_integrity",
            "Explain Member Integrity",
            input_object_types=["member"],
            output_object_types=["source_artifact"],
            source_required=True,
            description="Explain a member's integrity score using source-backed evidence.",
        ),
        _action(
            "predict_member_vote",
            "Predict Member Vote",
            input_object_types=["bill", "member"],
            output_object_types=["vote_prediction"],
            source_required=True,
            description="Predict how a member will vote using cutoff-safe ontology features.",
        ),
        _action(
            "simulate_lobbying_pressure",
            "Simulate Lobbying Pressure",
            input_object_types=["bill", "member", "sector"],
            output_object_types=["vote_prediction"],
            source_required=True,
            description="Estimate marginal vote movement under a sector-specific influence scenario.",
        ),
    ]
