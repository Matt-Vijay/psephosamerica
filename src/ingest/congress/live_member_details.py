from __future__ import annotations

from src.load.congress import CommitteeMembershipSpec, MemberTermSpec

from .congress_api import CongressAPIClient
from .member_committees import committee_membership_specs_from_detail
from .member_terms import member_term_specs_from_detail
from .models import MemberRecord


def fetch_member_detail_specs(
    client: CongressAPIClient,
    members: list[MemberRecord],
) -> tuple[list[MemberTermSpec], list[CommitteeMembershipSpec]]:
    term_specs: list[MemberTermSpec] = []
    membership_specs: list[CommitteeMembershipSpec] = []

    for member in members:
        detail = client.get_member_detail_payload(member.bioguide_id)
        inner = detail.get("member", detail)
        term_specs.extend(member_term_specs_from_detail(inner, member))
        membership_specs.extend(committee_membership_specs_from_detail(inner, member))

    return term_specs, membership_specs


def fetch_member_term_specs(
    client: CongressAPIClient,
    members: list[MemberRecord],
) -> list[MemberTermSpec]:
    term_specs, _ = fetch_member_detail_specs(client, members)
    return term_specs


def fetch_committee_membership_specs(
    client: CongressAPIClient,
    members: list[MemberRecord],
) -> list[CommitteeMembershipSpec]:
    _, membership_specs = fetch_member_detail_specs(client, members)
    return membership_specs
