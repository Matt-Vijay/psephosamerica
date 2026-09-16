# Retained-Source Research Checks

Checked 2026-09-16. Six model-authored research tasks, not a nationwide accuracy
benchmark or a legal opinion. Exact arguments, result summaries, immutable IDs,
publisher URLs, artifact hashes and source clocks are in [research-checks.json](research-checks.json).
Call numbers below refer to that record.

## Method

Actual MCP stdio sessions: Python 3.12.13 / MCP 1.29.1 launched the existing
`create_server(Path("data")).run(transport="stdio")` subprocess and used
`ClientSession.initialize`, `list_tools`, and `call_tool`. There were 68 successful
tool invocations plus one tool listing. This was not a direct Reader fallback or
a pre-registered connector. The assistant chose/refined queries, inspected returned
text/markup, and wrote these findings; the Python client only transported calls.

The server opened the canonical store read-only. No network acquisition, imports,
credentials, new legal sources, or canonical DB edits were performed. This was a
shared live workspace, not an immutable corpus-wide snapshot.

## 1. Portland ADUs

**Question:** What bears on two ADUs on a 3,000-square-foot R5 house lot, including
size, visitability exceptions, and Oregon's minimum ADU requirement?

Calls 7, 11-13, 20, 23, 28-29, 47, 60. The retained PDF's Table 205-2 projection
reads R5: 3,000 square feet for two ADUs; at least one must be detached.
Street-frontage conditions and their private-street/pedestrian-connection exceptions
must also be checked. Size is generally the lesser of 75% of the primary unit's
living area or 800 square feet, with a five-year basement-conversion exception.
Visitability requirements continue across pages 290-291, including slope,
grade-rise and existing-building-conversion exemptions.
[Title 33, chapter 33.205, PDF pp. 287-291](https://efiles.portlandoregon.gov/record/17901607/file/document#page=287).

[ORS 197A.425](https://www.oregonlegislature.gov/bills_laws/ors/ors197A.html)
requires at least one ADU in its specified population/UGB/zoning scope; its
reasonable-siting/design definition excludes owner occupancy and additional
off-street parking, subject to the vacation-occupancy exception.

**Limit:** Partial answer, not buildability. PDF table/figure layout was not visually
verified; base-zone structure standards and parcel facts remain open. Portland's
complete edition is labeled July 1, 2026; these chapter pages carry 10/1/24.
The ORS 2025 edition expressly omits later session changes and has no exact snapshot date.

## 2. NYC Definition, Table and Exception

**Question:** Does every 5,000-square-foot R3-2 lot qualify for enhanced FAR, including
in Bay Ridge, and can the rule be reconstructed for August 12, 2026?

Calls 6, 8, 14-16, 30, 37-38, 44, 48, 54, 56, 68. No blanket size-only rule:
the first residential route also requires transit geography and frontage; other
community-facility/senior-housing routes exist. The relevant definition starts at
character 174905 of the 273324-character [section 12-10](https://zoningresolution.planning.nyc.gov/article-i/chapter-2/12-10).
[Section 114-02](https://zoningresolution.planning.nyc.gov/article-xi/chapter-4/114-02)
excludes Bay Ridge lots existing December 5, 2024 whose area exceeds five acres.

[Section 23-21](https://zoningresolution.planning.nyc.gov/article-ii/chapter-3/23-21)
markup confirms R3-2 standard FAR **0.75 with footnote 1**, not the flattened
text's apparent 0.751; qualifying-site FAR is 1.00. Footnote 1 caps the
single-dwelling equivalent FAR at 0.60 on standard lots of at least 4,000 square feet.
Markup pagination was followed separately from text.

**Limit:** No parcel/transit determination, complete special-district survey or HPD
agreement analysis. Section 12-10 flags 17 source-media descriptors. The August 12
cutoff returns nothing; August 13 succeeds. Two listed versions share one artifact
and snapshot date, so they do not establish two historical laws. The particular
definition says Last Amended 12/5/2024, distinct from section-level metadata.

## 3. Florida Residential Deposits

**Question:** What are return/claim-notice deadlines, email conditions and exclusions,
and is the September 1, 2026 rule established?

Calls 9, 17-18, 22, 43, 49, 53, 55, 61.
[Sections 83.49, 83.505 and 83.42](https://www.flsenate.gov/Laws/Statutes/2026/Chapter83/All)
distinguish a 15-day no-claim return from 30-day claim notice and the tenant's
15-day objection period after receipt. Email requires the voluntary signed
addendum and associated delivery/retention safeguards in 83.505. The
fewer-than-five-units exception belongs to **83.49(2)**, not all deposit duties.
Subsections (4)-(5), subsidized-housing qualifications and Part II exclusions matter.

**Limit:** No individual notice/lease/termination finding. Returned references were
empty, so printed cross-citations were followed manually. The 2026 edition has
null snapshot/publication/effective dates: `as_of=2026-09-01` cannot answer
historical law, and observation time cannot fill that gap.

## 4. Virginia Contractor Licensing

**Question:** Which Class C entry, electrical-specialty, value and exemption rules
must an applicant investigate?

Calls 10, 19, 21, 27, 31-33, 40, 46, 50-52, 57-58, 62.
[18VAC50-22-40](https://law.lis.virginia.gov/admincode/title18/agency50/chapter22/section40/)
requires an adult qualified individual, one year of relevant experience,
employment/responsible-management status, specialty qualifications, disclosures,
and a management business course.
[Section -61](https://law.lis.virginia.gov/admincode/title18/agency50/chapter22/section61/)
HTML pairs ELE with a master electrician license and "No" in the examination
column; this does not waive the separate master credential.

[Va. Code 54.1-1100](https://law.lis.virginia.gov/vacode/54.1-1100/) gives Class A/B/C
value alternatives. Its Class C wording is a project over $1,000 but under $30,000
**or** annual value under $250,000; no final class assignment is made.
[Section 54.1-1101(A)(15)](https://law.lis.virginia.gov/vacode/54.1-1101/)
has a qualified $25,000 subcontract-work exemption that preserves individual
licensing requirements.

**Limit:** Emergency rules, forms and Register instruments were not researched.
VAC's stored September 7 snapshot date is acquisition-derived, not publisher
currency; historical notes identify September 1, 2025 while `effective_on` stays
null. Statutory CSV dates are unknown. Applying a date cutoff therefore excludes
the retained statutory targets, correctly preventing an invented dated answer.

## 5. Federal and Florida Housing Interaction

**Question:** Does a Florida small owner-occupied-building exemption alone settle
whether an advertisement may express a familial-status preference?

Calls 24-25, 34-35, 39, 41-42, 45, 59, 63. It does not support that inference.
[42 USC 3615](https://uscode.house.gov/view.xhtml?edition=prelim&num=0&req=granuleid%3AUSC-prelim-title42-section3615)
preserves same-rights state/local protections but rejects contrary permission
to the extent of a federal discriminatory practice.
[Section 3603(b)](https://uscode.house.gov/view.xhtml?edition=prelim&num=0&req=granuleid%3AUSC-prelim-title42-section3603)
expressly preserves 3604(c)'s advertisement rule in its owner-occupied four-family
exemption. [Florida 760.23(3) and 760.29](https://www.flsenate.gov/Laws/Statutes/2026/Chapter760/All)
have their own prohibition and detailed exemptions.

**Limit:** Source comparison only, without an individual liability opinion, case law,
HUD guidance or additional/local exemption analysis. The federal July 12 release
point and undated Florida edition are not synchronized effective-law snapshots.
A federal observation cutoff one second before retained availability returns
nothing, not evidence that the law did not exist.

## 6. Unsupported Ann Arbor Municipality

**Question:** Can this store establish ADU permission, setbacks or parking at
longitude -83.743, latitude 42.28?

Calls 1-5, 26, 36, 64-67. **Not answerable from retained local coverage.**
The 83-entry collection directory contains no Ann Arbor local-law collection.
The guessed catalog jurisdiction returns `unknown_jurisdiction`. Census identifies
Ann Arbor city (PLACE GEOID 2603000), its county subdivision, Washtenaw County and
Michigan, but local identities are `not_crosswalked`. Michigan/federal collections
are separate. `zoning_at` returns no retained polygons.

Geometry vintage January 1, 2025, publication September 23, 2025 and September 2026
observations do not date municipal rules. Missing coverage never means permission.

## Fixes and Verification

- Virginia's retained VAC and statute URLs previously returned no acquired target.
  Exact native URL/key/collection checks now resolve them; altered URLs, ambiguity
  and date exclusions remain unresolved. Calls 27 and 57 show before/after.
- The 3603 link to 3604(c) previously failed although 3604 was retained. Resolution
  now verifies the exact nested USLM identifier and labels the result
  `resolved_within_section` / `enclosing_section`, preserving the original
  citation and evidence. Calls 35 and 41 show before/after.
- Six focused new regression cases cover these fixes, malformed/duplicate/missing
  identifiers, wrong collections/URLs, version selection and cutoffs. The final
  selected retrieval/navigation suite passed **57 tests**. Scoped Ruff and mypy
  passed. A separate read-only database check verified all 22 recorded evidence
  IDs, citations, URLs, acquisition IDs and hashes. Full-repository testing is not claimed.
- Changed only `src/psephos/retrieve.py`, `tests/test_research_workflows.py`,
  `docs/research-checks.json`, and this file. No commit or push.

Remaining issues include manual printed-citation navigation for Florida/ORS/PDF
text, no verified Portland section-to-page map, unverified PDF layout, and mixed
date granularity. These examples establish neither nationwide recall nor legal
accuracy outside the evidence actually inspected.
