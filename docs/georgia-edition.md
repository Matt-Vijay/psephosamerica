# Georgia: a closed publisher PDF inventory, not certified legal completeness

All **154 publisher-listed department PDFs** are retained at the same cover
filing-through date, **August 21, 2026**. The [verification receipt](georgia-edition-verification.json)
records 23,232 physical pages: 23,140 publisher-text pages, 90 pages with unverified
machine OCR, and two source-media-only diagram pages. All 19,709 native rule
bookmarks retain their hierarchy and physical-page destinations; bookmarks are
not independently counted operative rules.

This closes the named [Secretary of State download inventory](https://rules.sos.ga.gov/Download_pdf.aspx).
It does **not** establish complete current Georgia law, complete machine-readable
text, a verified transcription of diagrams/table cells, or a legal-effect timeline.
Fastcase's all-rights-reserved notices remain; no public redistribution permission
is inferred. Raw PDFs and derived OCR stay in the ignored local evidence store.

## Read and reproduce

From the repository root, with the existing evidence store:

```sh
.venv/bin/psephos status --collection ga-administrative-rules
.venv/bin/psephos search 'Water Well' --collection ga-administrative-rules
.venv/bin/psephos search 'Wetlands' --collection ga-administrative-rules
.venv/bin/psephos read ga-rules:department-110:page-298
.venv/bin/psephos read ga-rules:department-40:page-842
.venv/bin/psephos read ga-rules:department-20:page-1 --as-of 2026-08-14

# Completed campaign resumes without fetching or reindexing.
.venv/bin/python scripts/complete_georgia.py --data data
# Independent offline PDF/page/order/bookmark checks and actual stdio MCP calls.
.venv/bin/python scripts/verify_georgia.py --data data --out tmp/georgia-verification.json
.venv/bin/python scripts/coverage_population.py --data data --check
.venv/bin/pytest -q tests/test_georgia_rules.py tests/test_acquire.py tests/test_store_retrieval.py
```

Python 3.12+, Poppler (`pdftotext`, `pdftoppm`) and Tesseract with English language
data are needed for new projections. Recorded versions: Python 3.12.13, pypdf
6.17.0, Poppler 26.02.0 and Tesseract 5.5.3. Existing query tools do not run OCR or
access the network. `legal_read` returns explicit media descriptors and receipts
bound to the **same PDF hash**, even when a newer file exists at its URL.
Search results identify unverified OCR; original images always control.

The continuation uses a sealed `data/georgia-completion/plan.json`, seeded from
inventory receipt 11407 by `--index-receipt 11407 --edition 2026-08-21`.
`--acquire-only` separates acquisition from indexing when needed. This is a
resumable continuation of an accepted source inventory, not an undocumented
generic historical downloader. Do not delete/reset its plan, budget or writer lock
to circumvent the campaign limit.

## What changed, and what was checked

The previous collection had 124 department PDFs at August 14, with 23 omitted
image-only pages in four volumes. The missing 30 were acquired first. Their covers
revealed that the publisher had advanced the edition to August 21; a small older
department confirmed the change. The other older URLs were therefore refreshed
once, rather than calling a mixed-date inventory one edition. Every refreshed PDF
has a different source hash. All 124 accepted versions and 16,958 provision IDs
remain unchanged; four intermediate August-14 parser repairs also remain retained.

The new edition totals **221,137,027 PDF bytes**. Total new response consumption
is **222,371,568 bytes**, including the 163,341-byte inventory and one failed
1,071,200-byte small-file probe. The 512 MiB campaign cap is separate from the
prior collector's 198,207,232-byte ledger, which was not reset. The new driver
retains a persistent host clock, a single-writer lock, and write-ahead byte
reservations so an interrupted transfer cannot silently refund its allowance.
No uncertain reservations remain in this run.

Verification reconciles every physical page against the PDF page tree, every
native text projection against full-page layout extraction, and every bookmark
against the original outline. Exact replay checks exercise both a text-heavy
department and the mixed-media Department 110. A cache-only resume makes no
network requests and changes no acquisition/version/provision counts. Actual MCP
routes cover water wells, wetlands, manufactured homes, media/OCR access and
incorporation/observation cutoffs. These are retrieval proofs, not legal opinions.

The [media review](georgia-media-review.json) distinguishes physical retention from
text quality. Department 110 has 69 image-only pages: 24 forms/worksheets, six
tables/reference pages and 39 diagram/illustration pages. None is blank. Pages 298
and 311 contain faint building-detail drawings; captions are navigation, not a
transcription of their geometry or callouts. OCR makes other image pages
searchable but demonstrably misreads some fractions, words and table alignment.
Across the edition, 522 pages contain detected embedded image objects, including
covers and mixed text/image pages. Neither that count nor OCR success certifies
all graphics or embedded standards.

## Why this family, not California or Illinois

The comparison used retained evidence, then a 163,341-byte Georgia inventory
preflight, below the 16 MiB preflight ceiling. Population is the already-retained
July 2025 Census estimate, not a new Census collection.

| Candidate | Residents | Concrete retained-source finding |
| --- | ---: | --- |
| California statutes | 39,355,309 | PUBINFO provides a Sunday baseline and weekday new-record files, but omits deletions. Its daily full-session exports exclude Code tables. The retained loader and 203,380 archive members contain no documented TipIn image export/locator; 25 section records have missing media. A new baseline would not establish that those missing pages are supplied. |
| Illinois statutes | 12,719,141 | 68 native chapter entries, only one expanded. The advertised FTP tree has per-section HTML, not an evidenced all-code archive; one act alone lists 1,006 files. Whole-act acquisition is preferable but statewide act/byte totals are unestablished, with a ten-second publisher crawl delay. |
| Georgia regulations | 11,302,748 | Exact 154-department PDF inventory; bounded missing exports and identifiable image-page gaps. Selected and completed as a publisher edition, with searchable-text exclusions explicit. |

California evidence: canonical receipts 2989–2991 (loader, PUBINFO Readme pages
2–3, News page 2) and baseline 2996; original missing-media count is in
`data/collectors/california/report.json`. No further California download or
prohibited interactive-page access occurred. Illinois evidence remains in
`data/collectors/il/store`, receipts 7 and 19; the FTP README specifies an older
November 21, 2025 export and separate ordering/repealer sidecars, not a cheap
complete modern snapshot. Neither unfinished family was relabeled complete.

The single [coverage ledger](coverage-ledger.json) adds Georgia's 11,302,748
residents **once** to closed-publisher-inventory reach for state regulations.
That is a 3.3070 percentage-point increase, not an increase in the unestablished
complete-current-law metric. Statutory, regulatory and local coverage are never
summed into an invented national legal-coverage percentage.
