# Legal-information milestone

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
  legal applicability, a map→ordinance join, or permission to build. Oregon state law
  and Portland rules outside Title 33 are not acquired.
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

## Next gap

The highest-value coverage candidate is Oregon's state-law layer, with the publisher's
edition/update caveats intact. Before another adapter, prioritize reliable retrieval
of source media/table context and clearly dated updates. More rows alone would not
fix either limitation. The source notes distinguish researched leads from actual data.
