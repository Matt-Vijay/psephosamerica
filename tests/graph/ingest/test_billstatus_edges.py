from __future__ import annotations

from datetime import UTC, date, datetime

from src.graph.committees import CommitteeRef
from src.graph.ingest.billstatus_edges import (
    classification_edges,
    committee_referral_edges,
    sponsorship_edges,
    subject_id,
)
from src.graph.ingest.govinfo_billstatus import (
    billstatus_provenance,
    canonical_bill_id,
    parse_billstatus_xml,
)

_XML = """<billStatus><bill>
  <congress>118</congress><type>HR</type><number>1</number><title>Lower Energy Costs Act</title>
  <introducedDate>2023-03-14</introducedDate>
  <policyArea><name>Energy</name></policyArea>
  <subjects><legislativeSubjects>
    <item><name>Energy prices</name></item>
    <item><name>Oil and gas</name></item>
    <item><name>Energy prices</name></item>
  </legislativeSubjects></subjects>
  <sponsors><item><bioguideId>S001176</bioguideId><fullName>Scalise</fullName></item></sponsors>
  <cosponsors>
    <item><bioguideId>W000821</bioguideId><fullName>Westerman</fullName><sponsorshipDate>2023-03-20</sponsorshipDate></item>
    <item><bioguideId>UNKNOWN9</bioguideId><fullName>Ghost</fullName></item>
  </cosponsors>
</bill></billStatus>"""


def _prov():
    status = parse_billstatus_xml(_XML)
    return billstatus_provenance(
        status=status,
        source_url="https://www.govinfo.gov/bulkdata/BILLSTATUS/118/hr/BILLSTATUS-118hr1.xml",
        content_sha256="a" * 64,
        first_observed_at=datetime(2023, 4, 1, tzinfo=UTC),
    )


def test_subject_id_is_stable_and_case_insensitive() -> None:
    assert subject_id("Energy") == subject_id("energy ")
    assert subject_id("Energy").startswith("csub-")
    assert subject_id("Energy") != subject_id("Health")


def test_classification_edges_cover_policy_area_and_deduped_subjects() -> None:
    status = parse_billstatus_xml(_XML)
    edges = classification_edges(status, provenance=_prov())
    bill = canonical_bill_id(status)
    assert all(e.src_id == bill for e in edges)
    policy = [e for e in edges if e.edge_type == "policy_area"]
    subjects = [e for e in edges if e.edge_type == "legislative_subject"]
    assert len(policy) == 1 and policy[0].attributes["name"] == "Energy"
    assert policy[0].dst_id == subject_id("Energy")
    # duplicate "Energy prices" collapsed -> 2 distinct subjects
    assert len(subjects) == 2
    assert {e.attributes["name"] for e in subjects} == {"Energy prices", "Oil and gas"}


def test_classification_edges_omit_absent_policy_area() -> None:
    status = parse_billstatus_xml(
        "<billStatus><bill><congress>118</congress><type>HR</type><number>5</number>"
        "<title>No Topic Act</title></bill></billStatus>"
    )
    assert classification_edges(status, provenance=_prov()) == []


def test_classification_edges_feed_the_bill_dossier_density() -> None:
    # The classification edges are edges FROM the bill, so they surface in the
    # bill's dossier context -> enrich the embedding with subject text.
    from src.graph.enrichment.dossier_context import build_dossier_context
    from src.graph.ingest.govinfo_billstatus import billstatus_bill_row
    from src.graph.knowledge_graph import KnowledgeGraph

    status = parse_billstatus_xml(_XML)
    row = billstatus_bill_row(
        status,
        source_url="https://x",
        content_sha256="a" * 64,
        first_observed_at=datetime(2023, 4, 1, tzinfo=UTC),
    )
    edges = classification_edges(status, provenance=_prov())
    graph = KnowledgeGraph(nodes=[row], edges=edges)
    context = build_dossier_context(graph, row.canonical_id, as_of=datetime(2024, 1, 1, tzinfo=UTC))
    assert context is not None
    relations = {fact.relation for fact in context.facts}
    assert "policy_area" in relations and "legislative_subject" in relations


_XML_COMMITTEES = """<billStatus><bill>
  <congress>118</congress><type>HR</type><number>1</number><title>Lower Energy Costs Act</title>
  <introducedDate>2023-03-14</introducedDate>
  <committees>
    <item><systemCode>hsii00</systemCode><name>Natural Resources Committee</name><chamber>House</chamber></item>
    <item><systemCode>hsii00</systemCode><name>Natural Resources Committee</name><chamber>House</chamber></item>
    <item><name>No Code Committee</name></item>
  </committees>
</bill></billStatus>"""


def test_committee_referral_edges_use_canonical_committee_ids() -> None:
    status = parse_billstatus_xml(_XML_COMMITTEES)
    edges = committee_referral_edges(status, provenance=_prov())
    bill = canonical_bill_id(status)
    assert len(edges) == 1  # duplicate collapsed, the code-less committee skipped
    edge = edges[0]
    assert edge.edge_type == "referred_to"
    assert edge.src_id == bill
    assert (
        edge.dst_id
        == CommitteeRef(jurisdiction_id="us-congress", code="hsii00", chamber="House").canonical_id
    )
    assert edge.attributes["name"] == "Natural Resources Committee"


def test_committee_referral_skips_unparseable_chamber() -> None:
    status = parse_billstatus_xml(
        "<billStatus><bill><congress>118</congress><type>HR</type><number>3</number>"
        "<title>Bad Chamber Act</title><committees><item>"
        "<systemCode>xx00</systemCode><name>Mystery</name><chamber>Mars</chamber>"
        "</item></committees></bill></billStatus>"
    )
    assert committee_referral_edges(status, provenance=_prov()) == []


def test_committee_parse_captures_code_and_chamber() -> None:
    status = parse_billstatus_xml(_XML_COMMITTEES)
    first = status.committees[0]
    assert (first.name, first.system_code, first.chamber) == (
        "Natural Resources Committee",
        "hsii00",
        "House",
    )
    assert status.committees[2].system_code is None  # code-less committee still parsed


def test_sponsorship_edges_resolve_members_and_skip_unknown() -> None:
    status = parse_billstatus_xml(_XML)
    known = {"S001176": "cp-scalise", "W000821": "cp-westerman"}
    edges = sponsorship_edges(
        status,
        resolve_bioguide=known.get,
        source_url="https://x",
        content_sha256="a" * 64,
        first_observed_at=datetime(2023, 4, 1, tzinfo=UTC),
    )
    bill = canonical_bill_id(status)
    by_type = {e.edge_type: e for e in edges}
    assert set(by_type) == {"sponsorship", "cosponsorship"}
    assert by_type["sponsorship"].src_id == "cp-scalise"
    assert by_type["sponsorship"].dst_id == bill
    assert by_type["sponsorship"].provenance.valid_from == date(2023, 3, 14)  # introduced
    # cosponsor uses its own sponsorshipDate; UNKNOWN9 (unresolved) skipped
    assert by_type["cosponsorship"].src_id == "cp-westerman"
    assert by_type["cosponsorship"].provenance.valid_from == date(2023, 3, 20)
    assert len(edges) == 2  # ghost cosponsor dropped


def test_sponsorship_edges_skip_when_no_action_date() -> None:
    # A sponsor with neither an introduced date nor a sponsorship date -> no edge.
    status = parse_billstatus_xml(
        "<billStatus><bill><congress>118</congress><type>HR</type><number>9</number>"
        "<title>Undated Act</title>"
        "<sponsors><item><bioguideId>S001176</bioguideId></item></sponsors></bill></billStatus>"
    )
    assert status.introduced_date is None
    edges = sponsorship_edges(
        status,
        resolve_bioguide={"S001176": "cp-scalise"}.get,
        source_url="https://x",
        content_sha256="a" * 64,
        first_observed_at=datetime(2023, 4, 1, tzinfo=UTC),
    )
    assert edges == []
