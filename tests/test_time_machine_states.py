from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from src.time_machine.inventory import InventoryEntry
from src.time_machine.model import TABLE_SCHEMAS
from src.time_machine.states import normalize_openstates_zip


class _Rows:
    def __init__(self) -> None:
        self.rows: list[Mapping[str, Any]] = []

    def write(self, row: Mapping[str, Any]) -> None:
        self.rows.append(row)


def _csv(fieldnames: list[str], rows: list[dict[str, str]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _write_fixture(path: Path) -> None:
    prefix = "MI/2025-2026/MI_2025-2026"
    bill_id = "ocd-bill/11111111-1111-1111-1111-111111111111"
    vote_id = "ocd-vote/22222222-2222-2222-2222-222222222222"
    person_id = "ocd-person/33333333-3333-3333-3333-333333333333"
    organization_id = "ocd-organization/44444444-4444-4444-4444-444444444444"
    version_id = "55555555-5555-5555-5555-555555555555"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "README",
            "Open States Data Export\n\n"
            "State: MI\n"
            "Session: 2025-2026\n"
            "Generated At: 2026-06-23 23:11:13.802706\n",
        )
        archive.writestr(
            f"{prefix}_organizations.csv",
            _csv(
                ["id", "classification", "jurisdiction_id"],
                [
                    {
                        "id": organization_id,
                        "classification": "upper",
                        "jurisdiction_id": "ocd-jurisdiction/country:us/state:mi/government",
                    }
                ],
            ),
        )
        archive.writestr(
            f"{prefix}_bills.csv",
            _csv(
                [
                    "id",
                    "identifier",
                    "title",
                    "classification",
                    "subject",
                    "session_identifier",
                    "jurisdiction",
                    "organization_classification",
                ],
                [
                    {
                        "id": bill_id,
                        "identifier": "SB 1",
                        "title": "A real state bill",
                        "classification": "['bill']",
                        "subject": "['Courts', 'Public records']",
                        "session_identifier": "2025-2026",
                        "jurisdiction": "Michigan",
                        "organization_classification": "upper",
                    }
                ],
            ),
        )
        archive.writestr(
            f"{prefix}_bill_sources.csv",
            _csv(
                ["id", "note", "url", "bill_id"],
                [
                    {
                        "id": "bill-source-1",
                        "note": "",
                        "url": "https://legislature.mi.gov/Bills/Bill?ObjectName=2025-SB-0001",
                        "bill_id": bill_id,
                    }
                ],
            ),
        )
        archive.writestr(
            f"{prefix}_bill_actions.csv",
            _csv(
                [
                    "id",
                    "bill_id",
                    "organization_id",
                    "description",
                    "date",
                    "classification",
                    "order",
                ],
                [
                    {
                        "id": "action-1",
                        "bill_id": bill_id,
                        "organization_id": organization_id,
                        "description": "Introduced",
                        "date": "2025-01-15",
                        "classification": "['introduction']",
                        "order": "0",
                    },
                    {
                        "id": "action-future",
                        "bill_id": bill_id,
                        "organization_id": organization_id,
                        "description": "Effective on a future date",
                        "date": "2030-01-01",
                        "classification": "['became-law']",
                        "order": "1",
                    },
                ],
            ),
        )
        archive.writestr(
            f"{prefix}_bill_versions.csv",
            _csv(
                ["id", "bill_id", "note", "date", "classification", "extras"],
                [
                    {
                        "id": version_id,
                        "bill_id": bill_id,
                        "note": "Senate Introduced Bill",
                        "date": "2030-01-02",
                        "classification": "['introduced']",
                        "extras": "{}",
                    }
                ],
            ),
        )
        archive.writestr(
            f"{prefix}_bill_version_links.csv",
            _csv(
                ["id", "media_type", "url", "version_id"],
                [
                    {
                        "id": "version-link-1",
                        "media_type": "text/html",
                        "url": "https://legislature.mi.gov/documents/2025-SB-0001.htm",
                        "version_id": version_id,
                    }
                ],
            ),
        )
        archive.writestr(
            f"{prefix}_votes.csv",
            _csv(
                [
                    "id",
                    "identifier",
                    "motion_text",
                    "motion_classification",
                    "start_date",
                    "result",
                    "organization_id",
                    "bill_id",
                    "bill_action_id",
                    "jurisdiction",
                    "session_identifier",
                ],
                [
                    {
                        "id": vote_id,
                        "identifier": "Roll Call 1",
                        "motion_text": "Passage",
                        "motion_classification": "['passage']",
                        "start_date": "2025-02-03",
                        "result": "pass",
                        "organization_id": organization_id,
                        "bill_id": bill_id,
                        "bill_action_id": "action-1",
                        "jurisdiction": "Michigan",
                        "session_identifier": "2025-2026",
                    }
                ],
            ),
        )
        archive.writestr(
            f"{prefix}_vote_sources.csv",
            _csv(
                ["id", "note", "url", "vote_event_id"],
                [
                    {
                        "id": "vote-source-1",
                        "note": "",
                        "url": "https://legislature.mi.gov/journals/2025-SJ-02-03.htm",
                        "vote_event_id": vote_id,
                    }
                ],
            ),
        )
        archive.writestr(
            f"{prefix}_vote_people.csv",
            _csv(
                ["id", "vote_event_id", "option", "voter_name", "voter_id", "note"],
                [
                    {
                        "id": "member-vote-upstream-1",
                        "vote_event_id": vote_id,
                        "option": "yes",
                        "voter_name": "Ada Example",
                        "voter_id": person_id,
                        "note": "",
                    },
                    {
                        "id": "",
                        "vote_event_id": vote_id,
                        "option": "no",
                        "voter_name": "Unresolved Name",
                        "voter_id": "",
                        "note": "",
                    },
                ],
            ),
        )


def test_normalizes_one_openstates_zip_without_inventing_links_or_text(tmp_path: Path) -> None:
    zip_path = tmp_path / "Michigan_2025_2026_Regular_Session.zip"
    _write_fixture(zip_path)
    artifact = InventoryEntry(
        source_artifact_id="artifact:sha256:" + "a" * 64,
        source_family="openstates_bulk",
        relative_path="data/raw/openstates_bulk/" + zip_path.name,
        absolute_path=str(zip_path),
        source_url="https://data.openstates.org/",
        media_type="application/zip",
        content_sha256="a" * 64,
        byte_count=zip_path.stat().st_size,
        modified_at="2026-06-24T00:00:00Z",
        modified_ns=zip_path.stat().st_mtime_ns,
        observed_at="2026-06-24T00:00:00Z",
    )
    sinks = {table: _Rows() for table in TABLE_SCHEMAS}

    report = normalize_openstates_zip(zip_path, artifact, sinks)

    assert report.bills == 1
    assert report.actions == 2
    assert report.bill_text_versions == 1
    assert report.roll_calls == 1
    assert report.member_votes == 2
    assert report.people == 1
    assert report.unresolved_member_votes == 1

    source_artifact = sinks["source_artifacts"].rows[0]
    assert source_artifact["content_sha256"] == "a" * 64
    assert source_artifact["retained"] is True
    assert source_artifact["published_at"].isoformat().startswith("2026-06-23T23:11:13")

    bill = sinks["bills"].rows[0]
    assert bill["source_bill_id"] == "ocd-bill/11111111-1111-1111-1111-111111111111"
    assert bill["bill_id"].endswith(bill["source_bill_id"])
    assert bill["subjects"] == ["Courts", "Public records"]
    assert bill["source_url"].startswith("https://legislature.mi.gov/Bills/")
    assert bill["availability_basis"] == "local_observation"

    action = sinks["actions"].rows[0]
    assert action["event_at"].isoformat() == "2025-01-15T00:00:00+00:00"
    assert action["available_at"] == action["event_at"]
    assert action["availability_basis"] == "official_event_date"

    future_action = sinks["actions"].rows[1]
    assert future_action["event_at"].isoformat() == "2030-01-01T00:00:00+00:00"
    assert future_action["available_at"].isoformat() == "2026-06-24T00:00:00+00:00"
    assert future_action["availability_basis"] == "local_observation"

    version = sinks["bill_text_versions"].rows[0]
    assert version["source_url"].endswith("2025-SB-0001.htm")
    assert version["is_full_text"] is False
    assert version["text_content"] is None
    assert version["content_path"] is None
    assert version["event_at"].isoformat() == "2030-01-02T00:00:00+00:00"
    assert version["available_at"].isoformat() == "2026-06-24T00:00:00+00:00"
    assert version["availability_basis"] == "local_observation"

    roll_call = sinks["roll_calls"].rows[0]
    assert roll_call["source_roll_call_id"].startswith("ocd-vote/")
    assert roll_call["source_bill_id"] == bill["source_bill_id"]
    assert roll_call["source_url"].endswith("2025-SJ-02-03.htm")
    assert roll_call["chamber"] == "upper"

    resolved, unresolved = sinks["member_votes"].rows
    assert resolved["member_vote_id"] == "member-vote:openstates:member-vote-upstream-1"
    assert resolved["source_person_id"].startswith("ocd-person/")
    assert resolved["person_id"].endswith(resolved["source_person_id"])
    assert resolved["content_sha256"] == "a" * 64
    assert unresolved["person_id"] is None
    assert unresolved["source_person_id"] is None
    assert unresolved["member_name"] == "Unresolved Name"
    assert unresolved["member_vote_id"].startswith("member-vote:openstates:derived:")

    assert sinks["terms"].rows == []
    assert sinks["amendments"].rows == []
    assert sinks["law_links"].rows == []
