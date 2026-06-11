"""Probe candidate Legistar client codes to grow the municipal registry (v7 #6).

``webapi.legistar.com/v1/<client>/bodies`` answers 200 with a JSON list for a
real client and 404/HTML otherwise, so candidate government codes can be
verified keylessly before they enter
:mod:`src.graph.ingest.legistar_registry`. The candidate catalog below maps
plausible codes for large US cities/counties not yet in the registry to their
jurisdiction, so a verified hit becomes a registry row mechanically.

Polite single-pass scan (paced, injectable sleep); a non-200 or non-list
answer just means "not a Legistar client" — omitted, never fabricated.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import httpx

from src.graph.ingest.legistar_registry import LEGISTAR_CLIENTS
from src.runtime.http_client import client_or_default

_API = "https://webapi.legistar.com/v1"

Level = Literal["city", "county"]

# code -> (level, state, place). Top-population cities/counties absent from the
# registry, with the code spellings Legistar clients conventionally use.
CANDIDATES: dict[str, tuple[Level, str, str]] = {
    "nyc": ("city", "ny", "New York"),
    "houston": ("city", "tx", "Houston"),
    "philadelphia": ("city", "pa", "Philadelphia"),
    "phila": ("city", "pa", "Philadelphia"),
    "dallas": ("city", "tx", "Dallas"),
    "jacksonville": ("city", "fl", "Jacksonville"),
    "fortworth": ("city", "tx", "Fort Worth"),
    "austin": ("city", "tx", "Austin"),
    "charlotte": ("city", "nc", "Charlotte"),
    "indianapolis": ("city", "in", "Indianapolis"),
    "elpaso": ("city", "tx", "El Paso"),
    "memphis": ("city", "tn", "Memphis"),
    "portlandor": ("city", "or", "Portland"),
    "portland": ("city", "or", "Portland"),
    "oklahomacity": ("city", "ok", "Oklahoma City"),
    "okc": ("city", "ok", "Oklahoma City"),
    "lasvegas": ("city", "nv", "Las Vegas"),
    "louisville": ("city", "ky", "Louisville"),
    "tucson": ("city", "az", "Tucson"),
    "sacramento": ("city", "ca", "Sacramento"),
    "atlanta": ("city", "ga", "Atlanta"),
    "atlantacityga": ("city", "ga", "Atlanta"),
    "miami": ("city", "fl", "Miami"),
    "miamifl": ("city", "fl", "Miami"),
    "tulsa": ("city", "ok", "Tulsa"),
    "minneapolis": ("city", "mn", "Minneapolis"),
    "wichita": ("city", "ks", "Wichita"),
    "neworleans": ("city", "la", "New Orleans"),
    "nola": ("city", "la", "New Orleans"),
    "cleveland": ("city", "oh", "Cleveland"),
    "tampa": ("city", "fl", "Tampa"),
    "tampagov": ("city", "fl", "Tampa"),
    "honolulu": ("city", "hi", "Honolulu"),
    "anaheim": ("city", "ca", "Anaheim"),
    "santaana": ("city", "ca", "Santa Ana"),
    "stlouis": ("city", "mo", "St. Louis"),
    "stlouismo": ("city", "mo", "St. Louis"),
    "riverside": ("city", "ca", "Riverside"),
    "cincinnati": ("city", "oh", "Cincinnati"),
    "bakersfield": ("city", "ca", "Bakersfield"),
    "aurora": ("city", "co", "Aurora"),
    "auroraco": ("city", "co", "Aurora"),
    "anchorage": ("city", "ak", "Anchorage"),
    "toledo": ("city", "oh", "Toledo"),
    "stpete": ("city", "fl", "St. Petersburg"),
    "stpetersburg": ("city", "fl", "St. Petersburg"),
    "laredo": ("city", "tx", "Laredo"),
    "chandler": ("city", "az", "Chandler"),
    "buffalo": ("city", "ny", "Buffalo"),
    "gilbert": ("city", "az", "Gilbert"),
    "glendaleaz": ("city", "az", "Glendale"),
    "northlasvegas": ("city", "nv", "North Las Vegas"),
    "irving": ("city", "tx", "Irving"),
    "scottsdale": ("city", "az", "Scottsdale"),
    "boise": ("city", "id", "Boise"),
    "richmond": ("city", "ca", "Richmond"),
    "spokane": ("city", "wa", "Spokane"),
    "spokanecity": ("city", "wa", "Spokane"),
    "tacoma": ("city", "wa", "Tacoma"),
    "cityoftacoma": ("city", "wa", "Tacoma"),
    "fremont": ("city", "ca", "Fremont"),
    "modesto": ("city", "ca", "Modesto"),
    "desmoines": ("city", "ia", "Des Moines"),
    "fayetteville": ("city", "nc", "Fayetteville"),
    "rochester": ("city", "ny", "Rochester"),
    "rochesterny": ("city", "ny", "Rochester"),
    "providence": ("city", "ri", "Providence"),
    "saltlakecity": ("city", "ut", "Salt Lake City"),
    "slcgov": ("city", "ut", "Salt Lake City"),
    "hartford": ("city", "ct", "Hartford"),
    "elkgrove": ("city", "ca", "Elk Grove"),
    "ontarioca": ("city", "ca", "Ontario"),
    "oceanside": ("city", "ca", "Oceanside"),
    "ranchocucamonga": ("city", "ca", "Rancho Cucamonga"),
    "santaclarita": ("city", "ca", "Santa Clarita"),
    "gardengrove": ("city", "ca", "Garden Grove"),
    "vancouverwa": ("city", "wa", "Vancouver"),
    "sioux": ("city", "sd", "Sioux Falls"),
    "siouxfalls": ("city", "sd", "Sioux Falls"),
    "springfieldmo": ("city", "mo", "Springfield"),
    "peoria": ("city", "az", "Peoria"),
    "pasadena": ("city", "ca", "Pasadena"),
    "rockford": ("city", "il", "Rockford"),
    "joliet": ("city", "il", "Joliet"),
    "torrance": ("city", "ca", "Torrance"),
    "bridgeport": ("city", "ct", "Bridgeport"),
    "alexandria": ("city", "va", "Alexandria"),
    "alexandriava": ("city", "va", "Alexandria"),
    "sunnyvaleca": ("city", "ca", "Sunnyvale"),
    "sunnyvale": ("city", "ca", "Sunnyvale"),
    "escondido": ("city", "ca", "Escondido"),
    "lancasterca": ("city", "ca", "Lancaster"),
    "eugene": ("city", "or", "Eugene"),
    "salem": ("city", "or", "Salem"),
    "salemor": ("city", "or", "Salem"),
    "fortcollins": ("city", "co", "Fort Collins"),
    "carrollton": ("city", "tx", "Carrollton"),
    "coralsprings": ("city", "fl", "Coral Springs"),
    "stamford": ("city", "ct", "Stamford"),
    "concordca": ("city", "ca", "Concord"),
    "hayward": ("city", "ca", "Hayward"),
    "topeka": ("city", "ks", "Topeka"),
    "simivalley": ("city", "ca", "Simi Valley"),
    "fullerton": ("city", "ca", "Fullerton"),
    "allentown": ("city", "pa", "Allentown"),
    "westpalmbeach": ("city", "fl", "West Palm Beach"),
    "columbia": ("city", "sc", "Columbia"),
    "athensga": ("city", "ga", "Athens"),
    "annapolis": ("city", "md", "Annapolis"),
    "berkeley": ("city", "ca", "Berkeley"),
    "cupertino": ("city", "ca", "Cupertino"),
    "emeryville": ("city", "ca", "Emeryville"),
    "burlingamecity": ("city", "ca", "Burlingame"),
    "carson": ("city", "ca", "Carson"),
    "monterey": ("city", "ca", "Monterey"),
    "rockville": ("city", "md", "Rockville"),
    "gaithersburg": ("city", "md", "Gaithersburg"),
    "olympia": ("city", "wa", "Olympia"),
    "everettwa": ("city", "wa", "Everett"),
    "bellevuewa": ("city", "wa", "Bellevue"),
    "bellevue": ("city", "wa", "Bellevue"),
    "redmond": ("city", "wa", "Redmond"),
    "kirkland": ("city", "wa", "Kirkland"),
    "kent": ("city", "wa", "Kent"),
    "auburnwa": ("city", "wa", "Auburn"),
    "renton": ("city", "wa", "Renton"),
    "duluthmn": ("city", "mn", "Duluth"),
    "annarbor": ("city", "mi", "Ann Arbor"),
    "grandrapids": ("city", "mi", "Grand Rapids"),
    "lansingmi": ("city", "mi", "Lansing"),
    "daytonoh": ("city", "oh", "Dayton"),
    "akron": ("city", "oh", "Akron"),
    "savannah": ("city", "ga", "Savannah"),
    "knoxvilletn": ("city", "tn", "Knoxville"),
    "chattanooga": ("city", "tn", "Chattanooga"),
    "shreveport": ("city", "la", "Shreveport"),
    "batonrouge": ("city", "la", "Baton Rouge"),
    "mobile": ("city", "al", "Mobile"),
    "birminghamal": ("city", "al", "Birmingham"),
    "jackson": ("city", "ms", "Jackson"),
    "littlerock": ("city", "ar", "Little Rock"),
    "omaha": ("city", "ne", "Omaha"),
    "lincoln": ("city", "ne", "Lincoln"),
    "fargo": ("city", "nd", "Fargo"),
    "billingsmt": ("city", "mt", "Billings"),
    "cheyenne": ("city", "wy", "Cheyenne"),
    "albany": ("city", "ny", "Albany"),
    "syracuse": ("city", "ny", "Syracuse"),
    "yonkers": ("city", "ny", "Yonkers"),
    "jerseycity": ("city", "nj", "Jersey City"),
    "paterson": ("city", "nj", "Paterson"),
    "trenton": ("city", "nj", "Trenton"),
    "wilmingtonde": ("city", "de", "Wilmington"),
    "wilmingtondelaware": ("city", "de", "Wilmington"),
    "portsmouthva": ("city", "va", "Portsmouth"),
    "norfolk": ("city", "va", "Norfolk"),
    "virginiabeach": ("city", "va", "Virginia Beach"),
    "chesapeakeva": ("city", "va", "Chesapeake"),
    "newportnews": ("city", "va", "Newport News"),
    "raleigh": ("city", "nc", "Raleigh"),
    "durhamnc": ("city", "nc", "Durham"),
    "winstonsalem": ("city", "nc", "Winston-Salem"),
    "charleston": ("city", "sc", "Charleston"),
    "charlestonsc": ("city", "sc", "Charleston"),
    "orlando": ("city", "fl", "Orlando"),
    "orlandofl": ("city", "fl", "Orlando"),
    "hialeah": ("city", "fl", "Hialeah"),
    "ftlauderdale": ("city", "fl", "Fort Lauderdale"),
    "fortlauderdale": ("city", "fl", "Fort Lauderdale"),
    # counties
    "cookcounty": ("county", "il", "Cook County"),
    "cook": ("county", "il", "Cook County"),
    "harriscounty": ("county", "tx", "Harris County"),
    "maricopa": ("county", "az", "Maricopa County"),
    "maricopacounty": ("county", "az", "Maricopa County"),
    "sandiegocounty": ("county", "ca", "San Diego County"),
    "orangecounty": ("county", "ca", "Orange County"),
    "ocgov": ("county", "ca", "Orange County"),
    "miamidade": ("county", "fl", "Miami-Dade County"),
    "miamidadecounty": ("county", "fl", "Miami-Dade County"),
    "dallascounty": ("county", "tx", "Dallas County"),
    "riversidecounty": ("county", "ca", "Riverside County"),
    "clarkcountynv": ("county", "nv", "Clark County"),
    "clarkcounty": ("county", "nv", "Clark County"),
    "tarrantcounty": ("county", "tx", "Tarrant County"),
    "bexar": ("county", "tx", "Bexar County"),
    "bexarcounty": ("county", "tx", "Bexar County"),
    "broward": ("county", "fl", "Broward County"),
    "browardcounty": ("county", "fl", "Broward County"),
    "santaclaracounty": ("county", "ca", "Santa Clara County"),
    "sccgov": ("county", "ca", "Santa Clara County"),
    "alamedacounty": ("county", "ca", "Alameda County"),
    "alco": ("county", "ca", "Alameda County"),
    "waynecounty": ("county", "mi", "Wayne County"),
    "fairfaxcounty": ("county", "va", "Fairfax County"),
    "fairfax": ("county", "va", "Fairfax County"),
    "fultoncountyga": ("county", "ga", "Fulton County"),
    "fultoncounty": ("county", "ga", "Fulton County"),
    "pimacounty": ("county", "az", "Pima County"),
    "pima": ("county", "az", "Pima County"),
    "hennepin": ("county", "mn", "Hennepin County"),
    "hennepincounty": ("county", "mn", "Hennepin County"),
    "cuyahogacounty": ("county", "oh", "Cuyahoga County"),
    "milwaukeecounty": ("county", "wi", "Milwaukee County"),
    "multco": ("county", "or", "Multnomah County"),
    "multnomah": ("county", "or", "Multnomah County"),
    "snohomishcountywa": ("county", "wa", "Snohomish County"),
    "snoco": ("county", "wa", "Snohomish County"),
    "piercecountywa": ("county", "wa", "Pierce County"),
    "montgomerycountymd": ("county", "md", "Montgomery County"),
    "mocomd": ("county", "md", "Montgomery County"),
    "princegeorgescountymd": ("county", "md", "Prince George's County"),
    "pgccouncil": ("county", "md", "Prince George's County"),
    "baltimorecountymd": ("county", "md", "Baltimore County"),
    "nassaucountyny": ("county", "ny", "Nassau County"),
    "suffolkcountyny": ("county", "ny", "Suffolk County"),
    "westchestercountyny": ("county", "ny", "Westchester County"),
    "westchester": ("county", "ny", "Westchester County"),
    "erie": ("county", "ny", "Erie County"),
    "eriecounty": ("county", "ny", "Erie County"),
    "monroecounty": ("county", "ny", "Monroe County"),
    "dekalbcountyga": ("county", "ga", "DeKalb County"),
    "gwinnettcounty": ("county", "ga", "Gwinnett County"),
    "wakegov": ("county", "nc", "Wake County"),
    "mecklenburg": ("county", "nc", "Mecklenburg County"),
    "shelbycountytn": ("county", "tn", "Shelby County"),
    "jeffersoncountyky": ("county", "ky", "Jefferson County"),
    "oaklandcountymi": ("county", "mi", "Oakland County"),
    "dupage": ("county", "il", "DuPage County"),
    "dupagecounty": ("county", "il", "DuPage County"),
    "kane": ("county", "il", "Kane County"),
    "lakecountyil": ("county", "il", "Lake County"),
    "willcountyil": ("county", "il", "Will County"),
    "stlouiscounty": ("county", "mo", "St. Louis County"),
    "jacksoncountymo": ("county", "mo", "Jackson County"),
    "sonomacounty": ("county", "ca", "Sonoma County"),
    "sonoma-county": ("county", "ca", "Sonoma County"),
    "fresnocounty": ("county", "ca", "Fresno County"),
    "kerncounty": ("county", "ca", "Kern County"),
    "venturacounty": ("county", "ca", "Ventura County"),
    "sanjoaquincounty": ("county", "ca", "San Joaquin County"),
    "stanislauscounty": ("county", "ca", "Stanislaus County"),
    "solanocounty": ("county", "ca", "Solano County"),
    "contracosta": ("county", "ca", "Contra Costa County"),
    "contracostacounty": ("county", "ca", "Contra Costa County"),
    "marincounty": ("county", "ca", "Marin County"),
    "placercounty": ("county", "ca", "Placer County"),
    "sacramentocounty": ("county", "ca", "Sacramento County"),
    "saccounty": ("county", "ca", "Sacramento County"),
}


@dataclass(frozen=True)
class ProbeResult:
    """The verified subset of the candidate catalog."""

    verified: tuple[str, ...]
    probed: int
    already_registered: int


def probe_legistar_client(code: str, *, client: httpx.Client) -> bool:
    """True when ``/v1/<code>/bodies`` answers 200 with a JSON list."""
    try:
        response = client.get(f"{_API}/{code}/bodies", params={"$top": "1"})
        if response.status_code != 200:
            return False
        return isinstance(response.json(), list)
    except (httpx.HTTPError, ValueError):
        return False


def probe_candidates(
    candidates: dict[str, tuple[Level, str, str]] | None = None,
    *,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    delay_seconds: float = 0.2,
) -> ProbeResult:
    """Probe every candidate code not already registered; return the hits."""
    catalog = candidates if candidates is not None else CANDIDATES
    registered = {entry.client for entry in LEGISTAR_CLIENTS}
    to_probe = [code for code in catalog if code not in registered]
    http, owns = client_or_default(client, timeout=15.0)
    verified: list[str] = []
    try:
        for index, code in enumerate(to_probe):
            if index > 0:
                sleep(delay_seconds)
            if probe_legistar_client(code, client=http):
                verified.append(code)
    finally:
        if owns:
            http.close()
    return ProbeResult(
        verified=tuple(verified),
        probed=len(to_probe),
        already_registered=len(catalog) - len(to_probe),
    )


def registry_lines(result: ProbeResult) -> list[str]:
    """The verified hits as ``LegistarClient(...)`` source lines (dedup by place)."""
    seen_places: set[tuple[str, str, str]] = set()
    lines = []
    for code in result.verified:
        level, state, place = CANDIDATES[code]
        key = (level, state, place)
        if key in seen_places:
            continue  # two code spellings hit the same government; keep the first
        seen_places.add(key)
        lines.append(f'    LegistarClient("{code}", "{level}", "{state}", "{place}"),')
    return lines


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI glue
    import argparse

    parser = argparse.ArgumentParser(description="Probe candidate Legistar client codes")
    parser.parse_args(argv)
    result = probe_candidates()
    print(f"probed={result.probed} verified={len(result.verified)}", flush=True)
    for line in registry_lines(result):
        print(line, flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
