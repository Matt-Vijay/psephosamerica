from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID
from unittest.mock import patch

import pytest

from src.ingest.congress.archive_manifest import load_manifest
from src.runtime.congress_archive_materialize import (
    _fetch_collection_body,
    _write_json,
    _write_text,
    materialize_congress_archive,
)


_FETCH_MEMBERS = "src.runtime.congress_archive_materialize._fetch_members_body"
_FETCH_COMMITTEES = "src.runtime.congress_archive_materialize._fetch_committees_body"
_FETCH_BILLS = "src.runtime.congress_archive_materialize._fetch_bills_body"
_FETCH_MEMBER_DETAIL = "src.runtime.congress_archive_materialize._fetch_member_detail_body"
_FETCH_BILL_DETAIL = "src.runtime.congress_archive_materialize._fetch_bill_detail_body"
_FETCH_COSPONSORS = "src.runtime.congress_archive_materialize._fetch_cosponsors_body"
_FETCH_HOUSE_INDEX_XML = "src.runtime.congress_archive_materialize._fetch_house_vote_index_xml"
_FETCH_HOUSE_INDEX_ROWS = "src.runtime.congress_archive_materialize.fetch_house_vote_index"
_FETCH_HOUSE_VOTE_XML = "src.runtime.congress_archive_materialize._fetch_house_vote_xml"
_FETCH_SENATE_INDEX_XML = "src.runtime.congress_archive_materialize._fetch_senate_vote_index_xml"
_FETCH_SENATE_INDEX_ROWS = "src.runtime.congress_archive_materialize.fetch_senate_vote_index"
_FETCH_SENATE_VOTE_XML = "src.runtime.congress_archive_materialize._fetch_senate_vote_xml"


def _members_body() -> dict[str, object]:
    return {
        "members": [
            {
                "bioguideId": "P000197",
                "firstName": "Nancy",
                "lastName": "Pelosi",
                "directOrderName": "Pelosi, Nancy",
                "partyName": "Democratic",
                "state": "CA",
                "currentMember": True,
                "terms": {"item": [{"chamber": "House of Representatives", "startYear": "2023"}]},
            }
        ]
    }


def _committees_body() -> dict[str, object]:
    return {"committees": [{"systemCode": "HSAG", "name": "Agriculture", "chamber": "House"}]}


def _bills_body() -> dict[str, object]:
    return {
        "bills": [
            {
                "congress": 119,
                "type": "hr",
                "number": "1",
                "title": "For the people",
                "introducedDate": "2025-01-03",
            }
        ]
    }


def _member_detail_body() -> dict[str, object]:
    return {"member": {"bioguideId": "P000197", "firstName": "Nancy", "lastName": "Pelosi"}}


def _bill_detail_body() -> dict[str, object]:
    return {"bill": {"congress": 119, "type": "hr", "number": "1", "title": "For the people"}}


def _cosponsors_body() -> dict[str, object]:
    return {"cosponsors": [{"bioguideId": "A000360", "isOriginalCosponsor": True}]}


class _FakeCongressClient:
    def __init__(self, bodies: dict[str, dict[str, object]]) -> None:
        self._base_url = "https://api.congress.gov/v3/"
        self.bodies = bodies
        self.calls: list[str] = []

    def _get(self, url: str) -> dict[str, object]:
        self.calls.append(url)
        return self.bodies[url]


class _HouseIndexRow:
    def __init__(self, roll_call_number: int, source_url: str) -> None:
        self.roll_call_number = roll_call_number
        self.source_url = source_url


class _SenateIndexRow:
    def __init__(self, vote_number: int, source_url: str) -> None:
        self.vote_number = vote_number
        self.source_url = source_url


def test_materialize_congress_archive_writes_archive_and_manifest(tmp_path: Path) -> None:
    archive_root = tmp_path / "congress_119"

    with (
        patch(_FETCH_MEMBERS, return_value=_members_body()),
        patch(_FETCH_COMMITTEES, return_value=_committees_body()),
        patch(_FETCH_BILLS, return_value=_bills_body()),
        patch(_FETCH_MEMBER_DETAIL, return_value=_member_detail_body()),
        patch(_FETCH_BILL_DETAIL, return_value=_bill_detail_body()),
        patch(_FETCH_COSPONSORS, return_value=_cosponsors_body()),
    ):
        result = materialize_congress_archive(
            api_key="test-key",
            archive_root=archive_root,
            congress=119,
        )

    assert result.archive_root == archive_root
    assert result.manifest_path == archive_root / "manifest.json"
    assert result.member_count == 1
    assert result.committee_count == 1
    assert result.bill_count == 1
    assert result.member_detail_count == 1
    assert result.bill_detail_count == 1
    assert result.cosponsor_file_count == 1
    assert result.house_vote_count == 0
    assert result.senate_vote_count == 0

    assert (archive_root / "members.json").exists()
    assert (archive_root / "committees.json").exists()
    assert (archive_root / "bills.json").exists()
    assert (archive_root / "member_details" / "P000197.json").exists()
    assert (archive_root / "bill_details" / "119_hr_1.json").exists()
    assert (archive_root / "cosponsors" / "119_hr_1.json").exists()

    manifest = load_manifest(result.manifest_path)
    assert manifest.congress == 119
    assert [source.bioguide_id for source in manifest.member_details] == ["P000197"]
    assert [
        (source.congress, source.bill_type, source.bill_number) for source in manifest.bill_details
    ] == [(119, "hr", 1)]
    assert manifest.house_votes == ()
    assert manifest.senate_votes == ()


def test_congress_archive_private_writers_use_unique_temp_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    json_path = tmp_path / "members.json"
    text_path = tmp_path / "house" / "2025" / "index.xml"
    guarded_outputs = {json_path, text_path}
    seen_temp_targets: set[str] = set()
    original_open = Path.open

    def guarded_open(path: Path, *args: object, **kwargs: object):
        mode = str(args[0]) if args else str(kwargs.get("mode", "r"))
        if path in guarded_outputs and any(flag in mode for flag in ("w", "a", "x", "+")):
            raise AssertionError(f"direct final-path write: {path.name}")
        for final_path in guarded_outputs:
            prefix = f".{final_path.name}."
            if (
                path.parent == final_path.parent
                and path.name.startswith(prefix)
                and path.name.endswith(".tmp")
            ):
                UUID(path.name.removeprefix(prefix).removesuffix(".tmp"))
                seen_temp_targets.add(final_path.name)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)

    _write_json(json_path, {"members": [{"bioguideId": "P000197"}]})
    _write_text(text_path, "<votes />")

    assert json.loads(json_path.read_text(encoding="utf-8")) == {
        "members": [{"bioguideId": "P000197"}]
    }
    assert text_path.read_text(encoding="utf-8") == "<votes />"
    assert seen_temp_targets == {path.name for path in guarded_outputs}


def test_fetch_collection_body_rejects_off_origin_pagination_next() -> None:
    url = "https://api.congress.gov/v3/member/congress/119?format=json"
    client = _FakeCongressClient(
        {
            url: {
                "members": [{"bioguideId": "P000197"}],
                "pagination": {"next": "https://evil.example/leak"},
            }
        }
    )

    with pytest.raises(ValueError, match="pagination.next"):
        _fetch_collection_body(client, url, "members")  # type: ignore[arg-type]

    assert client.calls == [url]


def test_fetch_collection_body_rejects_repeated_pagination_next() -> None:
    url = "https://api.congress.gov/v3/member/congress/119?format=json"
    client = _FakeCongressClient(
        {
            url: {
                "members": [{"bioguideId": "P000197"}],
                "pagination": {"next": url},
            }
        }
    )

    with pytest.raises(ValueError, match="pagination.next repeated URL"):
        _fetch_collection_body(client, url, "members")  # type: ignore[arg-type]

    assert client.calls == [url]


def test_fetch_collection_body_enforces_page_cap() -> None:
    first_url = "https://api.congress.gov/v3/member/congress/119?limit=250&offset=0&format=json"
    second_url = "https://api.congress.gov/v3/member/congress/119?limit=250&offset=250&format=json"
    client = _FakeCongressClient(
        {
            first_url: {
                "members": [{"bioguideId": "P000197"}],
                "pagination": {"next": second_url},
            },
            second_url: {
                "members": [{"bioguideId": "A000360"}],
            },
        }
    )

    with (
        patch("src.ingest.congress.congress_api.MAX_PAGINATION_PAGES", 1),
        pytest.raises(ValueError, match="pagination exceeded maximum page count"),
    ):
        _fetch_collection_body(client, first_url, "members")  # type: ignore[arg-type]

    assert client.calls == [first_url]


def test_materialize_congress_archive_rejects_unsafe_member_detail_filename(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "congress_119"
    members_body = _members_body()
    members_body["members"][0]["bioguideId"] = "../../escaped"  # type: ignore[index]

    with (
        patch(_FETCH_MEMBERS, return_value=members_body),
        patch(_FETCH_COMMITTEES, return_value=_committees_body()),
        patch(_FETCH_BILLS, return_value={"bills": []}),
        patch(
            _FETCH_MEMBER_DETAIL, return_value={"member": {"bioguideId": "../../escaped"}}
        ) as mock_member_detail,
    ):
        with pytest.raises(ValueError):
            materialize_congress_archive(
                api_key="test-key",
                archive_root=archive_root,
                congress=119,
            )

    mock_member_detail.assert_not_called()
    assert not (tmp_path / "escaped.json").exists()


def test_materialize_congress_archive_rejects_unsafe_bill_detail_filename(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "congress_119"
    bills_body = _bills_body()
    bills_body["bills"][0]["type"] = "x/../../../escaped"  # type: ignore[index]

    with (
        patch(_FETCH_MEMBERS, return_value={"members": []}),
        patch(_FETCH_COMMITTEES, return_value=_committees_body()),
        patch(_FETCH_BILLS, return_value=bills_body),
        patch(_FETCH_BILL_DETAIL, return_value=_bill_detail_body()) as mock_bill_detail,
        patch(_FETCH_COSPONSORS, return_value=_cosponsors_body()) as mock_cosponsors,
    ):
        with pytest.raises(ValueError):
            materialize_congress_archive(
                api_key="test-key",
                archive_root=archive_root,
                congress=119,
            )

    mock_bill_detail.assert_not_called()
    mock_cosponsors.assert_not_called()
    assert not (tmp_path / "escaped_1.json").exists()


def test_materialize_congress_archive_writes_requested_vote_coverage(tmp_path: Path) -> None:
    archive_root = tmp_path / "congress_119"

    with (
        patch(_FETCH_MEMBERS, return_value=_members_body()),
        patch(_FETCH_COMMITTEES, return_value=_committees_body()),
        patch(_FETCH_BILLS, return_value=_bills_body()),
        patch(_FETCH_MEMBER_DETAIL, return_value=_member_detail_body()),
        patch(_FETCH_BILL_DETAIL, return_value=_bill_detail_body()),
        patch(_FETCH_COSPONSORS, return_value=_cosponsors_body()),
        patch(_FETCH_HOUSE_INDEX_XML, return_value="<vote-summary/>"),
        patch(
            _FETCH_HOUSE_INDEX_ROWS, return_value=[_HouseIndexRow(7, "https://house/roll007.xml")]
        ),
        patch(_FETCH_HOUSE_VOTE_XML, return_value="<rollcall-vote/>"),
        patch(_FETCH_SENATE_INDEX_XML, return_value="<roll_call_vote_summary/>"),
        patch(
            _FETCH_SENATE_INDEX_ROWS,
            return_value=[_SenateIndexRow(3, "https://senate/vote_119_1_00003.xml")],
        ),
        patch(_FETCH_SENATE_VOTE_XML, return_value="<vote/>"),
    ):
        result = materialize_congress_archive(
            api_key="test-key",
            archive_root=archive_root,
            congress=119,
            include_votes=True,
            house_vote_year=2025,
            senate_session=1,
        )

    assert result.house_vote_count == 1
    assert result.senate_vote_count == 1
    assert result.house_vote_year == 2025
    assert result.senate_session == 1
    assert (archive_root / "house" / "2025" / "index.xml").exists()
    assert (archive_root / "house" / "2025" / "roll007.xml").exists()
    assert (archive_root / "senate" / "vote1191" / "vote_summary.xml").exists()
    assert (archive_root / "senate" / "vote1191" / "vote_119_1_00003.xml").exists()

    manifest = load_manifest(result.manifest_path)
    assert [(vote.year, vote.roll_call_number) for vote in manifest.house_votes] == [(2025, 7)]
    assert [
        (vote.congress, vote.session_number, vote.roll_call_number)
        for vote in manifest.senate_votes
    ] == [(119, 1, 3)]


def test_materialize_congress_archive_rejects_non_empty_archive_root(tmp_path: Path) -> None:
    archive_root = tmp_path / "congress_119"
    archive_root.mkdir()
    (archive_root / "stale.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="archive_root must not already contain files"):
        materialize_congress_archive(
            api_key="test-key",
            archive_root=archive_root,
            congress=119,
        )


def test_materialize_congress_archive_failure_leaves_no_partial_archive(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "congress_119"

    with (
        patch(_FETCH_MEMBERS, return_value=_members_body()),
        patch(_FETCH_COMMITTEES, return_value=_committees_body()),
        patch(_FETCH_BILLS, return_value=_bills_body()),
        patch(_FETCH_MEMBER_DETAIL, side_effect=RuntimeError("member detail failed")),
    ):
        with pytest.raises(RuntimeError, match="member detail failed"):
            materialize_congress_archive(
                api_key="test-key",
                archive_root=archive_root,
                congress=119,
            )

    assert not archive_root.exists()
