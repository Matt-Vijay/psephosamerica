from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Literal

from pydantic import Field

from src.export.contracts import ExportContractModel, MemberProfilePayload


class CurrentMemberLookupEntry(ExportContractModel):
    bioguide_id: str = Field(serialization_alias="b")
    slug: str = Field(serialization_alias="s")
    name: str = Field(serialization_alias="n")
    search_name: str = Field(serialization_alias="q")
    state: str = Field(serialization_alias="st")
    district: str | None = Field(default=None, serialization_alias="d")
    chamber: Literal["house", "senate"] = Field(serialization_alias="c")


class CurrentMemberLookupPayload(ExportContractModel):
    version: int = Field(default=1, serialization_alias="v")
    snapshot_date: date = Field(serialization_alias="sd")
    members: list[CurrentMemberLookupEntry] = Field(serialization_alias="m")


def normalize_lookup_name(raw: str) -> str:
    normalized = unicodedata.normalize("NFKD", raw.strip())
    without_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    lowered = without_accents.lower()
    collapsed = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", collapsed).strip()


def _normalize_district(raw: str | None) -> str | None:
    if raw is None:
        return None
    district = raw.strip()
    return district or None


def _district_sort_key(district: str | None) -> tuple[int, int | str]:
    if district is None:
        return (1, "")
    if district.isdigit():
        return (0, int(district))
    return (1, district)


def _district_query_form(member: CurrentMemberLookupEntry) -> str | None:
    if member.district is None:
        return None
    state_form = member.state.lower()
    district_form = normalize_lookup_name(member.district)
    prefix = f"{state_form} "
    if district_form.startswith(prefix):
        district_form = district_form[len(prefix) :]
    return district_form or None


def _lookup_query_forms(
    member: CurrentMemberLookupEntry,
) -> tuple[str, str, str, tuple[str, ...]]:
    name_form = member.search_name
    slug_form = normalize_lookup_name(member.slug)
    geo_parts = [member.state.lower()]
    district_form = _district_query_form(member)
    if district_form:
        geo_parts.append(district_form)
    geo_form = " ".join(geo_parts).strip()
    search_tokens = (
        name_form.split()
        + [member.state.lower(), member.chamber]
        + ([district_form] if district_form else [])
    )
    return name_form, slug_form, geo_form, tuple(search_tokens)


def _search_rank(member: CurrentMemberLookupEntry, query: str) -> tuple[int, int] | None:
    name_form, slug_form, geo_form, search_tokens = _lookup_query_forms(member)
    query_tokens = query.split()
    name_tokens = name_form.split()

    if query == name_form:
        return (0, 0)
    if query == slug_form:
        return (1, 0)
    if geo_form and query == geo_form:
        return (2, 0)
    if name_form.startswith(query):
        return (3, len(name_form))
    if all(token in search_tokens for token in query_tokens):
        return (4, len(search_tokens))
    if all(token in name_tokens for token in query_tokens):
        return (5, len(name_tokens))
    if all(any(word.startswith(token) for word in search_tokens) for token in query_tokens):
        return (6, len(name_form))
    if all(any(word.startswith(token) for word in name_tokens) for token in query_tokens):
        return (7, len(name_tokens))
    if query in name_form:
        return (8, len(name_form))
    if geo_form and query in geo_form:
        return (9, len(geo_form))
    return None


def _resolve_snapshot_date(
    member_profiles: list[MemberProfilePayload],
    snapshot_date: date | None,
) -> date:
    dates = {profile.snapshot_date for profile in member_profiles}
    if len(dates) > 1:
        raise ValueError("member_profiles must all have the same snapshot_date")
    if snapshot_date is None:
        if dates:
            return next(iter(dates))
        raise ValueError("snapshot_date is required when member_profiles is empty")
    if dates and snapshot_date not in dates:
        raise ValueError("snapshot_date must match member_profiles snapshot_date")
    return snapshot_date


def validate_current_member_lookup(
    payload: CurrentMemberLookupPayload,
) -> CurrentMemberLookupPayload:
    seen_bioguide_ids: set[str] = set()
    seen_slugs: set[str] = set()

    for member in payload.members:
        expected_search_name = normalize_lookup_name(member.name)
        if member.search_name != expected_search_name:
            raise ValueError(
                f"lookup entry {member.bioguide_id!r} has stale search_name: "
                f"{member.search_name!r} != {expected_search_name!r}"
            )
        if member.bioguide_id in seen_bioguide_ids:
            raise ValueError(
                f"duplicate bioguide_id in current-member lookup: {member.bioguide_id}"
            )
        if member.slug in seen_slugs:
            raise ValueError(f"duplicate slug in current-member lookup: {member.slug}")
        seen_bioguide_ids.add(member.bioguide_id)
        seen_slugs.add(member.slug)

    return payload


def search_current_member_lookup(
    payload: CurrentMemberLookupPayload,
    query: str,
    *,
    limit: int = 8,
) -> CurrentMemberLookupPayload:
    if limit < 0:
        raise ValueError("limit must be >= 0")

    normalized_query = normalize_lookup_name(query)
    if not normalized_query or limit == 0:
        return CurrentMemberLookupPayload(
            version=payload.version,
            snapshot_date=payload.snapshot_date,
            members=[],
        )

    ranked: list[tuple[tuple[int, int], int, CurrentMemberLookupEntry]] = []
    for index, member in enumerate(payload.members):
        rank = _search_rank(member, normalized_query)
        if rank is None:
            continue
        ranked.append((rank, index, member))

    ranked.sort(key=lambda item: (item[0], item[1]))
    return CurrentMemberLookupPayload(
        version=payload.version,
        snapshot_date=payload.snapshot_date,
        members=[member for _, _, member in ranked[:limit]],
    )


def build_current_member_lookup(
    member_profiles: list[MemberProfilePayload],
    *,
    snapshot_date: date | None = None,
) -> CurrentMemberLookupPayload:
    resolved_snapshot_date = _resolve_snapshot_date(member_profiles, snapshot_date)

    members = [
        CurrentMemberLookupEntry(
            bioguide_id=profile.bioguide_id,
            slug=profile.slug,
            name=profile.name,
            search_name=normalize_lookup_name(profile.name),
            state=profile.state.strip().upper(),
            district=_normalize_district(profile.district),
            chamber=profile.chamber,
        )
        for profile in member_profiles
    ]
    members.sort(
        key=lambda member: (
            member.state,
            0 if member.chamber == "house" else 1,
            _district_sort_key(member.district),
            member.search_name,
            member.bioguide_id,
        )
    )
    return validate_current_member_lookup(
        CurrentMemberLookupPayload(
            snapshot_date=resolved_snapshot_date,
            members=members,
        )
    )
