"""A vetted registry of public Legistar clients (cities + counties).

Each entry is a Legistar OData client code (``webapi.legistar.com/v1/<client>``)
verified reachable without a credential, mapped to its
:class:`~src.graph.jurisdictions.Jurisdiction`. This drives municipal coverage
across 50+ governments: resolve their officials, matters (bills), and votes the
same way Congress is handled.

Clients that returned 403/500 (wrong code, or the city uses a different vendor —
Granicus/PrimeGov/CivicClerk/BoardDocs) are omitted, not fabricated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.graph.jurisdictions import Jurisdiction

ClientLevel = Literal["city", "county"]


@dataclass(frozen=True)
class LegistarClient:
    """One verified Legistar client and the government it serves."""

    client: str
    level: ClientLevel
    state: str
    place: str

    def jurisdiction(self) -> Jurisdiction:
        if self.level == "city":
            return Jurisdiction.city(self.state, self.place)
        return Jurisdiction.county(self.state, self.place)


# Verified reachable 2026-06 (HTTP 200 on /bodies).
LEGISTAR_CLIENTS: tuple[LegistarClient, ...] = (
    # ── cities ──
    LegistarClient("chicago", "city", "il", "Chicago"),
    LegistarClient("sfgov", "city", "ca", "San Francisco"),
    LegistarClient("oakland", "city", "ca", "Oakland"),
    LegistarClient("longbeach", "city", "ca", "Long Beach"),
    LegistarClient("mountainview", "city", "ca", "Mountain View"),
    LegistarClient("seattle", "city", "wa", "Seattle"),
    LegistarClient("boston", "city", "ma", "Boston"),
    LegistarClient("denver", "city", "co", "Denver"),
    LegistarClient("phoenix", "city", "az", "Phoenix"),
    LegistarClient("sanjose", "city", "ca", "San Jose"),
    LegistarClient("columbus", "city", "oh", "Columbus"),
    LegistarClient("sanantonio", "city", "tx", "San Antonio"),
    LegistarClient("detroit", "city", "mi", "Detroit"),
    LegistarClient("nashville", "city", "tn", "Nashville"),
    LegistarClient("baltimore", "city", "md", "Baltimore"),
    LegistarClient("milwaukee", "city", "wi", "Milwaukee"),
    LegistarClient("fresno", "city", "ca", "Fresno"),
    LegistarClient("mesa", "city", "az", "Mesa"),
    LegistarClient("kansascity", "city", "mo", "Kansas City"),
    LegistarClient("corpuschristi", "city", "tx", "Corpus Christi"),
    LegistarClient("lexington", "city", "ky", "Lexington"),
    LegistarClient("stockton", "city", "ca", "Stockton"),
    LegistarClient("stpaul", "city", "mn", "Saint Paul"),
    LegistarClient("pittsburgh", "city", "pa", "Pittsburgh"),
    LegistarClient("greensboro", "city", "nc", "Greensboro"),
    LegistarClient("plano", "city", "tx", "Plano"),
    LegistarClient("newark", "city", "nj", "Newark"),
    LegistarClient("chulavista", "city", "ca", "Chula Vista"),
    LegistarClient("madison", "city", "wi", "Madison"),
    LegistarClient("sanbernardino", "city", "ca", "San Bernardino"),
    LegistarClient("fontana", "city", "ca", "Fontana"),
    LegistarClient("a2gov", "city", "mi", "Ann Arbor"),
    LegistarClient("cabq", "city", "nm", "Albuquerque"),
    LegistarClient("somervillema", "city", "ma", "Somerville"),
    LegistarClient("richmondva", "city", "va", "Richmond"),
    # ── counties ──
    LegistarClient("kingcounty", "county", "wa", "King County"),
    LegistarClient("lacounty", "county", "ca", "Los Angeles County"),
    LegistarClient("slco", "county", "ut", "Salt Lake County"),
    LegistarClient("sanmateocounty", "county", "ca", "San Mateo County"),
    LegistarClient("alameda", "county", "ca", "Alameda County"),
    LegistarClient("montgomerycountymd", "county", "md", "Montgomery County"),
    LegistarClient("milwaukeecounty", "county", "wi", "Milwaukee County"),
    LegistarClient("miamidade", "county", "fl", "Miami-Dade County"),
    LegistarClient("broward", "county", "fl", "Broward County"),
    LegistarClient("mecklenburg", "county", "nc", "Mecklenburg County"),
    LegistarClient("durhamcounty", "county", "nc", "Durham County"),
    LegistarClient("guilford", "county", "nc", "Guilford County"),
    LegistarClient("maricopa", "county", "az", "Maricopa County"),
    LegistarClient("pima", "county", "az", "Pima County"),
    LegistarClient("clark", "county", "nv", "Clark County"),
    LegistarClient("arapahoe", "county", "co", "Arapahoe County"),
    LegistarClient("fresnocounty", "county", "ca", "Fresno County"),
    LegistarClient("solano", "county", "ca", "Solano County"),
    LegistarClient("monterey", "county", "ca", "Monterey County"),
    LegistarClient("napa", "county", "ca", "Napa County"),
    LegistarClient("mendocino", "county", "ca", "Mendocino County"),
)
