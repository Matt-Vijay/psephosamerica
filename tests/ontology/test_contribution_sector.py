from __future__ import annotations

from src.normalize.taxonomy_runtime import Sector, TaxonomyRuntime
from src.ontology.contribution_sector import build_contribution_sector_resolver


def _taxonomy() -> TaxonomyRuntime:
    return TaxonomyRuntime(
        sectors=[
            Sector(
                sector_id="energy_utilities",
                label="Energy and Utilities",
                description="Power, oil, gas, and utilities.",
                aliases=("energy", "utilities", "oil_and_gas"),
            ),
            Sector(
                sector_id="financial_services",
                label="Financial Services",
                description="Banking and markets.",
                aliases=("finance", "banking", "securities"),
            ),
        ],
        committee_mappings=[],
        crp_mappings=[],
    )


def test_contribution_sector_resolver_maps_taxonomy_alias_in_donor_name() -> None:
    resolver = build_contribution_sector_resolver(_taxonomy())

    assert resolver({"donor_name": "AMERICAN ENERGY PAC"}) == "energy_utilities"


def test_contribution_sector_resolver_maps_normalized_employer_alias() -> None:
    resolver = build_contribution_sector_resolver(_taxonomy())

    assert resolver({"donor_employer": "FIRST BANKING GROUP"}) == "financial_services"


def test_contribution_sector_resolver_is_conservative_when_multiple_sectors_match() -> None:
    resolver = build_contribution_sector_resolver(_taxonomy())

    assert resolver({"donor_name": "ENERGY FINANCE COALITION"}) is None


def test_contribution_sector_resolver_returns_none_without_sector_evidence() -> None:
    resolver = build_contribution_sector_resolver(_taxonomy())

    assert resolver({"donor_name": "JANE DOE", "donor_occupation": "ATTORNEY"}) is None
