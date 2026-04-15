"""Tests for src/runtime/congress_live_full.py."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

from src.ingest.congress.models import BillRecord, CommitteeRecord, CosponsorRecord, MemberRecord
from src.runtime.congress_live_full import run_live_congress_load_full


# ---------------------------------------------------------------------------
# Shared fixture builders
# ---------------------------------------------------------------------------


def _member(bioguide_id: str = "A000001") -> MemberRecord:
    return MemberRecord(
        bioguide_id=bioguide_id,
        first_name="Jane",
        last_name="Doe",
        full_name="Jane Doe",
        chamber="house",
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
        patch(f"{_MOD}.fetch_member_detail_specs", return_value=(member_terms or [], memberships or [])) as env.fetch_member_detail_specs,
        patch(f"{_MOD}.fetch_committees", return_value=committees or []) as env.fetch_committees,
        patch(f"{_MOD}.fetch_bills", return_value=bills or []) as env.fetch_bills,
        patch(f"{_MOD}.fetch_primary_sponsor_specs", return_value=primary_sponsors or []) as env.fetch_primary_sponsor_specs,
        patch(f"{_MOD}.fetch_cosponsors_for_bills", return_value=cosponsors or []) as env.fetch_cosponsors_for_bills,
        patch(f"{_MOD}.fetch_congress_vote_records", return_value=MagicMock(vote_events=vote_events or [], vote_casts=vote_casts or [])) as env.fetch_congress_vote_records,
        patch(f"{_MOD}.run_congress_load_runtime", return_value=runtime_return or MagicMock()) as env.run_congress_load_runtime,
    ):
        env.client_cls.return_value.__enter__.return_value = MagicMock()
        yield env


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
        _, inputs, _ = _run(include_votes=True, vote_events=[ve], vote_casts=[vc])
        assert inputs.vote_events == [ve]
        assert inputs.vote_casts == [vc]

    def test_include_votes_true_passes_year_and_session(self):
        with _patched_env() as env:
            run_live_congress_load_full(
                MagicMock(), _settings(), congress=119,
                include_votes=True, house_vote_year=2025, senate_session=1,
            )
        env.fetch_congress_vote_records.assert_called_once_with(congress=119, house_vote_year=2025, senate_session=1)

    def test_include_votes_true_none_args_forwarded(self):
        with _patched_env() as env:
            run_live_congress_load_full(
                MagicMock(), _settings(), congress=119, include_votes=True,
            )
        env.fetch_congress_vote_records.assert_called_once_with(congress=119, house_vote_year=None, senate_session=None)


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
            run_live_congress_load_full(MagicMock(), _settings(api_key="my-secret-key"), congress=119)
        env.client_cls.assert_called_once_with("my-secret-key")
