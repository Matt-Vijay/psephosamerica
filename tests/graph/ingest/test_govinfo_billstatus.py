from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.bills import BillRef
from src.graph.ingest.govinfo_billstatus import (
    BillStatus,
    bill_ref,
    billstatus_bill_row,
    billstatus_dossier_text,
    billstatus_external_ids,
    billstatus_provenance,
    bulk_billstatus_url,
    canonical_bill_id,
    canonical_bill_id_from_filename,
    govinfo_record_id,
    parse_billstatus_xml,
    vote_link_report,
)
from src.graph.ingest.house_clerk import bill_canonical_id_for, parse_house_rollcall_xml

_BILLSTATUS = """<billStatus>
  <bill>
    <congress>118</congress>
    <type>HR</type>
    <number>1</number>
    <title>Lower Energy Costs Act</title>
    <introducedDate>2023-03-14</introducedDate>
    <policyArea><name>Energy</name></policyArea>
    <subjects>
      <legislativeSubjects>
        <item><name>Energy prices</name></item>
        <item><name>Oil and gas</name></item>
      </legislativeSubjects>
    </subjects>
    <sponsors>
      <item><bioguideId>S001176</bioguideId><fullName>Rep. Scalise, Steve</fullName></item>
    </sponsors>
    <cosponsors>
      <item><bioguideId>W000821</bioguideId><fullName>Rep. Westerman</fullName><sponsorshipDate>2023-03-14</sponsorshipDate></item>
      <item><bioguideId>W000821</bioguideId><fullName>Rep. Westerman (dup)</fullName></item>
    </cosponsors>
    <committees>
      <item><name>Energy and Commerce Committee</name></item>
    </committees>
    <summaries>
      <summary><text>This bill lowers energy costs by increasing domestic production.</text></summary>
    </summaries>
  </bill>
</billStatus>"""


def test_parse_extracts_all_fields() -> None:
    status = parse_billstatus_xml(_BILLSTATUS)
    assert status.congress == 118
    assert status.bill_type == "hr"
    assert status.number == 1
    assert status.title == "Lower Energy Costs Act"
    assert status.introduced_date == date(2023, 3, 14)
    assert status.policy_area == "Energy"
    assert status.subjects == ("Energy prices", "Oil and gas")
    assert [s.bioguide_id for s in status.sponsors] == ["S001176"]
    assert [c.name for c in status.committees] == ["Energy and Commerce Committee"]
    assert status.summary_text is not None and "lowers energy costs" in status.summary_text


def test_cosponsors_are_deduped_with_dates() -> None:
    status = parse_billstatus_xml(_BILLSTATUS)
    assert [c.bioguide_id for c in status.cosponsors] == ["W000821"]
    assert status.cosponsors[0].sponsorship_date == date(2023, 3, 14)


def test_canonical_id_matches_the_vote_linkage() -> None:
    # THE unlock: a BILLSTATUS bill's canonical id is identical to the id the
    # House Clerk vote adapter resolves its roll-call to -> votes link for free.
    status = parse_billstatus_xml(_BILLSTATUS)
    rollcall = parse_house_rollcall_xml(
        """<rollcall-vote><vote-metadata>
        <congress>118</congress><session>1st</session><rollcall-num>5</rollcall-num>
        <legis-num>H R 1</legis-num><vote-question>On Passage</vote-question>
        <vote-result>Passed</vote-result><action-date>14-Mar-2023</action-date>
        </vote-metadata></rollcall-vote>"""
    )
    assert canonical_bill_id(status) == bill_canonical_id_for(rollcall)
    assert canonical_bill_id(status) == BillRef.for_congress(118, "H.R. 1").canonical_id
    assert canonical_bill_id(status).startswith("cb-")


def test_dossier_text_is_dense_and_distinct() -> None:
    status = parse_billstatus_xml(_BILLSTATUS)
    text = billstatus_dossier_text(status)
    assert "Lower Energy Costs Act" in text
    assert "Policy area: Energy." in text
    assert "Energy prices; Oil and gas" in text
    assert "lowers energy costs" in text


def test_dossier_text_differs_for_two_same_policy_area_bills() -> None:
    base = parse_billstatus_xml(_BILLSTATUS)
    other = BillStatus(
        congress=118,
        bill_type="hr",
        number=2,
        title="Strategic Petroleum Reserve Reform Act",
        introduced_date=date(2023, 1, 9),
        policy_area="Energy",  # same sector...
        subjects=("Strategic Petroleum Reserve",),
        sponsors=(),
        cosponsors=(),
        committees=(),
        summary_text="This bill reforms the SPR drawdown rules.",
    )
    # ...but the dense text is different, so embeddings will not collapse.
    assert billstatus_dossier_text(base) != billstatus_dossier_text(other)


def test_record_id_and_bill_ref() -> None:
    status = parse_billstatus_xml(_BILLSTATUS)
    assert govinfo_record_id(status) == "BILLSTATUS-118hr1"
    assert bill_ref(status) == BillRef(
        jurisdiction_id="us-congress", session_id="118", identifier="hr-1"
    )


def test_provenance_known_at_is_introduced_day() -> None:
    status = parse_billstatus_xml(_BILLSTATUS)
    prov = billstatus_provenance(
        status=status,
        source_url=bulk_billstatus_url(118, "hr"),
        content_sha256="a" * 64,
        first_observed_at=datetime(2023, 4, 1, tzinfo=UTC),
    )
    assert prov.known_at == datetime(2023, 3, 14, tzinfo=UTC)
    assert prov.valid_from == date(2023, 3, 14)
    assert prov.known_as_of(datetime(2023, 3, 14, tzinfo=UTC)) is True
    assert prov.known_as_of(datetime(2023, 3, 13, tzinfo=UTC)) is False


def test_provenance_falls_back_to_observed_when_no_introduced_date() -> None:
    status = BillStatus(
        congress=117,
        bill_type="s",
        number=50,
        title="A bill",
        introduced_date=None,
        policy_area=None,
        subjects=(),
        sponsors=(),
        cosponsors=(),
        committees=(),
        summary_text=None,
    )
    observed = datetime(2021, 5, 1, tzinfo=UTC)
    prov = billstatus_provenance(
        status=status, source_url="https://x", content_sha256="b" * 64, first_observed_at=observed
    )
    assert prov.known_at == observed
    assert prov.valid_from == date(2021, 5, 1)


def test_summary_parsed_from_real_cdata_nesting_and_html_stripped() -> None:
    # govinfo's real schema: summaries/summary/cdata/text with HTML-escaped body.
    xml = """<billStatus><bill>
      <congress>118</congress><type>HR</type><number>9</number><title>Real Schema Act</title>
      <summaries><summary><actionDesc>Introduced in House</actionDesc>
        <cdata><text>&lt;p&gt;&lt;b&gt;Real Schema Act&lt;/b&gt;&lt;/p&gt; &lt;p&gt;This bill does things.&lt;/p&gt;</text></cdata>
      </summary></summaries>
    </bill></billStatus>"""
    status = parse_billstatus_xml(xml)
    assert status.summary_text == "Real Schema Act This bill does things."


def test_schema_drift_alternate_tags() -> None:
    xml = """<billStatus><bill>
      <congress>115</congress><billType>S</billType><billNumber>50</billNumber>
      <title>Older Schema Bill</title>
      <summaries><billSummaries><item><text>Summary via old schema.</text></item></billSummaries></summaries>
    </bill></billStatus>"""
    status = parse_billstatus_xml(xml)
    assert (status.bill_type, status.number) == ("s", 50)
    assert status.summary_text == "Summary via old schema."


def test_canonical_id_from_filename_matches_parsed() -> None:
    status = parse_billstatus_xml(_BILLSTATUS)
    assert canonical_bill_id_from_filename("BILLSTATUS-118hr1.xml") == canonical_bill_id(status)
    # case-insensitive, and equals the vote-linkage id
    assert canonical_bill_id_from_filename("billstatus-118HR1.xml") == canonical_bill_id(status)


def test_canonical_id_from_filename_rejects_bad_names() -> None:
    assert canonical_bill_id_from_filename("index.xml") is None
    assert canonical_bill_id_from_filename("BILLSTATUS-118zz1.xml") is None  # unknown type
    assert canonical_bill_id_from_filename("README.txt") is None


def test_bulk_url() -> None:
    assert bulk_billstatus_url(118, "hr") == "https://www.govinfo.gov/bulkdata/BILLSTATUS/118/hr"
    assert bulk_billstatus_url(117, "S.") == "https://www.govinfo.gov/bulkdata/BILLSTATUS/117/s"


def test_parse_rejects_blank_and_malformed() -> None:
    with pytest.raises(ValueError, match="empty"):
        parse_billstatus_xml("   ")
    with pytest.raises(ValueError, match="invalid BILLSTATUS XML"):
        parse_billstatus_xml("<billStatus><bill>")
    with pytest.raises(ValueError, match="missing <bill>"):
        parse_billstatus_xml("<billStatus></billStatus>")
    with pytest.raises(ValueError, match="missing congress/type/number"):
        parse_billstatus_xml("<billStatus><bill><congress>118</congress></bill></billStatus>")


def test_parse_rejects_unknown_bill_type() -> None:
    xml = "<billStatus><bill><congress>118</congress><type>ZZ</type><number>1</number></bill></billStatus>"
    with pytest.raises(ValueError, match="unknown congress bill type"):
        parse_billstatus_xml(xml)


def test_bulk_url_rejects_non_alpha_type() -> None:
    with pytest.raises(ValueError, match="invalid bill type"):
        bulk_billstatus_url(118, "123")


def test_parse_tolerates_empty_child_containers() -> None:
    # Present-but-empty <policyArea/>, <subjects/>, <sponsors/> exercise the
    # "all candidate paths empty -> default" fall-throughs in _text/_iter.
    xml = """<billStatus><bill>
      <congress>119</congress><type>S</type><number>7</number><title>Empty Containers Act</title>
      <introducedDate>not-a-date</introducedDate>
      <policyArea></policyArea><subjects></subjects><sponsors></sponsors>
      <cosponsors></cosponsors><committees></committees><summaries></summaries>
    </bill></billStatus>"""
    status = parse_billstatus_xml(xml)
    assert status.policy_area is None
    assert status.subjects == () and status.sponsors == () and status.committees == ()
    assert status.summary_text is None
    # bad/blank introduced date -> None (date fall-through)
    assert status.introduced_date is None


def test_dossier_text_title_only_when_sparse() -> None:
    status = BillStatus(
        congress=119,
        bill_type="s",
        number=7,
        title="Bare Title Act",
        introduced_date=None,
        policy_area=None,
        subjects=(),
        sponsors=(),
        cosponsors=(),
        committees=(),
        summary_text=None,
    )
    assert billstatus_dossier_text(status) == "Bare Title Act"


def test_external_ids_are_sorted_unique_citation_and_govinfo() -> None:
    status = parse_billstatus_xml(_BILLSTATUS)
    assert billstatus_external_ids(status) == [
        "congress:118-hr-1",
        "govinfo:billstatus-118hr1",
    ]


def test_bill_row_is_vote_linkable_pending_row() -> None:
    status = parse_billstatus_xml(_BILLSTATUS)
    row = billstatus_bill_row(
        status,
        source_url=bulk_billstatus_url(118, "hr"),
        content_sha256="a" * 64,
        first_observed_at=datetime(2023, 4, 1, tzinfo=UTC),
    )
    assert row.entity_type == "bill"
    assert row.canonical_id == bill_canonical_id_for(
        parse_house_rollcall_xml(
            "<rollcall-vote><vote-metadata><congress>118</congress><session>1</session>"
            "<rollcall-num>5</rollcall-num><legis-num>H R 1</legis-num>"
            "<vote-question>q</vote-question><vote-result>Passed</vote-result>"
            "<action-date>14-Mar-2023</action-date></vote-metadata></rollcall-vote>"
        )
    )
    assert row.display_name == "Lower Energy Costs Act"
    assert row.enrichment_status == "pending"  # enriched later by regenerate_corpus
    assert row.source_anchors[0].source_system == "govinfo"
    assert row.known_at == datetime(2023, 3, 14, tzinfo=UTC)


def test_bill_row_enriches_to_a_dense_vote_linked_embedding() -> None:
    # End-to-end proof of the unlock: a govinfo bill row + its roll-call vote edge
    # -> regenerate_corpus -> the bill row is 'ready' with a non-trivial dossier
    # embedding, and the vote's dst_id resolves to that embedded bill.
    from datetime import datetime as _dt

    from src.graph.ingest.votes import vote_edge, vote_provenance
    from src.graph.regenerate import regenerate_corpus

    status = parse_billstatus_xml(_BILLSTATUS)
    bill_id = canonical_bill_id(status)
    row = billstatus_bill_row(
        status,
        source_url=bulk_billstatus_url(118, "hr"),
        content_sha256="a" * 64,
        first_observed_at=_dt(2023, 4, 1, tzinfo=UTC),
    )
    edge = vote_edge(
        member_canonical_id="cp-member-1",
        bill_canonical_id=bill_id,
        choice="yea",
        provenance=vote_provenance(
            source_url="https://clerk.house.gov/evs/2023/roll005.xml",
            content_sha256="b" * 64,
            vote_date=date(2023, 3, 30),
            first_observed_at=_dt(2023, 3, 31, tzinfo=UTC),
        ),
    )
    result = regenerate_corpus(
        person_records=[],
        bill_outputs=[row],
        edges=[edge],
        as_of=_dt(2024, 1, 1, tzinfo=UTC),
    )
    bill_rows = [r for r in result.rows if r.entity_type == "bill"]
    assert len(bill_rows) == 1
    enriched = bill_rows[0]
    assert enriched.canonical_id == bill_id == edge.dst_id  # vote resolves to the bill
    assert enriched.enrichment_status == "ready"
    assert enriched.dossier_embedding is not None and len(enriched.dossier_embedding) == 256
    assert any(abs(v) > 0 for v in enriched.dossier_embedding)


def test_vote_link_report_counts_substantive_and_procedural() -> None:
    embedded = {"cb-aaa", "cb-bbb"}
    # 5 votes: 2 link, 1 substantive-but-unlinked, 2 procedural (None).
    report = vote_link_report(
        vote_bill_ids=["cb-aaa", "cb-bbb", "cb-missing", None, None],
        embedded_bill_ids=embedded,
    )
    assert report["votes"] == 5.0
    assert report["votes_with_bill_ref"] == 3.0
    assert report["votes_linked_embedded"] == 2.0
    assert report["pct_of_all_votes_linked"] == 40.0
    assert report["pct_of_substantive_votes_linked"] == pytest.approx(66.67, abs=0.01)


def test_vote_link_report_empty() -> None:
    report = vote_link_report(vote_bill_ids=[], embedded_bill_ids=[])
    assert report["votes"] == 0.0
    assert report["pct_of_all_votes_linked"] == 0.0
    assert report["pct_of_substantive_votes_linked"] == 0.0


def test_provenance_honors_explicit_known_at() -> None:
    status = parse_billstatus_xml(_BILLSTATUS)
    forced = datetime(2023, 6, 1, tzinfo=UTC)
    prov = billstatus_provenance(
        status=status,
        source_url="https://x",
        content_sha256="c" * 64,
        first_observed_at=datetime(2023, 6, 2, tzinfo=UTC),
        known_at=forced,
    )
    assert prov.known_at == forced
