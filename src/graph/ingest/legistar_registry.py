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
    # ── second wave (probed 2026-06, verified jurisdiction) ──
    LegistarClient("louisville", "city", "ky", "Louisville"),
    LegistarClient("sacramento", "city", "ca", "Sacramento"),
    LegistarClient("toledo", "city", "oh", "Toledo"),
    LegistarClient("salem", "city", "or", "Salem"),
    LegistarClient("olympia", "city", "wa", "Olympia"),
    LegistarClient("gainesville", "city", "fl", "Gainesville"),
    LegistarClient("alexandria", "city", "va", "Alexandria"),
    LegistarClient("fortlauderdale", "city", "fl", "Fort Lauderdale"),
    LegistarClient("clearwater", "city", "fl", "Clearwater"),
    LegistarClient("deltona", "city", "fl", "Deltona"),
    LegistarClient("doral", "city", "fl", "Doral"),
    LegistarClient("ocala", "city", "fl", "Ocala"),
    LegistarClient("pompano", "city", "fl", "Pompano Beach"),
    LegistarClient("wellington", "city", "fl", "Wellington"),
    LegistarClient("marietta", "city", "ga", "Marietta"),
    LegistarClient("roswell", "city", "ga", "Roswell"),
    LegistarClient("joliet", "city", "il", "Joliet"),
    LegistarClient("naperville", "city", "il", "Naperville"),
    LegistarClient("lombard", "city", "il", "Lombard"),
    LegistarClient("romeoville", "city", "il", "Romeoville"),
    LegistarClient("jonesboro", "city", "ar", "Jonesboro"),
    LegistarClient("revere", "city", "ma", "Revere"),
    LegistarClient("beverlyhills", "city", "ca", "Beverly Hills"),
    LegistarClient("carson", "city", "ca", "Carson"),
    LegistarClient("chino", "city", "ca", "Chino"),
    LegistarClient("commerce", "city", "ca", "Commerce"),
    LegistarClient("corona", "city", "ca", "Corona"),
    LegistarClient("costamesa", "city", "ca", "Costa Mesa"),
    LegistarClient("cupertino", "city", "ca", "Cupertino"),
    LegistarClient("fullerton", "city", "ca", "Fullerton"),
    LegistarClient("hayward", "city", "ca", "Hayward"),
    LegistarClient("hesperia", "city", "ca", "Hesperia"),
    LegistarClient("murrieta", "city", "ca", "Murrieta"),
    LegistarClient("newportbeach", "city", "ca", "Newport Beach"),
    LegistarClient("palmsprings", "city", "ca", "Palm Springs"),
    LegistarClient("pomona", "city", "ca", "Pomona"),
    LegistarClient("redondo", "city", "ca", "Redondo Beach"),
    LegistarClient("rialto", "city", "ca", "Rialto"),
    LegistarClient("salinas", "city", "ca", "Salinas"),
    LegistarClient("sanleandro", "city", "ca", "San Leandro"),
    LegistarClient("visalia", "city", "ca", "Visalia"),
    LegistarClient("huntingtonbeach", "city", "ca", "Huntington Beach"),
    LegistarClient("chapelhill", "city", "nc", "Chapel Hill"),
    LegistarClient("rockhill", "city", "sc", "Rock Hill"),
    LegistarClient("coppell", "city", "tx", "Coppell"),
    LegistarClient("grandprairie", "city", "tx", "Grand Prairie"),
    LegistarClient("killeen", "city", "tx", "Killeen"),
    LegistarClient("mckinney", "city", "tx", "McKinney"),
    LegistarClient("mesquite", "city", "tx", "Mesquite"),
    LegistarClient("newbraunfels", "city", "tx", "New Braunfels"),
    LegistarClient("pflugerville", "city", "tx", "Pflugerville"),
    LegistarClient("roundrock", "city", "tx", "Round Rock"),
    LegistarClient("goodyear", "city", "az", "Goodyear"),
    LegistarClient("hampton", "city", "va", "Hampton"),
    LegistarClient("redmond", "city", "wa", "Redmond"),
    LegistarClient("bellevue", "city", "wa", "Bellevue"),
    LegistarClient("racine", "city", "wi", "Racine"),
    LegistarClient("waukesha", "city", "wi", "Waukesha"),
    LegistarClient("providenceri", "city", "ri", "Providence"),
    # counties (verified)
    LegistarClient("alachua", "county", "fl", "Alachua County"),
    LegistarClient("pinellas", "county", "fl", "Pinellas County"),
    LegistarClient("dakota", "county", "mn", "Dakota County"),
    LegistarClient("fulton", "county", "ga", "Fulton County"),
    LegistarClient("snohomish", "county", "wa", "Snohomish County"),
    LegistarClient("dane", "county", "wi", "Dane County"),
    LegistarClient("erie", "county", "ny", "Erie County"),
    LegistarClient("humboldt", "county", "ca", "Humboldt County"),
    LegistarClient("eldorado", "county", "ca", "El Dorado County"),
)
