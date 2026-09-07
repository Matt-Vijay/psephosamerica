# Portland municipal-code library

The `portland-code` adapter follows the Council Clerk's actual title and chapter
inventories and retains the linked whole-title printable HTML. It does not guess
URL ranges. Native membership, headings and nesting must agree with the export;
each section's prefix/body text, image locators and table count must also agree
with the independent native chapter display before the title is published.

The resulting `portland-city-code` collection is separate from `portland-zoning`.
Title 33 reuses the already retained July 1, 2026 PDF; it is not downloaded again
or silently called the same edition as the newly observed HTML.

The [September 7 receipt](portland-code-verification.json) accounts for all 34
listed titles: 33 HTML exports and the reused PDF. The new collection contains
3,095 sections, 366 chapter contexts, 33 title contexts, 33 change contexts and
three figure/context units. No listed title was omitted; Title 8 is not listed.
477 successful retained responses total 48,026,316 bytes, within the 128 MiB cap;
40 city-hosted image originals are retained, not transcribed. All new objects
rehashed, and 27 real offline MCP calls passed (6.360 seconds of tool-call time),
including acquired city/ORS references and an explicitly unmapped Title 33 link.
These are this acquisition's measurements, not a national-corpus retest.

## Acquire and verify

```sh
psephos --data data sync portland-code
python scripts/verify_portland.py --data data --out docs/portland-code-verification.json
```

Only run one acquisition writer. The durable `data/portland-code/plan.json`
records actual membership, receipts and failures. An interrupted run reuses
immutable acquisitions and retries the failed title; a parser or membership
failure cannot publish half a title. No historical-date, partial-title or refresh
mode is exposed by this adapter. New response payloads are bounded cumulatively
at 128 MiB, individual objects at 8 MiB, with at least 2.1 seconds between requests
on the city host. Robots and Retry-After restrictions remain enforced.
A cache-only resume with a transport that raises on every HTTP request preserved
all 33 version rows, made zero acquisitions and downloaded zero bytes. Its ordered
version-row SHA-256 is `055a215c61045ec8eab79860a676123544da67a5721faceea5478865ff22c7a8`.

The verification command uses only this addition's retained sources and one real
stdio MCP session; it does not rerun the national-corpus audit. Its receipt is the
authority for completed titles, counts, dates, source hashes, bytes and omissions.

## Read the source, including its limits

- `legal_coverage(collection="portland-city-code")` opens the source card;
  the `documents` view supplies title navigation keys.
- Scoped `legal_search` supports permit, tree, erosion, public-improvement and
  housing reading paths. Read complete relevant sections and their qualification
  text; lexical relevance does not establish which laws apply.
- `legal_read(key_or_id="PCC 24.10.070")` reads the permit application section.
  `PCC` aliases resolve retained citations, not arithmetic conversions to URLs.
- `legal_read(key_or_id="PCC 24.70.090")` preserves the grading setbacks text and
  calls out its drawing/table dependency. Full-size city-hosted images are retained
  where available; text is explicitly incomplete without those images.
- `legal_references` follows exact acquired city keys/URLs and printed Oregon
  statutory citations. Title 33 references offer collection navigation only:
  there is no verified section-to-PDF-page map. Bare Oregon chapter URLs are not
  fabricated section links.
- `legal_sources_at(longitude=-122.65, latitude=45.52)` discovers the Portland
  source collections using retained Census identities. This is discovery, not
  legal applicability or permission to build.

Chapters, sections, figure contexts and upcoming/recent-change contexts remain
distinct units. Prefix histories, repealed/reserved entries, source notes, list
markup and tables are retained. Upcoming ordinances are **not applied** to the
current displayed text. Unknown snapshot/effective dates remain unknown: crawl
time, a title replacement date and a section's legal effective date are different
clocks. Dated as-of queries exclude these undated HTML editions; observation
cutoffs remain independently enforceable.

The Title 24 figures page has an actual source-label inconsistency: its Figure
1/2 headings accompany Figure 2/3 alt labels referencing section 24.70.090.
Psephos preserves both labels and warns; it does not renumber or transcribe the
figures. The September 7 review visually inspected the two retained grading
drawings and Table 24.70-C (receipts 11195–11197): the table's feet units and
interceptor-drain footnote and the drawings' boundary/structure labels remain
legible. This is a three-image check, not certification of all source images.
External media, text embedded in images, incorporated model codes,
charter, policies and other city law are not silently included. Publisher access
is not a blanket redistribution license; source bytes and acquisition stores
remain local and ignored by Git. The retained privacy/accuracy notice is evidence
of the publisher's stated terms, not a newly inferred license.

Nationwide coverage accountability belongs in the separate canonical
[coverage ledger](coverage-ledger.json); a complete listed city-code inventory is
not comprehensive legal coverage of every Portland resident.
