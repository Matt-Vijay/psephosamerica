# Virginia Administrative Code: retained publisher text, explicit limits

The scoped source is the publisher's permanent [Virginia Administrative Code](https://law.lis.virginia.gov/admincode/),
not every regulation effective in Virginia. Its retained native inventory lists
24 titles, 152 agencies and 2,027 chapters, including 774 chapters labeled
`[Repealed]`. These are publisher entries, not counts of operative rules.
All 2,027 chapter bodies are retained and exactly replayed against their native
section inventories. The [verification receipt](virginia-edition-verification.json)
records the source hashes, counts, clocks, media accounting and real MCP checks.

The [Code Commission FAQ](https://codecommission.dls.virginia.gov/faq_va_admin_code.shtml)
excludes emergency regulations and describes daily permanent-code updates, with
some exempt actions lagging effectiveness. Historical reconstruction, intervening
amendment application and complete-current-law certification are out of scope.
Commonwealth copyright notices remain; developer access is not a public corpus
redistribution license. Raw HTML, JSON and image responses stay local.

## Read and reproduce

Use Python 3.12+ and the installed project dependencies. From the repository root:

```sh
.venv/bin/psephos status --collection va-administrative-code
.venv/bin/psephos search stormwater --collection va-administrative-code
.venv/bin/psephos read va-vac:9VAC25-875-10
.venv/bin/psephos read va-vac:9VAC25-875-20
.venv/bin/psephos read va-vac:9VAC25-875-1375
.venv/bin/psephos read va-vac:2VAC5-332-70
.venv/bin/psephos read va-vac:1VAC17-10-10
.venv/bin/psephos read va-vac:18VAC115-100-60  # Explicitly ambiguous publisher citation.
.venv/bin/psephos read va-vac:18VAC115-100-60/_occurrence/1

# Bounded native discovery, then resumable chapter bodies and agency summaries.
.venv/bin/python scripts/complete_virginia.py --data data --inventory
.venv/bin/python scripts/complete_virginia.py --data data
.venv/bin/python scripts/complete_virginia.py --data data --prefaces
.venv/bin/python scripts/complete_virginia.py --data data --media

# Offline exact replay, source hashes, native membership and real stdio MCP calls.
.venv/bin/python scripts/verify_virginia.py --data data --check-resume --out tmp/virginia-verification.json
.venv/bin/pytest -q tests/test_virginia_rules.py tests/test_acquire.py tests/test_georgia_rules.py tests/test_store_retrieval.py
```

The campaign seals the native inventory in `data/virginia-completion/inventory.json`.
`--limit N` bounds newly ingested chapters, not the denominator; pending/error
entries remain visible. The initial preflight has a 16 MiB ceiling and the entire
campaign has one non-resetting 512 MiB payload allowance. Requests are paced at
least 1.1 seconds apart, subject to any stricter publisher policy. One writer lock,
durable byte reservations and rehashed cache receipts survive restart. Do not
delete budgets or start a parallel writer to bypass the limit. The verifier
refuses to run while the campaign writer is active.
Successful verification seals the campaign's receipt range so later independent
source imports do not change its byte accounting.
`--check-resume` first reruns the existing chapter, summary and image stages with
HTTP forbidden, checking that receipt/provision/version counts and charged bytes
do not change. It may update local progress files; verification without that flag
does not invoke the writers.

## Verified retained scope

The September 7–8, 2026 UTC acquisition observations yielded 2,178 documents:
2,027 chapters and 151 agency summaries. Chapter projections contain 40,224
retrieval units: 33,873 sections, 724 source-reference lists, 2,027 chapter contexts
and 3,600 hierarchy contexts. There are 34,596 distinct native section/reference-list
citations; one citation occurs in two different source parts. These are not counts
of operative regulations. All source text, unit order, markup, metadata and links
matched exact offline replay; 1,614 chapter tables and 270 image occurrences were
accounted for independently against raw tags.

The image pass retained 175 of 180 distinct external URLs (4,494,540 response bytes).
Five literal HTTP URLs were outside the reviewed HTTPS routes; none was silently
upgraded or declared unavailable at the publisher. One embedded-data image has no
fetchable URL and remains in the source HTML/markup. Diagrams are not certified
transcriptions, even when their image bytes are retained.

The initial metadata/seven-chapter preflight consumed 2,869,997 bytes. Total
receipted payload was 219,531,172 bytes; the conservative campaign charge was
219,596,708 bytes, including one previously recorded 65,536-byte interruption
allowance, below the 536,870,912-byte cap. All 4,399 distinct newly retained objects
were rehashed (219,481,223 unique bytes). Failed policy/transport observations
remain receipted, not hidden or refunded.

The verifier finished in 28.885 seconds on Python 3.12.13, lxml 6.1.3, httpx 0.28.1
and mcp 1.29.1. Ten actual stdio MCP cases checked coverage, search, bounded reads,
media receipts and duplicate-citation ambiguity, with receipt hashes and historical
observation exclusions on concrete reads. A network-forbidden rerun of all three
acquisition stages changed neither the 2,178 versions/40,375 provisions nor the
18,040 catalog acquisition count or campaign charge. This proves cached resumption,
not a live-source refresh or legal-currentness guarantee.

The retained-Census [coverage ledger](coverage-ledger.json) adds Virginia's
8,880,107 residents once to closed regulatory-source inventory reach: 33,653,506
people across five states, or 9.8464% of the recorded U.S. population denominator.
Verified complete-current-regulatory-layer reach remains zero. Reproduce the
separate population accounting with `scripts/coverage_population.py --check`.

## What the source actually supplies

The [advertised developer services](https://law.lis.virginia.gov/xmlapi/)
use `/api/` endpoints. XML-named operations return JSON. The sampled section-list
operation supplies membership/hierarchy but null body fields; those records are
**not retained rule text**. Each chapter index supplies its actual *Read Chapter*
link, which leads to the complete publisher HTML. No chapter/body URL enumeration
is guessed. Native headings, section order, parts/articles, tables, authority and
history text are reconciled against that chapter and its section index.

Agency prefaces use a separately advertised operation and are labeled
`agency_summary`, never regulations. All 152 responses are retained: 151 supply a
body, while 14VAC10 explicitly supplies none. That absence is preserved without
creating a placeholder provision.

The HTML page title can name the last section; the actual chapter heading controls
the document label. Some publisher HTML leaves zero-padding layout wrappers open
across sections. The parser respects the literal article boundary, unwraps only
the measured presentation-only wrappers, and checks ordered text and table/image
counts. It never imports the site footer as law. Repeated headings and repeated
forms content remain in their native source identity; conflicting duplicate
identities are rejected rather than silently merged.
Identical repeated section-index entries retain their publisher IDs and all body
occurrences together, with an explicit unresolved-identity warning. Reference-standard
wrapper attributes are preserved in each section fragment, not discarded as formatting.
The same citation published in different parts remains separate source occurrences;
an unqualified read reports ambiguity instead of choosing one. These are source
identifiers and observed occurrences, not a reconstructed legal-version lineage.
An observed `<ahref="...">` typo is repaired by inserting its missing separator
only; its literal URL and label survive, and the original bytes remain available.

Images retain visible omission descriptors and exact source URLs. The bounded
image pass follows only embedded HTTPS resources under `law.lis.virginia.gov/RISImages/`
and `ris.dls.virginia.gov/uploads/`, not
links to forms or documents incorporated by reference. Missing resources remain
explicit, and literal HTTP links are not silently rewritten to HTTPS.
Successful image responses are separately observed artifacts, not a
certified transcription or proof of the image's historical content at the HTML
snapshot date. Embedded image data remains in the original HTML and markup.

## Clocks and boundaries

Snapshots use the actual acquisition date, explicitly labeled as an observation,
not a uniform edition or legal effective date. The page's print date is separate
presentation metadata. Published/effective/amended/repealed date columns remain
unknown; source authority/history wording is retained without guessing a
consolidated timeline. `observation_cutoff` also restricts independently acquired
image receipts.

The single [coverage ledger](coverage-ledger.json) must distinguish this named
publisher inventory from complete current regulations. Repealed/removed entries,
hierarchy headings, forms lists and agency summaries are separately countable;
none substitutes for missing emergency rules, unincorporated actions, external
standards or case law.
