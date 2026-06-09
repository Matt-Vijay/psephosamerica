from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.bills import BillRef
from src.graph.ingest.govinfo_billstatus import (
    BillStatus,
    bill_ref,
    billstatus_dossier_text,
    billstatus_provenance,
    bulk_billstatus_url,
    canonical_bill_id,
    govinfo_record_id,
    parse_billstatus_xml,
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
    assert status.committees == ("Energy and Commerce Committee",)
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


def test_schema_drift_alternate_tags() -> None:
    xml = """<billStatus><bill>
      <congress>115</congress><billType>S</billType><billNumber>50</billNumber>
      <title>Older Schema Bill</title>
      <summaries><billSummaries><item><text>Summary via old schema.</text></item></billSummaries></summaries>
    </bill></billStatus>"""
    status = parse_billstatus_xml(xml)
    assert (status.bill_type, status.number) == ("s", 50)
    assert status.summary_text == "Summary via old schema."


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
