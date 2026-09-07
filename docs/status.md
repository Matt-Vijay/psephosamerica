# Legal-information milestone

The measurements below freeze the initial `e077e98` checkpoint, not the expanding
live catalog. Run `psephos status` for current accepted collections. The subsequent
[navigation receipt](navigation-verification.json) exercises eight MCP tools and
the [NYC/Portland reading workflows](navigation.md). Collector stores are reviewed
separately before a single writer publishes them into the canonical catalog.

This checkpoint implements the fresh source-retrieval mission, not the earlier
Psephos evaluation/RL experiments. It is a working local library with substantial
real text, actual geometry and read-only MCP. Nationwide coverage, legal conclusions
and complete legal histories are not acceptance claims.

## Evidence

- [Integrity measurements](integrity.json): all retained artifacts rehashed;
  SQLite, keys, acquisition relationships, clock constraints and geometry indexes
  checked. Complete source bytes are separately ignored, not copied into Git.
- [HTTP/source manifest](source-manifest.json): exact artifact hashes, sizes, URLs,
  headers and observed times. Initial development receipts retain their original
  request-start time; completed-byte observation was conservatively migrated using
  the retained object's filesystem mtime, explicitly labeled in the headers. New
  receipts record completion directly. This is not backdated historical observation.
- [Real retrieval verification](retrieval-verification.json): actual stdio MCP
  initialization, all seven tools, search/read, source receipts, historical cutoff,
  unavailable-source exclusion, D.C. code→law relation, PDF-page/table warning, image
  warnings and two geographic lookups. The independent publisher checks compare
  three statutory paragraphs against OLRC's alternate HTML ZIP and Portland's
  point-query object ID against local polygons.
- [Execution receipt](verification.json): fresh temporary installation/demo,
  focused tests, static checks, software versions, durations and repository boundary.

Reproduce storage checks with `psephos --data data audit`; reproduce retrieval checks
with `python scripts/verify_corpus.py --data data --out verification.json`. The first
publisher verification may need `--publisher-checks` (5 MiB cap). Routine query/MCP
operation is offline. Receipts measure the actual recorded inventory, not hypothetical
future runs of mutable publisher indexes.

## Known limits found by measurement

- 2,961 D.C. law records are metadata-only. A code-to-law edge can resolve to a stub;
  the original act's complete text is not invented.
- 2,802 retrieved units contain source-media elements: 2,713 eCFR, 81 NYC, six U.S.
  Code and two D.C. Code. Their external media is not transcribed. Markers and links
  prevent an empty text projection from silently claiming completeness.
- Of 1,855 Portland PDF pages, 224 have an image/extraction warning and 1,631 have
  unverified layout text. Blank pages and diagrams can contribute to that count.
  There is one representative visual table check, not a whole-document certification.
- 51 of 32,604 acquired/transformed polygons are topologically invalid. The point
  tool reports nearby invalid geometry and does not silently repair or use it for a
  topological conclusion. Valid/invalid details are in the integrity receipt.
- 376,594 latest-document references use supported internal identifier families;
  273,541 have an exact acquired target. The remainder can point to missing acts,
  lower-level fragments, hierarchy nodes or identifiers outside this corpus. An
  unmatched reference is retained, not joined by title similarity.
- Only eCFR Titles 1–3 have the extra 2024 snapshot in this corpus. The other 46
  historical title acquisitions remain pending. Texas and both GIS exports lack an
  exact publisher snapshot day and are excluded by date-based as-of queries.
- Source-native section projections are not lossless whole-document rendering:
  original bytes are the fidelity authority, with source containers, hierarchy and
  selected scope notes exposed for reading. PDF images, complicated table semantics,
  current legal effect and exhaustive contextual applicability remain reader work.
- GIS vintages differ from text vintages. The point tool is not parcel delineation,
  legal applicability, a map→ordinance join, or permission to build. At this initial
  checkpoint Oregon state law and Portland rules outside Title 33 were not acquired.
  Subsequent Oregon collection is a separately reviewed, bounded 2025-edition tranche.
- Acquisition is capped/resumable, but no scheduler or continual synchronization
  service is included. A later mutable source refresh can fail without making its
  older local bytes current. Page-by-page legal collections expose per-document
  coverage; only complete GIS exports have dataset-wide atomic publication.
- Lexical search is useful and deterministic, not semantic recall certification.
  There is no model, embedding index, legal-reasoning engine or arbitrary SQL tool.

The earlier development parser representations were removed from this fresh catalog
after successful replacement: 9,945 derived versions, 590,691 derived units and
32,604 duplicated geometry rows. These were parser iterations, not distinct legal
snapshots. All original artifacts/acquisition receipts remain; indexes are rebuildable.

## Subsequent work

The [geographic source-discovery checkpoint](geographic-discovery.md) adds pinned
2025 Census entities and exact retained-source routing through `legal_sources_at`,
separate from zoning. Its [receipt](geographic-discovery-verification.json) measures
only that addition; it does not rerun or revise earlier campaign evidence.

The [completed collection wave](collection-wave.md) published 45 accepted batches
from isolated source stores and rehashed the entire expanded catalog. The
[portable receipt](collection-wave.json) freezes that later measurement separately
from the baseline above. Held and blocked states remain separate. Whole-source
inventory, readable text, mixed PDF pages and current legal effect are different
claims. No population-coverage percentage is asserted.

The subsequent [retrieval review](retrieval-review.json) corrects list structure,
bounded image metadata and exact NYC links, with 41 focused tests and an installed
wheel/MCP check. Reindexing adds 117 parser projections while preserving all old IDs,
source bytes and normalized legal content; it adds no legal snapshots or latest
source units. The [California reuse correction](sources.md#california-reuse-correction)
is an annotation addendum, not a rewrite of frozen acquisition/publication evidence.

The [accepted-source replay checkpoint](../replay/README.md) preserves the reviewed
code for all 45 published batches and exercises exact offline reconstruction for
Florida, New Jersey and Mississippi. Its [receipt](../replay/verification.json)
is separate from the prior campaign; no new collection or whole-corpus audit was
performed. The other 42 batches remain provenance-only in that replay surface.

The later [source-discovery checkpoint](discovery.md) replaces the unfiltered
national coverage dump with scoped, paginated metadata views. It adds two receipted
current collection annotations, not legal versions or source data. Its focused
MCP/tests [receipt](discovery-verification.json) is separate from the prior audits.
Receipt read tools now filter imported headers at output without rewriting stored
evidence. Sync/audit summaries explicitly separate requested-source results and
corpus counts from paginated discovery.

The [Washington land-use checkpoint](washington-land-use.md) adds nine complete
selected SEPA/GMA WAC chapters, supplemental RCW 43.21C, three Register filings and
bounded index/agency context: 18 documents and 428 mixed units, including 335 WAC
rule sections. Its [separate receipt](washington-land-use-verification.json)
records 27 source objects rehashed, exact reconstruction of 13 primary documents /
423 units, a cache-only resume with zero new source data, and an initial 22-call
MCP reading path. A final affected-call check verifies the smaller action-table
metadata output without altering stored evidence. Exact native Washington citation
resolution preserves both cutoffs. Live compilation, certified archives, proposals,
filings, agency characterizations and legal effectiveness remain distinct. This is
not statewide regulatory completeness or a new historical-law reconstruction.

The [Florida edition checkpoint](florida-edition.md) extends the earlier 52-chapter
slice to all 638 chapters listed under the retained 49-title 2026 Senate inventory:
24,993 section nodes and 638 separately labeled context units. Its
[receipt](florida-edition-verification.json) verifies all index/body/store
memberships, rehashes 688 objects / 150,262,942 bytes, preserves the original 52
version rows and 1,831 provision IDs, and exactly reconstructs five representative
chapters / 582 units. A network-denying resume reused every version with no new
source data; 17 real MCP calls exercised tenancy, environmental-permit and business
reading paths, notes, references and both cutoffs. The current source card reports
the expanded scope while preserving its old review as historical metadata.
Administrative/local law, source media and exact legal effectiveness remain
outside this edition-completeness claim. Earlier campaign/replay/discovery receipts
are frozen; they were not rewritten or rerun to manufacture a new whole-corpus audit.
