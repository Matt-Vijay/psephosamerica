from __future__ import annotations

import pytest

from src.export.contracts import SourceAnchor
from src.ontology.contracts import OntologyEdgePayload, OntologyNodeRef
from src.ontology.static_schema import (
    ONTOLOGY_FRONTEND_INDEX_VERSION,
    ONTOLOGY_STATIC_SCHEMA_VERSION,
    OntologyFrontendIndexPayload,
    build_ontology_frontend_index,
    build_ontology_static_schema,
)


def _committee_edge() -> OntologyEdgePayload:
    return OntologyEdgePayload(
        edge_id="ont-edge-schema-committee",
        edge_type="member_committee_assignment",
        subject=OntologyNodeRef(node_type="member", node_id="P000197", label="Nancy Pelosi"),
        object=OntologyNodeRef(node_type="committee", node_id="HSEC", label="Energy"),
        source_anchors=[
            SourceAnchor(
                source_type="committee_membership",
                source_id="cm-schema-1",
                url="https://api.congress.gov/v3/committee/house/HSEC?format=json",
                label="Committee membership",
            )
        ],
    )


def _transaction_edge() -> OntologyEdgePayload:
    return OntologyEdgePayload(
        edge_id="ont-edge-schema-transaction",
        edge_type="member_sector_transaction_exposure",
        subject=OntologyNodeRef(node_type="member", node_id="P000197", label="Nancy Pelosi"),
        object=OntologyNodeRef(node_type="sector", node_id="energy", label="Energy"),
        source_anchors=[
            SourceAnchor(
                source_type="financial_disclosure",
                source_id="fd-schema-1",
                url="https://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd-schema-1.pdf",
                label="Financial disclosure",
            )
        ],
        attributes={"transaction_date": "2025-02-10"},
    )


def test_build_ontology_static_schema_exposes_typed_object_link_and_action_catalog() -> None:
    schema = build_ontology_static_schema()

    assert schema.schema_version == ONTOLOGY_STATIC_SCHEMA_VERSION
    object_types = [item.object_type for item in schema.object_types]
    link_types = [item.link_type for item in schema.link_types]
    action_types = [item.action_type for item in schema.action_types]

    assert object_types == sorted(object_types)
    assert link_types == sorted(link_types)
    assert action_types == sorted(action_types)
    assert {
        "bill",
        "committee",
        "disclosure_transaction",
        "donor",
        "issuer",
        "member",
        "pac",
        "sector",
        "source_artifact",
        "statement",
        "vote_event",
        "vote_prediction",
    }.issubset(object_types)
    assert {
        "member_committee_assignment",
        "member_sector_transaction_exposure",
        "bill_vote_event",
        "member_vote_cast",
        "member_public_statement",
    }.issubset(link_types)
    assert {
        "audit_claim_sources",
        "explain_member_integrity",
        "predict_member_vote",
        "simulate_lobbying_pressure",
    }.issubset(action_types)

    member_type = next(item for item in schema.object_types if item.object_type == "member")
    assert member_type.primary_key == "bioguide_id"
    assert "slug" in {prop.name for prop in member_type.properties}

    transaction_link = next(
        item for item in schema.link_types if item.link_type == "member_sector_transaction_exposure"
    )
    assert transaction_link.subject_type == "member"
    assert transaction_link.object_type == "sector"
    assert transaction_link.source_required is True
    assert transaction_link.availability_date_attributes == ["transaction_date"]


def test_static_schema_rejects_link_or_action_references_to_unknown_objects() -> None:
    from src.ontology.static_schema import (
        OntologyActionTypeDefinitionPayload,
        OntologyLinkTypeDefinitionPayload,
        OntologyStaticSchemaPayload,
    )

    schema = build_ontology_static_schema()

    try:
        OntologyStaticSchemaPayload(
            schema_version=schema.schema_version,
            object_types=schema.object_types,
            link_types=sorted(
                [
                    *schema.link_types,
                    OntologyLinkTypeDefinitionPayload(
                        link_type="bad_link",
                        subject_type="member",
                        object_type="unknown_object",
                        cardinality="many_to_many",
                        source_required=True,
                        availability_date_attributes=[],
                        description="bad reference",
                    ),
                ],
                key=lambda item: item.link_type,
            ),
            action_types=schema.action_types,
        )
    except ValueError as exc:
        assert "link type references unknown object type" in str(exc)
    else:
        raise AssertionError("schema with unknown link object should fail")

    try:
        OntologyStaticSchemaPayload(
            schema_version=schema.schema_version,
            object_types=schema.object_types,
            link_types=schema.link_types,
            action_types=sorted(
                [
                    *schema.action_types,
                    OntologyActionTypeDefinitionPayload(
                        action_type="bad_action",
                        label="Bad Action",
                        input_object_types=["member"],
                        output_object_types=["missing_output"],
                        source_required=True,
                        description="bad action reference",
                    ),
                ],
                key=lambda item: item.action_type,
            ),
        )
    except ValueError as exc:
        assert "action type references unknown object type" in str(exc)
    else:
        raise AssertionError("schema with unknown action output should fail")


def test_build_ontology_frontend_index_materializes_fast_sorted_lookup_sets() -> None:
    result = build_ontology_frontend_index(
        snapshot_id="2026-05-05",
        edges=[_transaction_edge(), _committee_edge()],
    )

    assert result.schema_version == ONTOLOGY_FRONTEND_INDEX_VERSION
    assert result.snapshot_id == "2026-05-05"
    assert result.edge_count == 2
    assert result.object_type_counts == {"committee": 1, "member": 1, "sector": 1}
    assert result.link_type_counts == {
        "member_committee_assignment": 1,
        "member_sector_transaction_exposure": 1,
    }
    assert result.member_ids == ["P000197"]
    assert result.committee_ids == ["HSEC"]
    assert result.sector_ids == ["energy"]
    assert result.source_keys == [
        "committee_membership:cm-schema-1",
        "financial_disclosure:fd-schema-1",
    ]
    assert result.by_member["P000197"].edge_ids == [
        "ont-edge-schema-committee",
        "ont-edge-schema-transaction",
    ]
    assert result.by_member["P000197"].linked_object_ids_by_type == {
        "committee": ["HSEC"],
        "sector": ["energy"],
    }
    assert result.by_sector["energy"].edge_ids == ["ont-edge-schema-transaction"]
    assert result.by_link_type == {
        "member_committee_assignment": ["ont-edge-schema-committee"],
        "member_sector_transaction_exposure": ["ont-edge-schema-transaction"],
    }
    assert result.by_source_key["financial_disclosure:fd-schema-1"].edge_ids == [
        "ont-edge-schema-transaction"
    ]
    assert result.by_source_key["financial_disclosure:fd-schema-1"].source_type == (
        "financial_disclosure"
    )


def test_frontend_index_defaults_missing_schema_version_for_legacy_artifacts() -> None:
    index = build_ontology_frontend_index(snapshot_id="2026-05-05", edges=[])
    payload = index.model_dump(mode="json")
    payload.pop("schema_version")

    loaded = OntologyFrontendIndexPayload.model_validate(payload)

    assert loaded.schema_version == ONTOLOGY_FRONTEND_INDEX_VERSION


def test_frontend_index_rejects_unknown_schema_version() -> None:
    payload = build_ontology_frontend_index(snapshot_id="2026-05-05", edges=[]).model_dump(
        mode="json"
    )
    payload["schema_version"] = "unknown-version"

    try:
        OntologyFrontendIndexPayload.model_validate(payload)
    except ValueError as exc:
        assert "schema_version must match frontend index contract" in str(exc)
    else:
        raise AssertionError("frontend index with unknown schema_version should fail")


def test_frontend_index_rejects_lookup_key_payload_mismatch() -> None:
    payload = build_ontology_frontend_index(
        snapshot_id="2026-05-05",
        edges=[_committee_edge()],
    ).model_dump(mode="json")
    payload["by_member"]["P000197"]["object_id"] = "S000148"

    try:
        OntologyFrontendIndexPayload.model_validate(payload)
    except ValueError as exc:
        assert "lookup object_id must match lookup key" in str(exc)
    else:
        raise AssertionError("frontend index with mismatched lookup object_id should fail")


def test_frontend_index_rejects_source_key_payload_mismatch() -> None:
    payload = build_ontology_frontend_index(
        snapshot_id="2026-05-05",
        edges=[_committee_edge()],
    ).model_dump(mode="json")
    source_key = payload["source_keys"][0]
    payload["by_source_key"][source_key]["source_key"] = "financial_disclosure:fd-schema-1"

    try:
        OntologyFrontendIndexPayload.model_validate(payload)
    except ValueError as exc:
        assert "source lookup source_key must match lookup key" in str(exc)
    else:
        raise AssertionError("frontend index with mismatched source_key should fail")


def test_frontend_index_rejects_duplicate_edge_ids_across_link_types() -> None:
    payload = build_ontology_frontend_index(
        snapshot_id="2026-05-05",
        edges=[_committee_edge(), _transaction_edge()],
    ).model_dump(mode="json")
    payload["by_link_type"]["member_sector_transaction_exposure"] = ["ont-edge-schema-committee"]

    try:
        OntologyFrontendIndexPayload.model_validate(payload)
    except ValueError as exc:
        assert "by_link_type edge IDs must cover each edge exactly once" in str(exc)
    else:
        raise AssertionError("frontend index with duplicated link edge IDs should fail")


def test_frontend_index_rejects_lookup_edge_ids_not_in_link_tables() -> None:
    payload = build_ontology_frontend_index(
        snapshot_id="2026-05-05",
        edges=[_committee_edge()],
    ).model_dump(mode="json")
    payload["by_member"]["P000197"]["edge_ids"] = ["ont-edge-schema-missing"]

    try:
        OntologyFrontendIndexPayload.model_validate(payload)
    except ValueError as exc:
        assert "lookup edge_ids must reference known edge IDs" in str(exc)
    else:
        raise AssertionError("frontend index with unknown lookup edge ID should fail")


def test_frontend_index_rejects_lookup_source_keys_not_in_source_lookup() -> None:
    payload = build_ontology_frontend_index(
        snapshot_id="2026-05-05",
        edges=[_committee_edge()],
    ).model_dump(mode="json")
    payload["by_member"]["P000197"]["source_keys"] = ["financial_disclosure:fd-schema-1"]

    try:
        OntologyFrontendIndexPayload.model_validate(payload)
    except ValueError as exc:
        assert "lookup source_keys must reference known source keys" in str(exc)
    else:
        raise AssertionError("frontend index with unknown lookup source key should fail")


def test_frontend_index_rejects_stale_object_type_counts() -> None:
    payload = build_ontology_frontend_index(
        snapshot_id="2026-05-05",
        edges=[_committee_edge()],
    ).model_dump(mode="json")
    payload["object_type_counts"]["member"] = 2

    try:
        OntologyFrontendIndexPayload.model_validate(payload)
    except ValueError as exc:
        assert "object_type_counts must match lookup table sizes" in str(exc)
    else:
        raise AssertionError("frontend index with stale object_type_counts should fail")


def test_frontend_index_rejects_stale_link_type_counts() -> None:
    payload = build_ontology_frontend_index(
        snapshot_id="2026-05-05",
        edges=[_committee_edge()],
    ).model_dump(mode="json")
    payload["link_type_counts"]["member_committee_assignment"] = 2
    payload["edge_count"] = 2

    try:
        OntologyFrontendIndexPayload.model_validate(payload)
    except ValueError as exc:
        assert "link_type_counts must match by_link_type edge IDs" in str(exc)
    else:
        raise AssertionError("frontend index with stale link_type_counts should fail")


def test_frontend_index_rejects_boolean_edge_count() -> None:
    payload = build_ontology_frontend_index(
        snapshot_id="2026-05-05",
        edges=[_committee_edge()],
    ).model_dump(mode="json")
    payload["edge_count"] = True

    try:
        OntologyFrontendIndexPayload.model_validate(payload)
    except ValueError as exc:
        assert "edge_count must be an integer" in str(exc)
    else:
        raise AssertionError("frontend index with boolean edge_count should fail")


def test_frontend_index_rejects_boolean_count_map_values() -> None:
    payload = build_ontology_frontend_index(
        snapshot_id="2026-05-05",
        edges=[_committee_edge()],
    ).model_dump(mode="json")
    payload["link_type_counts"]["member_committee_assignment"] = True

    try:
        OntologyFrontendIndexPayload.model_validate(payload)
    except ValueError as exc:
        assert "link_type_counts values must be integers" in str(exc)
    else:
        raise AssertionError("frontend index with boolean link_type_counts should fail")


def _valid_frontend_index_payload() -> dict:
    return build_ontology_frontend_index(
        snapshot_id="2026-05-05",
        edges=[_committee_edge()],
    ).model_dump(mode="json")


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda p: p.update(edge_count=99), "edge_count must match link_type_counts total"),
        (lambda p: p.update(member_ids=["bogus"]), "member_ids must match by_member keys"),
        (lambda p: p.update(committee_ids=["bogus"]), "committee_ids must match by_committee keys"),
        (lambda p: p.update(sector_ids=["bogus"]), "sector_ids must match by_sector keys"),
        (lambda p: p.update(issuer_ids=["bogus"]), "issuer_ids must match by_issuer keys"),
        (lambda p: p.update(source_keys=["b", "a"]), "source_keys must be sorted and unique"),
        (
            lambda p: p.update(source_keys=["unmatched-key"]),
            "source_keys must match by_source_key keys",
        ),
        (
            lambda p: p["link_type_counts"].update({"bogus_link": 0}),
            "link_type_counts must match by_link_type keys",
        ),
    ],
)
def test_frontend_index_match_validations_reject_inconsistent_payloads(mutate, message) -> None:
    payload = _valid_frontend_index_payload()
    mutate(payload)
    with pytest.raises(ValueError, match=message):
        OntologyFrontendIndexPayload.model_validate(payload)
