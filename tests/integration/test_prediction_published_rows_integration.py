"""Integration coverage for DB-backed prediction input rows."""

from __future__ import annotations

import datetime as dt
import os

import pytest

from src.db.bootstrap import apply_sql, read_schema_sql
from src.prediction.backtest import build_vote_baseline_backtest
from src.query.published_rows import (
    fetch_vote_prediction_backtest_bill_signal_rows,
    fetch_vote_prediction_backtest_feature_rows,
    fetch_vote_prediction_backtest_label_rows,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("PSEPHOS_TEST_POSTGRES_DSN"),
    reason="PSEPHOS_TEST_POSTGRES_DSN not set",
)


@pytest.fixture()
def db(pg_conn_clean):
    apply_sql(pg_conn_clean, read_schema_sql())
    return pg_conn_clean


def test_prediction_rows_preserve_congress_sources_and_context_from_db(db) -> None:
    member_id = _insert_member(db)
    _insert_member_term(
        db,
        member_id=member_id,
        congress=118,
        start_date=dt.date(2023, 1, 3),
        end_date=dt.date(2025, 1, 2),
        is_current=False,
    )
    _insert_member_term(
        db,
        member_id=member_id,
        congress=119,
        start_date=dt.date(2025, 1, 3),
        end_date=None,
        is_current=True,
    )
    _insert_vote(
        db,
        member_id=member_id,
        congress=118,
        session_number=2,
        roll_call_number=701,
        vote_date=dt.date(2024, 12, 15),
        question="On Passage of H.R. 1, Energy Reliability",
        vote_option="yea",
        source_url="https://clerk.house.gov/Votes/2024701",
    )
    _insert_vote(
        db,
        member_id=member_id,
        congress=119,
        session_number=1,
        roll_call_number=7,
        vote_date=dt.date(2025, 1, 10),
        question="On Passage of H.R. 1, Energy Reliability",
        vote_option="yea",
        source_url="https://clerk.house.gov/Votes/2025007",
    )
    bill_id = _insert_bill(db)
    _insert_bill_sponsor(db, bill_id=bill_id, member_id=member_id)

    feature_rows = fetch_vote_prediction_backtest_feature_rows(db, dt.date(2024, 12, 31))
    label_rows = fetch_vote_prediction_backtest_label_rows(
        db,
        dt.date(2025, 1, 1),
        dt.date(2025, 12, 31),
    )
    bill_rows = fetch_vote_prediction_backtest_bill_signal_rows(db, dt.date(2024, 12, 31))
    result = build_vote_baseline_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=feature_rows,
        label_rows=label_rows,
    )

    assert len(feature_rows) == 1
    feature = feature_rows[0]
    assert feature["jurisdiction_id"] == "us_congress"
    assert feature["legislative_body_id"] == "us_congress_house"
    assert feature["latest_legislative_session_id"] == "congress_118_session_2"
    assert feature["latest_vote_source_url"] == "https://clerk.house.gov/Votes/2024701"
    assert feature["latest_vote_event_key"] == "house-118-2-701"
    assert feature["vote_source_url_count"] == 1

    assert len(label_rows) == 1
    label = label_rows[0]
    assert label["jurisdiction_id"] == "us_congress"
    assert label["legislative_body_id"] == "us_congress_house"
    assert label["legislative_session_id"] == "congress_119_session_1"
    assert label["source_url"] == "https://clerk.house.gov/Votes/2025007"

    assert len(bill_rows) == 1
    bill = bill_rows[0]
    assert bill["jurisdiction_id"] == "us_congress"
    assert bill["legislative_body_id"] == "us_congress_house"
    assert bill["legislative_session_id"] == "congress_118_session_2"
    assert bill["bill_source_url"] == "https://api.congress.gov/v3/bill/118/hr/1?format=json"
    assert bill["sponsor_source_url"] == (
        "https://api.congress.gov/v3/bill/118/hr/1/cosponsors?format=json"
    )

    prediction = result.predictions[0]
    assert result.metrics.evaluated_count == 1
    assert prediction.source_url == "https://clerk.house.gov/Votes/2025007"
    assert prediction.event_key == "house-119-1-7"
    assert prediction.legislative_body_id == "us_congress_house"
    assert prediction.legislative_session_id == "congress_119_session_1"


def _insert_member(db) -> int:
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO member (
                bioguide_id, slug, first_name, last_name, full_name,
                party, state, chamber, current_term_start, is_current,
                source_record_id
            )
            VALUES (
                'A000001', 'jane-doe', 'Jane', 'Doe', 'Jane Doe',
                'D', 'CA', 'house', '2023-01-03', true,
                'https://api.congress.gov/v3/member/A000001?format=json'
            )
            RETURNING id
            """,
        )
        row = cur.fetchone()
    assert row is not None
    return int(row[0])


def _insert_member_term(
    db,
    *,
    member_id: int,
    congress: int,
    start_date: dt.date,
    end_date: dt.date | None,
    is_current: bool,
) -> None:
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO member_term (
                member_id, congress, chamber, state, district, start_date,
                end_date, is_current, source_record_id
            )
            VALUES (%s, %s, 'house', 'CA', 1, %s, %s, %s, %s)
            """,
            (
                member_id,
                congress,
                start_date,
                end_date,
                is_current,
                f"https://api.congress.gov/v3/member/A000001?format=json#term-{congress}",
            ),
        )


def _insert_vote(
    db,
    *,
    member_id: int,
    congress: int,
    session_number: int,
    roll_call_number: int,
    vote_date: dt.date,
    question: str,
    vote_option: str,
    source_url: str,
) -> None:
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO vote_event (
                source_record_id, chamber, congress, session_number,
                roll_call_number, vote_date, question, result
            )
            VALUES (%s, 'house', %s, %s, %s, %s, %s, 'Passed')
            RETURNING id
            """,
            (source_url, congress, session_number, roll_call_number, vote_date, question),
        )
        row = cur.fetchone()
        assert row is not None
        cur.execute(
            """
            INSERT INTO vote_cast (
                vote_event_id, member_id, source_record_id, vote_option
            )
            VALUES (%s, %s, %s, %s)
            """,
            (int(row[0]), member_id, source_url, vote_option),
        )


def _insert_bill(db) -> int:
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO bill (
                source_record_id, congress, bill_type, bill_number, title,
                short_title, introduced_date, latest_action_date, current_status
            )
            VALUES (
                'https://api.congress.gov/v3/bill/118/hr/1?format=json',
                118, 'hr', 1, 'Energy Reliability Act',
                'Energy Reliability', '2024-12-01', '2024-12-20', 'Introduced'
            )
            RETURNING id
            """,
        )
        row = cur.fetchone()
    assert row is not None
    return int(row[0])


def _insert_bill_sponsor(db, *, bill_id: int, member_id: int) -> None:
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO bill_sponsor (
                bill_id, member_id, source_record_id, sponsor_role,
                is_primary, sponsor_date
            )
            VALUES (
                %s, %s,
                'https://api.congress.gov/v3/bill/118/hr/1/cosponsors?format=json',
                'cosponsor', false, '2024-12-15'
            )
            """,
            (bill_id, member_id),
        )
