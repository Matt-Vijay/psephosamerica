from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.ingest.congressional_record import (
    CrecSpeech,
    crec_package_url,
    floor_speech_edge,
    floor_speech_provenance,
    parse_crec_mods,
)
from src.graph.provenance import ProvenanceEnvelope

_MODS = """<mods:mods xmlns:mods="http://www.loc.gov/mods/v3">
<mods:relatedItem type="constituent">
  <extension xmlns="http://www.loc.gov/mods/v3">
    <congMember bioGuideId="S000148" chamber="S" congress="118" party="D" role="SPEAKING" state="NY">
      <name type="parsed">Mr. SCHUMER</name>
      <name type="authority-fnf">Charles E. Schumer</name>
    </congMember>
  </extension>
</mods:relatedItem>
<mods:relatedItem type="constituent">
  <extension>
    <congMember bioGuideId="M000355" chamber="S" role="SPEAKING" state="KY">
      <name type="parsed">Mr. McCONNELL</name>
      <name type="authority-fnf">Mitch McConnell</name>
    </congMember>
  </extension>
</mods:relatedItem>
</mods:mods>"""

_ISSUE = date(2024, 1, 8)


def test_crec_package_url() -> None:
    assert crec_package_url(_ISSUE) == (
        "https://www.govinfo.gov/metadata/pkg/CREC-2024-01-08/mods.xml"
    )


def test_parse_mods_extracts_speaking_members() -> None:
    speeches = parse_crec_mods(_MODS, issue_date=_ISSUE)
    assert len(speeches) == 2
    schumer = next(s for s in speeches if s.bioguide_id == "S000148")
    assert isinstance(schumer, CrecSpeech)
    assert schumer.member_name == "Charles E. Schumer"
    assert schumer.chamber == "S"
    assert schumer.role == "SPEAKING"
    assert schumer.speech_date == _ISSUE


def test_parse_mods_dedupes_per_member() -> None:
    doubled = _MODS.replace("</mods:mods>", _MODS.split(">", 1)[1])
    speeches = parse_crec_mods(doubled, issue_date=_ISSUE)
    assert sorted(s.bioguide_id for s in speeches) == ["M000355", "S000148"]


def test_parse_skips_member_without_bioguide() -> None:
    xml = '<x><congMember chamber="S" role="SPEAKING"><name type="parsed">Mr. X</name></congMember></x>'
    assert parse_crec_mods(xml, issue_date=_ISSUE) == []


def test_parse_falls_back_to_parsed_name() -> None:
    xml = '<x><congMember bioGuideId="A1" role="SPEAKING"><name type="parsed">Mr. ALPHA</name></congMember></x>'
    speeches = parse_crec_mods(xml, issue_date=_ISSUE)
    assert speeches[0].member_name == "Mr. ALPHA"


def test_parse_falls_back_to_bioguide_when_no_name() -> None:
    xml = '<x><congMember bioGuideId="A1" role="SPEAKING"></congMember></x>'
    speeches = parse_crec_mods(xml, issue_date=_ISSUE)
    assert speeches[0].member_name == "A1"


# ── floor-speech edge ──────────────────────────────────────────────


def _prov() -> ProvenanceEnvelope:
    return floor_speech_provenance(
        source_url=crec_package_url(_ISSUE),
        content_sha256="a" * 64,
        speech_date=_ISSUE,
        first_observed_at=datetime(2024, 1, 10, tzinfo=UTC),
    )


def test_floor_speech_provenance_known_at_is_issue_day() -> None:
    prov = _prov()
    assert prov.valid_from == _ISSUE
    assert prov.known_at == datetime(2024, 1, 8, tzinfo=UTC)


def test_floor_speech_edge() -> None:
    speech = parse_crec_mods(_MODS, issue_date=_ISSUE)[0]
    edge = floor_speech_edge(member_canonical_id="ce-schumer", speech=speech, provenance=_prov())
    assert edge.edge_type == "floor_speech"
    assert edge.src_id == "ce-schumer"
    assert edge.dst_id == "congressional_record:2024-01-08"
    assert edge.attributes["role"] == "speaking"
    assert edge.external_key == f"{speech.bioguide_id}:2024-01-08"


def test_floor_speech_edge_without_chamber_or_role() -> None:
    xml = '<x><congMember bioGuideId="A1"><name type="parsed">Mr. ALPHA</name></congMember></x>'
    speech = parse_crec_mods(xml, issue_date=_ISSUE)[0]
    edge = floor_speech_edge(member_canonical_id="ce-a1", speech=speech, provenance=_prov())
    assert "chamber" not in edge.attributes
    assert "role" not in edge.attributes  # blank role dropped


def test_floor_speech_edge_leakage_gate() -> None:
    speech = parse_crec_mods(_MODS, issue_date=_ISSUE)[0]
    edge = floor_speech_edge(member_canonical_id="ce-schumer", speech=speech, provenance=_prov())
    assert edge.known_as_of(datetime(2024, 1, 8, tzinfo=UTC)) is True
    assert edge.known_as_of(datetime(2024, 1, 7, tzinfo=UTC)) is False


def test_parse_rejects_blank() -> None:
    with pytest.raises(ValueError, match="mods"):
        parse_crec_mods("", issue_date=_ISSUE)
