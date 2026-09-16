# Data notices

## Software and source material

The [MIT license](../LICENSE), copyright Matthew Vijayasegar, covers original
Psephos code and documentation. It does **not** relicense source law, publisher
annotations, maps, images, datasets, dependencies, or third-party artifacts.
Preserve their notices. Neither public access nor successful import/export is
permission to redistribute a source collection.

Raw responses and databases are not in Git. A bundle may carry more than the
indexed text: an immutable source ZIP can contain other files. Review every
included artifact's scope, not just the selected collection names. Do not share
local filesystem paths, credentials, cookies, or private acquisition metadata.

## Starter release review

The [v0.1.0 starter](starter.md) includes only U.S. Code release 119-102.
The full D.C. ZIP and all other collections are excluded. This September 16,
2026 review inspected the two dependent artifacts: OLRC's retained download
index and its all-title XML ZIP. The export contains two public publisher URLs,
filtered response headers and no additional media or third-party archive files.

**U.S. Code: scoped starter.** The retained export includes OLRC's
[release 119-102 all-title XML ZIP](https://uscode.house.gov/download/releasepoints/us/pl/119/102/xml_uscAll@119-102.zip),
SHA-256 `55c8d19543c4a972a33e33532b592ac3984c83fdcb04de9f5a64ef1f8483d300`.
Its local ZIP directory contains 58 XML files and no other file types. The
[House XML site](https://xml.house.gov/) identifies the OLRC XML publication;
OLRC's [content guide](https://uscode.house.gov/detailed_guide.xhtml) describes
congressional laws compiled by its government editors. The domestic reuse
basis for that government-authored text is
[17 U.S.C. 101 and 105](https://www.copyright.gov/title17/92chap1.html#105), not
the fact that a download is public. This is a scoped assessment, not a publisher
license newly granted to Psephos. The live OLRC download page could not be
retrieved during this review; exact archive identity comes from the retained
[acquisition manifest](source-manifest.json).

Exclude separately acquired third-party annotations, incorporated standards,
images, and unrelated datasets unless independently cleared. GPO's official
[copyright notice](https://www.govinfo.gov/about/policies)
expressly warns that government publication does not remove third-party rights.
The XML file inventory is not a line-by-line rights audit. Source notices are
preserved; the final selected artifact inventory is recorded in [starter scope](starter.md).

**D.C.: code/law text supported; full retained ZIP not yet cleared.** The
[D.C. Law Library](https://code.dccouncil.gov/) explicitly places its codes and
laws in the public domain and requests bulk downloads instead of scraping.
Psephos uses the Council's
[codified XML repository](https://github.com/DCCouncil/law-xml-codified), pinned
to `6231fae42de60c3c498548c489b0ca84ef60353a`, rather than the uncompiled
`law-xml` repository linked from the landing page. The retained ZIP's SHA-256 is
`b6e55dec4c602e79278941725f4f798dbd14e5beaeaee00b34c4abd4a22f9621`.

That ZIP also contains five schemas, seven JPEGs, UK parliamentary XML, federal
legislative XML, and repository support files. The D.C. code/law statement alone
does not establish rights to every one of those members. Hold the whole D.C. ZIP
out of a public starter until those rights are resolved. Removing members would
create a derived artifact: it must not keep the original archive hash or be
represented as the same retained publisher response. D.C. law metadata stubs and
OCR search fields also do not establish full, verified law-text coverage.

## Other collections

These are summaries of retained evidence, not new distribution authorizations.
See [publisher boundaries](sources.md) and the per-batch `terms` in the
[archive catalog](../replay/catalog.json) before transferring any other source.

| Source | Retained notice or unresolved boundary |
| --- | --- |
| California Codes and Constitution | GOV 10248.5 supplies a scoped public-domain rule for information made public under 10248. The [documented correction](sources.md#california-reuse-correction) supersedes the older catalog annotation citing only 10248(g); unrelated third-party material is not covered. |
| eCFR, Texas, Oregon | Publisher APIs/downloads establish acquisition routes, not a blanket license to every incorporated work or media item. Oregon's retained 2025 ORS edition excludes later session changes. eCFR is not the official legal edition. |
| NYC GIS | NYC Open Data and agency-specific conditions apply; no generic CC0 claim. |
| Portland text and GIS | Preserve city-specific notices. The retained PortlandMaps PDDL terms PDF is marked DRAFT, so it is not conclusive redistribution permission. |
| Washington, Virginia, Florida | Publisher copyright/reuse notices remain. Washington's retained notice addresses selling copies; developer/download access does not resolve distribution rights. |
| Other imported state archives | Terms vary. Examples include Maine republication notices, Georgia Fastcase notices, Arkansas LexisNexis/Matthew Bender notices, and Kentucky commercial-use conditions. Local acceptance did not clear public redistribution. |

Do not describe snapshots as complete or current law. Preserve edition labels,
source URLs, acquisition clocks, unknown dates, and media/coverage exclusions.
Psephos is an independent research tool, not an official publisher or legal advice.
