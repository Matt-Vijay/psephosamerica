"""Materialize a canonical local Congress archive from official upstream sources."""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from src.ingest.congress.archive import CongressArchive, manifest_from_existing_archive
from src.ingest.congress.archive_manifest import write_manifest
from src.ingest.congress.archive_validate import validate_congress_archive_manifest
from src.ingest.congress.congress_api import (
    CongressAPIClient,
    bills_url,
    committees_url,
    cosponsors_url,
    member_detail_url,
    members_url,
    bill_detail_url,
    next_pagination_url,
)
from src.ingest.congress.house_vote_index import fetch_house_vote_index, house_vote_index_url
from src.ingest.congress.official_fetch import fetch_official_congress_text
from src.ingest.congress.senate_vote_index import fetch_senate_vote_index, senate_vote_index_url
from src.ingest.congress.house_votes import roll_call_url as house_roll_call_url
from src.ingest.congress.senate_votes import roll_call_url as senate_roll_call_url
from src.runtime.congress_options import resolve_congress_vote_coverage

_BILL_TYPES = frozenset({"hr", "s", "hjres", "sjres", "hconres", "sconres", "hres", "sres"})


@dataclass(frozen=True)
class MaterializedCongressArchiveResult:
    archive_root: Path
    manifest_path: Path
    congress: int
    include_votes: bool
    house_vote_year: int | None
    senate_session: int | None
    member_count: int
    committee_count: int
    bill_count: int
    member_detail_count: int
    bill_detail_count: int
    cosponsor_file_count: int
    house_vote_count: int
    senate_vote_count: int


def materialize_congress_archive(
    *,
    api_key: str,
    archive_root: Path,
    congress: int,
    include_votes: bool = False,
    house_vote_year: int | None = None,
    senate_session: int | None = None,
    manifest_path: Path | None = None,
) -> MaterializedCongressArchiveResult:
    if not api_key.strip():
        raise ValueError("api_key is required")
    if archive_root.exists() and (not archive_root.is_dir() or any(archive_root.iterdir())):
        raise ValueError("archive_root must not already contain files")
    staging_root = _sibling_staging_dir(archive_root)

    try:
        with CongressAPIClient(api_key) as client:
            members_body = _fetch_members_body(client, congress)
            committees_body = _fetch_committees_body(client, congress)
            bills_body = _fetch_bills_body(client, congress)

            members = _list_items(members_body, "members")
            bills = _list_items(bills_body, "bills")

            _write_json(staging_root / "members.json", members_body)
            _write_json(staging_root / "committees.json", committees_body)
            _write_json(staging_root / "bills.json", bills_body)

            bioguide_ids: list[str] = []
            for member in members:
                bioguide_id = _bioguide_id_field(member, "bioguideId", location="members[]")
                bioguide_ids.append(bioguide_id)
                _write_json(
                    staging_root / "member_details" / f"{bioguide_id}.json",
                    _fetch_member_detail_body(client, bioguide_id),
                )

            bill_keys: list[tuple[int, str, int]] = []
            for bill in bills:
                bill_congress = _int_field(bill, "congress", location="bills[]")
                bill_type = _bill_type_field(bill, "type", location="bills[]")
                bill_number = _int_field(bill, "number", location="bills[]")
                bill_keys.append((bill_congress, bill_type, bill_number))
                _write_json(
                    staging_root
                    / "bill_details"
                    / f"{bill_congress}_{bill_type}_{bill_number}.json",
                    _fetch_bill_detail_body(client, bill_congress, bill_type, bill_number),
                )
                _write_json(
                    staging_root / "cosponsors" / f"{bill_congress}_{bill_type}_{bill_number}.json",
                    _fetch_cosponsors_body(client, bill_congress, bill_type, bill_number),
                )

        house_vote_keys: list[tuple[int, int]] = []
        senate_vote_keys: list[tuple[int, int, int]] = []
        effective_house_vote_year: int | None = None
        effective_senate_session: int | None = None

        if include_votes:
            coverage = resolve_congress_vote_coverage(
                congress,
                house_vote_year=house_vote_year,
                senate_session=senate_session,
            )
            effective_house_vote_year = coverage.house_vote_year
            effective_senate_session = coverage.senate_session
            with httpx.Client(timeout=30.0) as client:
                if effective_house_vote_year is not None:
                    house_index_xml = _fetch_house_vote_index_xml(client, effective_house_vote_year)
                    _write_text(
                        staging_root / "house" / str(effective_house_vote_year) / "index.xml",
                        house_index_xml,
                    )
                    for house_row in fetch_house_vote_index(
                        effective_house_vote_year, client=client
                    ):
                        house_vote_keys.append(
                            (effective_house_vote_year, house_row.roll_call_number)
                        )
                        _write_text(
                            staging_root
                            / "house"
                            / str(effective_house_vote_year)
                            / f"roll{house_row.roll_call_number:03d}.xml",
                            _fetch_house_vote_xml(
                                client, effective_house_vote_year, house_row.roll_call_number
                            ),
                        )
                if effective_senate_session is not None:
                    senate_index_xml = _fetch_senate_vote_index_xml(
                        client, congress, effective_senate_session
                    )
                    senate_dir = (
                        staging_root / "senate" / f"vote{congress}{effective_senate_session}"
                    )
                    _write_text(senate_dir / "vote_summary.xml", senate_index_xml)
                    for senate_row in fetch_senate_vote_index(
                        congress, effective_senate_session, client=client
                    ):
                        senate_vote_keys.append(
                            (congress, effective_senate_session, senate_row.vote_number)
                        )
                        _write_text(
                            senate_dir
                            / f"vote_{congress}_{effective_senate_session}_{senate_row.vote_number:05d}.xml",
                            _fetch_senate_vote_xml(
                                client, congress, effective_senate_session, senate_row.vote_number
                            ),
                        )

        archive = CongressArchive(staging_root, congress)
        manifest = manifest_from_existing_archive(archive)
        if manifest_path is None:
            write_manifest(staging_root / "manifest.json", manifest)
        validation = validate_congress_archive_manifest(manifest)
        if not validation.valid:
            missing_labels = ", ".join(missing.label for missing in validation.missing)
            raise ValueError(f"materialized congress archive is missing files: {missing_labels}")

        _promote_staged_directory(staging_root, archive_root)
        final_manifest_path = (
            manifest_path if manifest_path is not None else archive_root / "manifest.json"
        )
        if manifest_path is not None:
            final_manifest = manifest_from_existing_archive(CongressArchive(archive_root, congress))
            write_manifest(final_manifest_path, final_manifest)
    except BaseException:
        _cleanup_staging_path(staging_root)
        raise

    return MaterializedCongressArchiveResult(
        archive_root=archive_root,
        manifest_path=final_manifest_path,
        congress=congress,
        include_votes=include_votes,
        house_vote_year=effective_house_vote_year,
        senate_session=effective_senate_session,
        member_count=len(members),
        committee_count=len(_list_items(committees_body, "committees")),
        bill_count=len(bills),
        member_detail_count=len(bioguide_ids),
        bill_detail_count=len(bill_keys),
        cosponsor_file_count=len(bill_keys),
        house_vote_count=len(house_vote_keys),
        senate_vote_count=len(senate_vote_keys),
    )


def _fetch_members_body(client: CongressAPIClient, congress: int) -> dict[str, Any]:
    return _fetch_collection_body(client, members_url(congress), "members")


def _fetch_committees_body(client: CongressAPIClient, congress: int) -> dict[str, Any]:
    return _fetch_collection_body(client, committees_url(congress), "committees")


def _fetch_bills_body(client: CongressAPIClient, congress: int) -> dict[str, Any]:
    return _fetch_collection_body(client, bills_url(congress), "bills")


def _fetch_member_detail_body(client: CongressAPIClient, bioguide_id: str) -> dict[str, Any]:
    return _json_object(
        client._get(member_detail_url(bioguide_id)), context=f"member detail {bioguide_id}"
    )


def _fetch_bill_detail_body(
    client: CongressAPIClient,
    congress: int,
    bill_type: str,
    bill_number: int,
) -> dict[str, Any]:
    return _json_object(
        client._get(bill_detail_url(congress, bill_type, bill_number)),
        context=f"bill detail {congress}/{bill_type}/{bill_number}",
    )


def _fetch_cosponsors_body(
    client: CongressAPIClient,
    congress: int,
    bill_type: str,
    bill_number: int,
) -> dict[str, Any]:
    return _fetch_collection_body(
        client,
        cosponsors_url(congress, bill_type, bill_number),
        "cosponsors",
    )


def _fetch_house_vote_index_xml(client: httpx.Client, year: int) -> str:
    return fetch_official_congress_text(house_vote_index_url(year), client=client)


def _fetch_house_vote_xml(client: httpx.Client, year: int, roll_call_number: int) -> str:
    return fetch_official_congress_text(house_roll_call_url(year, roll_call_number), client=client)


def _fetch_senate_vote_index_xml(client: httpx.Client, congress: int, session: int) -> str:
    return fetch_official_congress_text(senate_vote_index_url(congress, session), client=client)


def _fetch_senate_vote_xml(
    client: httpx.Client, congress: int, session: int, vote_number: int
) -> str:
    return fetch_official_congress_text(
        senate_roll_call_url(congress, session, vote_number), client=client
    )


def _fetch_collection_body(
    client: CongressAPIClient,
    url: str,
    items_key: str,
) -> dict[str, Any]:
    current_url: str | None = url
    seen_urls: set[str] = set()
    page_count = 0
    items: list[dict[str, Any]] = []
    while current_url:
        if current_url in seen_urls:
            raise ValueError(f"pagination.next repeated URL: {current_url!r}")
        seen_urls.add(current_url)
        page_count += 1
        body = _json_object(client._get(current_url), context=f"response from {current_url}")
        raw_items = body.get(items_key, [])
        emitted = False
        if isinstance(raw_items, list):
            for item in raw_items:
                if isinstance(item, dict):
                    items.append(item)
                    emitted = True
        pagination = body.get("pagination", {})
        pagination_obj = pagination if isinstance(pagination, dict) else {}
        next_info = pagination_obj.get("next")
        current_url = next_pagination_url(
            current_url,
            next_info,
            emitted=emitted,
            seen_urls=seen_urls,
            page_count=page_count,
            base_url=client._base_url,
        )
    return {items_key: items}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _write_text_atomic(path, json.dumps(payload, sort_keys=True))


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text_atomic(path, text)


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temp_path.write_text(text, encoding="utf-8")
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def _json_object(value: object, *, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a JSON object, got {type(value).__name__}")
    return value


def _list_items(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    raw_items = payload.get(key, [])
    if not isinstance(raw_items, list):
        raise ValueError(f"{key} payload must be a list")
    items = [item for item in raw_items if isinstance(item, dict)]
    return items


def _string_field(raw: dict[str, Any], field: str, *, location: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{location}.{field} must be a non-empty string")
    return value


def _bioguide_id_field(raw: dict[str, Any], field: str, *, location: str) -> str:
    value = _string_field(raw, field, location=location)
    if len(value) == 7 and value[0].isalpha() and value[1:].isdigit():
        return value
    raise ValueError(f"{location}.{field} must be a valid Bioguide ID")


def _bill_type_field(raw: dict[str, Any], field: str, *, location: str) -> str:
    value = _string_field(raw, field, location=location).lower().replace(".", "")
    if value in _BILL_TYPES:
        return value
    raise ValueError(f"{location}.{field} must be a supported bill type")


def _int_field(raw: dict[str, Any], field: str, *, location: str) -> int:
    value = raw.get(field)
    if isinstance(value, bool):
        raise ValueError(f"{location}.{field} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise ValueError(f"{location}.{field} must be an integer")


def _sibling_staging_dir(final_path: Path) -> Path:
    final_path.parent.mkdir(parents=True, exist_ok=True)
    return Path(
        tempfile.mkdtemp(
            prefix=f".{final_path.name}.",
            dir=final_path.parent,
        )
    )


def _promote_staged_directory(staging_path: Path, final_path: Path) -> None:
    if final_path.exists():
        final_path.rmdir()
    staging_path.replace(final_path)


def _cleanup_staging_path(path: Path | None) -> None:
    if path is not None and path.exists():
        shutil.rmtree(path, ignore_errors=True)
