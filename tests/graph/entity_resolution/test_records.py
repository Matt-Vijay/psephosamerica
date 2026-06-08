from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from src.graph.entity_resolution.names import PersonName
from src.graph.entity_resolution.records import ExternalId, SourceRecord
from src.graph.provenance import ProvenanceEnvelope


def _prov(**overrides: object) -> ProvenanceEnvelope:
    base: dict[str, object] = {
        "source_url": "https://www.fec.gov/data/candidate/H0CA12345/",
        "content_sha256": "b" * 64,
        "first_observed_at": datetime(2024, 1, 10, 12, 0, tzinfo=UTC),
        "valid_from": date(2024, 1, 1),
        "known_at": datetime(2024, 1, 5, tzinfo=UTC),
    }
    base.update(overrides)
    return ProvenanceEnvelope(**base)  # type: ignore[arg-type]


def _record(**overrides: object) -> SourceRecord:
    base: dict[str, object] = {
        "source_system": "fec",
        "source_record_id": "H0CA12345",
        "entity_type": "person",
        "display_name": "Jane M. Doe",
        "external_ids": [{"system": "fec_candidate", "value": "H0CA12345"}],
        "jurisdiction": "us",
        "provenance": _prov(),
    }
    base.update(overrides)
    return SourceRecord(**base)  # type: ignore[arg-type]


# ── ExternalId ─────────────────────────────────────────────────────


def test_external_id_normalizes_system_and_strips_value() -> None:
    ext = ExternalId(system="  FEC_Candidate ", value="  H0CA12345 ")
    assert ext.system == "fec_candidate"
    assert ext.value == "H0CA12345"


def test_external_id_canonical_key_is_casefolded() -> None:
    assert ExternalId(system="bioguide", value="P000197").canonical_key == "bioguide:p000197"


def test_external_ids_agree_case_insensitively() -> None:
    a = ExternalId(system="bioguide", value="P000197")
    b = ExternalId(system="Bioguide", value="p000197")
    assert a.agrees_with(b) is True


def test_external_ids_disagree_on_value() -> None:
    a = ExternalId(system="bioguide", value="P000197")
    b = ExternalId(system="bioguide", value="P000198")
    assert a.agrees_with(b) is False


def test_external_ids_disagree_on_system() -> None:
    a = ExternalId(system="bioguide", value="P000197")
    b = ExternalId(system="fec_candidate", value="P000197")
    assert a.agrees_with(b) is False


@pytest.mark.parametrize("blank", ["", "   "])
def test_external_id_rejects_blank(blank: str) -> None:
    with pytest.raises(ValidationError):
        ExternalId(system=blank, value="x")
    with pytest.raises(ValidationError):
        ExternalId(system="x", value=blank)


def test_external_id_non_string_fields_rejected() -> None:
    # Non-string inputs fall through the normalizers to pydantic type checks.
    with pytest.raises(ValidationError):
        ExternalId(system=123, value="x")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ExternalId(system="x", value=123)  # type: ignore[arg-type]


# ── SourceRecord identity ──────────────────────────────────────────


def test_record_id_is_deterministic_and_prefixed() -> None:
    rid = _record().record_id
    assert rid.startswith("sr-")
    assert _record().record_id == rid


def test_record_id_changes_with_source_identity() -> None:
    a = _record(source_record_id="H0CA12345")
    b = _record(source_record_id="H0CA99999")
    assert a.record_id != b.record_id


def test_record_id_changes_with_entity_type() -> None:
    person = _record(entity_type="person")
    org = _record(entity_type="org", display_name="Acme PAC", external_ids=[])
    assert person.record_id != org.record_id


# ── SourceRecord validation ────────────────────────────────────────


@pytest.mark.parametrize("field", ["source_system", "source_record_id", "display_name"])
def test_blank_required_fields_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        _record(**{field: "   "})


def test_bad_entity_type_rejected() -> None:
    with pytest.raises(ValidationError):
        _record(entity_type="robot")


def test_non_string_required_field_rejected() -> None:
    with pytest.raises(ValidationError):
        _record(source_system=123)


def test_non_string_optional_field_rejected() -> None:
    with pytest.raises(ValidationError):
        _record(jurisdiction=123)


def test_non_sequence_external_ids_rejected() -> None:
    with pytest.raises(ValidationError):
        _record(external_ids="not-a-list")


def test_optional_context_blanks_become_none() -> None:
    rec = _record(jurisdiction="  ", party="", region="   ")
    assert rec.jurisdiction is None
    assert rec.party is None
    assert rec.region is None


# ── external_ids dedupe + ordering ─────────────────────────────────


def test_external_ids_are_deduped_and_sorted() -> None:
    rec = _record(
        external_ids=[
            {"system": "fec_candidate", "value": "H0CA12345"},
            {"system": "bioguide", "value": "D000001"},
            {"system": "fec_candidate", "value": "H0CA12345"},  # exact dupe
        ]
    )
    assert [(e.system, e.value) for e in rec.external_ids] == [
        ("bioguide", "D000001"),
        ("fec_candidate", "H0CA12345"),
    ]


def test_external_id_keys_set() -> None:
    rec = _record(
        external_ids=[
            {"system": "bioguide", "value": "D000001"},
            {"system": "fec_candidate", "value": "H0CA12345"},
        ]
    )
    assert rec.external_id_keys == frozenset({"bioguide:d000001", "fec_candidate:h0ca12345"})


def test_record_with_no_external_ids() -> None:
    rec = _record(external_ids=[])
    assert rec.external_ids == ()
    assert rec.external_id_keys == frozenset()


# ── shared external IDs (the strongest linkage signal) ─────────────


def test_shares_external_id_true_when_one_overlaps() -> None:
    a = _record(external_ids=[{"system": "bioguide", "value": "D000001"}])
    b = _record(
        source_record_id="other",
        external_ids=[
            {"system": "fec_candidate", "value": "H0XX"},
            {"system": "Bioguide", "value": "d000001"},  # same id, different case/source
        ],
    )
    assert a.shares_external_id(b) is True


def test_shares_external_id_false_when_disjoint() -> None:
    a = _record(external_ids=[{"system": "bioguide", "value": "D000001"}])
    b = _record(external_ids=[{"system": "bioguide", "value": "D000002"}])
    assert a.shares_external_id(b) is False


def test_shares_external_id_false_when_either_empty() -> None:
    a = _record(external_ids=[])
    b = _record(external_ids=[{"system": "bioguide", "value": "D000002"}])
    assert a.shares_external_id(b) is False


# ── parsed person name + leakage passthrough ───────────────────────


def test_person_name_parses_display_name() -> None:
    name = _record(display_name="Doe, Jane M.").person_name()
    assert isinstance(name, PersonName)
    assert name.family == "doe"
    assert name.given == "jane"


def test_person_name_is_none_for_org() -> None:
    org = _record(entity_type="org", display_name="Acme PAC", external_ids=[])
    assert org.person_name() is None


def test_known_at_passes_through_provenance() -> None:
    rec = _record()
    assert rec.known_at == rec.provenance.known_at
    assert rec.known_as_of(datetime(2024, 6, 1, tzinfo=UTC)) is True
    assert rec.known_as_of(datetime(2023, 1, 1, tzinfo=UTC)) is False


def test_record_is_frozen() -> None:
    rec = _record()
    with pytest.raises(ValidationError):
        rec.display_name = "x"  # type: ignore[misc]
