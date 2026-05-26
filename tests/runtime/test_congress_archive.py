"""Tests for src/runtime/congress_archive.py.

All external dependencies are mocked — no network, no DB, no filesystem.
Covers: inputs wiring, vote-option gating, client/archive forwarding,
        delegation and result propagation, manifest-path routing, and
        manifest validation.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from src.ingest.congress.archive import CongressArchive, CongressArchiveManifest
from src.ingest.congress.models import (
    BillRecord,
    CommitteeRecord,
    CosponsorRecord,
    MemberRecord,
)
from src.runtime.congress_archive import run_congress_archive_load
from src.runtime.congress_options import CongressLoadOptions


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _archive(congress: int = 119, root: Path | None = None) -> CongressArchive:
    return CongressArchive(root or Path("/fake/archive"), congress)


def _options(
    congress: int = 119,
    include_votes: bool = False,
    house_vote_year: int | None = None,
    senate_session: int | None = None,
) -> CongressLoadOptions:
    return CongressLoadOptions(
        congress=congress,
        include_votes=include_votes,
        house_vote_year=house_vote_year,
        senate_session=senate_session,
    )


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


def _vote_archive_result(
    events: list | None = None,
    casts: list | None = None,
) -> MagicMock:
    r = MagicMock()
    r.events = events or []
    r.casts = casts or []
    return r


def _fake_manifest(root: Path = Path("/fake/archive"), congress: int = 119) -> MagicMock:
    """Return a MagicMock shaped like CongressArchiveManifest."""
    m = MagicMock(spec=CongressArchiveManifest)
    m.congress = congress
    m.members = MagicMock()
    m.members.path = root / "members.json"
    return m


# ---------------------------------------------------------------------------
# Shared patch context
# ---------------------------------------------------------------------------

_MOD = "src.runtime.congress_archive"


@dataclass
class _PatchedEnv:
    archive_client_cls: MagicMock = field(default_factory=MagicMock)
    fetch_members: MagicMock = field(default_factory=MagicMock)
    fetch_committees: MagicMock = field(default_factory=MagicMock)
    fetch_bills: MagicMock = field(default_factory=MagicMock)
    fetch_cosponsors_for_bills: MagicMock = field(default_factory=MagicMock)
    load_member_detail_payload_map: MagicMock = field(default_factory=MagicMock)
    load_bill_detail_payload_map: MagicMock = field(default_factory=MagicMock)
    member_term_specs_from_detail: MagicMock = field(default_factory=MagicMock)
    committee_membership_specs_from_detail: MagicMock = field(default_factory=MagicMock)
    primary_sponsor_spec_from_bill_detail: MagicMock = field(default_factory=MagicMock)
    load_house_vote_records: MagicMock = field(default_factory=MagicMock)
    load_senate_vote_records: MagicMock = field(default_factory=MagicMock)
    run_congress_load_runtime: MagicMock = field(default_factory=MagicMock)
    load_manifest: MagicMock = field(default_factory=MagicMock)
    validate_congress_archive_manifest: MagicMock = field(default_factory=MagicMock)


@contextmanager
def _patched_env(
    *,
    members: list | None = None,
    committees: list | None = None,
    bills: list | None = None,
    cosponsors: list | None = None,
    member_terms: list | None = None,
    memberships: list | None = None,
    primary_sponsors: list | None = None,
    house_vote_result: MagicMock | None = None,
    senate_vote_result: MagicMock | None = None,
    runtime_return: Any = None,
    manifest_return: MagicMock | None = None,
):
    env = _PatchedEnv()

    # Default empty detail maps — no detail files present
    member_detail_map: dict = {}
    bill_detail_map: dict = {}

    # Default manifest — root at /fake/archive
    default_manifest = _fake_manifest()

    with (
        patch(f"{_MOD}.CongressArchiveClient") as env.archive_client_cls,
        patch(f"{_MOD}.fetch_members", return_value=members or []) as env.fetch_members,
        patch(f"{_MOD}.fetch_committees", return_value=committees or []) as env.fetch_committees,
        patch(f"{_MOD}.fetch_bills", return_value=bills or []) as env.fetch_bills,
        patch(
            f"{_MOD}.fetch_cosponsors_for_bills",
            return_value=cosponsors or [],
        ) as env.fetch_cosponsors_for_bills,
        patch(
            f"{_MOD}.load_member_detail_payload_map",
            return_value=member_detail_map,
        ) as env.load_member_detail_payload_map,
        patch(
            f"{_MOD}.load_bill_detail_payload_map",
            return_value=bill_detail_map,
        ) as env.load_bill_detail_payload_map,
        patch(
            f"{_MOD}.member_term_specs_from_detail",
            return_value=member_terms or [],
        ) as env.member_term_specs_from_detail,
        patch(
            f"{_MOD}.committee_membership_specs_from_detail",
            return_value=memberships or [],
        ) as env.committee_membership_specs_from_detail,
        patch(
            f"{_MOD}.primary_sponsor_spec_from_bill_detail",
            return_value=primary_sponsors[0] if primary_sponsors else None,
        ) as env.primary_sponsor_spec_from_bill_detail,
        patch(
            f"{_MOD}.load_house_vote_records",
            return_value=house_vote_result or _vote_archive_result(),
        ) as env.load_house_vote_records,
        patch(
            f"{_MOD}.load_senate_vote_records",
            return_value=senate_vote_result or _vote_archive_result(),
        ) as env.load_senate_vote_records,
        patch(
            f"{_MOD}.run_congress_load_runtime",
            return_value=runtime_return or MagicMock(),
        ) as env.run_congress_load_runtime,
        patch(
            f"{_MOD}.load_manifest",
            return_value=manifest_return or default_manifest,
        ) as env.load_manifest,
        patch(
            f"{_MOD}.validate_congress_archive_manifest"
        ) as env.validate_congress_archive_manifest,
    ):
        # Default: manifest is valid (no missing files)
        _valid_result = MagicMock()
        _valid_result.valid = True
        _valid_result.missing = ()
        env.validate_congress_archive_manifest.return_value = _valid_result
        yield env


# ---------------------------------------------------------------------------
# Convenience runner
# ---------------------------------------------------------------------------


def _run(
    *,
    members: list | None = None,
    committees: list | None = None,
    bills: list | None = None,
    cosponsors: list | None = None,
    member_terms: list | None = None,
    memberships: list | None = None,
    primary_sponsors: list | None = None,
    house_vote_result: MagicMock | None = None,
    senate_vote_result: MagicMock | None = None,
    options: CongressLoadOptions | None = None,
    archive: CongressArchive | None = None,
) -> tuple[Any, Any, Any]:
    conn = MagicMock()
    arc = archive or _archive()
    opts = options or _options()
    fake_result = MagicMock()
    captured: list = []

    def _capture(c, inputs):
        captured.append(inputs)
        return fake_result

    with _patched_env(
        members=members,
        committees=committees,
        bills=bills,
        cosponsors=cosponsors,
        member_terms=member_terms,
        memberships=memberships,
        primary_sponsors=primary_sponsors,
        house_vote_result=house_vote_result,
        senate_vote_result=senate_vote_result,
    ) as env:
        env.run_congress_load_runtime.side_effect = _capture
        result = run_congress_archive_load(conn, arc, opts)

    return result, captured[0] if captured else None, fake_result


# ---------------------------------------------------------------------------
# CongressIngestInputs wiring — list records
# ---------------------------------------------------------------------------


class TestListRecordsWiring:
    def test_members_in_inputs(self):
        m = _member()
        _, inputs, _ = _run(members=[m])
        assert inputs.members == [m]

    def test_committees_in_inputs(self):
        c = _committee()
        _, inputs, _ = _run(committees=[c])
        assert inputs.committees == [c]

    def test_bills_in_inputs(self):
        b = _bill()
        _, inputs, _ = _run(bills=[b])
        assert inputs.bills == [b]

    def test_cosponsors_in_inputs(self):
        cs = _cosponsor()
        _, inputs, _ = _run(cosponsors=[cs])
        assert inputs.cosponsors == [cs]

    def test_empty_lists_when_no_records(self):
        _, inputs, _ = _run()
        assert inputs.members == []
        assert inputs.committees == []
        assert inputs.bills == []
        assert inputs.cosponsors == []


# ---------------------------------------------------------------------------
# CongressIngestInputs wiring — detail enrichment
# ---------------------------------------------------------------------------


class TestDetailEnrichmentWiring:
    def test_member_terms_populated_from_detail(self):
        term = MagicMock()
        # Need at least one member so the detail-enrichment loop iterates
        _, inputs, _ = _run(members=[_member()], member_terms=[term])
        assert term in inputs.member_terms

    def test_memberships_populated_from_detail(self):
        ms = MagicMock()
        # Need at least one member so the detail-enrichment loop iterates
        _, inputs, _ = _run(members=[_member()], memberships=[ms])
        assert ms in inputs.memberships

    def test_member_detail_map_queried_for_each_member(self):
        m1 = _member("A000001")
        m2 = _member("B000002")
        conn = MagicMock()
        with _patched_env(members=[m1, m2]) as env:
            run_congress_archive_load(conn, _archive(), _options())
        assert env.member_term_specs_from_detail.call_count == 2
        assert env.committee_membership_specs_from_detail.call_count == 2

    def test_bill_detail_map_queried_for_each_bill(self):
        b1 = _bill(1)
        b2 = _bill(2)
        # Provide a detail map entry for each bill so primary_sponsor_spec is called
        bill_detail_map = {
            (119, "hr", 1): {"sponsors": [{"bioguideId": "A000001"}]},
            (119, "hr", 2): {"sponsors": [{"bioguideId": "B000002"}]},
        }
        conn = MagicMock()
        with _patched_env(bills=[b1, b2]) as env:
            env.load_bill_detail_payload_map.return_value = bill_detail_map
            run_congress_archive_load(conn, _archive(), _options())
        assert env.primary_sponsor_spec_from_bill_detail.call_count == 2

    def test_primary_sponsors_skips_bills_without_detail(self):
        b = _bill(99)
        # bill_detail_map is empty by default — no detail for this bill
        _, inputs, _ = _run(bills=[b])
        assert inputs.primary_sponsors == []

    def test_primary_sponsors_skips_none_spec(self):
        b = _bill(1)
        bill_detail_map = {(119, "hr", 1): {"sponsors": []}}
        conn = MagicMock()
        with _patched_env(bills=[b]) as env:
            env.load_bill_detail_payload_map.return_value = bill_detail_map
            env.primary_sponsor_spec_from_bill_detail.return_value = None
            run_congress_archive_load(conn, _archive(), _options())
        _, inputs, _ = (None, env.run_congress_load_runtime.call_args.args[1], None)
        assert inputs.primary_sponsors == []


# ---------------------------------------------------------------------------
# Vote gating via options
# ---------------------------------------------------------------------------


class TestVoteHandling:
    def test_include_votes_false_gives_empty_vote_lists(self):
        _, inputs, _ = _run(options=_options(include_votes=False))
        assert inputs.vote_events == []
        assert inputs.vote_casts == []

    def test_include_votes_false_does_not_call_house_loader(self):
        with _patched_env() as env:
            run_congress_archive_load(MagicMock(), _archive(), _options(include_votes=False))
        env.load_house_vote_records.assert_not_called()

    def test_include_votes_false_does_not_call_senate_loader(self):
        with _patched_env() as env:
            run_congress_archive_load(MagicMock(), _archive(), _options(include_votes=False))
        env.load_senate_vote_records.assert_not_called()

    def test_include_votes_true_no_year_or_session_skips_both(self):
        opts = _options(include_votes=True)
        with _patched_env() as env:
            run_congress_archive_load(MagicMock(), _archive(), opts)
        env.load_house_vote_records.assert_not_called()
        env.load_senate_vote_records.assert_not_called()

    def test_house_votes_loaded_when_year_set(self):
        ve = MagicMock()
        vc = MagicMock()
        opts = _options(include_votes=True, house_vote_year=2025)
        _, inputs, _ = _run(
            options=opts,
            house_vote_result=_vote_archive_result(events=[ve], casts=[vc]),
        )
        assert ve in inputs.vote_events
        assert vc in inputs.vote_casts

    def test_senate_votes_loaded_when_session_set(self):
        ve = MagicMock()
        vc = MagicMock()
        opts = _options(include_votes=True, senate_session=1)
        _, inputs, _ = _run(
            options=opts,
            senate_vote_result=_vote_archive_result(events=[ve], casts=[vc]),
        )
        assert ve in inputs.vote_events
        assert vc in inputs.vote_casts

    def test_both_vote_legs_combined(self):
        h_ve, h_vc = MagicMock(), MagicMock()
        s_ve, s_vc = MagicMock(), MagicMock()
        opts = _options(include_votes=True, house_vote_year=2025, senate_session=1)
        _, inputs, _ = _run(
            options=opts,
            house_vote_result=_vote_archive_result(events=[h_ve], casts=[h_vc]),
            senate_vote_result=_vote_archive_result(events=[s_ve], casts=[s_vc]),
        )
        assert h_ve in inputs.vote_events
        assert s_ve in inputs.vote_events
        assert h_vc in inputs.vote_casts
        assert s_vc in inputs.vote_casts

    def test_house_loader_called_with_archive_root_and_year(self):
        root = Path("/my/data")
        arc = _archive(root=root)
        opts = _options(include_votes=True, house_vote_year=2025)
        with _patched_env() as env:
            run_congress_archive_load(MagicMock(), arc, opts)
        env.load_house_vote_records.assert_called_once_with(root, 2025)

    def test_senate_loader_called_with_archive_root_congress_session(self):
        root = Path("/my/data")
        arc = _archive(congress=119, root=root)
        opts = _options(congress=119, include_votes=True, senate_session=2)
        with _patched_env() as env:
            run_congress_archive_load(MagicMock(), arc, opts)
        env.load_senate_vote_records.assert_called_once_with(root, 119, 2)

    def test_house_skipped_when_year_is_none(self):
        opts = _options(include_votes=True, senate_session=1)
        with _patched_env() as env:
            run_congress_archive_load(MagicMock(), _archive(), opts)
        env.load_house_vote_records.assert_not_called()

    def test_senate_skipped_when_session_is_none(self):
        opts = _options(include_votes=True, house_vote_year=2025)
        with _patched_env() as env:
            run_congress_archive_load(MagicMock(), _archive(), opts)
        env.load_senate_vote_records.assert_not_called()


# ---------------------------------------------------------------------------
# CongressArchiveClient construction
# ---------------------------------------------------------------------------


class TestArchiveClientConstruction:
    def test_client_built_with_archive(self):
        arc = _archive()
        with _patched_env() as env:
            run_congress_archive_load(MagicMock(), arc, _options())
        env.archive_client_cls.assert_called_once_with(arc)

    def test_client_instance_passed_to_fetch_members(self):
        arc = _archive(congress=118)
        with _patched_env() as env:
            fake_client = MagicMock()
            env.archive_client_cls.return_value = fake_client
            run_congress_archive_load(MagicMock(), arc, _options(congress=118))
        env.fetch_members.assert_called_once_with(fake_client, 118)

    def test_client_instance_passed_to_fetch_committees(self):
        with _patched_env() as env:
            fake_client = MagicMock()
            env.archive_client_cls.return_value = fake_client
            run_congress_archive_load(MagicMock(), _archive(), _options(congress=119))
        env.fetch_committees.assert_called_once_with(fake_client, 119)

    def test_client_instance_passed_to_fetch_bills(self):
        with _patched_env() as env:
            fake_client = MagicMock()
            env.archive_client_cls.return_value = fake_client
            run_congress_archive_load(MagicMock(), _archive(), _options(congress=119))
        env.fetch_bills.assert_called_once_with(fake_client, 119)

    def test_client_instance_passed_to_cosponsors(self):
        b = _bill()
        with _patched_env(bills=[b]) as env:
            fake_client = MagicMock()
            env.archive_client_cls.return_value = fake_client
            run_congress_archive_load(MagicMock(), _archive(), _options())
        env.fetch_cosponsors_for_bills.assert_called_once_with(fake_client, [b])

    def test_archive_passed_to_member_detail_map_loader(self):
        arc = _archive()
        with _patched_env() as env:
            run_congress_archive_load(MagicMock(), arc, _options())
        env.load_member_detail_payload_map.assert_called_once_with(arc)

    def test_archive_passed_to_bill_detail_map_loader(self):
        arc = _archive()
        with _patched_env() as env:
            run_congress_archive_load(MagicMock(), arc, _options())
        env.load_bill_detail_payload_map.assert_called_once_with(arc)


# ---------------------------------------------------------------------------
# Delegation and result propagation
# ---------------------------------------------------------------------------


class TestDelegation:
    def test_returns_result_from_runtime(self):
        expected = MagicMock()
        with _patched_env(runtime_return=expected):
            result = run_congress_archive_load(MagicMock(), _archive(), _options())
        assert result is expected

    def test_conn_forwarded_to_runtime(self):
        conn = MagicMock()
        with _patched_env() as env:
            run_congress_archive_load(conn, _archive(), _options())
        assert env.run_congress_load_runtime.call_args.args[0] is conn

    def test_congress_number_forwarded_to_list_fetchers(self):
        opts = _options(congress=118)
        with _patched_env() as env:
            run_congress_archive_load(MagicMock(), _archive(congress=118), opts)
        # Verify congress=118 passed to each list fetcher
        _, members_args, _ = env.fetch_members.mock_calls[0]
        assert members_args[1] == 118
        _, committees_args, _ = env.fetch_committees.mock_calls[0]
        assert committees_args[1] == 118
        _, bills_args, _ = env.fetch_bills.mock_calls[0]
        assert bills_args[1] == 118


# ---------------------------------------------------------------------------
# Directory-path input — existing behaviour preserved
# ---------------------------------------------------------------------------


class TestDirectoryPathInput:
    def test_directory_path_wraps_into_congress_archive(self):
        root = Path("/some/dir")
        opts = _options(congress=119)
        with _patched_env() as env:
            run_congress_archive_load(MagicMock(), root, opts)
        call_arg = env.archive_client_cls.call_args.args[0]
        assert isinstance(call_arg, CongressArchive)
        assert call_arg.root == root
        assert call_arg.congress == 119

    def test_directory_path_does_not_call_load_manifest(self):
        with _patched_env() as env:
            run_congress_archive_load(MagicMock(), Path("/some/dir"), _options())
        env.load_manifest.assert_not_called()

    def test_directory_path_does_not_call_validate_manifest(self):
        with _patched_env() as env:
            run_congress_archive_load(MagicMock(), Path("/some/dir"), _options())
        env.validate_congress_archive_manifest.assert_not_called()

    def test_congress_archive_input_does_not_call_load_manifest(self):
        with _patched_env() as env:
            run_congress_archive_load(MagicMock(), _archive(), _options())
        env.load_manifest.assert_not_called()

    def test_congress_archive_input_does_not_call_validate_manifest(self):
        with _patched_env() as env:
            run_congress_archive_load(MagicMock(), _archive(), _options())
        env.validate_congress_archive_manifest.assert_not_called()


# ---------------------------------------------------------------------------
# Manifest-path input — load, validate, then run
# ---------------------------------------------------------------------------


class TestManifestPathInput:
    def test_json_path_calls_load_manifest(self):
        manifest_path = Path("/fake/archive/manifest.json")
        with _patched_env() as env:
            run_congress_archive_load(MagicMock(), manifest_path, _options())
        env.load_manifest.assert_called_once_with(manifest_path)

    def test_json_path_calls_validate_manifest_with_loaded_manifest(self):
        manifest_path = Path("/fake/archive/manifest.json")
        manifest = _fake_manifest()
        with _patched_env(manifest_return=manifest) as env:
            run_congress_archive_load(MagicMock(), manifest_path, _options())
        env.validate_congress_archive_manifest.assert_called_once_with(manifest)

    def test_validate_called_before_client_construction(self):
        """validate_congress_archive_manifest must be called before any archive I/O begins."""
        manifest_path = Path("/fake/archive/manifest.json")
        call_order: list[str] = []

        valid_result = MagicMock()
        valid_result.valid = True
        valid_result.missing = ()

        with _patched_env() as env:
            env.validate_congress_archive_manifest.side_effect = lambda _m: (
                call_order.append("validate") or valid_result
            )
            env.archive_client_cls.side_effect = lambda _a: (
                call_order.append("client") or MagicMock()
            )
            run_congress_archive_load(MagicMock(), manifest_path, _options())

        assert call_order.index("validate") < call_order.index("client")

    def test_validation_error_propagates(self):
        manifest_path = Path("/fake/archive/manifest.json")
        invalid_result = MagicMock()
        invalid_result.valid = False
        invalid_result.missing = (MagicMock(label="members"),)
        with _patched_env() as env:
            env.validate_congress_archive_manifest.return_value = invalid_result
            raised = False
            try:
                run_congress_archive_load(MagicMock(), manifest_path, _options())
            except ValueError:
                raised = True
        assert raised

    def test_validation_error_prevents_runtime_call(self):
        manifest_path = Path("/fake/archive/manifest.json")
        invalid_result = MagicMock()
        invalid_result.valid = False
        invalid_result.missing = (MagicMock(label="bills"),)
        with _patched_env() as env:
            env.validate_congress_archive_manifest.return_value = invalid_result
            try:
                run_congress_archive_load(MagicMock(), manifest_path, _options())
            except ValueError:
                pass
        env.run_congress_load_runtime.assert_not_called()

    def test_archive_root_derived_from_manifest_members_path(self):
        """CongressArchive root must equal manifest.members.path.parent."""
        root = Path("/data/congress/119")
        manifest = _fake_manifest(root=root, congress=119)
        manifest_path = Path("/data/congress/119/manifest.json")

        with _patched_env(manifest_return=manifest) as env:
            run_congress_archive_load(MagicMock(), manifest_path, _options(congress=119))

        call_arg = env.archive_client_cls.call_args.args[0]
        assert isinstance(call_arg, CongressArchive)
        assert call_arg.root == root

    def test_archive_congress_derived_from_manifest(self):
        """CongressArchive.congress must equal manifest.congress."""
        manifest = _fake_manifest(congress=118)
        manifest_path = Path("/data/manifest.json")

        with _patched_env(manifest_return=manifest) as env:
            run_congress_archive_load(MagicMock(), manifest_path, _options(congress=118))

        call_arg = env.archive_client_cls.call_args.args[0]
        assert call_arg.congress == 118

    def test_non_json_path_treated_as_directory(self):
        """A Path without .json suffix must NOT trigger manifest loading."""
        dir_path = Path("/some/archive/dir")
        with _patched_env() as env:
            run_congress_archive_load(MagicMock(), dir_path, _options())
        env.load_manifest.assert_not_called()
        env.validate_congress_archive_manifest.assert_not_called()
