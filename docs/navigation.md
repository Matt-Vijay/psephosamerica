# Source-guided reading

Start with [bounded source discovery](discovery.md): an exact
`legal_coverage(jurisdiction="us-ny-nyc")` or
`legal_coverage(jurisdiction="us-or-portland")` lists only that catalog scope.
Its document/inventory views expose retained entry points and gaps without a
national provision scan. Other potentially applicable jurisdictions are separate.

These paths are exercised against retained sources by
`python scripts/verify_corpus.py --data data --out verification.json`.
[The recorded MCP receipt](navigation-verification.json) includes source-version
IDs, hashes, offsets and timings. It is not an applicability or buildability test.
The later [retrieval-review receipt](retrieval-review.json) verifies the list,
media-pagination and exact-link fixes through the installed non-editable package:
28 real MCP calls and complete correspondence across 117 retained NYC documents
and 4,068 units. The earlier receipt's immutable IDs/offsets remain valid.

`legal_find` searches literal text, case-insensitively with flexible whitespace.
Use its immutable `id` and `read_offset` in `legal_read`, then follow `next_offset`
for all qualifications. Offsets count Unicode characters in that exact text
version, not bytes or markup. A search excerpt is only a navigation aid.

Current HTML list projections preserve nesting and source order. `[ordered item N]`
is a DOM position, **not** an inferred legal paragraph label: NYC's bare lists can
use external CSS numbering that this projection does not reconstruct. Literal
source labels and cross-references remain in the text; full attributes are in
preserved markup. Do not assume a marker means `(a)`, `(i)`, or a cited paragraph.

For already-retained NYC data, `psephos reindex nyc` reprojects local source bytes
without network access. The `/text-3` parser generation has new immutable IDs;
old IDs still return their old text and offsets. These are parser projections of
the same source state, not additional legal snapshots. Repeating reindex adds
nothing. Other collections are not reprojected by this command.

Default reads consolidate raw image metadata into a single `media` list, with
`media_count` and `media_next_offset`. Pass the latter as `media_offset` (CLI:
`--media-offset`) to continue in pages of 20 descriptors. Inline image bytes are
omitted from descriptors, not erased from source markup/artifacts. Text and markup
have independent pagination; source-media warnings do not certify a transcription.

## NYC: general definition, scoped modification, district text

1. `legal_search(query="qualifying residential site", collection="nyc-zoning", limit=3)`
   returns §12-10 first and also exposes §114-02's scoped modification. Textual
   ranking is not legal precedence.
2. `legal_find(key_or_id="nyc-zr:12-10", query="qualifying residential site", limit=1)`
   locates the retained General Definition labeled Last Amended 12/5/2024. Read
   from the returned offset; do not treat the opening conditions as the whole rule.
   `legal_references` follows its explicit chapter-fragment links to acquired
   `nyc-zr:23-21` and `nyc-zr:66-11` only when the section's publisher URL matches
   that chapter and identifier. The original link/evidence and cutoffs are preserved.
3. Read `nyc-zr:114-02` separately. A definition modified for a special district is
   not silently substituted into the general definition.
4. `zoning_at(-73.985428, 40.748817, collection="nyc-zoning-gis")` returns the acquired
   C5-3 and Special Midtown District polygons, as well as the map-amendment layer.
   These layers have different meanings. Find `Midtown District (MID)` inside
   `nyc-zr:/appendix-b-index-special-purpose-districts`; the publisher's index points
   to §81-00. Read `nyc-zr:81-00` and follow its neighboring provisions/references.

The district index also contains historical status annotations; retain them.
This path demonstrates a publisher-encoded reference, not a computed conclusion
that every indexed restriction applies to a parcel.

## Portland: base zone, overlay guide, full code pages

Acquire the two small city guides once with `psephos sync portland-guides`.
They are indexed as `publisher_guide`, not codified law, with unknown snapshot dates.
The [city's base-zone guide](https://www.portland.gov/ppd/zoning-land-use/zoning-code-overview/base-zones)
warns that its standards are incomplete and may be superseded by overlays or plan
districts; the [overlay guide](https://www.portland.gov/ppd/zoning-land-use/zoning-code-overview/overlay-zones)
provides the map-symbol-to-chapter reading directions.

1. `zoning_at(-122.6765, 45.5231, collection="portland-zoning-gis")` returns the retained
   feature with base `ZONE=CX`, overlay `OVRLY=d`, and plan district `PLDIST=CC`.
   Keep all those source fields; base zoning alone is not the result of the research.
2. Find `CX` in `portland:guide/base-zones`. Read the complete Commercial/Mixed Use
   context and its link to Chapter 33.130. Search `33.130` in `portland-zoning` and
   read the code, including table notes. Page 169 in the retained edition contains
   Table 130-2; its layout text is unverified and the original PDF is the authority
   for visual table interpretation.
3. Find `d – Design Overlay Zone` in `portland:guide/overlay-zones`; the source points
   to 33.420. Search `33.420` in `portland-zoning`, read the chapter opening, then
   continue through applicability, timing and exemptions. Very short PDF pages
   rank below substantive matches but remain readable and searchable.
4. Search `Central City Plan District` in `portland-zoning` and inspect Chapter
   33.510's own applicability text. The retained GIS label is a reading lead, not
   an automatic map-to-legal-rule join.

The overlay guide notes a historic-resource overlay not displayed on maps. Empty
GIS results therefore cannot certify absence of restrictions. Code, guide and GIS
vintages remain independent; explicit date cutoffs exclude undated guides/GIS.

## Idaho: cited section in a retained chapter PDF

The current checkout accepts `legal_search(document="EXACT_DOCUMENT_ID", ...)`
and CLI `search --document`. The document filter combines with collection,
jurisdiction and date restrictions; an unknown or mismatched scope never falls
back to the national corpus. Search results include their document IDs.

Native Idaho PDF links can identify a retained enclosing chapter. Their status
is `chapter_retained_section_location_unverified`, with no `acquired_targets`:
the tool has not established an exact section-to-page map. Use the returned
`navigation.document` and `navigation.search_query` in `legal_search`.

For example, the retained Title 6, Chapter 3, page 9 links to 6-310. Searching
`6-310` within `id-statutes:T6CH3` returns both cross-references and its actual
heading on physical page 4. Read candidates and look for the source heading;
the highest-ranked lexical hit is not necessarily the section itself. Follow
neighboring pages for continuations and inspect the original PDF for tables.
This workflow is exercised by `scripts/verify_idaho.py`; it does not establish
current legal effect. These additions are not in the older v0.2.0 wheel.

## CLI equivalents

```sh
psephos search 'qualifying residential site' --collection nyc-zoning --limit 3
psephos find 'nyc-zr:12-10' 'qualifying residential site' --limit 1
# Substitute the returned immutable id and read_offset:
psephos read ID --offset OFFSET --length 3000
psephos find 'portland:guide/overlay-zones' 'd – Design Overlay Zone'
psephos search '33.420' --collection portland-zoning --limit 3
psephos search '6-310' --document id-statutes:T6CH3 --limit 10
```

No task asks the server to fetch an arbitrary URL, execute SQL, infer legal
precedence, or treat publisher text as instructions to an agent.
