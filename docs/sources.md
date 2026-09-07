# Publisher boundaries

Source URLs, bytes, hashes, HTTP headers and acquisition clocks are in
[source-manifest.json](source-manifest.json). All raw bytes are local and ignored by
Git. Metadata-only publications are labeled as such. Public access is not a blanket
permission to redistribute every map, image or derivative dataset.

## Federal

**OLRC U.S. Code.** The [publisher download index](https://uscode.house.gov/download/download.shtml)
provides supported all-title and per-title ZIPs. Release 119-102 contains 58 XML
documents; the index labels Title 53 reserved. Appendix notices, court rules and
compiled acts are separate unit types, not invented statutory sections. Duplicate
publisher section identifiers remain separate occurrences. Positive-law flags and
source-created literals are retained. Source `/us/usc/...` references are legal
identifiers, not fictitious HTTP paths. Reading links open the *live* preliminary
edition; the retained release archive is the reproducible evidence.

**eCFR.** The [title inventory](https://www.ecfr.gov/api/versioner/v1/titles.json)
and [versioner documentation](https://www.ecfr.gov/reader-aids/ecfr-developer-resources/rest-api-interactive-documentation)
provide keyless XML snapshots. Title 35 is reserved. An active import blocks current
ingestion. The [publisher's date guide](https://www.ecfr.gov/reader-aids/ecfr-developer-resources/understanding-ecfr-dates)
distinguishes amendment, issue and up-to-date clocks; current title metadata is not
asserted as metadata for an earlier snapshot. Government-maintained eCFR is not the
official legal edition. Acquisition retains the requested incorporation state,
not a derived conclusion about the effective law.

Some required forms/wording are images. In 21 CFR 10.31, the two official image
locators redirect to an access-block service whose robots policy disallows this
client. Acquisition stopped; neither image was transcribed or bypassed. Reads now
return inline omission markers, source locators and `incomplete_without_source_media`.

## D.C. and Texas

The [D.C. publisher](https://code.dccouncil.gov/) states public-domain use and asks
users to prefer bulk XML/HTML to scraping. This build uses
[`law-xml-codified`](https://github.com/dccouncil/law-xml-codified), not the similarly
named uncompiled baseline repository. A pinned commit and local-only XInclude
expansion preserve exact member identities/hashes. Publisher annotations retain
separate effective/application dates. `search-text` OCR is not exposed as verified
law text. Native code-to-law links can terminate at an honestly labeled metadata
record; a successful identity join does not magically provide its missing text.

Texas ingestion uses the [Council's download inventory](https://statutes.capitol.texas.gov/assets/StatuteCodeDownloads.json)
and supported `tcss.legis.texas.gov/resources/Zips/` HTML exports, not a browser crawl
of individual sections. The [currency notice](https://statutes.capitol.texas.gov/information/)
describes legislative-session coverage and constitutional updates. Neither that
notice nor HTTP Last-Modified supplies a precise legal snapshot date. Parallel
future-effective versions and source notes remain present. The parser does not
consolidate them or treat a bill-text link as proof that a bill was enacted.

## Municipal text and geography

**New York City.** The [Zoning Resolution](https://zoningresolution.planning.nyc.gov/)
has chapter HTML, section last-amended labels and linked appendices. Sixteen indexed
appendix *pages* are not claimed to be sixteen legally distinct appendices. The
[Open Data archive](https://data.cityofnewyork.us/api/views/mm69-vrje.json) contains six
actual geodatabase layers. EPSG:2263 geometries are transformed to WGS84 using the
declared CRS. Zoning districts, commercial overlays, special districts/subdistricts,
limited-height districts and map-amendment features remain distinct. A pending or
adopted map-amendment property is not substituted for a zoning district.
[NYC Open Data terms](https://opendata.cityofnewyork.us/overview/) and agency conditions
apply; this is not represented as a generic CC0 dataset. All six layers publish in
one local transaction from one immutable archive.

**Portland.** The [Title 33 page](https://www.portland.gov/code/33) links the complete
printable edition through the [City Archives record](https://efiles.portlandoregon.gov/record/17901607).
This is the July 1, 2026 edition. A separately listed November 1, 2026 ordinance is
not folded into it. Page retrieval preserves layout text and the original PDF;
page counts are not provision counts. The review visually checked PDF page 169,
Table 130-2, including its column headings and footnotes. It did not visually verify
all 1,855 pages or determine which development standard applies to a parcel.

The [PortlandMaps layer](https://www.portlandmaps.com/arcgis/rest/services/Public/Zoning/MapServer/3)
provides explicit object-ID inventory and paginated geometry. Each accepted version
contains the entire enumerated layer and exact page receipts. Missing/duplicate IDs
or failed refreshes leave the preceding accepted version unchanged. Initial/final
membership checks do not freeze attributes during the export; this service does
not advertise the needed historical snapshot isolation. Plan, zoning, overlays,
unincorporated areas and other fields keep their source meanings.

The posted [PortlandMaps terms PDF](https://www.portlandmaps.com/bps/arpa/tos.pdf)
describes default PDDL except where overridden, but is visibly watermarked **DRAFT**.
The acquisition stores that exact evidence; it does not treat the PDF as a conclusive
redistribution license. Confirm operative terms before redistributing a corpus.

## Next coherent coverage option — not ingested

Oregon would supply the state-law layer above Portland's city sources. The
[2025 ORS index](https://www.oregonlegislature.gov/bills_laws/Pages/ORS.aspx) expressly
excludes 2025 special-session and 2026 regular-session changes, and distinguishes
online text from the official printed edition. Its
[update inventory](https://www.oregonlegislature.gov/lc/Pages/ORSupdate.aspx) and
[Chapter 197 HTML](https://www.oregonlegislature.gov/bills_laws/ors/ors197.html) expose
update warnings and session-law links. A future adapter should preserve those
warnings, statutory notes and source links, not claim to consolidate current law.

Oregon administrative-rule annual compilations are a separate, unimplemented lead.
The coordination audit encountered HTTP 200 with a cybersecurity block page at an
annual endpoint, not usable source data. No access bypass, credential request,
purchase or Oregon corpus acquisition was performed in this milestone. California
bulk data and Washington publisher services are also leads, not acquired coverage.
