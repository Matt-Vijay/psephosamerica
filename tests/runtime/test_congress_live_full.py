"""Tests for src/runtime/congress_live_full.py."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.ingest.congress.congress_api import (
    CongressAPIClient,
    bill_detail_url,
    bills_url,
    committees_url,
    cosponsors_url,
    member_detail_url,
    members_url,
)
from src.ingest.congress.house_votes import parse_house_vote_xml
from src.ingest.congress.models import BillRecord, CommitteeRecord, CosponsorRecord, MemberRecord
from src.ingest.congress.senate_votes import parse_senate_vote_xml
from src.runtime.congress_options import CongressVoteCoverage
from src.runtime.congress_live_full import run_live_congress_load_full
from src.runtime.congress_votes import VoteFetchResult
from tests.support.congress_live_fixtures import (
    bill_detail_item,
    bill_detail_response,
    bill_item,
    bills_page,
    committee_item,
    committees_page,
    cosponsor_item,
    cosponsors_page,
    house_vote_xml,
    member_detail_item,
    member_detail_response,
    member_item,
    members_page,
    senate_vote_xml,
)


# ---------------------------------------------------------------------------
# Shared fixture builders
# ---------------------------------------------------------------------------


def _member(
    bioguide_id: str = "A000001",
    *,
    chamber: str = "house",
    lis_member_id: str | None = None,
) -> MemberRecord:
    return MemberRecord(
        bioguide_id=bioguide_id,
        first_name="Jane",
        last_name="Doe",
        full_name="Jane Doe",
        chamber=chamber,
        lis_member_id=lis_member_id,
    )


def _committee(code: str = "hsag00") -> CommitteeRecord:
    return CommitteeRecord(
        committee_code=code,
        congress=119,
        chamber="house",
        committee_type="standing",
        name="Agriculture",
    )


def _bill(bill_number: int = 1) -> BillRecord:
    return BillRecord(
        congress=119,
        bill_type="hr",
        bill_number=bill_number,
        title="A bill",
    )


def _cosponsor(bill_number: int = 1) -> CosponsorRecord:
    return CosponsorRecord(
        congress=119,
        bill_type="hr",
        bill_number=bill_number,
        bioguide_id="B000001",
    )


def _settings(api_key: str = "test-key") -> Any:
    s = MagicMock()
    s.congress_api_key = api_key
    return s


# ---------------------------------------------------------------------------
# Shared patch context — eliminates repeated 8-patch blocks
# ---------------------------------------------------------------------------

_MOD = "src.runtime.congress_live_full"


@dataclass
class _PatchedEnv:
    """Holds all mocks produced by ``_patched_env`` for direct inspection."""

    client_cls: MagicMock = field(default_factory=MagicMock)
    fetch_members: MagicMock = field(default_factory=MagicMock)
    fetch_member_detail_specs: MagicMock = field(default_factory=MagicMock)
    fetch_committees: MagicMock = field(default_factory=MagicMock)
    fetch_bills: MagicMock = field(default_factory=MagicMock)
    fetch_primary_sponsor_specs: MagicMock = field(default_factory=MagicMock)
    fetch_cosponsors_for_bills: MagicMock = field(default_factory=MagicMock)
    fetch_congress_vote_records: MagicMock = field(default_factory=MagicMock)
    run_congress_load_runtime: MagicMock = field(default_factory=MagicMock)


@contextmanager
def _patched_env(
    *,
    members: list | None = None,
    member_terms: list | None = None,
    committees: list | None = None,
    memberships: list | None = None,
    bills: list | None = None,
    primary_sponsors: list | None = None,
    cosponsors: list | None = None,
    vote_events: list | None = None,
    vote_casts: list | None = None,
    runtime_return: Any = None,
):
    """Context manager that patches every dependency of ``run_live_congress_load_full``.

    Yields a ``_PatchedEnv`` so callers can inspect / override individual mocks.
    """
    env = _PatchedEnv()
    with (
        patch(f"{_MOD}.CongressAPIClient") as env.client_cls,
        patch(f"{_MOD}.fetch_members", return_value=members or []) as env.fetch_members,
        patch(
            f"{_MOD}.fetch_member_detail_specs",
            return_value=(member_terms or [], memberships or []),
        ) as env.fetch_member_detail_specs,
        patch(f"{_MOD}.fetch_committees", return_value=committees or []) as env.fetch_committees,
        patch(f"{_MOD}.fetch_bills", return_value=bills or []) as env.fetch_bills,
        patch(
            f"{_MOD}.fetch_primary_sponsor_specs", return_value=primary_sponsors or []
        ) as env.fetch_primary_sponsor_specs,
        patch(
            f"{_MOD}.fetch_cosponsors_for_bills", return_value=cosponsors or []
        ) as env.fetch_cosponsors_for_bills,
        patch(
            f"{_MOD}.fetch_congress_vote_records",
            return_value=MagicMock(vote_events=vote_events or [], vote_casts=vote_casts or []),
        ) as env.fetch_congress_vote_records,
        patch(
            f"{_MOD}.run_congress_load_runtime", return_value=runtime_return or MagicMock()
        ) as env.run_congress_load_runtime,
    ):
        env.client_cls.return_value.__enter__.return_value = MagicMock()
        yield env


class _FixtureCongressAPIClient(CongressAPIClient):
    def __init__(self, api_key: str, *, response_map: dict[str, dict[str, Any]]) -> None:
        self.api_key = api_key
        self._response_map = response_map
        self._base_url = "fixture://congress"

    def close(self) -> None:
        return None

    def __enter__(self) -> "_FixtureCongressAPIClient":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def _get(self, url: str) -> dict[str, Any]:
        try:
            return self._response_map[url]
        except KeyError as exc:
            raise AssertionError(f"Unexpected fixture Congress API URL: {url}") from exc


def _fixture_response_map(
    *,
    congress: int,
    member_pages: list[dict[str, Any]],
    committee_pages: list[dict[str, Any]],
    bill_pages: list[dict[str, Any]],
    cosponsor_pages: dict[tuple[int, str, int], list[dict[str, Any]]],
    member_details: dict[str, dict[str, Any]],
    bill_details: dict[tuple[int, str, int], dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    response_map: dict[str, dict[str, Any]] = {
        members_url(congress): member_pages[0],
        committees_url(congress): committee_pages[0],
        bills_url(congress): bill_pages[0],
    }

    for page in member_pages[1:]:
        next_url = response_map[members_url(congress)]["pagination"]["next"]
        response_map[next_url] = page

    for page in committee_pages[1:]:
        next_url = response_map[committees_url(congress)]["pagination"]["next"]
        response_map[next_url] = page

    for page in bill_pages[1:]:
        next_url = response_map[bills_url(congress)]["pagination"]["next"]
        response_map[next_url] = page

    for bioguide_id, payload in member_details.items():
        response_map[member_detail_url(bioguide_id)] = member_detail_response(payload)

    for bill_key, payload in bill_details.items():
        response_map[bill_detail_url(*bill_key)] = bill_detail_response(payload)

    for bill_key, pages in cosponsor_pages.items():
        response_map[cosponsors_url(*bill_key)] = pages[0]

    return response_map


def _fixture_vote_result() -> VoteFetchResult:
    house_event, house_casts = parse_house_vote_xml(
        house_vote_xml(
            roll_call_number=12,
            year=2025,
            bioguide_id="H000001",
            legislator_name="Tubman",
            state="NY",
        )
    )
    senate_event, senate_casts = parse_senate_vote_xml(
        senate_vote_xml(
            congress=119,
            session=1,
            roll_call_number=44,
            lis_member_id="S001",
            member_full="Sumner (R-MA)",
        )
    )
    return VoteFetchResult(
        vote_events=[house_event, senate_event],
        vote_casts=[*house_casts, *senate_casts],
    )


def _run_fixture_chain(
    *,
    congress: int = 119,
    response_map: dict[str, dict[str, Any]],
    vote_result: VoteFetchResult | None = None,
    include_votes: bool = False,
    house_vote_year: int | None = None,
    senate_session: int | None = None,
) -> tuple[Any, Any, Any]:
    captured: list[Any] = []
    fake_result = MagicMock()

    def _capture(conn: Any, inputs: Any) -> Any:
        captured.append(inputs)
        return fake_result

    with (
        patch(
            f"{_MOD}.CongressAPIClient",
            side_effect=lambda api_key: _FixtureCongressAPIClient(
                api_key,
                response_map=response_map,
            ),
        ),
        patch(
            f"{_MOD}.fetch_congress_vote_records",
            return_value=vote_result or VoteFetchResult(vote_events=[], vote_casts=[]),
        ),
        patch(f"{_MOD}.run_congress_load_runtime", side_effect=_capture),
    ):
        result = run_live_congress_load_full(
            MagicMock(),
            _settings(),
            congress=congress,
            include_votes=include_votes,
            house_vote_year=house_vote_year,
            senate_session=senate_session,
        )

    assert captured, "run_congress_load_runtime was never called"
    return result, captured[0], fake_result


# ---------------------------------------------------------------------------
# Convenience runner — returns (result, captured_inputs, fake_runtime_result)
# ---------------------------------------------------------------------------


def _run(
    *,
    members: list | None = None,
    member_terms: list | None = None,
    committees: list | None = None,
    memberships: list | None = None,
    bills: list | None = None,
    primary_sponsors: list | None = None,
    cosponsors: list | None = None,
    vote_events: list | None = None,
    vote_casts: list | None = None,
    include_votes: bool = False,
    house_vote_year: int | None = None,
    senate_session: int | None = None,
    congress: int = 119,
    api_key: str = "test-key",
) -> tuple[Any, Any, Any]:
    conn = MagicMock()
    settings = _settings(api_key=api_key)
    fake_result = MagicMock()
    captured: list = []

    def _capture(c, inputs):
        captured.append(inputs)
        return fake_result

    with _patched_env(
        members=members,
        member_terms=member_terms,
        committees=committees,
        memberships=memberships,
        bills=bills,
        primary_sponsors=primary_sponsors,
        cosponsors=cosponsors,
        vote_events=vote_events,
        vote_casts=vote_casts,
    ) as env:
        env.run_congress_load_runtime.side_effect = _capture
        result = run_live_congress_load_full(
            conn,
            settings,
            congress=congress,
            include_votes=include_votes,
            house_vote_year=house_vote_year,
            senate_session=senate_session,
        )

    return result, captured[0] if captured else None, fake_result


# ---------------------------------------------------------------------------
# CongressIngestInputs wiring
# ---------------------------------------------------------------------------


class TestInputsWiring:
    def test_members_in_inputs(self):
        m = _member()
        _, inputs, _ = _run(members=[m])
        assert inputs.members == [m]

    def test_member_terms_in_inputs(self):
        term = MagicMock()
        _, inputs, _ = _run(member_terms=[term])
        assert inputs.member_terms == [term]

    def test_committees_in_inputs(self):
        c = _committee()
        _, inputs, _ = _run(committees=[c])
        assert inputs.committees == [c]

    def test_memberships_in_inputs(self):
        ms = MagicMock()
        _, inputs, _ = _run(memberships=[ms])
        assert inputs.memberships == [ms]

    def test_bills_in_inputs(self):
        b = _bill()
        _, inputs, _ = _run(bills=[b])
        assert inputs.bills == [b]

    def test_primary_sponsors_in_inputs(self):
        ps = MagicMock()
        _, inputs, _ = _run(primary_sponsors=[ps])
        assert inputs.primary_sponsors == [ps]

    def test_cosponsors_in_inputs(self):
        cs = _cosponsor()
        _, inputs, _ = _run(cosponsors=[cs])
        assert inputs.cosponsors == [cs]

    def test_empty_lists_when_no_records(self):
        _, inputs, _ = _run()
        assert inputs.members == []
        assert inputs.member_terms == []
        assert inputs.committees == []
        assert inputs.memberships == []
        assert inputs.bills == []
        assert inputs.primary_sponsors == []
        assert inputs.cosponsors == []


# ---------------------------------------------------------------------------
# Vote handling
# ---------------------------------------------------------------------------


class TestVoteHandling:
    def test_include_votes_false_gives_empty_vote_lists(self):
        _, inputs, _ = _run(include_votes=False)
        assert inputs.vote_events == []
        assert inputs.vote_casts == []

    def test_include_votes_false_does_not_call_vote_fetcher(self):
        with _patched_env() as env:
            run_live_congress_load_full(MagicMock(), _settings(), congress=119, include_votes=False)
        env.fetch_congress_vote_records.assert_not_called()

    def test_include_votes_true_populates_vote_lists(self):
        ve = MagicMock()
        vc = MagicMock()
        _, inputs, _ = _run(
            members=[_member("S000001", chamber="senate", lis_member_id="S001")],
            include_votes=True,
            vote_events=[ve],
            vote_casts=[vc],
        )
        assert inputs.vote_events == [ve]
        assert inputs.vote_casts == [vc]

    def test_include_votes_true_passes_year_and_session(self):
        with _patched_env(
            members=[_member("S000001", chamber="senate", lis_member_id="S001")]
        ) as env:
            run_live_congress_load_full(
                MagicMock(),
                _settings(),
                congress=119,
                include_votes=True,
                house_vote_year=2025,
                senate_session=1,
            )
        env.fetch_congress_vote_records.assert_called_once_with(
            congress=119, house_vote_year=2025, senate_session=1
        )

    def test_include_votes_true_derives_default_year_and_session(self):
        coverage = CongressVoteCoverage(
            house_vote_year=2026, senate_session=2, explicit_request=False
        )

        with (
            _patched_env(
                members=[_member("S000001", chamber="senate", lis_member_id="S001")],
                vote_events=[MagicMock()],
                vote_casts=[MagicMock()],
            ) as env,
            patch(f"{_MOD}.resolve_congress_vote_coverage", return_value=coverage) as mock_coverage,
        ):
            run_live_congress_load_full(
                MagicMock(),
                _settings(),
                congress=119,
                include_votes=True,
            )

        mock_coverage.assert_called_once_with(119, house_vote_year=None, senate_session=None)
        env.fetch_congress_vote_records.assert_called_once_with(
            congress=119, house_vote_year=2026, senate_session=2
        )

    def test_include_votes_true_historical_congress_uses_real_derived_defaults(self):
        with _patched_env(
            members=[_member("S000001", chamber="senate", lis_member_id="S001")],
            vote_events=[MagicMock()],
            vote_casts=[MagicMock()],
        ) as env:
            run_live_congress_load_full(
                MagicMock(),
                _settings(),
                congress=118,
                include_votes=True,
            )

        env.fetch_congress_vote_records.assert_called_once_with(
            congress=118,
            house_vote_year=2024,
            senate_session=2,
        )

    def test_include_votes_true_raises_on_insufficient_senate_lis_coverage(self):
        with _patched_env(
            members=[_member("S000001", chamber="senate", lis_member_id=None)]
        ) as env:
            with pytest.raises(RuntimeError, match="Senate LIS identity coverage"):
                run_live_congress_load_full(
                    MagicMock(),
                    _settings(),
                    congress=119,
                    include_votes=True,
                    senate_session=1,
                )

        env.fetch_congress_vote_records.assert_not_called()
        env.run_congress_load_runtime.assert_not_called()

    def test_include_votes_true_derived_defaults_trigger_senate_lis_preflight(self):
        with _patched_env(
            members=[_member("S000001", chamber="senate", lis_member_id=None)],
        ) as env:
            with pytest.raises(RuntimeError, match=r"S000001"):
                run_live_congress_load_full(
                    MagicMock(),
                    _settings(),
                    congress=118,
                    include_votes=True,
                )

        env.fetch_congress_vote_records.assert_not_called()
        env.run_congress_load_runtime.assert_not_called()

    def test_include_votes_true_raises_when_default_coverage_loads_zero_votes(self):
        coverage = CongressVoteCoverage(
            house_vote_year=2026, senate_session=2, explicit_request=False
        )

        with (
            _patched_env(
                members=[_member("S000001", chamber="senate", lis_member_id="S001")]
            ) as env,
            patch(f"{_MOD}.resolve_congress_vote_coverage", return_value=coverage),
        ):
            with pytest.raises(RuntimeError, match="no votes were loaded"):
                run_live_congress_load_full(
                    MagicMock(),
                    _settings(),
                    congress=119,
                    include_votes=True,
                )

        env.run_congress_load_runtime.assert_not_called()

    def test_include_votes_true_historical_default_zero_votes_reports_resolved_scope(self):
        with _patched_env(
            members=[_member("S000001", chamber="senate", lis_member_id="S001")],
        ) as env:
            with pytest.raises(
                RuntimeError,
                match=r"house_vote_year=2024, senate_session=2",
            ):
                run_live_congress_load_full(
                    MagicMock(),
                    _settings(),
                    congress=118,
                    include_votes=True,
                )

        env.run_congress_load_runtime.assert_not_called()

    def test_include_votes_true_allows_explicit_empty_vote_scope(self):
        _, inputs, _ = _run(include_votes=True, house_vote_year=2025, vote_events=[], vote_casts=[])

        assert inputs.vote_events == []
        assert inputs.vote_casts == []


class TestFixtureDrivenRuntimeWiring:
    def test_real_fixture_chain_preserves_enrichment_and_votes(self):
        response_map = _fixture_response_map(
            congress=119,
            member_pages=[
                members_page(
                    [
                        member_item(
                            bioguideId="H000001",
                            firstName="Harriet",
                            lastName="Tubman",
                            directOrderName="Harriet Tubman",
                            state="NY",
                            terms={
                                "item": [
                                    {
                                        "chamber": "House of Representatives",
                                        "startYear": "2025-01-03",
                                    }
                                ]
                            },
                        ),
                        member_item(
                            bioguideId="S000001",
                            firstName="Charles",
                            lastName="Sumner",
                            directOrderName="Charles Sumner",
                            state="MA",
                            lisId="S001",
                            terms={"item": [{"chamber": "Senate", "startYear": "2025-01-03"}]},
                        ),
                    ]
                )
            ],
            committee_pages=[
                committees_page(
                    [
                        committee_item(
                            systemCode="hswm00",
                            chamber="House",
                            name="Committee on Ways and Means",
                        ),
                    ]
                )
            ],
            bill_pages=[
                bills_page(
                    [
                        bill_item(
                            congress=119,
                            type="HR",
                            number=7,
                            title="Congress Runtime Confidence Act",
                        ),
                    ]
                )
            ],
            cosponsor_pages={
                (119, "hr", 7): [
                    cosponsors_page(
                        [
                            cosponsor_item(
                                bioguideId="S000001",
                                sponsorshipDate="2025-01-16",
                            )
                        ]
                    )
                ]
            },
            member_details={
                "H000001": member_detail_item(
                    bioguideId="H000001",
                    firstName="Harriet",
                    lastName="Tubman",
                    directOrderName="Harriet Tubman",
                    state="NY",
                    terms={
                        "item": [
                            {
                                "congress": 119,
                                "chamber": "House of Representatives",
                                "startYear": "2025-01-03",
                                "stateCode": "NY",
                                "district": 10,
                            }
                        ]
                    },
                    committees={
                        "item": [
                            {
                                "committee": {
                                    "systemCode": "hswm00",
                                    "url": "https://api.congress.gov/v3/committee/hswm00",
                                },
                                "congress": 119,
                                "role": "Ranking Member",
                                "startDate": "2025-01-03",
                                "isCurrent": True,
                            }
                        ]
                    },
                ),
                "S000001": member_detail_item(
                    bioguideId="S000001",
                    firstName="Charles",
                    lastName="Sumner",
                    directOrderName="Charles Sumner",
                    state="MA",
                    terms={
                        "item": [
                            {
                                "congress": 119,
                                "chamber": "Senate",
                                "startYear": "2025-01-03",
                                "stateCode": "MA",
                            }
                        ]
                    },
                    committees={"item": []},
                ),
            },
            bill_details={
                (119, "hr", 7): bill_detail_item(
                    congress=119,
                    type="HR",
                    number=7,
                    title="Congress Runtime Confidence Act",
                    sponsors=[
                        {
                            "bioguideId": "H000001",
                            "sponsorshipDate": "2025-01-10",
                            "url": "https://api.congress.gov/v3/member/H000001",
                        }
                    ],
                ),
            },
        )

        _, inputs, _ = _run_fixture_chain(
            response_map=response_map,
            vote_result=_fixture_vote_result(),
            include_votes=True,
            house_vote_year=2025,
            senate_session=1,
        )

        assert [member.bioguide_id for member in inputs.members] == ["H000001", "S000001"]
        assert [
            (term.record.bioguide_id, term.chamber, term.state, term.district)
            for term in inputs.member_terms
        ] == [
            ("H000001", "house", "NY", 10),
            ("S000001", "senate", "MA", None),
        ]
        assert [
            (membership.bioguide_id, membership.committee_code, membership.role)
            for membership in inputs.memberships
        ] == [
            ("H000001", "hswm00", "ranking_member"),
        ]
        assert [
            (sponsor.record.bill_number, sponsor.bioguide_id) for sponsor in inputs.primary_sponsors
        ] == [
            (7, "H000001"),
        ]
        assert [
            (cosponsor.bill_number, cosponsor.bioguide_id) for cosponsor in inputs.cosponsors
        ] == [
            (7, "S000001"),
        ]
        assert {(event.chamber, event.roll_call_number) for event in inputs.vote_events} == {
            ("house", 12),
            ("senate", 44),
        }
        assert any(cast.bioguide_id == "H000001" for cast in inputs.vote_casts)
        assert any(cast.lis_member_id == "S001" for cast in inputs.vote_casts)

    def test_partial_detail_enrichment_keeps_available_rows(self):
        response_map = _fixture_response_map(
            congress=119,
            member_pages=[
                members_page(
                    [
                        member_item(
                            bioguideId="H000001",
                            firstName="Harriet",
                            lastName="Tubman",
                            directOrderName="Harriet Tubman",
                            state="NY",
                            terms={
                                "item": [
                                    {
                                        "chamber": "House of Representatives",
                                        "startYear": "2025-01-03",
                                    }
                                ]
                            },
                        ),
                        member_item(
                            bioguideId="S000001",
                            firstName="Charles",
                            lastName="Sumner",
                            directOrderName="Charles Sumner",
                            state="MA",
                            lisId="S001",
                            terms={"item": [{"chamber": "Senate", "startYear": "2025-01-03"}]},
                        ),
                    ]
                )
            ],
            committee_pages=[
                committees_page([committee_item(systemCode="hswm00", chamber="House")])
            ],
            bill_pages=[
                bills_page(
                    [
                        bill_item(congress=119, type="HR", number=7, title="Primary Sponsor Bill"),
                        bill_item(congress=119, type="S", number=8, title="Missing Sponsor Bill"),
                    ]
                )
            ],
            cosponsor_pages={
                (119, "hr", 7): [cosponsors_page([])],
                (119, "s", 8): [cosponsors_page([])],
            },
            member_details={
                "H000001": member_detail_item(
                    bioguideId="H000001",
                    firstName="Harriet",
                    lastName="Tubman",
                    directOrderName="Harriet Tubman",
                    state="NY",
                    terms={
                        "item": [
                            {
                                "congress": 119,
                                "chamber": "House of Representatives",
                                "startYear": "2025-01-03",
                                "stateCode": "NY",
                                "district": 10,
                            }
                        ]
                    },
                    committees={
                        "item": [
                            {
                                "committee": {"systemCode": "hswm00"},
                                "congress": 119,
                                "role": "Member",
                                "startDate": "2025-01-03",
                                "isCurrent": True,
                            }
                        ]
                    },
                ),
                "S000001": member_item(
                    bioguideId="S000001",
                    firstName="Charles",
                    lastName="Sumner",
                    directOrderName="Charles Sumner",
                    state="MA",
                    lisId="S001",
                    terms={"item": [{"chamber": "Senate", "startYear": "2025-01-03"}]},
                ),
            },
            bill_details={
                (119, "hr", 7): bill_detail_item(
                    congress=119,
                    type="HR",
                    number=7,
                    sponsors=[{"bioguideId": "H000001", "sponsorshipDate": "2025-01-10"}],
                ),
                (119, "s", 8): bill_detail_item(
                    congress=119,
                    type="S",
                    number=8,
                    sponsors=[],
                ),
            },
        )

        _, inputs, _ = _run_fixture_chain(response_map=response_map)

        assert [member.bioguide_id for member in inputs.members] == ["H000001", "S000001"]
        assert [(term.record.bioguide_id, term.congress) for term in inputs.member_terms] == [
            ("H000001", 119),
        ]
        assert [
            (membership.bioguide_id, membership.committee_code) for membership in inputs.memberships
        ] == [
            ("H000001", "hswm00"),
        ]
        assert [
            (sponsor.record.bill_number, sponsor.bioguide_id) for sponsor in inputs.primary_sponsors
        ] == [
            (7, "H000001"),
        ]
        assert [bill.bill_number for bill in inputs.bills] == [7, 8]


# ---------------------------------------------------------------------------
# Delegation and result propagation
# ---------------------------------------------------------------------------


class TestDelegation:
    def test_returns_result_from_runtime(self):
        expected = MagicMock()
        with _patched_env(runtime_return=expected):
            result = run_live_congress_load_full(MagicMock(), _settings(), congress=119)
        assert result is expected

    def test_conn_passed_to_runtime(self):
        conn = MagicMock()
        with _patched_env() as env:
            run_live_congress_load_full(conn, _settings(), congress=119)
        assert env.run_congress_load_runtime.call_args.args[0] is conn


# ---------------------------------------------------------------------------
# Client construction
# ---------------------------------------------------------------------------


class TestClientConstruction:
    def test_client_built_with_api_key(self):
        with _patched_env() as env:
            run_live_congress_load_full(
                MagicMock(), _settings(api_key="my-secret-key"), congress=119
            )
        env.client_cls.assert_called_once_with("my-secret-key")
