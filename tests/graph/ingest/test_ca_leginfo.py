from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.graph.ingest.ca_leginfo import (
    CaBillTitle,
    ca_bill_ref,
    ca_bill_row,
    parse_bill_titles,
    parse_ca_legislators,
    parse_ca_session,
)

_OBS = datetime(2026, 6, 11, tzinfo=UTC)
_URL = "https://downloads.leginfo.legislature.ca.gov/pubinfo_2025.zip"

# Columns: version id, bill id, version number (newest = lowest), date, action,
# ?, subject, ... -- mirrors the live BILL_VERSION_TBL layout.
_VERSIONS = "\n".join(
    [
        "`20250AB1399INT`\t`202520260AB13`\t99\t2025-01-02 00:00:00\t`Introduced`\tNULL\t`Surplus land: planning.`\tx",
        "`20250AB1395AMD`\t`202520260AB13`\t95\t2025-09-19 00:00:00\t`Amended`\tNULL\t`Surplus land: exempt surplus land.`\tx",
        "`20250AB1397AMD`\t`202520260AB13`\t97\t2025-05-01 00:00:00\t`Amended`\tNULL\t`Older amendment.`\tx",  # 97 > 95 -> not newest
        "`20250AB00INT`\t`202520260AB`\t99\t2025-01-02 00:00:00\t`I`\tNULL\t`Measure with no number.`\tx",
        "`20250SB3397INT`\t`202520260SB33`\t99\t2024-12-02 00:00:00\t`Introduced`\tNULL\tNULL\tx",  # no subject
        "`20250HR199INT`\t`202520260HR1`\t99\t2024-12-02 00:00:00\t`Introduced`\tNULL\t`Relative to rules.`\tx",
        "",  # blank tolerated
        "`short`\t`x`",  # too few columns
        "`20250XX99`\t`BADBILLID`\t99\t2024-12-02 00:00:00\t`I`\tNULL\t`Bad id.`\tx",  # bad bill id
        "`20250AB99`\t`202520260AB7`\tnot-int\t2024-12-02 00:00:00\t`I`\tNULL\t`Bad version.`\tx",
    ]
)

# Columns: district, session, full name, house, last, first, ..., party@11, ...
_LEGISLATORS = "\n".join(
    [
        "`SD02`\t`20252026`\t`McGuire, Mike`\t`S`\t`McGuire`\t`Mike`\t`McGuire`\tNULL\tNULL\t`Senator`\t`Senator`\t`DEM`\t`Y`\tx\tts\t`Y`",
        "`AD78`\t`20252026`\t`Ward, Chris`\t`A`\t`Ward`\t`Chris`\t`Ward`\tN\tNULL\t`AM`\t`AM`\t`REP`\t`Y`\tx\tts\t`Y`",
        "`SD02`\t`20252026`\t`McGuire, Mike`\t`S`\t`McGuire`\t`Mike`\t`McGuire`\tNULL\tNULL\t`Senator`\t`Senator`\t`DEM`\t`Y`\tx\tts\t`Y`",  # dup seat
        "`AD01`\t`bad-session`\t`X, Y`\t`A`\t`X`\t`Y`\t`X`\tN\tN\t`AM`\t`AM`\t`DEM`\t`Y`\tx\tts\t`Y`",
        "",  # blank tolerated
        "``\t`20252026`\t`No District`\t`A`\t`No`\t`D`\t`No`\tN\tN\t`AM`\t`AM`\t`DEM`\t`Y`\tx\tts\t`Y`",
        "`short`\t`row`",
    ]
)


def test_parse_ca_session() -> None:
    assert parse_ca_session("20252026") == "2025-2026"
    with pytest.raises(ValueError):
        parse_ca_session("2025")


def test_ca_bill_ref_matches_corpus_scheme() -> None:
    ref = ca_bill_ref("2025-2026", "AB13")
    assert ref.jurisdiction_id == "us-ca" and ref.identifier == "ab-13"
    assert ref.canonical_id.startswith("cb-")
    assert ref.canonical_id == ca_bill_ref("2025-2026", "AB013").canonical_id  # number normalized
    with pytest.raises(ValueError):
        ca_bill_ref("2025-2026", "13AB")


def test_parse_bill_titles_newest_subject_oldest_date() -> None:
    titles = parse_bill_titles(_VERSIONS)
    assert [t.raw_bill_id for t in titles] == ["202520260AB13", "202520260HR1"]
    ab13 = titles[0]
    assert ab13.subject == "Surplus land: exempt surplus land."  # version 95 < 99 -> newest
    assert ab13.introduced_date == "2025-01-02"  # oldest version date
    assert ab13.session == "2025-2026" and ab13.measure == "AB13"


def test_ca_bill_row_builds_contract_row() -> None:
    title = CaBillTitle(
        raw_bill_id="202520260AB13",
        session="2025-2026",
        measure="AB13",
        subject="Surplus land.",
        introduced_date="2025-01-02",
    )
    row = ca_bill_row(title, source_url=_URL, first_observed_at=_OBS)
    assert row.entity_type == "bill"
    assert row.canonical_id == ca_bill_ref("2025-2026", "AB13").canonical_id
    assert row.display_name == "AB13: Surplus land."
    assert row.external_ids == ["ca_leginfo:202520260ab13"]
    assert row.known_at.date().isoformat() == "2025-01-02"
    assert row.enrichment_status == "pending"


def test_ca_bill_row_future_introduction_clamps_known_at() -> None:
    title = CaBillTitle(
        raw_bill_id="202520260AB99",
        session="2025-2026",
        measure="AB99",
        subject="Future bill.",
        introduced_date="2030-01-01",  # after first_observed_at
    )
    row = ca_bill_row(title, source_url=_URL, first_observed_at=_OBS)
    assert row.known_at == _OBS  # known_at never precedes... never exceeds observation


def test_parse_ca_legislators() -> None:
    records = parse_ca_legislators(_LEGISLATORS, source_url=_URL, first_observed_at=_OBS)
    assert [r.source_record_id for r in records] == ["2025-2026:SD02", "2025-2026:AD78"]
    mcguire = records[0]
    assert mcguire.display_name == "Mike McGuire"
    assert mcguire.entity_type == "person"
    assert mcguire.jurisdiction == "us-ca"
    assert any(e.system == "ca_leginfo_seat" for e in mcguire.external_ids)
    assert mcguire.provenance.known_at.year == 2025  # session start


def test_ca_session_of_extraordinary_sessions_stay_distinct() -> None:
    from src.graph.ingest.ca_leginfo import ca_session_of

    assert ca_session_of("202520260AB1") == "2025-2026"
    assert ca_session_of("202520261AB1") == "2025-2026-x1"
    assert ca_session_of("201320142AB1") == "2013-2014-x2"
    with pytest.raises(ValueError):
        ca_session_of("2025AB1")
    # regular vs extraordinary AB1 mint DIFFERENT canonical bills
    regular = ca_bill_ref(ca_session_of("202520260AB1"), "AB1").canonical_id
    special = ca_bill_ref(ca_session_of("202520261AB1"), "AB1").canonical_id
    assert regular != special


def test_parse_bill_titles_keeps_special_session_separate() -> None:
    versions = "\n".join(
        [
            "`20250AB199INT`\t`202520260AB1`\t99\t2025-01-02 00:00:00\t`I`\tNULL\t`Regular AB1.`\tx",
            "`20251AB199INT`\t`202520261AB1`\t99\t2025-06-02 00:00:00\t`I`\tNULL\t`Special AB1.`\tx",
        ]
    )
    titles = parse_bill_titles(versions)
    assert len(titles) == 2
    sessions = {t.session for t in titles}
    assert sessions == {"2025-2026", "2025-2026-x1"}
    ids = {ca_bill_ref(t.session, t.measure).canonical_id for t in titles}
    assert len(ids) == 2  # no collision
