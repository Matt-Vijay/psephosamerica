"""Tests for src/runtime/disclosures_bundle_validate.py — semantic validation.

No network calls.  No DB.  Pure in-memory bundle construction.
"""

from __future__ import annotations

import pytest

from src.runtime.disclosures_bundle import (
    DisclosureArtifactEntry,
    DisclosuresBundle,
    HouseBundledIndexRow,
    SenateBundledIndexRow,
    disclosures_bundle_from_dict,
)
from src.runtime.disclosures_bundle_validate import (
    DisclosuresBundleValidationError,
    check_doc_id_source_record_id_coherence,
    check_duplicate_entries,
    check_index_row_chamber_shape,
    check_source_url_chamber_coherence,
    check_source_slug_chamber_coherence,
    validate_disclosures_bundle,
)
from src.runtime.sources import HOUSE_DISCLOSURES, SENATE_DISCLOSURES

_SHA256 = "a" * 64


# ---------------------------------------------------------------------------
# Canonical payload builders
# ---------------------------------------------------------------------------


def _house_entry_dict(
    source_record_id: str = "12345",
    storage_uri: str | None = None,
    source_slug: str = "house_disclosures",
    doc_id: str | None = None,
) -> dict:
    return {
        "source_record_id": source_record_id,
        "chamber": "house",
        "filing_year": 2024,
        "storage_uri": storage_uri or f"house/2024/{source_record_id}.pdf",
        "source_url": (
            f"https://disclosures.house.gov/public_disc/financial-pdfs/2024/{source_record_id}.pdf"
        ),
        "source_slug": source_slug,
        "artifact_kind": "pdf",
        "sha256": _SHA256,
        "index_row": {
            "last_name": "Smith",
            "first_name": "John",
            "suffix": "",
            "raw_filing_type": "O",
            "state_dst": "CA08",
            "filing_date": "2024-01-15",
            "doc_id": doc_id if doc_id is not None else source_record_id,
            "filing_kind": "annual",
        },
    }


def _senate_entry_dict(
    source_record_id: str = "uuid-xyz",
    source_slug: str = "senate_disclosures",
    doc_id: str | None = None,
) -> dict:
    return {
        "source_record_id": source_record_id,
        "chamber": "senate",
        "filing_year": 2024,
        "storage_uri": f"senate/2024/{source_record_id}.pdf",
        "source_url": f"https://efdsearch.senate.gov/search/view/paper/{source_record_id}/",
        "source_slug": source_slug,
        "artifact_kind": "pdf",
        "sha256": _SHA256,
        "index_row": {
            "first_name": "Jane",
            "last_name": "Doe",
            "office": "Senator, TX",
            "report_type": "Annual Report for CY2023",
            "date_filed": "01/15/2024",
            "doc_id": doc_id if doc_id is not None else source_record_id,
        },
    }


def _bundle(*entries: dict) -> DisclosuresBundle:
    return disclosures_bundle_from_dict({"artifacts": list(entries)})


# ---------------------------------------------------------------------------
# check_source_slug_chamber_coherence
# ---------------------------------------------------------------------------


class TestCheckSourceSlugChamberCoherence:
    def test_valid_house_slug_no_violations(self) -> None:
        bundle = _bundle(_house_entry_dict(source_slug="house_disclosures"))
        assert check_source_slug_chamber_coherence(bundle) == []

    def test_valid_senate_slug_no_violations(self) -> None:
        bundle = _bundle(_senate_entry_dict(source_slug="senate_disclosures"))
        assert check_source_slug_chamber_coherence(bundle) == []

    def test_house_wrong_slug_reports_expected(self) -> None:
        bundle = _bundle(_house_entry_dict(source_slug="senate_disclosures"))
        violations = check_source_slug_chamber_coherence(bundle)
        assert len(violations) == 1
        assert HOUSE_DISCLOSURES.slug in violations[0]
        assert SENATE_DISCLOSURES.slug in violations[0]

    def test_senate_wrong_slug_reports_expected(self) -> None:
        bundle = _bundle(_senate_entry_dict(source_slug="house_disclosures"))
        violations = check_source_slug_chamber_coherence(bundle)
        assert len(violations) == 1
        assert SENATE_DISCLOSURES.slug in violations[0]

    def test_both_wrong_two_violations(self) -> None:
        bundle = _bundle(
            _house_entry_dict(source_slug="wrong"),
            _senate_entry_dict(source_slug="wrong"),
        )
        assert len(check_source_slug_chamber_coherence(bundle)) == 2

    def test_mixed_valid_and_invalid_one_violation(self) -> None:
        bundle = _bundle(
            _house_entry_dict(source_slug="house_disclosures"),
            _senate_entry_dict(source_slug="bad_slug"),
        )
        violations = check_source_slug_chamber_coherence(bundle)
        assert len(violations) == 1
        assert "artifacts[1]" in violations[0]

    def test_empty_bundle_no_violations(self) -> None:
        assert check_source_slug_chamber_coherence(_bundle()) == []

    def test_violation_includes_entry_index(self) -> None:
        bundle = _bundle(
            _house_entry_dict(source_record_id="A"),
            _house_entry_dict(source_record_id="B", source_slug="wrong"),
        )
        violations = check_source_slug_chamber_coherence(bundle)
        assert "artifacts[1]" in violations[0]


# ---------------------------------------------------------------------------
# check_doc_id_source_record_id_coherence
# ---------------------------------------------------------------------------


class TestCheckDocIdSourceRecordIdCoherence:
    def test_matching_house_ids_no_violations(self) -> None:
        bundle = _bundle(_house_entry_dict(source_record_id="12345"))
        assert check_doc_id_source_record_id_coherence(bundle) == []

    def test_matching_senate_ids_no_violations(self) -> None:
        bundle = _bundle(_senate_entry_dict(source_record_id="uuid-abc"))
        assert check_doc_id_source_record_id_coherence(bundle) == []

    def test_house_mismatch_reports_both_values(self) -> None:
        bundle = _bundle(_house_entry_dict(source_record_id="12345", doc_id="99999"))
        violations = check_doc_id_source_record_id_coherence(bundle)
        assert len(violations) == 1
        assert "12345" in violations[0]
        assert "99999" in violations[0]

    def test_senate_mismatch_violation(self) -> None:
        bundle = _bundle(_senate_entry_dict(source_record_id="abc", doc_id="xyz"))
        assert len(check_doc_id_source_record_id_coherence(bundle)) == 1

    def test_first_entry_ok_second_bad(self) -> None:
        bundle = _bundle(
            _house_entry_dict(source_record_id="A"),
            _house_entry_dict(source_record_id="B", doc_id="C"),
        )
        violations = check_doc_id_source_record_id_coherence(bundle)
        assert len(violations) == 1
        assert "artifacts[1]" in violations[0]

    def test_multiple_mismatches_all_reported(self) -> None:
        bundle = _bundle(
            _house_entry_dict(source_record_id="A", doc_id="X"),
            _senate_entry_dict(source_record_id="B", doc_id="Y"),
        )
        assert len(check_doc_id_source_record_id_coherence(bundle)) == 2

    def test_empty_bundle_no_violations(self) -> None:
        assert check_doc_id_source_record_id_coherence(_bundle()) == []


# ---------------------------------------------------------------------------
# check_duplicate_entries
# ---------------------------------------------------------------------------


class TestCheckDuplicateEntries:
    def test_unique_ids_no_violations(self) -> None:
        bundle = _bundle(
            _house_entry_dict(source_record_id="A"),
            _senate_entry_dict(source_record_id="B"),
        )
        assert check_duplicate_entries(bundle) == []

    def test_duplicate_source_record_id_one_violation(self) -> None:
        bundle = _bundle(
            _house_entry_dict(source_record_id="DUP"),
            _house_entry_dict(source_record_id="DUP"),
        )
        violations = check_duplicate_entries(bundle)
        assert len(violations) == 1
        assert "DUP" in violations[0]

    def test_duplicate_violation_names_second_index(self) -> None:
        bundle = _bundle(
            _house_entry_dict(source_record_id="DUP"),
            _house_entry_dict(source_record_id="DUP"),
        )
        violations = check_duplicate_entries(bundle)
        assert "artifacts[1]" in violations[0]

    def test_first_occurrence_index_referenced(self) -> None:
        bundle = _bundle(
            _house_entry_dict(source_record_id="ORIG"),
            _house_entry_dict(source_record_id="ORIG"),
        )
        violations = check_duplicate_entries(bundle)
        assert "first seen at index 0" in violations[0]

    def test_triple_duplicate_reports_two_violations(self) -> None:
        bundle = _bundle(
            _house_entry_dict(source_record_id="X"),
            _house_entry_dict(source_record_id="X"),
            _house_entry_dict(source_record_id="X"),
        )
        assert len(check_duplicate_entries(bundle)) == 2

    def test_two_separate_duplicate_pairs(self) -> None:
        bundle = _bundle(
            _house_entry_dict(source_record_id="P"),
            _house_entry_dict(source_record_id="P"),
            _senate_entry_dict(source_record_id="Q"),
            _senate_entry_dict(source_record_id="Q"),
        )
        assert len(check_duplicate_entries(bundle)) == 2

    def test_empty_bundle_no_violations(self) -> None:
        assert check_duplicate_entries(_bundle()) == []


# ---------------------------------------------------------------------------
# check_index_row_chamber_shape
# ---------------------------------------------------------------------------


class TestCheckIndexRowChamberShape:
    def test_house_chamber_house_row_ok(self) -> None:
        bundle = _bundle(_house_entry_dict())
        assert check_index_row_chamber_shape(bundle) == []

    def test_senate_chamber_senate_row_ok(self) -> None:
        bundle = _bundle(_senate_entry_dict())
        assert check_index_row_chamber_shape(bundle) == []

    def test_house_chamber_senate_row_violation(self) -> None:
        senate_row = SenateBundledIndexRow(
            first_name="Jane",
            last_name="Doe",
            office="Senator, TX",
            report_type="Annual",
            date_filed="01/01/2024",
            doc_id="99",
        )
        entry = DisclosureArtifactEntry(
            source_record_id="99",
            chamber="house",
            filing_year=2024,
            storage_uri="house/2024/99.pdf",
            source_url="https://disclosures.house.gov/public_disc/ptr-pdfs/2024/99.pdf",
            source_slug="house_disclosures",
            artifact_kind="pdf",
            sha256=_SHA256,
            index_row=senate_row,
        )
        bundle = DisclosuresBundle(artifacts=(entry,))
        violations = check_index_row_chamber_shape(bundle)
        assert len(violations) == 1
        assert "SenateBundledIndexRow" in violations[0]
        assert "house" in violations[0]

    def test_senate_chamber_house_row_violation(self) -> None:
        house_row = HouseBundledIndexRow(
            last_name="Smith",
            first_name="John",
            suffix="",
            raw_filing_type="O",
            state_dst="CA08",
            filing_date="2024-01-15",
            doc_id="77",
            filing_kind="annual",
        )
        entry = DisclosureArtifactEntry(
            source_record_id="77",
            chamber="senate",
            filing_year=2024,
            storage_uri="senate/2024/77.pdf",
            source_url="https://efdsearch.senate.gov/search/view/paper/77/",
            source_slug="senate_disclosures",
            artifact_kind="pdf",
            sha256=_SHA256,
            index_row=house_row,
        )
        bundle = DisclosuresBundle(artifacts=(entry,))
        violations = check_index_row_chamber_shape(bundle)
        assert len(violations) == 1
        assert "HouseBundledIndexRow" in violations[0]
        assert "senate" in violations[0]

    def test_violation_includes_entry_index(self) -> None:
        senate_row = SenateBundledIndexRow(
            first_name="X",
            last_name="Y",
            office="Senator, CA",
            report_type="Annual",
            date_filed="01/01/2024",
            doc_id="5",
        )
        entry = DisclosureArtifactEntry(
            source_record_id="5",
            chamber="house",
            filing_year=2024,
            storage_uri="house/2024/5.pdf",
            source_url="https://disclosures.house.gov/public_disc/ptr-pdfs/2024/5.pdf",
            source_slug="house_disclosures",
            artifact_kind="pdf",
            sha256=_SHA256,
            index_row=senate_row,
        )
        bundle = DisclosuresBundle(artifacts=(entry,))
        violations = check_index_row_chamber_shape(bundle)
        assert "artifacts[0]" in violations[0]

    def test_empty_bundle_no_violations(self) -> None:
        assert check_index_row_chamber_shape(_bundle()) == []


# ---------------------------------------------------------------------------
# check_source_url_chamber_coherence
# ---------------------------------------------------------------------------


class TestCheckSourceUrlChamberCoherence:
    def test_valid_house_url_no_violations(self) -> None:
        bundle = _bundle(_house_entry_dict())
        assert check_source_url_chamber_coherence(bundle) == []

    def test_valid_senate_url_no_violations(self) -> None:
        bundle = _bundle(_senate_entry_dict())
        assert check_source_url_chamber_coherence(bundle) == []

    def test_direct_house_entry_rejects_non_official_url(self) -> None:
        entry = DisclosureArtifactEntry(
            source_record_id="99",
            chamber="house",
            filing_year=2024,
            storage_uri="house/2024/99.pdf",
            source_url="https://example.com/public_disc/ptr-pdfs/2024/99.pdf",
            source_slug="house_disclosures",
            artifact_kind="pdf",
            sha256=_SHA256,
            index_row=HouseBundledIndexRow(
                last_name="Smith",
                first_name="John",
                suffix="",
                raw_filing_type="O",
                state_dst="CA08",
                filing_date="2024-01-15",
                doc_id="99",
                filing_kind="annual",
            ),
        )
        violations = check_source_url_chamber_coherence(DisclosuresBundle((entry,)))
        assert len(violations) == 1
        assert "artifacts[0]" in violations[0]
        assert "source_url" in violations[0]

    def test_direct_senate_entry_rejects_house_url(self) -> None:
        entry = DisclosureArtifactEntry(
            source_record_id="uuid-xyz",
            chamber="senate",
            filing_year=2024,
            storage_uri="senate/2024/uuid-xyz.pdf",
            source_url="https://disclosures.house.gov/public_disc/ptr-pdfs/2024/uuid-xyz.pdf",
            source_slug="senate_disclosures",
            artifact_kind="pdf",
            sha256=_SHA256,
            index_row=SenateBundledIndexRow(
                first_name="Jane",
                last_name="Doe",
                office="Senator, TX",
                report_type="Annual",
                date_filed="01/01/2024",
                doc_id="uuid-xyz",
            ),
        )
        violations = check_source_url_chamber_coherence(DisclosuresBundle((entry,)))
        assert len(violations) == 1
        assert "source_url" in violations[0]

    def test_house_source_url_doc_id_must_match_source_record_id(self) -> None:
        entry = _house_entry_dict(source_record_id="99")
        entry["source_url"] = (
            "https://disclosures.house.gov/public_disc/financial-pdfs/2024/100.pdf"
        )
        violations = check_source_url_chamber_coherence(_bundle(entry))
        assert len(violations) == 1
        assert "source_record_id='99'" in violations[0]
        assert "source_url document id='100'" in violations[0]

    def test_house_source_url_year_must_match_filing_year(self) -> None:
        entry = _house_entry_dict(source_record_id="99")
        entry["source_url"] = "https://disclosures.house.gov/public_disc/financial-pdfs/2023/99.pdf"
        violations = check_source_url_chamber_coherence(_bundle(entry))
        assert len(violations) == 1
        assert "filing_year=2024" in violations[0]
        assert "source_url year=2023" in violations[0]

    def test_house_source_url_kind_must_match_index_row_filing_kind(self) -> None:
        entry = _house_entry_dict(source_record_id="99")
        entry["source_url"] = "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/99.pdf"
        violations = check_source_url_chamber_coherence(_bundle(entry))
        assert len(violations) == 1
        assert "filing_kind='annual'" in violations[0]
        assert "source_url kind='ptr'" in violations[0]

    def test_senate_source_url_doc_id_must_match_source_record_id(self) -> None:
        entry = _senate_entry_dict(source_record_id="uuid-xyz")
        entry["source_url"] = "https://efdsearch.senate.gov/search/view/paper/other-doc/"
        violations = check_source_url_chamber_coherence(_bundle(entry))
        assert len(violations) == 1
        assert "source_record_id='uuid-xyz'" in violations[0]
        assert "source_url document id='other-doc'" in violations[0]

    def test_validate_disclosures_bundle_includes_source_url_violation(self) -> None:
        entry = DisclosureArtifactEntry(
            source_record_id="99",
            chamber="house",
            filing_year=2024,
            storage_uri="house/2024/99.pdf",
            source_url="https://evil.example/public_disc/ptr-pdfs/2024/99.pdf",
            source_slug=HOUSE_DISCLOSURES.slug,
            artifact_kind="pdf",
            sha256=_SHA256,
            index_row=HouseBundledIndexRow(
                last_name="Smith",
                first_name="John",
                suffix="",
                raw_filing_type="O",
                state_dst="CA08",
                filing_date="2024-01-15",
                doc_id="99",
                filing_kind="annual",
            ),
        )
        with pytest.raises(ValueError, match="source_url"):
            validate_disclosures_bundle(DisclosuresBundle((entry,)))

    def test_empty_bundle_no_violations(self) -> None:
        assert check_source_url_chamber_coherence(_bundle()) == []


# ---------------------------------------------------------------------------
# validate_disclosures_bundle — aggregation
# ---------------------------------------------------------------------------


class TestValidateDisclosuresBundle:
    def test_valid_bundle_no_exception(self) -> None:
        bundle = _bundle(_house_entry_dict(), _senate_entry_dict())
        validate_disclosures_bundle(bundle)  # must not raise

    def test_empty_bundle_no_exception(self) -> None:
        validate_disclosures_bundle(_bundle())  # must not raise

    def test_slug_violation_raises_value_error(self) -> None:
        bundle = _bundle(_house_entry_dict(source_slug="wrong_slug"))
        with pytest.raises(ValueError, match="validation failed"):
            validate_disclosures_bundle(bundle)

    def test_doc_id_mismatch_raises_value_error(self) -> None:
        bundle = _bundle(_house_entry_dict(source_record_id="A", doc_id="B"))
        with pytest.raises(ValueError, match="validation failed"):
            validate_disclosures_bundle(bundle)

    def test_duplicate_raises_value_error(self) -> None:
        bundle = _bundle(
            _house_entry_dict(source_record_id="DUP"),
            _house_entry_dict(source_record_id="DUP"),
        )
        with pytest.raises(ValueError, match="validation failed"):
            validate_disclosures_bundle(bundle)

    def test_multiple_violations_all_in_single_error(self) -> None:
        # Entry with both wrong slug and mismatched doc_id
        bundle = _bundle(
            _house_entry_dict(source_slug="bad_slug", source_record_id="X", doc_id="Y"),
        )
        with pytest.raises(DisclosuresBundleValidationError) as exc_info:
            validate_disclosures_bundle(bundle)
        msg = str(exc_info.value)
        assert "source_slug" in msg
        assert "doc_id" in msg
        assert exc_info.value.violations == (
            "artifacts[0]: chamber='house' expects source_slug='house-disclosures', got 'bad_slug'",
            "artifacts[0]: source_record_id='X' but index_row.doc_id='Y'",
        )

    def test_error_message_lists_violations_as_bullets(self) -> None:
        bundle = _bundle(_house_entry_dict(source_slug="wrong"))
        with pytest.raises(DisclosuresBundleValidationError) as exc_info:
            validate_disclosures_bundle(bundle)
        assert "  - " in str(exc_info.value)
        assert exc_info.value.violations == (
            "artifacts[0]: chamber='house' expects source_slug='house-disclosures', got 'wrong'",
        )
