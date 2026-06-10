from __future__ import annotations

from datetime import UTC, date, datetime

from src.graph.ingest.congress_legislators import (
    DEFAULT_SINCE,
    legislator_source_record,
    parse_legislators,
)

_OBS = datetime(2024, 6, 1, tzinfo=UTC)


def _member(
    bioguide: str, *, terms: list[dict], lis: str | None = None, official: str = "Jane Doe"
) -> dict:
    ids = {"bioguide": bioguide}
    if lis:
        ids["lis"] = lis
    return {"id": ids, "name": {"official_full": official}, "terms": terms}


def test_in_window_member_maps_to_source_record() -> None:
    member = _member(
        "C000127",
        lis="S275",
        official="Maria Cantwell",
        terms=[
            {"type": "sen", "start": "2001-01-03", "end": "2007-01-03", "state": "WA"},
            {"type": "sen", "start": "2019-01-03", "end": "2025-01-03", "state": "WA"},
        ],
    )
    record = legislator_source_record(member, since=DEFAULT_SINCE, first_observed_at=_OBS)
    assert record is not None
    assert record.entity_type == "person"
    assert record.display_name == "Maria Cantwell"
    assert record.region == "WA"
    systems = {(e.system, e.value) for e in record.external_ids}
    assert ("bioguide", "C000127") in systems  # system lowercased, value preserved
    assert ("lis", "S275") in systems
    # known_at sits at the earliest IN-WINDOW term start (2019), not the 2001 term
    assert record.provenance.valid_from == date(2019, 1, 3)


def test_member_entirely_before_window_is_dropped() -> None:
    member = _member("X000001", terms=[{"start": "1995-01-03", "end": "2001-01-03", "state": "NY"}])
    assert legislator_source_record(member, since=DEFAULT_SINCE, first_observed_at=_OBS) is None


def test_member_without_bioguide_is_dropped() -> None:
    member = {"id": {}, "name": {"official_full": "No Id"}, "terms": [{"end": "2024-01-01"}]}
    assert legislator_source_record(member, since=DEFAULT_SINCE, first_observed_at=_OBS) is None


def test_name_falls_back_to_first_last() -> None:
    member = {
        "id": {"bioguide": "A000001"},
        "name": {"first": "Sam", "last": "Rayburn"},
        "terms": [{"start": "2021-01-03", "end": "2027-01-03", "state": "TX"}],
    }
    record = legislator_source_record(member, since=DEFAULT_SINCE, first_observed_at=_OBS)
    assert record is not None and record.display_name == "Sam Rayburn"


def test_term_with_unparseable_dates_is_ignored() -> None:
    member = _member(
        "B000002",
        terms=[
            {"start": "bad", "end": "also-bad"},
            {"start": "2023-01-03", "end": "2025-01-03", "state": "CA"},
        ],
    )
    record = legislator_source_record(member, since=DEFAULT_SINCE, first_observed_at=_OBS)
    assert record is not None and record.provenance.valid_from == date(2023, 1, 3)


def test_in_window_term_without_start_uses_observation_date() -> None:
    member = _member("D000004", terms=[{"end": "2024-01-03", "state": "OH"}])  # no start
    record = legislator_source_record(member, since=DEFAULT_SINCE, first_observed_at=_OBS)
    assert record is not None and record.provenance.valid_from == _OBS.date()


def test_parse_legislators_filters_window() -> None:
    records = [
        _member("IN0001", terms=[{"start": "2021-01-03", "end": "2027-01-03", "state": "CA"}]),
        _member("OLD001", terms=[{"start": "1990-01-03", "end": "1996-01-03", "state": "NY"}]),
    ]
    out = parse_legislators(records, first_observed_at=_OBS)
    assert [r.source_record_id for r in out] == ["IN0001"]
