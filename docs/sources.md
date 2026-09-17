# Publisher boundaries

Initial source URLs, bytes, hashes, HTTP headers and acquisition clocks are in
[source-manifest.json](source-manifest.json). The [collection-wave receipt](collection-wave.json)
identifies the later complete local manifest. All raw bytes are local and ignored by
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

The [municipal-code adapter](portland-code.md) adds separately observed Clerk
printable titles outside Title 33, with native inventory/body reconciliation,
source histories and explicit figure limits. It reuses this Title 33 PDF rather
than silently refreshing it or inferring section-to-page links.

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

## Later state collection

The subsequent [Oregon completion receipt](oregon-completion-verification.json)
closes the retained 2025 ORS chapter inventory at 689/689. It adds 92 former gaps
using 20,090,622 response bytes; 597 accepted versions and 65,234 prior provision
IDs remain unchanged. This still excludes later sessions, regulations and
untranscribed media. `scripts/complete_oregon.py` resumes this bounded campaign
from the retained store with a cumulative 64 MiB cap.

The [completed wave](collection-wave.md) adds bounded state sources, with exact
inventory states and mixed retrieval-unit counts kept distinct. Oregon now supplies
a bounded state-law tranche above Portland's city sources. The
[2025 ORS index](https://www.oregonlegislature.gov/bills_laws/Pages/ORS.aspx) expressly
excludes 2025 special-session and 2026 regular-session changes, and distinguishes
online text from the official printed edition. Its
[update inventory](https://www.oregonlegislature.gov/lc/Pages/ORSupdate.aspx) and
[Chapter 197 HTML](https://www.oregonlegislature.gov/bills_laws/ors/ors197.html) expose
update warnings and session-law links. The adapter preserves those warnings,
statutory notes and source links; it does not consolidate current law.

Oregon administrative-rule annual compilations are a separate, unimplemented lead.
The coordination audit encountered HTTP 200 with a cybersecurity block page at an
annual endpoint, not usable source data. No access bypass, credential request,
purchase or administrative-rule acquisition was performed. The accepted Oregon
tranche is ORS text, not that blocked administrative-rule source. California bulk
Code/Constitution tables and a Washington RCW tranche were separately accepted.

### South Carolina continuation

The maintained `south-carolina-code` command follows the retained
[publisher title inventory](https://www.scstatehouse.gov/code/statmast.php) to
whole-chapter HTML exports. It preserves native sections, repeated identifiers,
history, amendment notes, tables and chapter context. Untagged bodies and
repeal/transfer notices remain explicit whole-chapter units, not invented sections.
Probate Code articles keep their native hierarchy. Heading-only exports require
a matching RESERVED label in the retained title inventory and remain inventory
notices. A publisher section number outside its enclosing chapter is retained
verbatim with a warning and source-local key, never silently repaired or assigned
the conflicting canonical section identity.
The retained inventory notice describes incorporation through the 2025 session;
retrieval dates are separate, and precise snapshot/effective dates remain unknown.

The original 200 MiB ledger and its exclusive collector lock are reused directly.
The historical request list remains unchanged; new receipts are in the canonical
store. Access denials and Retry-After stop collection without automatic retries.
Neither the separately retained regulations and constitution nor later session
laws are silently re-collected or consolidated. The publisher's copying notice,
personal/noncommercial site notice and unofficial-online-text qualification remain
in the source records; no broader redistribution permission is inferred.

### Minnesota continuation

`minnesota-statutes` follows the retained 105-part inventory to 1,133 whole-chapter
HTML exports. It preserves native section/disposition IDs, table structure,
history, notes and repeated occurrences, and checks each body's publisher edition
label against the master. Original PDF companions stay retained; new PDF links are
explicitly unacquired. The publisher designates authenticated PDFs and printed
volumes as official records, not these HTML projections; no signature verification
or comprehensive current-law claim is made.

The original 200 MiB allowance uses its existing `decoded_bytes` counter directly.
No automatic retries or new allowance is granted by a resume/import. Current,
undated section links resolve only to matching retained Minnesota section IDs and
URLs. Dated/subdivision-specific URLs remain unresolved rather than being silently
redirected to current whole sections. Rules and constitution are unchanged.

### Idaho continuation

`idaho-statutes` expands all native title rows into exact linked chapter PDFs or
explicit nonexport repeal/reservation notices. It preserves the original 79
chapter versions without reindexing. New projections retain each physical page,
source PDF and publisher URI annotations; annotation evidence uses stable page,
rectangle and URI fields instead of runtime object representations.

The original `collectors/idaho/bytes.json` counter, 200 MiB cap, 1 MiB safety
reserve and 100 GiB disk threshold remain in force. Completed bodies are reused;
`--limit` applies to newly attempted bodies, not inventory requests. No automatic
retries, new allowance or scheduled refresh is implied. Unreadable PDF pages fail
acceptance and remain visible for source-media review.

An observed 404/410 export stays `source_unavailable`, with its receipt, while
independent chapter exports can continue. Ordinary resumes do not request that
missing URL again; an explicit refresh can reconsider it. Access denials,
rate limits, Retry-After responses and transport errors still stop acquisition.
Missing exports never close a title inventory.

Short history-only pages can match a narrow full-page statutory-history syntax
after an optional physical-page number. With no embedded image, those pages are
labelled `short_history_note_pattern_unverified`, not visually certified. This
does not accept arbitrary short text, missing brackets, blank pages or draft
placeholders. Earlier hash-specific visual-review labels remain unchanged.
Native bracketed chapter labels, such as `CHAPTER 21 [22]`, retain their wording
and use the publisher-linked bracketed identity; unrelated chapter mismatches
remain rejected. New projections require Poppler's `pdftotext`: pypdf's layout
fallback reversed line order on an inspected publisher PDF and is not accepted.

The publisher identifies Lexis printed copies as the official Code. The retained
2026 legislative-session notice does not establish exact snapshot or effective
dates. Page text is unverified layout extraction, not a complete section-level
or table-geometry representation. Constitution, regulations and later session
laws are outside this command; no redistribution permission is inferred.

### New York continuation

`new-york-laws` uses the already-reviewed public Senate whole-law PDF contract,
not its authenticated JSON API or previously denied index. It reuses the original
180 MiB ledger, 1 MiB reserve and 1.1-second minimum pacing. Selected candidate IDs
are not an authoritative statewide inventory; 404s remain source gaps and ordinary
resumes do not retry them. Denials and transport failures stop acquisition.

Every new PDF must contain substantive text and an explicit title heading or
statutory self-naming clause matching its label. Scattered occurrences of words
from a title do not establish identity. PDF-backed corrections are REL (Rural
Electric Cooperative), CAN (Cannabis), PBG (Public Housing) and PBL (Public Lands).
Earlier unverified alias guesses are withdrawn, not silently treated as equivalent
laws. The [verification receipt](new-york-laws-continuation-20260917.json) records
the corrections, raw-source preservation and MCP behavior.

Units remain physical PDF pages, including contents and headings where present,
not statutory sections. Text-empty pages remain in the raw PDF and are explicitly
accounted for; no OCR, visual/table fidelity or blanket legal date is asserted.
Regulations, local law, later session-law consolidation and redistribution rights
are outside this selected-source command.

### Illinois continuation

`illinois-statutes` follows native chapter/act links and each actual **View Entire
Act** link. It reconciles chapter and act identifiers, retains alternate/future
text, notes, tables and linked-media descriptors, and rejects lost source text.
The publisher's unofficial drafting-database and future-effective-text warnings
remain visible. Repeal labels are inventory metadata, not acquired historical law.

The original 200 MiB allowance, 10.1-second pacing and 100 GiB disk floor remain
in force. The [preflight](illinois-statutes-preflight-20260917.json) stopped at a
TLS issuer-validation failure; a separate diagnostic HEAD with normal system trust
returned 403 without fetching a body. Retained denials prevent retries. The adapter
has retained-source and offline-fixture validation, not successful live statewide
acquisition. No TLS bypass, alternate-host acquisition or new coverage is claimed.

### Oklahoma continuation

`oklahoma-statutes` follows the [Senate catalog](https://oksenate.gov/search-statutes-constitution)
and its actual pagination/PDF links. The retained seven-page catalog has 127
entries: 89 title-labeled exports, 37 constitution article companions and the
whole constitution. No paths are guessed and companion PDFs are not silently
assumed equivalent. The two Title 85 exports have identical observed bytes but
separate URLs/receipts; raw bytes are deduplicated by hash.

Each new PDF must have a matching printed identity and source body beyond
contents lines. Article 28-A begins directly with its own section number. The
catalog's 74E is explicitly mapped to its printed **Title 74, Appendix I, Ethics
Commission Rules** heading, not presented as a separate statutory title. Page
text, escaped markup, image-appearance notices and any encoded URI annotations
are retained; tables, maps and figures are not certified visual transcriptions.

The original 200 MiB ledger preserves both decoded spending and the earlier
16 MiB uncertain charge, with 1.1-second pacing, a 16 MiB response cap and a
105 GiB disk floor. The original collector's literal robots-rule interpretation
is retained: `/search?` does not prohibit `/search-statutes-constitution`, while
actual `/search?query` and `/search/` paths remain prohibited. Rules are not
removed or rewritten to permit a request; access denials remain enforced.

[Exact replay and MCP verification](oklahoma-pdf-catalog-20260917.json) establishes
retained catalog closure, not all/current Oklahoma law. Original PDFs have older
creation/upload metadata; neither these nor observation times assign incorporation
or legal-effect dates. Later amendments, separate rules collections and uncodified
session laws are not reconciled. This command is not scheduled for automatic refresh,
and no blanket right to redistribute the source corpus is asserted.

### California reuse correction

The retained GOV §10248.5 explicitly overrides §10248(g)'s proprietary reservation
for information the Legislative Counsel makes public under §10248. The latter's
subdivisions (a)(9)–(10) list the California Codes and Constitution. The scoped
public-domain rule is also established in the official
[2016 chaptered AB 884, Chapter 441, section 2](https://www.leginfo.ca.gov/pub/15-16/bill/asm/ab_0851-0900/ab_884_bill_20160922_chaptered.pdf).
The original collector review cited §10248(g) alone and was incomplete. The adapter
and current catalog annotation are corrected; frozen acceptance evidence is not
rewritten. A local addendum preserves the prior annotation and its correction.

Exact retained evidence: provision `e8c9fb085e6ef2c463795ed03ee9a546` (GOV §10248.5),
from bulk artifact SHA-256
`830a8445a3107840604f7a719ad2b71c2da86040f39b92e87439c30fef563491`.
This source-specific rule does not license unrelated third-party content or this
repository's software, authorize access bypass, or establish certified/current
legal text. No external publication of the retained corpus is requested or performed.
