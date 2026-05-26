"""Archive-driven integration tests for src/runtime/congress_archive.py.

All file I/O is real — temporary directories with generated JSON/XML content.
The only patched boundary is run_congress_load_runtime, which requires a live
DB connection that is not available in unit-test context.

Two exercises per scenario:
  1. directory path input  — a Path to a directory wraps into CongressArchive
  2. manifest path input   — a .json manifest file is loaded and validated
                             before the archive root is resolved

No network calls, no sys.modules injection, no binary fixture files.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.ingest.congress.archive import CongressArchive, manifest_from_existing_archive
from src.ingest.congress.archive_manifest import write_manifest
from src.runtime.congress_archive import run_congress_archive_load
from src.runtime.congress_options import CongressLoadOptions

# ---------------------------------------------------------------------------
# Module path for the single patched boundary
# ---------------------------------------------------------------------------

_MOD = "src.runtime.congress_archive"


# ---------------------------------------------------------------------------
# Minimal inline fixture content (generated; no binary blobs)
# ---------------------------------------------------------------------------

_CONGRESS = 119
_BIOGUIDE = "A000001"
_COSPONSOR_BIOGUIDE = "B000001"
_BILL_TYPE = "hr"
_BILL_NUMBER = 1
_BILL_STEM = f"{_CONGRESS}_{_BILL_TYPE}_{_BILL_NUMBER}"
_YEAR = 2025
_SESSION = 1


def _members_payload() -> dict[str, Any]:
    return {
        "members": [
            {
                "bioguideId": _BIOGUIDE,
                "firstName": "Jane",
                "lastName": "Doe",
                "directOrderName": "Jane Doe",
                "currentMember": True,
                # congress_api._parse_date requires ISO format; omit startYear/endYear
                # to avoid the year-only string that fromisoformat rejects.
                "terms": {
                    "item": [
                        {
                            "chamber": "House of Representatives",
                        }
                    ]
                },
            }
        ]
    }


def _committees_payload() -> dict[str, Any]:
    return {
        "committees": [
            {
                "systemCode": "hsag00",
                "chamber": "House",
                "committeeTypeCode": "standing",
                "name": "Committee on Agriculture",
            }
        ]
    }


def _bills_payload() -> dict[str, Any]:
    return {
        "bills": [
            {
                "congress": _CONGRESS,
                "type": "HR",
                "number": _BILL_NUMBER,
                "title": "A Test Bill",
            }
        ]
    }


def _member_detail_payload() -> dict[str, Any]:
    return {
        "member": {
            "bioguideId": _BIOGUIDE,
            "firstName": "Jane",
            "lastName": "Doe",
            "directOrderName": "Jane Doe",
            "state": "CA",
            "terms": {
                "item": [
                    {
                        "congress": _CONGRESS,
                        "chamber": "House of Representatives",
                        "startYear": "2023",
                        "stateCode": "CA",
                        "district": 12,
                    }
                ]
            },
            "committees": {
                "item": [
                    {
                        # member_committees.py reads committee.systemCode, not top-level systemCode
                        "committee": {"systemCode": "hsag00"},
                        "congress": _CONGRESS,
                        "startDate": "2023-01-03",
                    }
                ]
            },
        }
    }


def _bill_detail_payload() -> dict[str, Any]:
    return {
        "bill": {
            "congress": _CONGRESS,
            "type": "HR",
            "number": _BILL_NUMBER,
            "title": "A Test Bill",
            "sponsors": [
                {
                    "bioguideId": _BIOGUIDE,
                    "sponsorshipDate": "2025-01-10",
                }
            ],
        }
    }


def _cosponsors_payload() -> dict[str, Any]:
    return {
        "cosponsors": [
            {
                "bioguideId": _COSPONSOR_BIOGUIDE,
                "isOriginalCosponsor": False,
            }
        ]
    }


# ---------------------------------------------------------------------------
# Minimal vote XML fragments (real parseable XML; no DTD)
# ---------------------------------------------------------------------------

_HOUSE_INDEX_XML = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <vote-summary>
      <congress>119</congress>
      <session>1</session>
      <vote-total>
        <vote-number>1</vote-number>
        <vote-question>On Passage</vote-question>
        <vote-result>Passed</vote-result>
        <action-date>03-Jan-2025</action-date>
      </vote-total>
    </vote-summary>
""")

_HOUSE_VOTE_XML = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <rollcall-vote>
      <vote-metadata>
        <congress>119</congress>
        <session>1</session>
        <rollcall-num>1</rollcall-num>
        <vote-question>On Passage</vote-question>
        <vote-result>Passed</vote-result>
        <action-date date="2025-01-03">03-Jan-2025</action-date>
      </vote-metadata>
      <vote-data>
        <recorded-vote>
          <legislator name-id="A000001">Jane Doe</legislator>
          <vote>Yea</vote>
        </recorded-vote>
      </vote-data>
    </rollcall-vote>
""")

_SENATE_INDEX_XML = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <vote_summary>
      <votes>
        <vote>
          <vote_number>1</vote_number>
          <vote_date>January 3, 2025</vote_date>
          <question>On Passage</question>
          <vote_result>Passed</vote_result>
        </vote>
      </votes>
    </vote_summary>
""")

_SENATE_VOTE_XML = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <roll_call_vote>
      <congress>119</congress>
      <session>1</session>
      <vote_number>1</vote_number>
      <vote_question_text>On Passage</vote_question_text>
      <vote_result_text>Passed</vote_result_text>
      <vote_date>January 3, 2025</vote_date>
      <members>
        <member>
          <lis_member_id>S001</lis_member_id>
          <vote_cast>Yea</vote_cast>
        </member>
      </members>
    </roll_call_vote>
""")


# ---------------------------------------------------------------------------
# Fixture builder — writes a minimal but real archive tree
# ---------------------------------------------------------------------------


class _ArchiveFixture:
    """Temp archive directory populated with real JSON/XML files."""

    def __init__(self, root: Path, *, congress: int = _CONGRESS) -> None:
        self.root = root
        self.congress = congress

    def write_core(self) -> "_ArchiveFixture":
        """Write members.json, committees.json, bills.json."""
        (self.root / "members.json").write_text(json.dumps(_members_payload()), encoding="utf-8")
        (self.root / "committees.json").write_text(
            json.dumps(_committees_payload()), encoding="utf-8"
        )
        (self.root / "bills.json").write_text(json.dumps(_bills_payload()), encoding="utf-8")
        return self

    def write_member_detail(self) -> "_ArchiveFixture":
        d = self.root / "member_details"
        d.mkdir(exist_ok=True)
        (d / f"{_BIOGUIDE}.json").write_text(json.dumps(_member_detail_payload()), encoding="utf-8")
        return self

    def write_bill_detail(self) -> "_ArchiveFixture":
        d = self.root / "bill_details"
        d.mkdir(exist_ok=True)
        (d / f"{_BILL_STEM}.json").write_text(json.dumps(_bill_detail_payload()), encoding="utf-8")
        return self

    def write_cosponsors(self) -> "_ArchiveFixture":
        d = self.root / "cosponsors"
        d.mkdir(exist_ok=True)
        (d / f"{_BILL_STEM}.json").write_text(json.dumps(_cosponsors_payload()), encoding="utf-8")
        return self

    def write_house_votes(self) -> "_ArchiveFixture":
        house_year_dir = self.root / "house" / str(_YEAR)
        house_year_dir.mkdir(parents=True, exist_ok=True)
        (house_year_dir / "index.xml").write_text(_HOUSE_INDEX_XML, encoding="utf-8")
        (house_year_dir / "roll001.xml").write_text(_HOUSE_VOTE_XML, encoding="utf-8")
        return self

    def write_senate_votes(self) -> "_ArchiveFixture":
        prefix = f"vote{self.congress}{_SESSION}"
        senate_dir = self.root / "senate" / prefix
        senate_dir.mkdir(parents=True, exist_ok=True)
        (senate_dir / "vote_summary.xml").write_text(_SENATE_INDEX_XML, encoding="utf-8")
        vote_filename = f"vote_{self.congress}_{_SESSION}_{1:05d}.xml"
        (senate_dir / vote_filename).write_text(_SENATE_VOTE_XML, encoding="utf-8")
        return self

    def write_manifest(self) -> Path:
        """Write a manifest.json covering all files present in the tree and return its path."""
        archive = CongressArchive(self.root, self.congress)
        manifest = manifest_from_existing_archive(archive)
        return write_manifest(self.root / "manifest.json", manifest)


# ---------------------------------------------------------------------------
# Helper: capture CongressIngestInputs via patched runtime boundary
# ---------------------------------------------------------------------------


def _run_with_dir(
    root: Path,
    *,
    congress: int = _CONGRESS,
    include_votes: bool = False,
    house_vote_year: int | None = None,
    senate_session: int | None = None,
) -> Any:
    """Call run_congress_archive_load with a directory path; return captured inputs."""
    options = CongressLoadOptions(
        congress=congress,
        include_votes=include_votes,
        house_vote_year=house_vote_year,
        senate_session=senate_session,
    )
    captured: list = []

    def _capture(conn, inputs):
        captured.append(inputs)
        return MagicMock()

    with patch(f"{_MOD}.run_congress_load_runtime", side_effect=_capture):
        run_congress_archive_load(MagicMock(), root, options)

    assert captured, "run_congress_load_runtime was never called"
    return captured[0]


def _run_with_manifest(
    manifest_path: Path,
    *,
    congress: int = _CONGRESS,
    include_votes: bool = False,
    house_vote_year: int | None = None,
    senate_session: int | None = None,
) -> Any:
    """Call run_congress_archive_load with a manifest path; return captured inputs."""
    options = CongressLoadOptions(
        congress=congress,
        include_votes=include_votes,
        house_vote_year=house_vote_year,
        senate_session=senate_session,
    )
    captured: list = []

    def _capture(conn, inputs):
        captured.append(inputs)
        return MagicMock()

    with patch(f"{_MOD}.run_congress_load_runtime", side_effect=_capture):
        run_congress_archive_load(MagicMock(), manifest_path, options)

    assert captured, "run_congress_load_runtime was never called"
    return captured[0]


def _assert_full_archive_payload(inputs: Any) -> None:
    assert [member.bioguide_id for member in inputs.members] == [_BIOGUIDE]
    assert [(term.record.bioguide_id, term.congress) for term in inputs.member_terms] == [
        (_BIOGUIDE, _CONGRESS),
    ]
    assert [
        (membership.bioguide_id, membership.committee_code) for membership in inputs.memberships
    ] == [
        (_BIOGUIDE, "hsag00"),
    ]
    assert [committee.committee_code for committee in inputs.committees] == ["hsag00"]
    assert [bill.bill_number for bill in inputs.bills] == [_BILL_NUMBER]
    assert [
        (sponsor.record.bill_number, sponsor.bioguide_id) for sponsor in inputs.primary_sponsors
    ] == [
        (_BILL_NUMBER, _BIOGUIDE),
    ]
    assert [(cosponsor.bill_number, cosponsor.bioguide_id) for cosponsor in inputs.cosponsors] == [
        (_BILL_NUMBER, _COSPONSOR_BIOGUIDE),
    ]
    assert {(event.chamber, event.roll_call_number) for event in inputs.vote_events} == {
        ("house", 1),
        ("senate", 1),
    }
    assert any(cast.bioguide_id == _BIOGUIDE for cast in inputs.vote_casts)
    assert any(cast.lis_member_id == "S001" for cast in inputs.vote_casts)


# ===========================================================================
# Directory path input — real temp archive tree
# ===========================================================================


class TestDirectoryPathInput:
    """run_congress_archive_load(conn, Path(dir), options) — no manifest."""

    def test_members_loaded_from_real_file(self, tmp_path: Path) -> None:
        _ArchiveFixture(tmp_path).write_core()
        inputs = _run_with_dir(tmp_path)
        assert len(inputs.members) == 1
        assert inputs.members[0].bioguide_id == _BIOGUIDE

    def test_committees_loaded_from_real_file(self, tmp_path: Path) -> None:
        _ArchiveFixture(tmp_path).write_core()
        inputs = _run_with_dir(tmp_path)
        assert len(inputs.committees) == 1
        assert inputs.committees[0].committee_code == "hsag00"

    def test_bills_loaded_from_real_file(self, tmp_path: Path) -> None:
        _ArchiveFixture(tmp_path).write_core()
        inputs = _run_with_dir(tmp_path)
        assert len(inputs.bills) == 1
        assert inputs.bills[0].congress == _CONGRESS
        assert inputs.bills[0].bill_number == _BILL_NUMBER

    def test_cosponsors_loaded_from_real_file(self, tmp_path: Path) -> None:
        _ArchiveFixture(tmp_path).write_core().write_cosponsors()
        inputs = _run_with_dir(tmp_path)
        assert len(inputs.cosponsors) == 1
        assert inputs.cosponsors[0].bioguide_id == _COSPONSOR_BIOGUIDE

    def test_empty_cosponsors_when_file_absent(self, tmp_path: Path) -> None:
        _ArchiveFixture(tmp_path).write_core()
        inputs = _run_with_dir(tmp_path)
        assert inputs.cosponsors == []

    def test_member_terms_built_from_real_detail_file(self, tmp_path: Path) -> None:
        _ArchiveFixture(tmp_path).write_core().write_member_detail()
        inputs = _run_with_dir(tmp_path)
        assert len(inputs.member_terms) >= 1
        # MemberTermSpec carries the MemberRecord on .record, not a bare bioguide_id
        assert inputs.member_terms[0].record.bioguide_id == _BIOGUIDE
        assert inputs.member_terms[0].congress == _CONGRESS

    def test_committee_memberships_built_from_real_detail_file(self, tmp_path: Path) -> None:
        _ArchiveFixture(tmp_path).write_core().write_member_detail()
        inputs = _run_with_dir(tmp_path)
        assert len(inputs.memberships) >= 1
        assert inputs.memberships[0].bioguide_id == _BIOGUIDE
        assert inputs.memberships[0].committee_code == "hsag00"

    def test_primary_sponsors_built_from_real_bill_detail(self, tmp_path: Path) -> None:
        _ArchiveFixture(tmp_path).write_core().write_bill_detail()
        inputs = _run_with_dir(tmp_path)
        assert len(inputs.primary_sponsors) == 1
        assert inputs.primary_sponsors[0].bioguide_id == _BIOGUIDE

    def test_no_detail_dirs_gives_empty_enrichment(self, tmp_path: Path) -> None:
        _ArchiveFixture(tmp_path).write_core()
        inputs = _run_with_dir(tmp_path)
        assert inputs.member_terms == []
        assert inputs.memberships == []
        assert inputs.primary_sponsors == []

    def test_votes_excluded_when_include_votes_false(self, tmp_path: Path) -> None:
        _ArchiveFixture(tmp_path).write_core().write_house_votes().write_senate_votes()
        inputs = _run_with_dir(tmp_path, include_votes=False)
        assert inputs.vote_events == []
        assert inputs.vote_casts == []

    def test_house_votes_loaded_from_real_xml(self, tmp_path: Path) -> None:
        _ArchiveFixture(tmp_path).write_core().write_house_votes()
        inputs = _run_with_dir(tmp_path, include_votes=True, house_vote_year=_YEAR)
        assert len(inputs.vote_events) == 1
        assert inputs.vote_events[0].chamber == "house"
        assert inputs.vote_events[0].roll_call_number == 1
        assert len(inputs.vote_casts) == 1
        assert inputs.vote_casts[0].bioguide_id == _BIOGUIDE

    def test_senate_votes_loaded_from_real_xml(self, tmp_path: Path) -> None:
        _ArchiveFixture(tmp_path).write_core().write_senate_votes()
        inputs = _run_with_dir(tmp_path, include_votes=True, senate_session=_SESSION)
        assert len(inputs.vote_events) == 1
        assert inputs.vote_events[0].chamber == "senate"
        assert inputs.vote_events[0].roll_call_number == 1
        assert len(inputs.vote_casts) == 1
        assert inputs.vote_casts[0].lis_member_id == "S001"

    def test_both_vote_chambers_combined(self, tmp_path: Path) -> None:
        _ArchiveFixture(tmp_path).write_core().write_house_votes().write_senate_votes()
        inputs = _run_with_dir(
            tmp_path,
            include_votes=True,
            house_vote_year=_YEAR,
            senate_session=_SESSION,
        )
        chambers = {e.chamber for e in inputs.vote_events}
        assert chambers == {"house", "senate"}
        assert len(inputs.vote_events) == 2

    def test_full_archive_chain_preserves_enrichment_and_votes(self, tmp_path: Path) -> None:
        _ArchiveFixture(
            tmp_path
        ).write_core().write_member_detail().write_bill_detail().write_cosponsors().write_house_votes().write_senate_votes()
        inputs = _run_with_dir(
            tmp_path,
            include_votes=True,
            house_vote_year=_YEAR,
            senate_session=_SESSION,
        )
        _assert_full_archive_payload(inputs)


# ===========================================================================
# Manifest path input — real manifest.json referencing real files
# ===========================================================================


class TestManifestPathInput:
    """run_congress_archive_load(conn, Path(manifest.json), options) — full validation."""

    def test_members_loaded_via_manifest(self, tmp_path: Path) -> None:
        fix = (
            _ArchiveFixture(tmp_path)
            .write_core()
            .write_member_detail()
            .write_bill_detail()
            .write_cosponsors()
        )
        manifest_path = fix.write_manifest()
        inputs = _run_with_manifest(manifest_path)
        assert len(inputs.members) == 1
        assert inputs.members[0].bioguide_id == _BIOGUIDE

    def test_committees_loaded_via_manifest(self, tmp_path: Path) -> None:
        fix = (
            _ArchiveFixture(tmp_path)
            .write_core()
            .write_member_detail()
            .write_bill_detail()
            .write_cosponsors()
        )
        manifest_path = fix.write_manifest()
        inputs = _run_with_manifest(manifest_path)
        assert len(inputs.committees) == 1
        assert inputs.committees[0].committee_code == "hsag00"

    def test_bills_loaded_via_manifest(self, tmp_path: Path) -> None:
        fix = (
            _ArchiveFixture(tmp_path)
            .write_core()
            .write_member_detail()
            .write_bill_detail()
            .write_cosponsors()
        )
        manifest_path = fix.write_manifest()
        inputs = _run_with_manifest(manifest_path)
        assert len(inputs.bills) == 1
        assert inputs.bills[0].bill_number == _BILL_NUMBER

    def test_cosponsors_loaded_via_manifest(self, tmp_path: Path) -> None:
        fix = (
            _ArchiveFixture(tmp_path)
            .write_core()
            .write_member_detail()
            .write_bill_detail()
            .write_cosponsors()
        )
        manifest_path = fix.write_manifest()
        inputs = _run_with_manifest(manifest_path)
        assert len(inputs.cosponsors) == 1
        assert inputs.cosponsors[0].bioguide_id == _COSPONSOR_BIOGUIDE

    def test_member_terms_from_manifest_detail(self, tmp_path: Path) -> None:
        fix = (
            _ArchiveFixture(tmp_path)
            .write_core()
            .write_member_detail()
            .write_bill_detail()
            .write_cosponsors()
        )
        manifest_path = fix.write_manifest()
        inputs = _run_with_manifest(manifest_path)
        assert len(inputs.member_terms) >= 1
        # MemberTermSpec carries the MemberRecord on .record
        assert inputs.member_terms[0].record.bioguide_id == _BIOGUIDE

    def test_primary_sponsors_from_manifest_bill_detail(self, tmp_path: Path) -> None:
        fix = (
            _ArchiveFixture(tmp_path)
            .write_core()
            .write_member_detail()
            .write_bill_detail()
            .write_cosponsors()
        )
        manifest_path = fix.write_manifest()
        inputs = _run_with_manifest(manifest_path)
        assert len(inputs.primary_sponsors) == 1
        assert inputs.primary_sponsors[0].bioguide_id == _BIOGUIDE

    def test_manifest_full_archive_chain_preserves_enrichment_and_votes(
        self, tmp_path: Path
    ) -> None:
        fix = (
            _ArchiveFixture(tmp_path)
            .write_core()
            .write_member_detail()
            .write_bill_detail()
            .write_cosponsors()
            .write_house_votes()
            .write_senate_votes()
        )
        inputs = _run_with_manifest(
            fix.write_manifest(),
            include_votes=True,
            house_vote_year=_YEAR,
            senate_session=_SESSION,
        )
        _assert_full_archive_payload(inputs)

    def test_manifest_validation_raises_on_missing_file(self, tmp_path: Path) -> None:
        """Manifest referencing absent member_details file raises ValueError."""
        _ArchiveFixture(tmp_path).write_core()
        # Write a manifest that claims a member_details file exists but don't write it
        manifest_dict = {
            "congress": _CONGRESS,
            "members": "members.json",
            "committees": "committees.json",
            "bills": "bills.json",
            "cosponsors": [],
            "member_details": [
                {
                    "bioguide_id": _BIOGUIDE,
                    "path": f"member_details/{_BIOGUIDE}.json",
                }
            ],
            "bill_details": [],
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest_dict), encoding="utf-8")

        options = CongressLoadOptions(congress=_CONGRESS)
        with pytest.raises(ValueError, match="missing"):
            run_congress_archive_load(MagicMock(), manifest_path, options)

    def test_manifest_archive_root_resolves_to_manifest_parent(self, tmp_path: Path) -> None:
        """The CongressArchive root must equal the manifest file's parent directory."""
        fix = (
            _ArchiveFixture(tmp_path)
            .write_core()
            .write_member_detail()
            .write_bill_detail()
            .write_cosponsors()
        )
        manifest_path = fix.write_manifest()

        captured_archive: list[CongressArchive] = []

        original_client_cls = __import__(
            "src.ingest.congress.archive_client",
            fromlist=["CongressArchiveClient"],
        ).CongressArchiveClient

        def _spy_client(archive: CongressArchive):
            captured_archive.append(archive)
            return original_client_cls(archive)

        with patch(f"{_MOD}.CongressArchiveClient", side_effect=_spy_client):
            with patch(f"{_MOD}.run_congress_load_runtime", return_value=MagicMock()):
                run_congress_archive_load(
                    MagicMock(), manifest_path, CongressLoadOptions(congress=_CONGRESS)
                )

        assert captured_archive, "CongressArchiveClient was not constructed"
        assert captured_archive[0].root == tmp_path
        assert captured_archive[0].congress == _CONGRESS

    def test_non_json_suffix_treated_as_directory(self, tmp_path: Path) -> None:
        """A Path without .json suffix must NOT trigger manifest loading."""
        _ArchiveFixture(tmp_path).write_core()
        captured: list = []

        def _capture(conn, inputs):
            captured.append(inputs)
            return MagicMock()

        # Pass the directory itself — no .json suffix
        with patch(f"{_MOD}.run_congress_load_runtime", side_effect=_capture):
            with patch(f"{_MOD}.load_manifest") as mock_load:
                run_congress_archive_load(
                    MagicMock(), tmp_path, CongressLoadOptions(congress=_CONGRESS)
                )
                mock_load.assert_not_called()

        assert captured
