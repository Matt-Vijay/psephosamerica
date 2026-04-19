from __future__ import annotations

from types import SimpleNamespace

from src.pipeline.congress_load_run import _build_resolvers


class TestCommitteeMembershipResolvers:
    def test_committee_membership_resolves_committee_id_from__congress_hint(self) -> None:
        bundle = SimpleNamespace(
            bioguide_map={"P000197": 1},
            lis_member_map={},
            committee_code_map={("HSJU00", 118): 42},
        )

        resolvers = _build_resolvers(bundle)
        target_column, resolve = resolvers["_committee_code"]

        row = {"_committee_code": "HSJU00", "_congress": 118}

        assert target_column == "committee_id"
        assert resolve(row) == 42
