from __future__ import annotations

from src.export.contracts import SourceAnchor
from src.prediction.source_anchors import (
    dedupe_prediction_source_anchors,
    source_anchor_identity,
)


def _anchor(
    source_type: str,
    source_id: str,
    *,
    url: str | None = None,
    label: str = "Source",
    jurisdiction_id: str | None = None,
    legislative_body_id: str | None = None,
    legislative_session_id: str | None = None,
) -> SourceAnchor:
    return SourceAnchor(
        source_type=source_type,
        source_id=source_id,
        jurisdiction_id=jurisdiction_id,
        legislative_body_id=legislative_body_id,
        legislative_session_id=legislative_session_id,
        url=url,
        label=label,
    )


def test_source_anchor_identity_uses_canonical_source_fields() -> None:
    anchor = _anchor(
        "financial_disclosure",
        "fd-1",
        url="https://example.com/one",
        label="First label",
    )

    assert source_anchor_identity(anchor) == ("financial_disclosure", "fd-1")


def test_source_anchor_identity_uses_normalized_source_fields() -> None:
    anchor = _anchor(
        " financial_disclosure ",
        " fd-1 ",
        url=" https://example.com/one ",
        label=" Source ",
    )

    assert source_anchor_identity(anchor) == ("financial_disclosure", "fd-1")
    assert anchor.url == "https://example.com/one"
    assert anchor.label == "Source"


def test_legislative_source_anchor_identity_includes_scope() -> None:
    anchor = _anchor(
        "legislative_bill",
        "2025-ab-12",
        jurisdiction_id="state_ca",
        legislative_body_id="ca_assembly",
        legislative_session_id="2025_regular",
    )

    assert source_anchor_identity(anchor) == (
        "legislative_bill",
        "2025-ab-12",
        "state_ca",
        "ca_assembly",
        "2025_regular",
    )


def test_dedupe_prediction_source_anchors_collapses_label_variants() -> None:
    deduped = dedupe_prediction_source_anchors(
        [
            _anchor("financial_disclosure", "fd-1", label="Long disclosure label"),
            _anchor("financial_disclosure", "fd-1", label="Short"),
        ]
    )

    assert [(anchor.source_type, anchor.source_id) for anchor in deduped] == [
        ("financial_disclosure", "fd-1")
    ]
    assert deduped[0].label == "Short"


def test_dedupe_prediction_source_anchors_preserves_legislative_scope_collisions() -> None:
    deduped = dedupe_prediction_source_anchors(
        [
            _anchor(
                "legislative_bill",
                "2025-ab-12",
                jurisdiction_id="state_ca",
                legislative_body_id="ca_assembly",
                legislative_session_id="2025_regular",
                label="California bill",
            ),
            _anchor(
                "legislative_bill",
                "2025-ab-12",
                jurisdiction_id="state_ny",
                legislative_body_id="ny_assembly",
                legislative_session_id="2025_regular",
                label="New York bill",
            ),
        ]
    )

    assert [(anchor.jurisdiction_id, anchor.source_id) for anchor in deduped] == [
        ("state_ca", "2025-ab-12"),
        ("state_ny", "2025-ab-12"),
    ]


def test_dedupe_prediction_source_anchors_collapses_matching_legislative_scope() -> None:
    deduped = dedupe_prediction_source_anchors(
        [
            _anchor(
                "legislative_bill",
                "2025-ab-12",
                jurisdiction_id="state_ca",
                legislative_body_id="ca_assembly",
                legislative_session_id="2025_regular",
                label="Long California bill label",
            ),
            _anchor(
                "legislative_bill",
                "2025-ab-12",
                jurisdiction_id="state_ca",
                legislative_body_id="ca_assembly",
                legislative_session_id="2025_regular",
                label="Short",
            ),
        ]
    )

    assert [(anchor.jurisdiction_id, anchor.source_id) for anchor in deduped] == [
        ("state_ca", "2025-ab-12")
    ]
    assert deduped[0].label == "Short"


def test_dedupe_prediction_source_anchors_keeps_non_legislative_identity_stable() -> None:
    deduped = dedupe_prediction_source_anchors(
        [
            _anchor(
                "financial_disclosure",
                "fd-1",
                jurisdiction_id="state_ca",
                legislative_body_id="ca_assembly",
                legislative_session_id="2025_regular",
                label="Scoped duplicate",
            ),
            _anchor("financial_disclosure", "fd-1", label="Short"),
        ]
    )

    assert [(anchor.source_type, anchor.source_id) for anchor in deduped] == [
        ("financial_disclosure", "fd-1")
    ]
    assert deduped[0].label == "Short"


def test_dedupe_prediction_source_anchors_prefers_official_urls() -> None:
    deduped = dedupe_prediction_source_anchors(
        [
            _anchor(
                "committee_membership",
                "cm-1",
                url="https://example.com/committee",
                label="Unofficial",
            ),
            _anchor(
                "committee_membership",
                "cm-1",
                url="https://api.congress.gov/v3/committee/house/HSEC?format=json",
                label="Official",
            ),
        ]
    )

    assert deduped[0].label == "Official"
    assert deduped[0].url == "https://api.congress.gov/v3/committee/house/HSEC?format=json"


def test_dedupe_prediction_source_anchors_prefers_any_url_over_no_url() -> None:
    deduped = dedupe_prediction_source_anchors(
        [
            _anchor("financial_disclosure", "fd-1", label="No URL"),
            _anchor(
                "financial_disclosure",
                "fd-1",
                url="https://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd.pdf",
                label="URL",
            ),
        ]
    )

    assert deduped[0].label == "URL"


def test_dedupe_prediction_source_anchors_returns_identity_sorted_rows() -> None:
    deduped = dedupe_prediction_source_anchors(
        [
            _anchor("financial_disclosure", "fd-2", label="FD 2"),
            _anchor("committee_membership", "cm-1", label="CM 1"),
            _anchor("financial_disclosure", "fd-1", label="FD 1"),
        ]
    )

    assert [(anchor.source_type, anchor.source_id) for anchor in deduped] == [
        ("committee_membership", "cm-1"),
        ("financial_disclosure", "fd-1"),
        ("financial_disclosure", "fd-2"),
    ]
