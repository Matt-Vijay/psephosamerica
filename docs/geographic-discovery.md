# Geographic source discovery

`legal_sources_at(longitude, latitude)` intersects real Census polygons and routes
reviewed entity identities to exact retained collections. It does **not** find all
applicable law, decide boundaries, infer the nearest municipality, or return zoning.
Use `zoning_at` separately. Coordinates are WGS84 longitude first; no automatic swap.

## Measured scope

The [offline verification receipt](geographic-discovery-verification.json) records
the exact archives, hashes, native/stored field equality, counts, CRS, HTTP receipts,
actual MCP calls, response sizes/timings and runtime hashes. Raw bytes remain in the
ignored local store; no geometry or legal corpus is distributed in Git.

| 2025 layer | Selected archives | Indexed entities |
| --- | ---: | ---: |
| STATE / equivalents | 1 national | 56 |
| COUNTY / equivalents | 1 national | 3,235 |
| PLACE | 56 state/equivalent | 32,629 |
| COUSUB | 35 state/equivalent | 31,263 |
| Total | 93 | 67,183 |

STATE/COUNTY/PLACE cover the 50 states, DC, Puerto Rico and the four Island Areas
in the publisher inventory. COUSUB includes the 29 MCD states, DC and those five
territorial equivalents, preserving every row in each selected archive. The 21
COUSUB archives for CCD states and Alaska census subareas are explicitly omitted,
not acquired-then-discarded. Oregon therefore has place/county/state discovery,
not its statistical CCD layer. This is not nationwide municipal-government coverage.
Tribal, special-purpose, consolidated-city-specific and other Census layers are
not acquired. Some selected MCD records also represent reservation equivalents;
that is not complete tribal geographic coverage.

Native directory membership and ZIP central directories were inspected before bulk
acquisition. All 114 candidate archives total **547,262,699 compressed bytes**, above
the 536,870,912-byte budget. The coherent selected scope is **418,534,430 compressed
bytes / 691,980,250 declared uncompressed bytes**. All acquisition runs together
consumed **428,490,371 response-payload bytes**, including inventory suffixes and
source documentation. This is payload accounting, not HTTP header or compressed-wire
traffic measurement. The 217 distinct retained objects total 428,401,358 bytes.

The receipts include 105 HTTP 200s, 114 validated 206 suffixes and 26 rate-limit
responses. Acquisition honored retry delays. Census bulk robots policy allowed this
agent. A supplementary TIGERweb robots endpoint returned markup rather than policy;
no download from that site proceeded. NYC/Portland identities were checked against
the actual bulk PLACE records, not substituted from that inaccessible endpoint.

## Identity, clocks and limitations

- GEOID, its component strings, GNIS IDs, GEOIDFQ, CLASSFP, LSAD, MTFCC and FUNCSTAT
  retain source meanings and leading zeros. STATE has STUSPS, not CLASSFP; PLACE
  has no single county parent. All native properties match retained DBF records.
- Incorporated places and CDPs are distinct. COUSUB contains legal, statistical,
  nonfunctioning and hierarchy-filling entities; it is not synonymous with township
  government. FUNCSTAT is not a yes/no government classifier. For example, the
  actual Connecticut planning regions have status N, while Baltimore's Census
  county/subdivision equivalents have F. Their native attributes are returned.
- A point can intersect several independent layers or shared boundaries. Exact
  polygon tests include boundaries and respect holes. No geometry is simplified or
  silently repaired. This retained Census slice has zero invalid polygons and zero
  duplicate layer/GEOID keys; existing zoning topology warnings remain unchanged.
- The edition's [boundary/name vintage is January 1, 2025; release September 23,
  2025](https://www.census.gov/geographies/mapping-files/2025/geo/tiger-line-file.html).
  Acquisition occurred September 7, 2026. These are separate clocks—not current
  boundaries. `geometry_as_of` selects only geographic snapshots; an earlier date
  returns no eligible geometry. `observation_cutoff` separately excludes later
  acquisitions. Legal-text snapshot ranges are independent, not overwritten by the
  geometry date; unknown effectiveness/history remains unknown.
- STATEFP/STUSPS identifies state/equivalent registry IDs. Only NYC (`3651000`,
  GNIS `02395220`) and Portland (`4159000`, GNIS `02411471`) currently have reviewed
  city crosswalks, guarded by exact native name/class/status. No fuzzy name joins.
  Other places, counties and subdivisions say `not_crosswalked`; an absent state
  registry says `unregistered_jurisdiction`. Registered collections without eligible
  bytes say so. These do not mean no law. Federal collections are labeled reference
  sources, not inferred applicability or a complete federal authority list.
- Results paginate at 10 entities by default, maximum 20, under 24 KiB. Collection
  lists are bounded with a `legal_coverage` continuation. Geometry is not dumped
  into tool responses. Missing point-scope archives and omitted subdivisions are
  explicit; follow pagination before drawing a coverage conclusion.

Source definitions and scope selection follow [Census 2025 Chapter 4, especially
§§4.7–4.8 and 4.15](https://www2.census.gov/geo/pdfs/maps-data/data/tiger/tgrshp2025/TGRSHP2025_TechDoc_Ch4.pdf).
That edition explicitly uses Connecticut's nine planning regions as county
equivalents. Older descriptions of its eight counties are not used.

## Reproduce and read

Use the existing Python 3.12 environment (`pip install -e '.[dev]'` if needed).
The GIS dependencies already belong to this package; no database service or new
stack is required. Acquisition is optional when the local store already contains
this checkpoint. Only the sync command uses the network.

```sh
.venv/bin/psephos --data data sync census-geography
.venv/bin/psephos --data data sources-at -73.9857 40.7484
.venv/bin/psephos --data data sources-at -122.675 45.52
.venv/bin/psephos --data data serve
.venv/bin/python scripts/verify_census.py --data data --output docs/geographic-discovery-verification.json
.venv/bin/pytest -q tests/test_census.py tests/test_acquire.py tests/test_geography_mcp.py
```

The sync pins this edition and subset: no `--refresh`, `--limit` or invented
historical edition. Each complete archive publishes atomically. Repeated requests
reuse rehashed full artifacts; a cached suffix can never substitute for full bytes.
Failed transfer bytes remain in the cumulative budget. Interrupted publication can
resume without replacing old versions. The proof injects a failure after the first
real NY PLACE feature in a fresh tiny catalog: no partial version survives; resume
then indexes all 1,293 features, and a repeat preserves the same version with zero
network calls/bytes. It does not copy the national catalog.

Actual MCP paths exercised by the receipt:

| Point | Source discovery | Separate reading path |
| --- | --- | --- |
| Midtown Manhattan | NYC, New York county, Manhattan subdivision, New York state; NYC/NY and US reference collections | `legal_coverage(collection="nyc-zoning", view="documents")` → `legal_read(first_key)` → `source_receipt`; separate `zoning_at` |
| Downtown Portland | Portland, Multnomah county, Oregon; city/OR and US reference collections; CCD omission explicit | `legal_coverage(collection="portland-zoning", view="documents")` → `legal_read(first_key)` → `source_receipt`; separate `zoning_at` |
| Hartford | Capitol Planning Region, Hartford place/subdivision, Connecticut | State collections retained; local entities not crosswalked |
| Honolulu | Urban Honolulu **CDP**, Honolulu county, Hawaii | Hawaii registry unregistered; not “no applicable law” |
| Baltimore | Independent-city place and Census county/subdivision equivalents, Maryland | Native entities remain separate, no invented surrounding-county parent |

A real NYC polygon vertex also returns `on_boundary=true`; a Queens point returns
the same NYC PLACE GEOID in a different county. Ocean/earlier-vintage/earlier-observation
queries return no eligible matches. The receipt records 20 actual MCP calls, including
bounded discovery, source navigation and zoning-scoped/unscoped equality.

Focused gates: 22 tests across the three files above; Ruff check/format across the
10 changed Python files; strict mypy across seven runtime/proof files. No Florida,
Washington, collection-wave or full-catalog verification was rerun.

## Rights and remaining gap

[Census Chapter 1](https://www2.census.gov/geo/pdfs/maps-data/data/tiger/tgrshp2025/TGRSHP2025_TechDoc_Ch1.pdf)
permits reproduction of U.S. government materials and requests Census attribution;
it separately states TIGER/Line® trademark conditions and disclaims positional
accuracy and jurisdictional-authority determinations. Psephos uses the name only to
identify the source, not as a proprietary product name. Legal-text collection rights
remain source-specific and are not changed by acquiring public Census geography.

The largest gap is **retained local legal collections and reviewed identity links**,
not more polygon detail. County and township boundaries do not supply their codes,
current law, delegated authority or overlap rules. This checkpoint adds a bounded
source-discovery route, not an applicability engine or a legal-completeness claim.
