from __future__ import annotations

from datetime import date

import pytest

from src.graph.ingest.house_clerk import (
    HouseRollCall,
    bill_canonical_id_for,
    parse_house_rollcall_xml,
)

_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rollcall-vote>
<vote-metadata>
<congress>118</congress>
<session>1st</session>
<chamber>U.S. House of Representatives</chamber>
<rollcall-num>14</rollcall-num>
<legis-num>H R 21</legis-num>
<vote-question>On Passage</vote-question>
<vote-result>Passed</vote-result>
<action-date>9-Jan-2023</action-date>
</vote-metadata>
<vote-data>
<recorded-vote><legislator name-id="A000055" party="R" state="AL">Aderholt</legislator><vote>Yea</vote></recorded-vote>
<recorded-vote><legislator name-id="A000370" party="D" state="NC">Adams</legislator><vote>Nay</vote></recorded-vote>
<recorded-vote><legislator name-id="B001302" party="R" state="IN">Baird</legislator><vote>Not Voting</vote></recorded-vote>
</vote-data>
</rollcall-vote>
"""

_QUORUM_XML = """<?xml version="1.0"?>
<rollcall-vote><vote-metadata>
<congress>118</congress><session>1st</session><rollcall-num>1</rollcall-num>
<legis-num>QUORUM</legis-num><vote-question>Call By States</vote-question>
<vote-result>Present</vote-result><action-date>3-Jan-2023</action-date>
</vote-metadata><vote-data></vote-data></rollcall-vote>
"""


def test_parse_metadata() -> None:
    rc = parse_house_rollcall_xml(_XML)
    assert isinstance(rc, HouseRollCall)
    assert rc.congress == 118
    assert rc.rollcall_num == 14
    assert rc.legis_num == "H R 21"
    assert rc.vote_question == "On Passage"
    assert rc.vote_result == "Passed"
    assert rc.action_date == date(2023, 1, 9)


def test_parse_recorded_votes_use_bioguide() -> None:
    rc = parse_house_rollcall_xml(_XML)
    assert rc.recorded_votes == (
        ("A000055", "Yea"),
        ("A000370", "Nay"),
        ("B001302", "Not Voting"),
    )


def test_bill_canonical_id_for_substantive_vote() -> None:
    rc = parse_house_rollcall_xml(_XML)
    bill_id = bill_canonical_id_for(rc)
    assert bill_id is not None
    from src.graph.bills import BillRef

    assert bill_id == BillRef.for_congress(118, "H.R. 21").canonical_id


def test_quorum_vote_has_no_bill() -> None:
    rc = parse_house_rollcall_xml(_QUORUM_XML)
    assert rc.legis_num == "QUORUM"
    assert rc.recorded_votes == ()
    assert bill_canonical_id_for(rc) is None


def test_parse_rejects_malformed_xml() -> None:
    with pytest.raises(ValueError, match="roll-call"):
        parse_house_rollcall_xml("<not-a-rollcall/>")


def test_parse_rejects_non_xml() -> None:
    with pytest.raises(ValueError):
        parse_house_rollcall_xml("garbage <<<")


def test_missing_metadata_rejected() -> None:
    with pytest.raises(ValueError, match="vote-metadata"):
        parse_house_rollcall_xml("<rollcall-vote><vote-data></vote-data></rollcall-vote>")


def test_recorded_votes_skip_incomplete_entries() -> None:
    xml = """<rollcall-vote><vote-metadata>
    <congress>118</congress><session>1st</session><rollcall-num>5</rollcall-num>
    <legis-num>H R 1</legis-num><vote-question>On Passage</vote-question>
    <vote-result>Passed</vote-result><action-date>9-Jan-2023</action-date>
    </vote-metadata><vote-data>
    <recorded-vote><vote>Yea</vote></recorded-vote>
    <recorded-vote><legislator name-id="">X</legislator><vote>Yea</vote></recorded-vote>
    <recorded-vote><legislator name-id="A000055">Aderholt</legislator><vote></vote></recorded-vote>
    <recorded-vote><legislator name-id="A000370">Adams</legislator><vote>Yea</vote></recorded-vote>
    </vote-data></rollcall-vote>"""
    rc = parse_house_rollcall_xml(xml)
    # Only the complete entry survives.
    assert rc.recorded_votes == (("A000370", "Yea"),)


def test_blank_legis_num_is_none_and_has_no_bill() -> None:
    xml = """<rollcall-vote><vote-metadata>
    <congress>118</congress><session>1st</session><rollcall-num>8</rollcall-num>
    <legis-num></legis-num><vote-question>Q</vote-question>
    <vote-result>Passed</vote-result><action-date>9-Jan-2023</action-date>
    </vote-metadata><vote-data></vote-data></rollcall-vote>"""
    rc = parse_house_rollcall_xml(xml)
    assert rc.legis_num is None
    assert bill_canonical_id_for(rc) is None


def test_unparseable_legis_num_has_no_bill() -> None:
    xml = """<rollcall-vote><vote-metadata>
    <congress>118</congress><session>1st</session><rollcall-num>7</rollcall-num>
    <legis-num>MOTION</legis-num><vote-question>On Motion to Adjourn</vote-question>
    <vote-result>Passed</vote-result><action-date>9-Jan-2023</action-date>
    </vote-metadata><vote-data></vote-data></rollcall-vote>"""
    rc = parse_house_rollcall_xml(xml)
    assert rc.legis_num == "MOTION"
    assert bill_canonical_id_for(rc) is None
