from __future__ import annotations

from datetime import date

import pytest

from src.export.contracts import MemberProfilePayload, SourceAnchor
from src.ontology.contracts import (
    OntologyMemberFeaturesPayload,
    OntologyNodeRef,
    OntologySectorExposurePayload,
)
from src.prediction.readiness import (
    build_prediction_bootstrap,
    build_prediction_committee_contexts,
    build_prediction_committee_readiness,
    build_prediction_member_context,
    build_prediction_readiness,
    build_prediction_readiness_index,
    build_prediction_sector_contexts,
    build_prediction_sector_readiness,
    build_prediction_source_contexts,
    build_prediction_source_index,
    build_prediction_topology,
)
from src.prediction.contracts import (
    PredictionCommitteeReadinessRowPayload,
    PredictionCommitteeReadinessPayload,
    PredictionCommitteeContextPayload,
    PredictionContextArtifactRefsPayload,
    PredictionJurisdictionCapabilityPayload,
    PredictionMemberContextPayload,
    PredictionMemberReadinessPayload,
    PredictionReadinessCoveragePayload,
    PredictionReadinessIndexPayload,
    PredictionReadinessPayload,
    PredictionReadinessReasonPayload,
    PredictionSectorContextPayload,
    PredictionSectorReadinessRowPayload,
    PredictionSectorReadinessPayload,
    PredictionSegmentReadinessPayload,
    PredictionSourceContextPayload,
    PredictionSourceIndexPayload,
    PredictionSourceIndexRowPayload,
    PredictionTopologyPayload,
    prediction_source_context_path,
    prediction_source_key,
)

_SNAPSHOT_DATE = date(2026, 4, 25)


def test_prediction_jurisdiction_capability_rejects_unscoped_legislative_sessions() -> None:
    with pytest.raises(ValueError, match="legislative_session_ids must be body scoped"):
        PredictionJurisdictionCapabilityPayload(
            jurisdiction_id="state_ca",
            implementation_status="portable_contract",
            legislative_body_ids=["ca_assembly"],
            legislative_session_ids=["2025_regular"],
            required_source_roles=["bills", "members", "votes"],
        )


def test_prediction_jurisdiction_capability_rejects_globally_scoped_body_ids() -> None:
    with pytest.raises(ValueError, match="legislative_body_ids must be local body ids"):
        PredictionJurisdictionCapabilityPayload(
            jurisdiction_id="state_ca",
            implementation_status="portable_contract",
            legislative_body_ids=["state_ca:ca_assembly"],
            legislative_session_ids=[],
            required_source_roles=["bills", "members", "votes"],
        )


def test_prediction_jurisdiction_capability_rejects_unknown_session_body() -> None:
    with pytest.raises(ValueError, match="legislative_session_ids must reference body ids"):
        PredictionJurisdictionCapabilityPayload(
            jurisdiction_id="state_ca",
            implementation_status="portable_contract",
            legislative_body_ids=["ca_assembly"],
            legislative_session_ids=["ca_senate:2025_regular"],
            required_source_roles=["bills", "members", "votes"],
        )


def _profile(
    bioguide_id: str = "P000197",
    slug: str = "nancy-pelosi",
) -> MemberProfilePayload:
    return MemberProfilePayload(
        bioguide_id=bioguide_id,
        name="Nancy Pelosi",
        slug=slug,
        state="CA",
        chamber="house",
        party="Democrat",
        scores=[],
        recent_rule_fires=[],
        committees=[],
        total_evidence_cards=0,
        snapshot_date=_SNAPSHOT_DATE,
    )


def _ready_features() -> OntologyMemberFeaturesPayload:
    return OntologyMemberFeaturesPayload(
        snapshot_id="2026-04-25",
        member_bioguide_id="P000197",
        edge_count=3,
        source_count=3,
        edge_type_counts={
            "committee_sector_jurisdiction": 1,
            "member_committee_assignment": 1,
            "member_sector_holding_exposure": 1,
        },
        source_type_counts={"committee_membership": 2, "financial_disclosure": 1},
        committees=[OntologyNodeRef(node_type="committee", node_id="HSEC", label="Energy")],
        sector_exposures=[
            OntologySectorExposurePayload(
                sector_id="energy",
                label="Energy",
                committee_jurisdiction_edge_count=1,
                holding_edge_count=1,
                source_count=2,
            )
        ],
        readiness_status="ready",
        readiness_reasons=[],
    )


def _vote_row() -> dict[str, object]:
    return {
        "bioguide_id": "P000197",
        "vote_count": 3,
        "yea_count": 2,
        "nay_count": 1,
        "present_count": 0,
        "not_voting_count": 0,
        "latest_vote_date": _SNAPSHOT_DATE,
        "vote_event_count": 12,
    }


def _source_anchor(source_id: str = "cm-1") -> SourceAnchor:
    return SourceAnchor(
        source_type="committee_membership",
        source_id=source_id,
        url="https://api.congress.gov/v3/committee/house/HSEC?format=json",
        label="Committee membership",
    )


def _legislative_anchor(
    *,
    jurisdiction_id: str,
    legislative_body_id: str,
    label: str,
) -> SourceAnchor:
    return SourceAnchor(
        source_type="legislative_bill",
        source_id="2025-ab-12",
        jurisdiction_id=jurisdiction_id,
        legislative_body_id=legislative_body_id,
        legislative_session_id="2025_regular",
        url="https://leginfo.legislature.ca.gov/faces/billTextClient.xhtml?bill_id=20250AB12",
        label=label,
    )


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


def test_build_prediction_readiness_marks_member_ready_with_votes_and_ontology() -> None:
    result = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[_vote_row()],
    )

    assert result.member_count == 1
    assert result.ready_member_count == 1
    assert result.partial_member_count == 0
    assert result.blocked_member_count == 0
    assert result.vote_event_count == 12
    assert result.vote_cast_count == 3
    assert result.members[0].readiness_status == "ready"
    assert result.members[0].readiness_reasons == []
    assert result.members[0].yea_rate == 2 / 3
    assert result.members[0].nay_rate == 1 / 3
    assert result.members[0].present_rate == 0
    assert result.members[0].not_voting_rate == 0
    assert result.members[0].participation_rate == 1


def test_build_prediction_readiness_reports_jurisdiction_capability_surface() -> None:
    result = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[
            _vote_row()
            | {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "ca_2025_regular",
            }
        ],
    )

    assert [item.jurisdiction_id for item in result.jurisdictions] == [
        "state_ca",
        "us_congress",
    ]
    state_capability = result.jurisdictions[0]
    assert state_capability.implementation_status == "portable_contract"
    assert state_capability.required_source_roles == [
        "bills",
        "members",
        "source_anchors",
        "votes",
    ]
    assert state_capability.legislative_body_ids == ["ca_assembly"]
    assert state_capability.legislative_session_ids == ["ca_assembly:ca_2025_regular"]
    congress_capability = result.jurisdictions[1]
    assert congress_capability.implementation_status == "implemented"
    assert congress_capability.legislative_body_ids == ["us_congress_house"]
    assert congress_capability.legislative_session_ids == []


def test_build_prediction_readiness_aggregates_vote_rows_across_jurisdictions() -> None:
    result = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[
            _vote_row()
            | {
                "vote_count": 3,
                "yea_count": 2,
                "nay_count": 1,
                "latest_vote_date": date(2026, 4, 20),
                "vote_event_count": 12,
            },
            _vote_row()
            | {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "ca_2025_regular",
                "vote_count": 2,
                "yea_count": 1,
                "nay_count": 1,
                "latest_vote_date": date(2026, 4, 25),
                "vote_event_count": 4,
            },
        ],
    )

    assert result.vote_event_count == 16
    assert result.vote_cast_count == 5
    assert result.members[0].vote_count == 5
    assert result.members[0].yea_count == 3
    assert result.members[0].nay_count == 2
    assert result.members[0].latest_vote_date == date(2026, 4, 25)
    assert result.members[0].readiness_status == "ready"
    state_capability = next(
        jurisdiction
        for jurisdiction in result.jurisdictions
        if jurisdiction.jurisdiction_id == "state_ca"
    )
    assert state_capability.legislative_body_ids == ["ca_assembly"]
    assert state_capability.legislative_session_ids == ["ca_assembly:ca_2025_regular"]


def test_build_prediction_readiness_does_not_count_boolean_vote_totals() -> None:
    result = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[
            {
                **_vote_row(),
                "vote_count": True,
                "yea_count": True,
                "nay_count": False,
                "vote_event_count": True,
            },
        ],
    )

    assert result.vote_event_count == 0
    assert result.vote_cast_count == 0
    assert result.ready_member_count == 0
    assert result.partial_member_count == 1
    assert result.members[0].vote_count == 0
    assert result.members[0].yea_count == 0
    assert result.members[0].readiness_status == "partial"
    assert result.members[0].readiness_reasons == ["missing_vote_history"]


def test_prediction_member_readiness_rejects_inconsistent_vote_counts() -> None:
    try:
        PredictionMemberReadinessPayload(
            bioguide_id="P000197",
            slug="nancy-pelosi",
            name="Nancy Pelosi",
            chamber="house",
            vote_count=3,
            yea_count=2,
            nay_count=0,
            yea_rate=2 / 3,
            participation_rate=2 / 3,
            ontology_readiness_status="ready",
            readiness_status="ready",
        )
    except ValueError as exc:
        assert "vote_count must match vote option counts" in str(exc)
    else:
        raise AssertionError("prediction readiness row with bad vote counts should fail")


def test_prediction_member_readiness_rejects_boolean_vote_counts() -> None:
    try:
        PredictionMemberReadinessPayload(
            bioguide_id="P000197",
            slug="nancy-pelosi",
            name="Nancy Pelosi",
            chamber="house",
            vote_count=True,
            yea_count=1,
            yea_rate=1,
            participation_rate=1,
            ontology_readiness_status="ready",
            readiness_status="ready",
        )
    except ValueError as exc:
        assert "vote_count must be an integer" in str(exc)
    else:
        raise AssertionError("prediction readiness row with boolean vote_count should fail")


def test_prediction_member_readiness_rejects_inconsistent_vote_rates() -> None:
    try:
        PredictionMemberReadinessPayload(
            bioguide_id="P000197",
            slug="nancy-pelosi",
            name="Nancy Pelosi",
            chamber="house",
            vote_count=2,
            yea_count=1,
            nay_count=1,
            yea_rate=1,
            nay_rate=0,
            participation_rate=1,
            ontology_readiness_status="ready",
            readiness_status="ready",
        )
    except ValueError as exc:
        assert "yea_rate must match vote option counts" in str(exc)
    else:
        raise AssertionError("prediction readiness row with bad vote rates should fail")


def test_prediction_member_readiness_normalizes_required_identity_fields() -> None:
    member = PredictionMemberReadinessPayload(
        bioguide_id=" P000197 ",
        slug=" nancy-pelosi ",
        name=" Nancy Pelosi ",
        chamber=" house ",
        party=" D ",
        state=" CA ",
        vote_count=0,
        ontology_readiness_status="ready",
        readiness_status="partial",
        readiness_reasons=["missing_vote_history"],
    )

    assert member.bioguide_id == "P000197"
    assert member.slug == "nancy-pelosi"
    assert member.name == "Nancy Pelosi"
    assert member.chamber == "house"
    assert member.party == "D"
    assert member.state == "CA"


def test_prediction_member_readiness_rejects_blank_identity_fields() -> None:
    try:
        PredictionMemberReadinessPayload(
            bioguide_id=" ",
            slug="nancy-pelosi",
            name="Nancy Pelosi",
            chamber="house",
            vote_count=0,
            ontology_readiness_status="ready",
            readiness_status="partial",
            readiness_reasons=["missing_vote_history"],
        )
    except ValueError as exc:
        assert "bioguide_id must be nonblank" in str(exc)
    else:
        raise AssertionError("prediction readiness row with blank bioguide ID should fail")


def test_prediction_member_readiness_rejects_blank_or_duplicate_reasons() -> None:
    try:
        PredictionMemberReadinessPayload(
            bioguide_id="P000197",
            slug="nancy-pelosi",
            name="Nancy Pelosi",
            chamber="house",
            vote_count=0,
            ontology_readiness_status="ready",
            readiness_status="partial",
            readiness_reasons=["missing_vote_history", " "],
        )
    except ValueError as exc:
        assert "readiness_reasons must be unique and nonblank" in str(exc)
    else:
        raise AssertionError("prediction readiness row with blank reason should fail")

    try:
        PredictionMemberReadinessPayload(
            bioguide_id="P000197",
            slug="nancy-pelosi",
            name="Nancy Pelosi",
            chamber="house",
            vote_count=0,
            ontology_readiness_status="ready",
            readiness_status="partial",
            readiness_reasons=["missing_vote_history", "missing_vote_history"],
        )
    except ValueError as exc:
        assert "readiness_reasons must be unique and nonblank" in str(exc)
    else:
        raise AssertionError("prediction readiness row with duplicate reasons should fail")


def test_build_prediction_readiness_marks_missing_votes_as_partial() -> None:
    result = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[],
    )

    assert result.ready_member_count == 0
    assert result.partial_member_count == 1
    assert result.members[0].readiness_status == "partial"
    assert result.members[0].readiness_reasons == ["missing_vote_history"]
    assert result.members[0].yea_rate == 0
    assert result.members[0].participation_rate == 0


def test_build_prediction_readiness_marks_missing_votes_and_ontology_as_blocked() -> None:
    result = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={},
        vote_rows=[],
    )

    assert result.blocked_member_count == 1
    assert result.members[0].readiness_status == "blocked"
    assert result.members[0].readiness_reasons == [
        "missing_vote_history",
        "missing_ontology_features",
    ]


def test_build_prediction_readiness_summarizes_coverage_and_reasons() -> None:
    partial_features = _ready_features().model_copy(
        update={
            "member_bioguide_id": "A000001",
            "edge_count": 0,
            "source_count": 0,
            "readiness_status": "blocked",
            "readiness_reasons": ["missing_ontology_edges"],
        }
    )

    result = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[
            _profile(),
            _profile(bioguide_id="A000001", slug="alex-adams"),
            _profile(bioguide_id="B000002", slug="beth-baker"),
        ],
        ontology_features={
            "P000197": _ready_features(),
            "A000001": partial_features,
        },
        vote_rows=[
            _vote_row(),
            {
                **_vote_row(),
                "bioguide_id": "A000001",
                "vote_count": 1,
                "yea_count": 1,
                "nay_count": 0,
                "vote_event_count": 12,
            },
        ],
    )

    assert result.coverage.readiness_rate == 1 / 3
    assert result.coverage.vote_coverage_rate == 2 / 3
    assert result.coverage.ontology_coverage_rate == 2 / 3
    assert result.coverage.average_votes_per_member == 4 / 3
    assert result.coverage.average_ontology_edges_per_member == 1
    assert [item.reason for item in result.coverage.readiness_reasons] == [
        "missing_vote_history",
        "missing_ontology_features",
        "ontology_features_not_ready",
    ]
    assert [item.member_count for item in result.coverage.readiness_reasons] == [1, 1, 1]


def test_prediction_readiness_rejects_inconsistent_coverage_rates() -> None:
    member = PredictionMemberReadinessPayload(
        bioguide_id="P000197",
        slug="nancy-pelosi",
        name="Nancy Pelosi",
        chamber="house",
        vote_count=1,
        yea_count=1,
        yea_rate=1,
        participation_rate=1,
        ontology_readiness_status="ready",
        ontology_edge_count=3,
        readiness_status="ready",
    )

    try:
        PredictionReadinessPayload(
            snapshot_id="2026-04-25",
            snapshot_date=_SNAPSHOT_DATE,
            member_count=1,
            ready_member_count=1,
            partial_member_count=0,
            blocked_member_count=0,
            vote_event_count=1,
            vote_cast_count=1,
            coverage=PredictionReadinessCoveragePayload(
                readiness_rate=0,
                vote_coverage_rate=1,
                ontology_coverage_rate=1,
                average_votes_per_member=1,
                average_ontology_edges_per_member=3,
            ),
            members=[member],
        )
    except ValueError as exc:
        assert "coverage.readiness_rate must match members" in str(exc)
    else:
        raise AssertionError("prediction readiness with inconsistent coverage should fail")


def test_prediction_readiness_rejects_inconsistent_coverage_reasons() -> None:
    member = PredictionMemberReadinessPayload(
        bioguide_id="P000197",
        slug="nancy-pelosi",
        name="Nancy Pelosi",
        chamber="house",
        vote_count=0,
        ontology_readiness_status="blocked",
        readiness_status="partial",
        readiness_reasons=["missing_vote_history"],
    )

    try:
        PredictionReadinessPayload(
            snapshot_id="2026-04-25",
            snapshot_date=_SNAPSHOT_DATE,
            member_count=1,
            ready_member_count=0,
            partial_member_count=1,
            blocked_member_count=0,
            vote_event_count=1,
            vote_cast_count=0,
            coverage=PredictionReadinessCoveragePayload(
                readiness_rate=0,
                vote_coverage_rate=0,
                ontology_coverage_rate=1,
                average_votes_per_member=0,
                average_ontology_edges_per_member=0,
                readiness_reasons=[],
            ),
            members=[member],
        )
    except ValueError as exc:
        assert "coverage.readiness_reasons must match member reasons" in str(exc)
    else:
        raise AssertionError("prediction readiness with inconsistent reasons should fail")


def test_prediction_readiness_coverage_rejects_duplicate_reasons() -> None:
    try:
        PredictionReadinessCoveragePayload(
            readiness_rate=0,
            vote_coverage_rate=0,
            ontology_coverage_rate=0,
            average_votes_per_member=0,
            average_ontology_edges_per_member=0,
            readiness_reasons=[
                PredictionReadinessReasonPayload(
                    reason="missing_vote_history",
                    member_count=1,
                ),
                PredictionReadinessReasonPayload(
                    reason="missing_vote_history",
                    member_count=1,
                ),
            ],
        )
    except ValueError as exc:
        assert "readiness_reasons must be unique" in str(exc)
    else:
        raise AssertionError("prediction coverage with duplicate reasons should fail")


def test_prediction_readiness_empty_coverage_denominators_are_zero() -> None:
    result = PredictionReadinessPayload(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_count=0,
        ready_member_count=0,
        partial_member_count=0,
        blocked_member_count=0,
        vote_event_count=0,
        vote_cast_count=0,
        coverage=PredictionReadinessCoveragePayload(
            readiness_rate=0,
            vote_coverage_rate=0,
            ontology_coverage_rate=0,
            average_votes_per_member=0,
            average_ontology_edges_per_member=0,
        ),
    )

    assert result.member_count == 0
    assert result.coverage.readiness_reasons == []


def test_build_prediction_readiness_summarizes_segments_by_chamber_and_party() -> None:
    result = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[
            _profile(),
            _profile(bioguide_id="A000001", slug="alex-adams"),
            _profile(bioguide_id="B000002", slug="beth-baker").model_copy(
                update={"chamber": "senate", "party": "Republican", "state": "TX"}
            ),
        ],
        ontology_features={
            "P000197": _ready_features(),
            "A000001": _ready_features().model_copy(update={"member_bioguide_id": "A000001"}),
        },
        vote_rows=[
            _vote_row(),
            {
                **_vote_row(),
                "bioguide_id": "A000001",
                "vote_count": 1,
                "yea_count": 1,
                "nay_count": 0,
            },
        ],
    )

    segment_by_key = {
        (segment.segment_type, segment.segment_key): segment for segment in result.segments
    }

    assert segment_by_key[("chamber", "house")].member_count == 2
    assert segment_by_key[("chamber", "house")].ready_member_count == 2
    assert segment_by_key[("chamber", "house")].readiness_rate == 1
    assert segment_by_key[("chamber", "senate")].blocked_member_count == 1
    assert segment_by_key[("party", "Democrat")].vote_coverage_rate == 1
    assert segment_by_key[("party", "Republican")].ontology_coverage_rate == 0


def test_prediction_segment_rejects_inconsistent_rates() -> None:
    try:
        PredictionSegmentReadinessPayload(
            segment_type="chamber",
            segment_key="house",
            member_count=2,
            ready_member_count=1,
            partial_member_count=1,
            blocked_member_count=0,
            vote_coverage_rate=1,
            ontology_coverage_rate=0.5,
            readiness_rate=1,
        )
    except ValueError as exc:
        assert "segment readiness_rate must match status counts" in str(exc)
    else:
        raise AssertionError("prediction segment with inconsistent rates should fail")


def test_prediction_segment_normalizes_and_rejects_blank_segment_key() -> None:
    segment = PredictionSegmentReadinessPayload(
        segment_type="chamber",
        segment_key=" house ",
        member_count=0,
        ready_member_count=0,
        partial_member_count=0,
        blocked_member_count=0,
        vote_coverage_rate=0,
        ontology_coverage_rate=0,
        readiness_rate=0,
    )

    assert segment.segment_key == "house"

    try:
        PredictionSegmentReadinessPayload(
            segment_type="chamber",
            segment_key=" ",
            member_count=0,
            ready_member_count=0,
            partial_member_count=0,
            blocked_member_count=0,
            vote_coverage_rate=0,
            ontology_coverage_rate=0,
            readiness_rate=0,
        )
    except ValueError as exc:
        assert "segment_key must be nonblank" in str(exc)
    else:
        raise AssertionError("prediction segment with blank key should fail")


def test_prediction_readiness_rejects_duplicate_segments() -> None:
    member = PredictionMemberReadinessPayload(
        bioguide_id="P000197",
        slug="nancy-pelosi",
        name="Nancy Pelosi",
        chamber="house",
        vote_count=0,
        ontology_readiness_status="ready",
        readiness_status="partial",
        readiness_reasons=["missing_vote_history"],
    )
    segment = PredictionSegmentReadinessPayload(
        segment_type="chamber",
        segment_key="house",
        member_count=1,
        ready_member_count=0,
        partial_member_count=1,
        blocked_member_count=0,
        vote_coverage_rate=0,
        ontology_coverage_rate=1,
        readiness_rate=0,
    )

    try:
        PredictionReadinessPayload(
            snapshot_id="2026-04-25",
            snapshot_date=_SNAPSHOT_DATE,
            member_count=1,
            ready_member_count=0,
            partial_member_count=1,
            blocked_member_count=0,
            vote_event_count=0,
            vote_cast_count=0,
            coverage=PredictionReadinessCoveragePayload(
                readiness_rate=0,
                vote_coverage_rate=0,
                ontology_coverage_rate=1,
                average_votes_per_member=0,
                average_ontology_edges_per_member=0,
                readiness_reasons=[
                    PredictionReadinessReasonPayload(
                        reason="missing_vote_history",
                        member_count=1,
                    )
                ],
            ),
            segments=[segment, segment],
            members=[member],
        )
    except ValueError as exc:
        assert "segments must be sorted and unique" in str(exc)
    else:
        raise AssertionError("prediction readiness with duplicate segments should fail")


def test_build_prediction_readiness_index_keys_members_for_fast_lookup() -> None:
    readiness = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[
            _vote_row(),
            _vote_row()
            | {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "ca_2025_regular",
            },
        ],
    )

    index = build_prediction_readiness_index(readiness)

    assert index.snapshot_id == readiness.snapshot_id
    assert index.snapshot_date == readiness.snapshot_date
    assert index.member_count == 1
    assert index.slug_to_bioguide == {"nancy-pelosi": "P000197"}
    assert index.members_by_bioguide["P000197"].yea_rate == 2 / 3


def test_prediction_readiness_index_rejects_mismatched_member_map_key() -> None:
    member = PredictionMemberReadinessPayload(
        bioguide_id="P000197",
        slug="nancy-pelosi",
        name="Nancy Pelosi",
        chamber="house",
        vote_count=1,
        yea_count=1,
        yea_rate=1,
        participation_rate=1,
        ontology_readiness_status="ready",
        readiness_status="ready",
    )

    try:
        PredictionReadinessIndexPayload(
            snapshot_id="2026-04-25",
            snapshot_date=_SNAPSHOT_DATE,
            member_count=1,
            slug_to_bioguide={"nancy-pelosi": "X000000"},
            members_by_bioguide={"X000000": member},
        )
    except ValueError as exc:
        assert "members_by_bioguide keys must match member bioguide IDs" in str(exc)
    else:
        raise AssertionError("prediction readiness index with stale member key should fail")


def test_build_prediction_member_context_combines_readiness_and_ontology_features() -> None:
    readiness = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[_vote_row()],
    )

    context = build_prediction_member_context(
        readiness.members[0],
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        ontology_features=_ready_features(),
        source_anchors=[_source_anchor()],
    )

    assert context.snapshot_id == "2026-04-25"
    assert context.snapshot_date == _SNAPSHOT_DATE
    assert context.member_bioguide_id == "P000197"
    assert context.member_readiness.yea_rate == 2 / 3
    assert context.ontology_features is not None
    assert context.ontology_features.sector_exposures[0].sector_id == "energy"
    assert context.context_status == "ready"
    assert context.context_reasons == []
    assert context.source_anchors == [_source_anchor()]


def test_build_prediction_member_context_carries_artifact_refs() -> None:
    readiness = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[_vote_row()],
    )
    refs = PredictionContextArtifactRefsPayload(
        member_profile_path="members/nancy-pelosi.json",
        member_page_path="member-pages/nancy-pelosi.json",
        prediction_member_readiness_path="prediction/members/P000197.json",
        prediction_member_context_path="prediction/member-context/P000197.json",
        ontology_member_features_path="ontology/member-features/P000197.json",
        ontology_member_graph_path="ontology/members/P000197.json",
    )

    context = build_prediction_member_context(
        readiness.members[0],
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        ontology_features=_ready_features(),
        artifact_refs=refs,
        source_anchors=[_source_anchor()],
    )

    assert context.artifact_refs == refs


def test_build_prediction_member_context_rejects_ready_context_without_source_anchors() -> None:
    readiness = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[_vote_row()],
    )

    try:
        build_prediction_member_context(
            readiness.members[0],
            snapshot_id=readiness.snapshot_id,
            snapshot_date=readiness.snapshot_date,
            ontology_features=_ready_features(),
        )
    except ValueError as exc:
        assert "ready prediction contexts require source anchors" in str(exc)
    else:
        raise AssertionError("ready prediction context without source anchors should fail")


def test_build_prediction_member_context_rejects_ready_context_without_official_source_anchor() -> (
    None
):
    readiness = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[_vote_row()],
    )
    unofficial_anchor = SourceAnchor(
        source_type="committee_membership",
        source_id="cm-1",
        url="https://example.com/not-official",
        label="Committee membership",
    )

    try:
        build_prediction_member_context(
            readiness.members[0],
            snapshot_id=readiness.snapshot_id,
            snapshot_date=readiness.snapshot_date,
            ontology_features=_ready_features(),
            source_anchors=[unofficial_anchor],
        )
    except ValueError as exc:
        assert "ready prediction contexts require an official source anchor" in str(exc)
    else:
        raise AssertionError("ready prediction context without official source anchor should fail")


def test_build_prediction_member_context_carries_deduped_source_anchors() -> None:
    readiness = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[_vote_row()],
    )
    anchor = _source_anchor()

    context = build_prediction_member_context(
        readiness.members[0],
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        ontology_features=_ready_features(),
        source_anchors=[anchor, anchor],
    )

    assert context.source_anchors == [anchor]
    assert len(context.source_keys) == 1
    assert context.source_context_paths == [prediction_source_context_path(context.source_keys[0])]


def test_build_prediction_member_context_dedupes_source_identity_with_different_label() -> None:
    readiness = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[_vote_row()],
    )
    anchor = _source_anchor()
    duplicate_identity = SourceAnchor(
        source_type=anchor.source_type,
        source_id=anchor.source_id,
        url=anchor.url,
        label="Duplicate display label",
    )

    context = build_prediction_member_context(
        readiness.members[0],
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        ontology_features=_ready_features(),
        source_anchors=[duplicate_identity, anchor],
    )

    assert context.source_anchors == [anchor]
    assert len(context.source_keys) == 1
    assert context.source_keys == [_source_key(anchor)]


def test_prediction_member_context_rejects_mismatched_source_key() -> None:
    readiness = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[_vote_row()],
    )
    anchor = _source_anchor()

    try:
        PredictionMemberContextPayload(
            snapshot_id=readiness.snapshot_id,
            snapshot_date=readiness.snapshot_date,
            member_bioguide_id="P000197",
            member_readiness=readiness.members[0],
            ontology_features=_ready_features(),
            context_status="ready",
            source_anchors=[anchor],
            source_keys=["abc123"],
            source_context_paths=["prediction/source-context/abc123.json"],
        )
    except ValueError as exc:
        assert "source_keys must match source_anchors" in str(exc)
    else:
        raise AssertionError("prediction member context with mismatched source key should fail")


def test_prediction_member_context_rejects_duplicate_source_keys() -> None:
    readiness = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[_vote_row()],
    )
    anchor = _source_anchor()
    source_key = _source_key(anchor)

    try:
        PredictionMemberContextPayload(
            snapshot_id=readiness.snapshot_id,
            snapshot_date=readiness.snapshot_date,
            member_bioguide_id="P000197",
            member_readiness=readiness.members[0],
            ontology_features=_ready_features(),
            context_status="ready",
            source_anchors=[anchor, anchor],
            source_keys=[source_key, source_key],
            source_context_paths=[
                prediction_source_context_path(source_key),
                prediction_source_context_path(source_key),
            ],
        )
    except ValueError as exc:
        assert "source_keys must be unique" in str(exc)
    else:
        raise AssertionError("prediction member context with duplicate source keys should fail")


def test_prediction_member_context_rejects_duplicate_source_identities() -> None:
    readiness = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[_vote_row()],
    )
    anchor = _source_anchor()
    duplicate_identity = SourceAnchor(
        source_type=anchor.source_type,
        source_id=anchor.source_id,
        url=anchor.url,
        label="Duplicate display label",
    )
    duplicate_key = _source_key(duplicate_identity)

    try:
        PredictionMemberContextPayload(
            snapshot_id=readiness.snapshot_id,
            snapshot_date=readiness.snapshot_date,
            member_bioguide_id="P000197",
            member_readiness=readiness.members[0],
            ontology_features=_ready_features(),
            context_status="ready",
            source_anchors=[anchor, duplicate_identity],
            source_keys=[_source_key(anchor), duplicate_key],
            source_context_paths=[
                prediction_source_context_path(_source_key(anchor)),
                prediction_source_context_path(duplicate_key),
            ],
        )
    except ValueError as exc:
        assert "duplicate source anchors" in str(exc)
    else:
        raise AssertionError(
            "prediction member context with duplicate source identities should fail"
        )


def test_prediction_member_context_rejects_blank_or_duplicate_context_reasons() -> None:
    member = PredictionMemberReadinessPayload(
        bioguide_id="P000197",
        slug="nancy-pelosi",
        name="Nancy Pelosi",
        chamber="house",
        vote_count=0,
        ontology_readiness_status="ready",
        readiness_status="partial",
        readiness_reasons=["missing_vote_history"],
    )

    try:
        PredictionMemberContextPayload(
            snapshot_id="2026-04-25",
            snapshot_date=_SNAPSHOT_DATE,
            member_bioguide_id="P000197",
            member_readiness=member,
            context_status="partial",
            context_reasons=["missing_vote_history", " "],
        )
    except ValueError as exc:
        assert "context_reasons must be unique and nonblank" in str(exc)
    else:
        raise AssertionError("prediction member context with blank reason should fail")

    try:
        PredictionMemberContextPayload(
            snapshot_id="2026-04-25",
            snapshot_date=_SNAPSHOT_DATE,
            member_bioguide_id="P000197",
            member_readiness=member,
            context_status="partial",
            context_reasons=["missing_vote_history", "missing_vote_history"],
        )
    except ValueError as exc:
        assert "context_reasons must be unique and nonblank" in str(exc)
    else:
        raise AssertionError("prediction member context with duplicate reasons should fail")


def test_prediction_member_context_rejects_mismatched_source_context_path() -> None:
    readiness = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[_vote_row()],
    )
    anchor = _source_anchor()
    source_key = _source_key(anchor)

    try:
        PredictionMemberContextPayload(
            snapshot_id=readiness.snapshot_id,
            snapshot_date=readiness.snapshot_date,
            member_bioguide_id="P000197",
            member_readiness=readiness.members[0],
            ontology_features=_ready_features(),
            context_status="ready",
            source_anchors=[anchor],
            source_keys=[source_key],
            source_context_paths=["prediction/source-context/wrong.json"],
        )
    except ValueError as exc:
        assert "source_context_paths must match source_keys" in str(exc)
    else:
        raise AssertionError(
            "prediction member context with mismatched source context path should fail"
        )

    assert prediction_source_context_path(source_key) != "prediction/source-context/wrong.json"


def test_build_prediction_member_context_marks_missing_ontology_features_partial() -> None:
    readiness = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={},
        vote_rows=[_vote_row()],
    )

    context = build_prediction_member_context(
        readiness.members[0],
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        ontology_features=None,
    )

    assert context.context_status == "partial"
    assert context.context_reasons == ["missing_ontology_features"]


def test_build_prediction_bootstrap_exposes_surface_paths_and_summary() -> None:
    readiness = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[
            _vote_row(),
            _vote_row()
            | {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "ca_2025_regular",
            },
        ],
    )

    bootstrap = build_prediction_bootstrap(
        readiness,
        readiness_path="prediction/readiness.json",
        index_path="prediction/index.json",
        topology_path="prediction/topology.json",
        source_index_path="prediction/sources.json",
        source_context_path_template="prediction/source-context/{source_key}.json",
        sector_readiness_path="prediction/sectors.json",
        sector_context_path_template="prediction/sector-context/{sector}.json",
        committee_readiness_path="prediction/committees.json",
        committee_context_path_template="prediction/committee-context/{committee}.json",
        member_readiness_path_template="prediction/members/{bioguide}.json",
        member_context_path_template="prediction/member-context/{bioguide}.json",
    )

    assert bootstrap.snapshot_id == "2026-04-25"
    assert bootstrap.member_count == 1
    assert bootstrap.ready_member_count == 1
    assert bootstrap.coverage.readiness_rate == 1
    assert [jurisdiction.jurisdiction_id for jurisdiction in bootstrap.jurisdictions] == [
        "state_ca",
        "us_congress",
    ]
    assert bootstrap.jurisdictions[0].implementation_status == "portable_contract"
    assert bootstrap.jurisdictions[0].legislative_body_ids == ["ca_assembly"]
    assert bootstrap.jurisdictions[0].legislative_session_ids == ["ca_assembly:ca_2025_regular"]
    assert bootstrap.jurisdictions[1].implementation_status == "implemented"
    assert bootstrap.readiness_path == "prediction/readiness.json"
    assert bootstrap.index_path == "prediction/index.json"
    assert bootstrap.topology_path == "prediction/topology.json"
    assert bootstrap.source_index_path == "prediction/sources.json"
    assert bootstrap.source_context_path_template == "prediction/source-context/{source_key}.json"
    assert bootstrap.sector_readiness_path == "prediction/sectors.json"
    assert bootstrap.sector_context_path_template == "prediction/sector-context/{sector}.json"
    assert bootstrap.committee_readiness_path == "prediction/committees.json"
    assert (
        bootstrap.committee_context_path_template == "prediction/committee-context/{committee}.json"
    )
    assert bootstrap.member_readiness_path_template == "prediction/members/{bioguide}.json"
    assert bootstrap.member_context_path_template == "prediction/member-context/{bioguide}.json"


def test_build_prediction_bootstrap_rejects_noncanonical_template() -> None:
    readiness = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[_vote_row()],
    )

    try:
        build_prediction_bootstrap(
            readiness,
            readiness_path="prediction/readiness.json",
            index_path="prediction/index.json",
            topology_path="prediction/topology.json",
            source_index_path="prediction/sources.json",
            source_context_path_template="../source-context/{source_key}.json",
            sector_readiness_path="prediction/sectors.json",
            sector_context_path_template="prediction/sector-context/{sector}.json",
            committee_readiness_path="prediction/committees.json",
            committee_context_path_template="prediction/committee-context/{committee}.json",
            member_readiness_path_template="prediction/members/{bioguide}.json",
            member_context_path_template="prediction/member-context/{bioguide}.json",
        )
    except ValueError as exc:
        assert "source_context_path_template must be a canonical" in str(exc)
    else:
        raise AssertionError("prediction bootstrap with noncanonical template should fail")


def test_build_prediction_sector_readiness_summarizes_member_contexts_by_sector() -> None:
    readiness = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[_vote_row()],
    )
    context = build_prediction_member_context(
        readiness.members[0],
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        ontology_features=_ready_features(),
        source_anchors=[_source_anchor()],
    )

    sectors = build_prediction_sector_readiness(
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        member_contexts=[context],
    )

    assert sectors.snapshot_id == "2026-04-25"
    assert sectors.sector_count == 1
    assert sectors.sectors[0].sector_id == "energy"
    assert sectors.sectors[0].label == "Energy"
    assert sectors.sectors[0].member_count == 1
    assert sectors.sectors[0].ready_member_count == 1
    assert sectors.sectors[0].readiness_rate == 1
    assert sectors.sectors[0].committee_jurisdiction_edge_count == 1
    assert sectors.sectors[0].holding_edge_count == 1
    assert sectors.sectors[0].source_count == 1
    assert len(sectors.sectors[0].source_keys) == 1


def test_build_prediction_source_index_maps_sources_to_contexts() -> None:
    readiness = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[_vote_row()],
    )
    anchor = _source_anchor()
    context = build_prediction_member_context(
        readiness.members[0],
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        ontology_features=_ready_features(),
        source_anchors=[anchor],
    )

    index = build_prediction_source_index(
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        member_contexts=[context],
    )

    assert index.source_count == 1
    assert len(index.sources[0].source_key) == 24
    assert index.sources[0].source_id == "cm-1"
    assert index.sources[0].source_context_path == (
        f"prediction/source-context/{index.sources[0].source_key}.json"
    )
    assert index.sources[0].member_bioguide_ids == ["P000197"]
    assert index.sources[0].sector_ids == ["energy"]
    assert index.sources[0].committee_ids == ["HSEC"]


def test_prediction_source_key_scopes_legislative_context() -> None:
    ca_anchor = _legislative_anchor(
        jurisdiction_id="state_ca",
        legislative_body_id="ca_assembly",
        label="Legislative bill 2025-ab-12",
    )
    ny_anchor = _legislative_anchor(
        jurisdiction_id="state_ny",
        legislative_body_id="ny_assembly",
        label="Legislative bill 2025-ab-12",
    )

    assert _source_key(ca_anchor) != _source_key(ny_anchor)


def test_build_prediction_source_index_preserves_legislative_context_collisions() -> None:
    readiness = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[_vote_row()],
    )
    ca_anchor = _legislative_anchor(
        jurisdiction_id="state_ca",
        legislative_body_id="ca_assembly",
        label="Legislative bill 2025-ab-12",
    )
    ny_anchor = _legislative_anchor(
        jurisdiction_id="state_ny",
        legislative_body_id="ny_assembly",
        label="Legislative bill 2025-ab-12",
    )
    context = build_prediction_member_context(
        readiness.members[0],
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        ontology_features=_ready_features(),
        source_anchors=[ca_anchor, ny_anchor],
    )

    index = build_prediction_source_index(
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        member_contexts=[context],
    )

    assert index.source_count == 2
    assert sorted(source.jurisdiction_id for source in index.sources) == [
        "state_ca",
        "state_ny",
    ]
    assert sorted(source.legislative_body_id for source in index.sources) == [
        "ca_assembly",
        "ny_assembly",
    ]
    assert sorted(source.source_key for source in index.sources) == sorted(context.source_keys)


def test_build_prediction_source_contexts_preserves_legislative_context_collisions() -> None:
    readiness = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[_vote_row()],
    )
    ca_anchor = _legislative_anchor(
        jurisdiction_id="state_ca",
        legislative_body_id="ca_assembly",
        label="Legislative bill 2025-ab-12",
    )
    ny_anchor = _legislative_anchor(
        jurisdiction_id="state_ny",
        legislative_body_id="ny_assembly",
        label="Legislative bill 2025-ab-12",
    )
    context = build_prediction_member_context(
        readiness.members[0],
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        ontology_features=_ready_features(),
        source_anchors=[ca_anchor, ny_anchor],
    )

    source_contexts = build_prediction_source_contexts(
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        member_contexts=[context],
    )

    assert len(source_contexts) == 2
    assert sorted(item.source.jurisdiction_id for item in source_contexts) == [
        "state_ca",
        "state_ny",
    ]
    assert [item.members[0].member_bioguide_id for item in source_contexts] == [
        "P000197",
        "P000197",
    ]


def test_prediction_source_index_row_rejects_unofficial_source_url() -> None:
    try:
        PredictionSourceIndexRowPayload(
            source_key="abc123",
            source_type="committee_membership",
            source_id="cm-1",
            url="https://example.com/not-official",
            label="Committee membership",
            source_context_path="prediction/source-context/abc123.json",
            member_count=1,
            member_bioguide_ids=["P000197"],
        )
    except ValueError as exc:
        assert "prediction source rows require official source URLs" in str(exc)
    else:
        raise AssertionError("prediction source row with unofficial URL should fail")


def test_prediction_source_index_row_rejects_boolean_member_count() -> None:
    anchor = _source_anchor()
    source_key = _source_key(anchor)

    try:
        PredictionSourceIndexRowPayload(
            source_key=source_key,
            source_type=anchor.source_type,
            source_id=anchor.source_id,
            url=anchor.url,
            label=anchor.label,
            source_context_path=prediction_source_context_path(source_key),
            member_count=True,
            member_bioguide_ids=["P000197"],
        )
    except ValueError as exc:
        assert "member_count must be an integer" in str(exc)
    else:
        raise AssertionError("prediction source row with boolean member_count should fail")


def test_prediction_source_index_row_rejects_mismatched_source_key() -> None:
    anchor = _source_anchor()
    try:
        PredictionSourceIndexRowPayload(
            source_key="abc123",
            source_type=anchor.source_type,
            source_id=anchor.source_id,
            url=anchor.url,
            label=anchor.label,
            source_context_path="prediction/source-context/abc123.json",
            member_count=1,
            member_bioguide_ids=["P000197"],
        )
    except ValueError as exc:
        assert "prediction source_key must match source identity" in str(exc)
    else:
        raise AssertionError("prediction source row with mismatched source_key should fail")


def test_prediction_source_index_row_rejects_legislative_source_without_context() -> None:
    source_key = prediction_source_key(
        source_type="legislative_bill",
        source_id="2025-ab-12",
        url="https://leginfo.legislature.ca.gov/faces/billTextClient.xhtml?bill_id=20250AB12",
        label="Legislative bill 2025-ab-12",
    )

    try:
        PredictionSourceIndexRowPayload(
            source_key=source_key,
            source_type="legislative_bill",
            source_id="2025-ab-12",
            url="https://leginfo.legislature.ca.gov/faces/billTextClient.xhtml?bill_id=20250AB12",
            label="Legislative bill 2025-ab-12",
            source_context_path=prediction_source_context_path(source_key),
            member_count=1,
            member_bioguide_ids=["P000197"],
        )
    except ValueError as exc:
        assert "legislative source rows require context" in str(exc)
    else:
        raise AssertionError("legislative source row without context should fail")


def test_prediction_source_index_row_accepts_scoped_legislative_source() -> None:
    anchor = _legislative_anchor(
        jurisdiction_id="state_ca",
        legislative_body_id="ca_assembly",
        label="Legislative bill 2025-ab-12",
    )
    source_key = _source_key(anchor)

    row = PredictionSourceIndexRowPayload(
        source_key=source_key,
        source_type=anchor.source_type,
        source_id=anchor.source_id,
        jurisdiction_id=anchor.jurisdiction_id,
        legislative_body_id=anchor.legislative_body_id,
        legislative_session_id=anchor.legislative_session_id,
        url=anchor.url,
        label=anchor.label,
        source_context_path=prediction_source_context_path(source_key),
        member_count=1,
        member_bioguide_ids=["P000197"],
    )

    assert row.jurisdiction_id == "state_ca"
    assert row.legislative_body_id == "ca_assembly"
    assert row.legislative_session_id == "2025_regular"


def test_prediction_topology_rejects_boolean_counts() -> None:
    try:
        PredictionTopologyPayload(
            snapshot_id="2026-04-25",
            snapshot_date=_SNAPSHOT_DATE,
            member_count=True,
            sector_count=0,
            committee_count=0,
            source_count=0,
            readiness_path="prediction/readiness.json",
            index_path="prediction/index.json",
            source_index_path="prediction/sources.json",
            sector_readiness_path="prediction/sectors.json",
            committee_readiness_path="prediction/committees.json",
            member_readiness_path_template="prediction/members/{bioguide}.json",
            member_context_path_template="prediction/member-context/{bioguide}.json",
            source_context_path_template="prediction/source-context/{source_key}.json",
            sector_context_path_template="prediction/sector-context/{sector}.json",
            committee_context_path_template="prediction/committee-context/{committee}.json",
        )
    except ValueError as exc:
        assert "member_count must be an integer" in str(exc)
    else:
        raise AssertionError("prediction topology with boolean member_count should fail")


def test_prediction_source_index_row_rejects_unsorted_or_duplicate_lookup_lists() -> None:
    anchor = _source_anchor()
    source_key = _source_key(anchor)

    try:
        PredictionSourceIndexRowPayload(
            source_key=source_key,
            source_type=anchor.source_type,
            source_id=anchor.source_id,
            url=anchor.url,
            label=anchor.label,
            source_context_path=prediction_source_context_path(source_key),
            member_count=2,
            member_bioguide_ids=["P000197", "A000001"],
        )
    except ValueError as exc:
        assert "member_bioguide_ids must be sorted and unique" in str(exc)
    else:
        raise AssertionError("source row with unsorted members should fail")

    try:
        PredictionSourceIndexRowPayload(
            source_key=source_key,
            source_type=anchor.source_type,
            source_id=anchor.source_id,
            url=anchor.url,
            label=anchor.label,
            source_context_path=prediction_source_context_path(source_key),
            member_count=2,
            member_bioguide_ids=["P000197", "P000197"],
        )
    except ValueError as exc:
        assert "member_bioguide_ids must be sorted and unique" in str(exc)
    else:
        raise AssertionError("source row with duplicate members should fail")


def test_prediction_source_index_row_normalizes_source_identity_fields() -> None:
    anchor = _source_anchor()
    source_key = prediction_source_key(
        source_type=anchor.source_type,
        source_id=anchor.source_id,
        url=anchor.url,
        label=anchor.label,
    )

    row = PredictionSourceIndexRowPayload(
        source_key=source_key,
        source_type=f" {anchor.source_type} ",
        source_id=f" {anchor.source_id} ",
        url=f" {anchor.url} ",
        label=f" {anchor.label} ",
        source_context_path=prediction_source_context_path(source_key),
        member_count=1,
        member_bioguide_ids=["P000197"],
    )

    assert row.source_type == anchor.source_type
    assert row.source_id == anchor.source_id
    assert row.url == anchor.url
    assert row.label == anchor.label


def test_prediction_source_index_row_rejects_blank_lookup_ids() -> None:
    anchor = _source_anchor()
    source_key = _source_key(anchor)

    try:
        PredictionSourceIndexRowPayload(
            source_key=source_key,
            source_type=anchor.source_type,
            source_id=anchor.source_id,
            url=anchor.url,
            label=anchor.label,
            source_context_path=prediction_source_context_path(source_key),
            member_count=1,
            member_bioguide_ids=[" "],
        )
    except ValueError as exc:
        assert "member_bioguide_ids must be sorted, unique, and nonblank" in str(exc)
    else:
        raise AssertionError("source row with blank member lookup ID should fail")


def test_prediction_source_index_row_rejects_blank_source_identity_fields() -> None:
    try:
        PredictionSourceIndexRowPayload(
            source_key=prediction_source_key(
                source_type="committee_membership",
                source_id="",
                url="https://api.congress.gov/v3/committee/house/HSEC?format=json",
                label="Committee membership",
            ),
            source_type="committee_membership",
            source_id=" ",
            url="https://api.congress.gov/v3/committee/house/HSEC?format=json",
            label="Committee membership",
            source_context_path=prediction_source_context_path(
                prediction_source_key(
                    source_type="committee_membership",
                    source_id="",
                    url="https://api.congress.gov/v3/committee/house/HSEC?format=json",
                    label="Committee membership",
                )
            ),
            member_count=1,
            member_bioguide_ids=["P000197"],
        )
    except ValueError as exc:
        assert "source_id must be nonblank" in str(exc)
    else:
        raise AssertionError("source row with blank source ID should fail")


def test_prediction_source_index_rejects_duplicate_source_keys() -> None:
    anchor = _source_anchor()
    row = PredictionSourceIndexRowPayload(
        source_key=_source_key(anchor),
        source_type=anchor.source_type,
        source_id=anchor.source_id,
        url=anchor.url,
        label=anchor.label,
        source_context_path=prediction_source_context_path(_source_key(anchor)),
        member_count=1,
        member_bioguide_ids=["P000197"],
    )

    try:
        PredictionSourceIndexPayload(
            snapshot_id="2026-04-25",
            snapshot_date=_SNAPSHOT_DATE,
            source_count=2,
            sources=[row, row],
        )
    except ValueError as exc:
        assert "source keys must be unique" in str(exc)
    else:
        raise AssertionError("prediction source index with duplicate source keys should fail")


def test_prediction_source_index_rejects_unsorted_sources() -> None:
    first_anchor = _source_anchor("cm-1")
    second_anchor = _source_anchor("cm-2")
    first_row = PredictionSourceIndexRowPayload(
        source_key=_source_key(first_anchor),
        source_type=first_anchor.source_type,
        source_id=first_anchor.source_id,
        url=first_anchor.url,
        label=first_anchor.label,
        source_context_path=prediction_source_context_path(_source_key(first_anchor)),
        member_count=1,
        member_bioguide_ids=["P000197"],
    )
    second_row = PredictionSourceIndexRowPayload(
        source_key=_source_key(second_anchor),
        source_type=second_anchor.source_type,
        source_id=second_anchor.source_id,
        url=second_anchor.url,
        label=second_anchor.label,
        source_context_path=prediction_source_context_path(_source_key(second_anchor)),
        member_count=1,
        member_bioguide_ids=["P000197"],
    )

    rows = sorted([first_row, second_row], key=lambda row: row.source_key, reverse=True)

    try:
        PredictionSourceIndexPayload(
            snapshot_id="2026-04-25",
            snapshot_date=_SNAPSHOT_DATE,
            source_count=2,
            sources=rows,
        )
    except ValueError as exc:
        assert "sources must be sorted by source_key" in str(exc)
    else:
        raise AssertionError("prediction source index with unsorted sources should fail")


def test_prediction_committee_readiness_rejects_blank_lookup_ids() -> None:
    try:
        PredictionCommitteeReadinessRowPayload(
            committee_id="HSEC",
            member_count=1,
            ready_member_count=1,
            partial_member_count=0,
            blocked_member_count=0,
            readiness_rate=1,
            vote_coverage_rate=1,
            sector_ids=[" "],
            source_count=0,
            source_keys=[],
            source_context_paths=[],
            member_bioguide_ids=["P000197"],
        )
    except ValueError as exc:
        assert "sector_ids must be sorted, unique, and nonblank" in str(exc)
    else:
        raise AssertionError("committee readiness with blank sector ID should fail")


def test_prediction_committee_readiness_normalizes_and_rejects_blank_committee_id() -> None:
    row = PredictionCommitteeReadinessRowPayload(
        committee_id=" HSEC ",
        label=" Energy ",
        member_count=0,
        ready_member_count=0,
        partial_member_count=0,
        blocked_member_count=0,
        readiness_rate=0,
        vote_coverage_rate=0,
    )

    assert row.committee_id == "HSEC"
    assert row.label == "Energy"

    try:
        PredictionCommitteeReadinessRowPayload(
            committee_id=" ",
            member_count=0,
            ready_member_count=0,
            partial_member_count=0,
            blocked_member_count=0,
            readiness_rate=0,
            vote_coverage_rate=0,
        )
    except ValueError as exc:
        assert "committee_id must be nonblank" in str(exc)
    else:
        raise AssertionError("committee readiness with blank committee ID should fail")


def test_prediction_sector_readiness_rejects_blank_lookup_ids() -> None:
    try:
        PredictionSectorReadinessRowPayload(
            sector_id="energy",
            member_count=1,
            ready_member_count=1,
            partial_member_count=0,
            blocked_member_count=0,
            readiness_rate=1,
            vote_coverage_rate=1,
            committee_jurisdiction_edge_count=1,
            holding_edge_count=1,
            transaction_edge_count=0,
            source_count=0,
            source_keys=[],
            source_context_paths=[],
            member_bioguide_ids=[" "],
        )
    except ValueError as exc:
        assert "member_bioguide_ids must be sorted, unique, and nonblank" in str(exc)
    else:
        raise AssertionError("sector readiness with blank member ID should fail")


def test_prediction_sector_readiness_normalizes_and_rejects_blank_sector_id() -> None:
    row = PredictionSectorReadinessRowPayload(
        sector_id=" energy ",
        label=" Energy ",
        member_count=0,
        ready_member_count=0,
        partial_member_count=0,
        blocked_member_count=0,
        readiness_rate=0,
        vote_coverage_rate=0,
        committee_jurisdiction_edge_count=0,
        holding_edge_count=0,
        transaction_edge_count=0,
    )

    assert row.sector_id == "energy"
    assert row.label == "Energy"

    try:
        PredictionSectorReadinessRowPayload(
            sector_id=" ",
            member_count=0,
            ready_member_count=0,
            partial_member_count=0,
            blocked_member_count=0,
            readiness_rate=0,
            vote_coverage_rate=0,
            committee_jurisdiction_edge_count=0,
            holding_edge_count=0,
            transaction_edge_count=0,
        )
    except ValueError as exc:
        assert "sector_id must be nonblank" in str(exc)
    else:
        raise AssertionError("sector readiness with blank sector ID should fail")


def test_build_prediction_source_contexts_bundle_member_contexts_by_source() -> None:
    readiness = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[_vote_row()],
    )
    anchor = _source_anchor()
    context = build_prediction_member_context(
        readiness.members[0],
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        ontology_features=_ready_features(),
        source_anchors=[anchor],
    )

    contexts = build_prediction_source_contexts(
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        member_contexts=[context],
    )

    assert len(contexts) == 1
    assert contexts[0].source.source_id == "cm-1"
    assert [member.member_bioguide_id for member in contexts[0].members] == ["P000197"]


def test_prediction_source_context_rejects_member_without_source_key() -> None:
    readiness = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[_vote_row()],
    )
    source_anchor = _source_anchor("cm-1")
    other_anchor = _source_anchor("cm-2")
    source_row = PredictionSourceIndexRowPayload(
        source_key=_source_key(source_anchor),
        source_type=source_anchor.source_type,
        source_id=source_anchor.source_id,
        url=source_anchor.url,
        label=source_anchor.label,
        source_context_path=prediction_source_context_path(_source_key(source_anchor)),
        member_count=1,
        member_bioguide_ids=["P000197"],
    )
    member_context = build_prediction_member_context(
        readiness.members[0],
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        ontology_features=_ready_features(),
        source_anchors=[other_anchor],
    )

    try:
        PredictionSourceContextPayload(
            snapshot_id=readiness.snapshot_id,
            snapshot_date=readiness.snapshot_date,
            source=source_row,
            members=[member_context],
        )
    except ValueError as exc:
        assert "source context members must reference source_key" in str(exc)
    else:
        raise AssertionError("source context with unrelated member source should fail")


def test_build_prediction_topology_summarizes_artifact_families() -> None:
    readiness = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[
            _vote_row(),
            _vote_row()
            | {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "ca_2025_regular",
            },
        ],
    )
    anchor = _source_anchor()
    context = build_prediction_member_context(
        readiness.members[0],
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        ontology_features=_ready_features(),
        source_anchors=[anchor],
    )
    source_index = build_prediction_source_index(
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        member_contexts=[context],
    )
    sector_readiness = build_prediction_sector_readiness(
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        member_contexts=[context],
    )
    committee_readiness = build_prediction_committee_readiness(
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        member_contexts=[context],
    )

    topology = build_prediction_topology(
        readiness=readiness,
        source_index=source_index,
        sector_readiness=sector_readiness,
        committee_readiness=committee_readiness,
        readiness_path="prediction/readiness.json",
        index_path="prediction/index.json",
        source_index_path="prediction/sources.json",
        sector_readiness_path="prediction/sectors.json",
        committee_readiness_path="prediction/committees.json",
        member_readiness_path_template="prediction/members/{bioguide}.json",
        member_context_path_template="prediction/member-context/{bioguide}.json",
        source_context_path_template="prediction/source-context/{source_key}.json",
        sector_context_path_template="prediction/sector-context/{sector}.json",
        committee_context_path_template="prediction/committee-context/{committee}.json",
    )

    assert topology.member_count == 1
    assert topology.source_count == 1
    assert topology.sector_count == 1
    assert topology.committee_count == 1
    assert topology.jurisdiction_count == 2
    assert topology.jurisdiction_ids == ["state_ca", "us_congress"]
    assert topology.implemented_jurisdiction_count == 1
    assert topology.implemented_jurisdiction_ids == ["us_congress"]
    assert topology.portable_jurisdiction_count == 1
    assert topology.portable_jurisdiction_ids == ["state_ca"]
    assert topology.legislative_body_count == 2
    assert topology.legislative_body_ids == [
        "state_ca:ca_assembly",
        "us_congress:us_congress_house",
    ]
    assert topology.legislative_session_count == 1
    assert topology.legislative_session_ids == ["state_ca:ca_assembly:ca_2025_regular"]
    assert topology.source_context_path_template == "prediction/source-context/{source_key}.json"


def test_build_prediction_topology_scopes_same_body_names_across_jurisdictions() -> None:
    topology = PredictionTopologyPayload(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_count=0,
        sector_count=0,
        committee_count=0,
        source_count=0,
        jurisdiction_count=2,
        implemented_jurisdiction_count=0,
        portable_jurisdiction_count=2,
        legislative_body_count=2,
        legislative_session_count=2,
        jurisdiction_ids=["state_ca", "state_ny"],
        implemented_jurisdiction_ids=[],
        portable_jurisdiction_ids=["state_ca", "state_ny"],
        legislative_body_ids=["state_ca:assembly", "state_ny:assembly"],
        legislative_session_ids=[
            "state_ca:assembly:2025_regular",
            "state_ny:assembly:2025_regular",
        ],
        readiness_path="prediction/readiness.json",
        index_path="prediction/index.json",
        source_index_path="prediction/sources.json",
        sector_readiness_path="prediction/sectors.json",
        committee_readiness_path="prediction/committees.json",
        member_readiness_path_template="prediction/members/{bioguide}.json",
        member_context_path_template="prediction/member-context/{bioguide}.json",
        source_context_path_template="prediction/source-context/{source_key}.json",
        sector_context_path_template="prediction/sector-context/{sector}.json",
        committee_context_path_template="prediction/committee-context/{committee}.json",
    )

    assert topology.legislative_body_ids == ["state_ca:assembly", "state_ny:assembly"]


def test_prediction_topology_rejects_unscoped_legislative_body_ids() -> None:
    with pytest.raises(ValueError, match="legislative_body_ids must be jurisdiction scoped"):
        PredictionTopologyPayload(
            snapshot_id="2026-04-25",
            snapshot_date=_SNAPSHOT_DATE,
            member_count=0,
            sector_count=0,
            committee_count=0,
            source_count=0,
            jurisdiction_count=1,
            implemented_jurisdiction_count=0,
            portable_jurisdiction_count=1,
            legislative_body_count=1,
            legislative_session_count=0,
            jurisdiction_ids=["state_ca"],
            implemented_jurisdiction_ids=[],
            portable_jurisdiction_ids=["state_ca"],
            legislative_body_ids=["assembly"],
            legislative_session_ids=[],
            readiness_path="prediction/readiness.json",
            index_path="prediction/index.json",
            source_index_path="prediction/sources.json",
            sector_readiness_path="prediction/sectors.json",
            committee_readiness_path="prediction/committees.json",
            member_readiness_path_template="prediction/members/{bioguide}.json",
            member_context_path_template="prediction/member-context/{bioguide}.json",
            source_context_path_template="prediction/source-context/{source_key}.json",
            sector_context_path_template="prediction/sector-context/{sector}.json",
            committee_context_path_template="prediction/committee-context/{committee}.json",
        )


def test_prediction_topology_rejects_session_ids_for_unknown_scoped_body() -> None:
    with pytest.raises(ValueError, match="legislative_session_ids must be body scoped"):
        PredictionTopologyPayload(
            snapshot_id="2026-04-25",
            snapshot_date=_SNAPSHOT_DATE,
            member_count=0,
            sector_count=0,
            committee_count=0,
            source_count=0,
            jurisdiction_count=1,
            implemented_jurisdiction_count=0,
            portable_jurisdiction_count=1,
            legislative_body_count=1,
            legislative_session_count=1,
            jurisdiction_ids=["state_ca"],
            implemented_jurisdiction_ids=[],
            portable_jurisdiction_ids=["state_ca"],
            legislative_body_ids=["state_ca:assembly"],
            legislative_session_ids=["state_ca:senate:2025_regular"],
            readiness_path="prediction/readiness.json",
            index_path="prediction/index.json",
            source_index_path="prediction/sources.json",
            sector_readiness_path="prediction/sectors.json",
            committee_readiness_path="prediction/committees.json",
            member_readiness_path_template="prediction/members/{bioguide}.json",
            member_context_path_template="prediction/member-context/{bioguide}.json",
            source_context_path_template="prediction/source-context/{source_key}.json",
            sector_context_path_template="prediction/sector-context/{sector}.json",
            committee_context_path_template="prediction/committee-context/{committee}.json",
        )


def test_build_prediction_topology_rejects_noncanonical_path() -> None:
    readiness = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[_vote_row()],
    )
    anchor = _source_anchor()
    context = build_prediction_member_context(
        readiness.members[0],
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        ontology_features=_ready_features(),
        source_anchors=[anchor],
    )
    source_index = build_prediction_source_index(
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        member_contexts=[context],
    )
    sector_readiness = build_prediction_sector_readiness(
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        member_contexts=[context],
    )
    committee_readiness = build_prediction_committee_readiness(
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        member_contexts=[context],
    )

    try:
        build_prediction_topology(
            readiness=readiness,
            source_index=source_index,
            sector_readiness=sector_readiness,
            committee_readiness=committee_readiness,
            readiness_path="prediction/readiness.json",
            index_path="../prediction/index.json",
            source_index_path="prediction/sources.json",
            sector_readiness_path="prediction/sectors.json",
            committee_readiness_path="prediction/committees.json",
            member_readiness_path_template="prediction/members/{bioguide}.json",
            member_context_path_template="prediction/member-context/{bioguide}.json",
            source_context_path_template="prediction/source-context/{source_key}.json",
            sector_context_path_template="prediction/sector-context/{sector}.json",
            committee_context_path_template="prediction/committee-context/{committee}.json",
        )
    except ValueError as exc:
        assert "index_path must be a canonical prediction artifact path" in str(exc)
    else:
        raise AssertionError("prediction topology with noncanonical path should fail")


def test_build_prediction_sector_contexts_bundle_member_contexts_by_sector() -> None:
    readiness = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[_vote_row()],
    )
    member_context = build_prediction_member_context(
        readiness.members[0],
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        ontology_features=_ready_features(),
        source_anchors=[_source_anchor()],
    )

    contexts = build_prediction_sector_contexts(
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        member_contexts=[member_context],
    )

    assert len(contexts) == 1
    assert contexts[0].sector.sector_id == "energy"
    assert contexts[0].sector.ready_member_count == 1
    assert [member.member_bioguide_id for member in contexts[0].members] == ["P000197"]
    assert contexts[0].members[0].context_status == "ready"


def test_prediction_sector_context_rejects_member_without_sector_exposure() -> None:
    readiness = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[_vote_row()],
    )
    member_context = build_prediction_member_context(
        readiness.members[0],
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        ontology_features=_ready_features(),
        source_anchors=[_source_anchor()],
    )
    sector_row = PredictionSectorReadinessRowPayload(
        sector_id="finance",
        member_count=1,
        ready_member_count=1,
        partial_member_count=0,
        blocked_member_count=0,
        readiness_rate=1,
        vote_coverage_rate=1,
        committee_jurisdiction_edge_count=1,
        holding_edge_count=1,
        transaction_edge_count=0,
        source_count=1,
        source_keys=member_context.source_keys,
        source_context_paths=member_context.source_context_paths,
        member_bioguide_ids=["P000197"],
    )

    try:
        PredictionSectorContextPayload(
            snapshot_id=readiness.snapshot_id,
            snapshot_date=readiness.snapshot_date,
            sector=sector_row,
            members=[member_context],
        )
    except ValueError as exc:
        assert "sector context members must include sector exposure" in str(exc)
    else:
        raise AssertionError("sector context with unrelated member sector should fail")


def test_prediction_sector_row_rejects_unsorted_or_duplicate_lookup_lists() -> None:
    source_key = prediction_source_key(
        source_type="committee_membership",
        source_id="cm-1",
        url="https://api.congress.gov/v3/committee/house/HSEC?format=json",
        label="Committee membership",
    )

    try:
        PredictionSectorReadinessRowPayload(
            sector_id="energy",
            member_count=2,
            ready_member_count=2,
            partial_member_count=0,
            blocked_member_count=0,
            readiness_rate=1,
            vote_coverage_rate=1,
            committee_jurisdiction_edge_count=1,
            holding_edge_count=1,
            transaction_edge_count=0,
            source_count=1,
            source_keys=[source_key],
            source_context_paths=[prediction_source_context_path(source_key)],
            member_bioguide_ids=["P000197", "A000001"],
        )
    except ValueError as exc:
        assert "member_bioguide_ids must be sorted and unique" in str(exc)
    else:
        raise AssertionError("sector row with unsorted members should fail")

    try:
        PredictionSectorReadinessRowPayload(
            sector_id="energy",
            member_count=1,
            ready_member_count=1,
            partial_member_count=0,
            blocked_member_count=0,
            readiness_rate=1,
            vote_coverage_rate=1,
            committee_jurisdiction_edge_count=1,
            holding_edge_count=1,
            transaction_edge_count=0,
            source_count=2,
            source_keys=[source_key, source_key],
            source_context_paths=[
                prediction_source_context_path(source_key),
                prediction_source_context_path(source_key),
            ],
            member_bioguide_ids=["P000197"],
        )
    except ValueError as exc:
        assert "source_keys must be sorted and unique" in str(exc)
    else:
        raise AssertionError("sector row with duplicate source keys should fail")


def test_prediction_sector_readiness_rejects_unsorted_rows() -> None:
    row_a = PredictionSectorReadinessRowPayload(
        sector_id="alpha",
        member_count=0,
        ready_member_count=0,
        partial_member_count=0,
        blocked_member_count=0,
        readiness_rate=0,
        vote_coverage_rate=0,
        committee_jurisdiction_edge_count=0,
        holding_edge_count=0,
        transaction_edge_count=0,
    )
    row_z = row_a.model_copy(update={"sector_id": "zeta"})

    try:
        PredictionSectorReadinessPayload(
            snapshot_id="2026-04-25",
            snapshot_date=_SNAPSHOT_DATE,
            sector_count=2,
            sectors=[row_z, row_a],
        )
    except ValueError as exc:
        assert "sectors must be sorted by sector_id" in str(exc)
    else:
        raise AssertionError("sector readiness with unsorted rows should fail")


def test_build_prediction_committee_readiness_summarizes_member_contexts_by_committee() -> None:
    readiness = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[_vote_row()],
    )
    context = build_prediction_member_context(
        readiness.members[0],
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        ontology_features=_ready_features(),
        source_anchors=[_source_anchor()],
    )

    committees = build_prediction_committee_readiness(
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        member_contexts=[context],
    )

    assert committees.snapshot_id == "2026-04-25"
    assert committees.committee_count == 1
    assert committees.committees[0].committee_id == "HSEC"
    assert committees.committees[0].label == "Energy"
    assert committees.committees[0].member_count == 1
    assert committees.committees[0].ready_member_count == 1
    assert committees.committees[0].readiness_rate == 1
    assert committees.committees[0].sector_ids == ["energy"]
    assert committees.committees[0].source_count == 1
    assert len(committees.committees[0].source_keys) == 1


def test_prediction_committee_row_rejects_inconsistent_rates_and_source_paths() -> None:
    source_key = prediction_source_key(
        source_type="committee_membership",
        source_id="cm-1",
        url="https://api.congress.gov/v3/committee/house/HSEC?format=json",
        label="Committee membership",
    )

    try:
        PredictionCommitteeReadinessRowPayload(
            committee_id="HSEC",
            member_count=2,
            ready_member_count=1,
            partial_member_count=1,
            blocked_member_count=0,
            readiness_rate=1,
            vote_coverage_rate=0.5,
            source_count=1,
            source_keys=[source_key],
            source_context_paths=[prediction_source_context_path(source_key)],
            member_bioguide_ids=["A000001", "P000197"],
        )
    except ValueError as exc:
        assert "committee readiness_rate must match status counts" in str(exc)
    else:
        raise AssertionError("committee row with inconsistent readiness_rate should fail")

    try:
        PredictionCommitteeReadinessRowPayload(
            committee_id="HSEC",
            member_count=1,
            ready_member_count=1,
            partial_member_count=0,
            blocked_member_count=0,
            readiness_rate=1,
            vote_coverage_rate=1,
            source_count=1,
            source_keys=[source_key],
            source_context_paths=["prediction/source-context/stale.json"],
            member_bioguide_ids=["P000197"],
        )
    except ValueError as exc:
        assert "committee source_context_paths must match source_keys" in str(exc)
    else:
        raise AssertionError("committee row with stale source context path should fail")


def test_prediction_committee_row_rejects_unsorted_or_duplicate_lookup_lists() -> None:
    source_key = prediction_source_key(
        source_type="committee_membership",
        source_id="cm-1",
        url="https://api.congress.gov/v3/committee/house/HSEC?format=json",
        label="Committee membership",
    )

    try:
        PredictionCommitteeReadinessRowPayload(
            committee_id="HSEC",
            member_count=2,
            ready_member_count=2,
            partial_member_count=0,
            blocked_member_count=0,
            readiness_rate=1,
            vote_coverage_rate=1,
            sector_ids=["energy"],
            source_count=1,
            source_keys=[source_key],
            source_context_paths=[prediction_source_context_path(source_key)],
            member_bioguide_ids=["P000197", "A000001"],
        )
    except ValueError as exc:
        assert "member_bioguide_ids must be sorted and unique" in str(exc)
    else:
        raise AssertionError("committee row with unsorted members should fail")

    try:
        PredictionCommitteeReadinessRowPayload(
            committee_id="HSEC",
            member_count=1,
            ready_member_count=1,
            partial_member_count=0,
            blocked_member_count=0,
            readiness_rate=1,
            vote_coverage_rate=1,
            sector_ids=["energy"],
            source_count=2,
            source_keys=[source_key, source_key],
            source_context_paths=[
                prediction_source_context_path(source_key),
                prediction_source_context_path(source_key),
            ],
            member_bioguide_ids=["P000197"],
        )
    except ValueError as exc:
        assert "source_keys must be sorted and unique" in str(exc)
    else:
        raise AssertionError("committee row with duplicate source keys should fail")


def test_prediction_committee_readiness_rejects_unsorted_rows() -> None:
    row_a = PredictionCommitteeReadinessRowPayload(
        committee_id="AAAA",
        member_count=0,
        ready_member_count=0,
        partial_member_count=0,
        blocked_member_count=0,
        readiness_rate=0,
        vote_coverage_rate=0,
    )
    row_z = row_a.model_copy(update={"committee_id": "ZZZZ"})

    try:
        PredictionCommitteeReadinessPayload(
            snapshot_id="2026-04-25",
            snapshot_date=_SNAPSHOT_DATE,
            committee_count=2,
            committees=[row_z, row_a],
        )
    except ValueError as exc:
        assert "committees must be sorted by committee_id" in str(exc)
    else:
        raise AssertionError("committee readiness with unsorted rows should fail")


def test_build_prediction_committee_contexts_bundle_member_contexts_by_committee() -> None:
    readiness = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[_vote_row()],
    )
    member_context = build_prediction_member_context(
        readiness.members[0],
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        ontology_features=_ready_features(),
        source_anchors=[_source_anchor()],
    )

    contexts = build_prediction_committee_contexts(
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        member_contexts=[member_context],
    )

    assert len(contexts) == 1
    assert contexts[0].committee.committee_id == "HSEC"
    assert contexts[0].committee.sector_ids == ["energy"]
    assert [member.member_bioguide_id for member in contexts[0].members] == ["P000197"]


def test_prediction_committee_context_rejects_member_without_committee() -> None:
    readiness = build_prediction_readiness(
        snapshot_id="2026-04-25",
        snapshot_date=_SNAPSHOT_DATE,
        member_profiles=[_profile()],
        ontology_features={"P000197": _ready_features()},
        vote_rows=[_vote_row()],
    )
    member_context = build_prediction_member_context(
        readiness.members[0],
        snapshot_id=readiness.snapshot_id,
        snapshot_date=readiness.snapshot_date,
        ontology_features=_ready_features(),
        source_anchors=[_source_anchor()],
    )
    committee_row = PredictionCommitteeReadinessRowPayload(
        committee_id="HSFI",
        member_count=1,
        ready_member_count=1,
        partial_member_count=0,
        blocked_member_count=0,
        readiness_rate=1,
        vote_coverage_rate=1,
        sector_ids=["energy"],
        source_count=1,
        source_keys=member_context.source_keys,
        source_context_paths=member_context.source_context_paths,
        member_bioguide_ids=["P000197"],
    )

    try:
        PredictionCommitteeContextPayload(
            snapshot_id=readiness.snapshot_id,
            snapshot_date=readiness.snapshot_date,
            committee=committee_row,
            members=[member_context],
        )
    except ValueError as exc:
        assert "committee context members must include committee" in str(exc)
    else:
        raise AssertionError("committee context with unrelated member committee should fail")


def test_prediction_sector_row_rejects_inconsistent_rates_and_source_paths() -> None:
    source_key = prediction_source_key(
        source_type="committee_membership",
        source_id="cm-1",
        url="https://api.congress.gov/v3/committee/house/HSEC?format=json",
        label="Committee membership",
    )

    try:
        PredictionSectorReadinessRowPayload(
            sector_id="energy",
            member_count=2,
            ready_member_count=1,
            partial_member_count=1,
            blocked_member_count=0,
            readiness_rate=1,
            vote_coverage_rate=0.5,
            committee_jurisdiction_edge_count=1,
            holding_edge_count=1,
            transaction_edge_count=0,
            source_count=1,
            source_keys=[source_key],
            source_context_paths=[prediction_source_context_path(source_key)],
            member_bioguide_ids=["A000001", "P000197"],
        )
    except ValueError as exc:
        assert "sector readiness_rate must match status counts" in str(exc)
    else:
        raise AssertionError("sector row with inconsistent readiness_rate should fail")

    try:
        PredictionSectorReadinessRowPayload(
            sector_id="energy",
            member_count=1,
            ready_member_count=1,
            partial_member_count=0,
            blocked_member_count=0,
            readiness_rate=1,
            vote_coverage_rate=1,
            committee_jurisdiction_edge_count=1,
            holding_edge_count=1,
            transaction_edge_count=0,
            source_count=1,
            source_keys=[source_key],
            source_context_paths=["prediction/source-context/stale.json"],
            member_bioguide_ids=["P000197"],
        )
    except ValueError as exc:
        assert "sector source_context_paths must match source_keys" in str(exc)
    else:
        raise AssertionError("sector row with stale source context path should fail")
