"""Tests for shared evidence source-anchor policy helpers."""

from __future__ import annotations

import pytest

from src.evidence.source_anchor_policy import (
    all_required_source_anchor_urls_present,
    anchor_source_id,
    anchor_source_type,
    anchor_url,
    has_any_source_url,
    has_official_claim_source_anchor,
    has_https_source_url,
    is_official_source_url,
    is_https_source_url,
    official_source_url_count,
    primary_source_anchor,
    duplicate_source_anchor_keys,
    source_anchor_key,
    source_anchors_missing_required_urls,
    validate_evidence_card_policy,
)
from src.export.contracts import EvidenceBlock, EvidenceSection, SourceAnchor


def _anchor(
    url: str | None,
    *,
    source_type: str = "financial_disclosure",
    source_id: str = "fd-001",
) -> SourceAnchor:
    return SourceAnchor(
        source_type=source_type,
        source_id=source_id,
        url=url,
        label=source_type.replace("_", " ").title(),
    )


def test_is_https_source_url_accepts_absolute_https_url() -> None:
    assert (
        is_https_source_url("https://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd-001")
        is True
    )


def test_is_https_source_url_rejects_http_url() -> None:
    assert (
        is_https_source_url("http://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd-001")
        is False
    )


def test_is_https_source_url_rejects_relative_path() -> None:
    assert is_https_source_url("/disclosures/fd-001") is False


def test_has_https_source_url_requires_at_least_one_https_anchor() -> None:
    anchors = [
        _anchor(None),
        _anchor("https://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd-001"),
    ]
    assert has_https_source_url(anchors) is True


def test_has_https_source_url_false_when_all_anchors_lack_https_urls() -> None:
    anchors = [
        _anchor(None),
        _anchor("http://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd-001"),
    ]
    assert has_https_source_url(anchors) is False


def test_source_anchor_field_helpers_support_objects_and_dicts() -> None:
    object_anchor = _anchor(
        "https://api.congress.gov/v3/bill/119/hr/1?format=json",
        source_type="congress_bill",
        source_id="119-hr-1",
    )
    dict_anchor = object_anchor.model_dump(mode="json")

    assert anchor_source_type(object_anchor) == "congress_bill"
    assert anchor_source_id(object_anchor) == "119-hr-1"
    assert anchor_url(object_anchor) == "https://api.congress.gov/v3/bill/119/hr/1?format=json"
    assert anchor_source_type(dict_anchor) == "congress_bill"
    assert anchor_source_id(dict_anchor) == "119-hr-1"
    assert anchor_url(dict_anchor) == "https://api.congress.gov/v3/bill/119/hr/1?format=json"


def test_source_anchor_rejects_blank_identity_fields() -> None:
    with pytest.raises(ValueError):
        SourceAnchor(source_type="", source_id="fd-001", url=None, label="Disclosure")
    with pytest.raises(ValueError):
        SourceAnchor(source_type="financial_disclosure", source_id="", url=None, label="Disclosure")
    with pytest.raises(ValueError):
        SourceAnchor(source_type="financial_disclosure", source_id="fd-001", url=None, label="")
    with pytest.raises(ValueError):
        SourceAnchor(source_type="   ", source_id="fd-001", url=None, label="Disclosure")
    with pytest.raises(ValueError):
        SourceAnchor(
            source_type="financial_disclosure", source_id="   ", url=None, label="Disclosure"
        )
    with pytest.raises(ValueError):
        SourceAnchor(source_type="financial_disclosure", source_id="fd-001", url=None, label="   ")


def test_source_anchor_normalizes_surrounding_whitespace() -> None:
    anchor = SourceAnchor(
        source_type=" financial_disclosure ",
        source_id=" fd-001 ",
        url="  ",
        label=" Disclosure ",
    )

    assert anchor.source_type == "financial_disclosure"
    assert anchor.source_id == "fd-001"
    assert anchor.url is None
    assert anchor.label == "Disclosure"


def test_source_anchor_key_ignores_blank_raw_dict_identity_fields() -> None:
    assert (
        duplicate_source_anchor_keys(
            [
                {"source_type": "financial_disclosure", "source_id": " ", "url": None},
                {"source_type": "financial_disclosure", "source_id": " ", "url": None},
            ]
        )
        == []
    )


def test_source_anchor_key_normalizes_raw_dict_identity_fields() -> None:
    assert duplicate_source_anchor_keys(
        [
            {"source_type": " financial_disclosure ", "source_id": " fd-001 "},
            {"source_type": "financial_disclosure", "source_id": "fd-001"},
        ]
    ) == ["financial_disclosure:fd-001"]


def test_source_anchor_key_scopes_legislative_identity_fields() -> None:
    anchor = SourceAnchor(
        source_type="legislative_bill",
        source_id="2025-ab-12",
        jurisdiction_id="state_ca",
        legislative_body_id="ca_assembly",
        legislative_session_id="2025_regular",
        url="https://leginfo.legislature.ca.gov/faces/billTextClient.xhtml?bill_id=20250AB12",
        label="California bill",
    )

    assert (
        source_anchor_key(anchor) == "legislative_bill:state_ca:ca_assembly:2025_regular:2025-ab-12"
    )


def test_duplicate_source_anchor_keys_preserves_legislative_scope_collisions() -> None:
    assert (
        duplicate_source_anchor_keys(
            [
                {
                    "source_type": "legislative_bill",
                    "source_id": "2025-ab-12",
                    "jurisdiction_id": "state_ca",
                    "legislative_body_id": "ca_assembly",
                    "legislative_session_id": "2025_regular",
                },
                {
                    "source_type": "legislative_bill",
                    "source_id": "2025-ab-12",
                    "jurisdiction_id": "state_ny",
                    "legislative_body_id": "ny_assembly",
                    "legislative_session_id": "2025_regular",
                },
            ]
        )
        == []
    )


def test_duplicate_source_anchor_keys_detects_matching_legislative_scope() -> None:
    assert duplicate_source_anchor_keys(
        [
            {
                "source_type": "legislative_bill",
                "source_id": "2025-ab-12",
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
            },
            {
                "source_type": "legislative_bill",
                "source_id": "2025-ab-12",
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
            },
        ]
    ) == ["legislative_bill:state_ca:ca_assembly:2025_regular:2025-ab-12"]


def test_policy_collection_helpers_support_mixed_object_and_dict_anchors() -> None:
    object_anchor = _anchor(
        "https://api.congress.gov/v3/bill/119/hr/1?format=json",
        source_type="congress_bill",
        source_id="119-hr-1",
    )
    dict_anchor = _anchor(
        "https://example.invalid/context",
        source_type="rule_context",
        source_id="context-1",
    ).model_dump(mode="json")

    assert has_any_source_url([dict_anchor]) is True
    assert has_https_source_url([dict_anchor]) is True
    assert has_official_claim_source_anchor([dict_anchor, object_anchor]) is True


def test_duplicate_source_anchor_keys_returns_repeated_source_identities() -> None:
    anchors = [
        _anchor(
            "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd-001.pdf",
            source_id="fd-001",
        ),
        _anchor(
            "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd-001-amended.pdf",
            source_id="fd-001",
        ),
        _anchor(
            "https://api.congress.gov/v3/committee/house/HSEN?format=json",
            source_type="committee_membership",
            source_id="committee-1",
        ),
    ]

    assert duplicate_source_anchor_keys(anchors) == ["financial_disclosure:fd-001"]


def test_required_source_anchor_urls_present_when_claim_anchor_has_https_url() -> None:
    anchors = [
        _anchor("https://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd-001.pdf"),
        _anchor(
            "https://api.congress.gov/v3/committee/house/HSEN?format=json",
            source_type="committee_membership",
            source_id="committee-1",
        ),
    ]

    assert all_required_source_anchor_urls_present(anchors) is True
    assert source_anchors_missing_required_urls(anchors) == []


def test_required_source_anchor_urls_ignore_non_claim_metadata_anchor() -> None:
    anchors = [_anchor(None, source_type="rule_context", source_id="context-1")]

    assert all_required_source_anchor_urls_present(anchors) is True


def test_required_source_anchor_urls_reject_missing_disclosure_url() -> None:
    missing = _anchor(None)
    anchors = [
        missing,
        _anchor(
            "https://api.congress.gov/v3/committee/house/HSEN?format=json",
            source_type="committee_membership",
            source_id="committee-1",
        ),
    ]

    assert all_required_source_anchor_urls_present(anchors) is False
    assert source_anchors_missing_required_urls(anchors) == [missing]


def test_required_source_anchor_urls_reject_non_https_disclosure_url() -> None:
    missing = _anchor("http://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd-001")

    assert source_anchors_missing_required_urls([missing]) == [missing]


def test_required_source_anchor_urls_reject_unofficial_disclosure_url() -> None:
    missing = _anchor("https://not-official.invalid/disclosures/fd-001")

    assert source_anchors_missing_required_urls([missing]) == [missing]
    assert all_required_source_anchor_urls_present([missing]) is False


def test_is_official_source_url_accepts_house_and_senate_disclosure_hosts() -> None:
    assert (
        is_official_source_url(
            "financial_disclosure",
            "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd-001.pdf",
        )
        is True
    )
    assert (
        is_official_source_url(
            "financial_disclosure",
            "https://efdsearch.senate.gov/search/view/paper/fd-001/",
        )
        is True
    )


def test_is_official_source_url_accepts_fec_and_vote_hosts() -> None:
    assert (
        is_official_source_url(
            "fec_contribution",
            "https://www.fec.gov/data/receipts/individual-contributions/?contributor_id=c1",
        )
        is True
    )
    assert (
        is_official_source_url(
            "vote_event",
            "https://clerk.house.gov/Votes/2024123",
        )
        is True
    )
    assert (
        is_official_source_url(
            "vote_event",
            "https://www.senate.gov/legislative/LIS/roll_call_votes/vote1191/vote_119_1_00001.xml",
        )
        is True
    )
    assert (
        is_official_source_url(
            "congress_vote",
            "https://clerk.house.gov/Votes/2024123",
        )
        is True
    )


def test_is_official_source_url_accepts_committee_membership_congress_hosts() -> None:
    assert (
        is_official_source_url(
            "committee_membership",
            "https://api.congress.gov/v3/committee/house/HSEN?format=json",
        )
        is True
    )
    assert (
        is_official_source_url(
            "committee_membership",
            "https://www.congress.gov/committees/science-space-and-technology/hsci00",
        )
        is True
    )


def test_is_official_source_url_accepts_congress_bill_hosts() -> None:
    assert (
        is_official_source_url(
            "congress_bill",
            "https://api.congress.gov/v3/bill/119/hr/1?format=json",
        )
        is True
    )
    assert (
        is_official_source_url(
            "congress_bill",
            "https://www.congress.gov/bill/119th-congress/house-bill/1",
        )
        is True
    )


def test_is_official_source_url_accepts_legislative_bill_official_hosts() -> None:
    assert (
        is_official_source_url(
            "legislative_bill",
            "https://leginfo.legislature.ca.gov/faces/billNavClient.xhtml?bill_id=202520260AB1",
        )
        is True
    )
    assert (
        is_official_source_url(
            "legislative_bill",
            "https://capitol.texas.gov/BillLookup/History.aspx?LegSess=89R&Bill=HB1",
        )
        is True
    )


def test_is_official_source_url_accepts_legislative_vote_official_hosts() -> None:
    assert (
        is_official_source_url(
            "legislative_vote",
            "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB1",
        )
        is True
    )
    assert (
        is_official_source_url(
            "legislative_vote",
            "https://capitol.texas.gov/BillLookup/Actions.aspx?LegSess=89R&Bill=HB1",
        )
        is True
    )


def test_is_official_source_url_accepts_member_public_statement_hosts() -> None:
    assert (
        is_official_source_url(
            "public_statement",
            "https://pelosi.house.gov/news/press-releases/energy-statement",
        )
        is True
    )
    assert (
        is_official_source_url(
            "public_statement",
            "https://www.senate.gov/senators/member-statement.htm",
        )
        is True
    )


def test_is_official_source_url_rejects_unofficial_claim_source_host() -> None:
    assert (
        is_official_source_url("financial_disclosure", "https://not-official.invalid/fd/1") is False
    )
    assert is_official_source_url("congress_bill", "https://example.invalid/bill/119/hr/1") is False
    assert (
        is_official_source_url("legislative_bill", "https://example.invalid/bill/ca/ab/1") is False
    )
    assert (
        is_official_source_url("legislative_vote", "https://example.invalid/vote/ca/ab/1") is False
    )
    assert is_official_source_url("congress_vote", "https://example.invalid/vote/119/1") is False
    assert is_official_source_url("legislative_bill", "https://www.epa.gov/bill/ca/ab/1") is False
    assert is_official_source_url("legislative_vote", "https://www.epa.gov/vote/ca/ab/1") is False


def test_required_source_anchor_urls_reject_missing_committee_membership_url() -> None:
    missing = _anchor(None, source_type="committee_membership", source_id="committee-1")

    assert source_anchors_missing_required_urls([missing]) == [missing]
    assert all_required_source_anchor_urls_present([missing]) is False


def test_required_source_anchor_urls_reject_missing_congress_bill_url() -> None:
    missing = _anchor(None, source_type="congress_bill", source_id="119-hr-1")

    assert source_anchors_missing_required_urls([missing]) == [missing]
    assert all_required_source_anchor_urls_present([missing]) is False


def test_required_source_anchor_urls_reject_unofficial_committee_membership_url() -> None:
    missing = _anchor(
        "https://not-official.invalid/committees/science",
        source_type="committee_membership",
        source_id="committee-1",
    )

    assert source_anchors_missing_required_urls([missing]) == [missing]


def test_is_official_source_url_rejects_house_disclosure_url_without_pdf_suffix() -> None:
    assert (
        is_official_source_url(
            "financial_disclosure",
            "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd-001",
        )
        is False
    )


def test_is_official_source_url_rejects_senate_disclosure_url_on_wrong_path() -> None:
    assert (
        is_official_source_url(
            "financial_disclosure",
            "https://efdsearch.senate.gov/filing/99",
        )
        is False
    )


def test_required_source_anchor_urls_reject_malformed_official_disclosure_path() -> None:
    missing = _anchor("https://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd-001")

    assert source_anchors_missing_required_urls([missing]) == [missing]


def test_official_source_url_count_counts_only_official_anchor_urls() -> None:
    anchors = [
        _anchor("https://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd-001.pdf"),
        _anchor("https://not-official.invalid/fd-002", source_id="fd-002"),
        _anchor(
            "https://api.congress.gov/v3/committee/house/HSEN?format=json",
            source_type="committee_membership",
            source_id="committee-1",
        ),
    ]

    assert official_source_url_count(anchors) == 2


def test_official_source_url_count_counts_legislative_bill_https_urls() -> None:
    anchors = [
        _anchor(
            "https://leginfo.legislature.ca.gov/faces/billNavClient.xhtml?bill_id=202520260AB1",
            source_type="legislative_bill",
            source_id="state_ca-2025-ab-1",
        ),
        _anchor(
            "http://leginfo.legislature.ca.gov/faces/billNavClient.xhtml?bill_id=202520260AB2",
            source_type="legislative_bill",
            source_id="state_ca-2025-ab-2",
        ),
    ]

    assert official_source_url_count(anchors) == 1


def test_official_source_url_count_ignores_non_claim_bearing_generic_https_urls() -> None:
    anchors = [
        _anchor(
            "https://example.com/context",
            source_type="generic_context",
            source_id="context-1",
        ),
    ]

    assert official_source_url_count(anchors) == 0


def test_primary_source_anchor_prefers_official_url_before_generic_https() -> None:
    unofficial = _anchor("https://not-official.invalid/fd-002", source_id="fd-002")
    official = _anchor(
        "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd-001.pdf",
        source_id="fd-001",
    )

    assert primary_source_anchor([unofficial, official]) == official


def test_primary_source_anchor_falls_back_to_generic_https_url() -> None:
    generic = _anchor(
        "https://www.congress.gov/committees/science",
        source_type="committee_membership",
        source_id="committee-1",
    )

    assert primary_source_anchor([_anchor(None), generic]) == generic


class _PolicyCard:
    score_delta = -5.0
    blocks = [EvidenceBlock(section=EvidenceSection.FACT, text="Claimed fact.")]
    source_anchors = [
        SourceAnchor(
            source_type="rule_context",
            source_id="context-1",
            url="https://example.com/context",
            label="Rule context",
        )
    ]


def test_nonzero_evidence_policy_requires_official_claim_source_anchor() -> None:
    with pytest.raises(ValueError, match="official source"):
        validate_evidence_card_policy(_PolicyCard())


def test_nonzero_evidence_policy_rejects_duplicate_source_anchor_keys() -> None:
    class DuplicateSourceCard:
        score_delta = -5.0
        blocks = [EvidenceBlock(section=EvidenceSection.FACT, text="Claimed fact.")]
        source_anchors = [
            _anchor(
                "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd-001.pdf",
                source_id="fd-001",
            ),
            _anchor(
                "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd-001-amended.pdf",
                source_id="fd-001",
            ),
        ]

    with pytest.raises(ValueError, match="duplicate source anchors"):
        validate_evidence_card_policy(DuplicateSourceCard())
