# Current status

September 17, 2026. Psephos is a local legal-source library and read-only MCP
server. It retrieves retained evidence for an LLM; it is not a legal reasoning
model, a hosted service, or a nationwide current-law guarantee.

## Usable release

- [v0.2.0](https://github.com/Matt-Vijay/psephosamerica/releases/tag/v0.2.0) packages
  automatic refresh, paginated retained history and the operational fixes below.
  The original v0.1.0 federal data bundle remains compatible and unchanged.
- [Install and connect an MCP client](getting-started.md). The wheel runs outside
  the checkout. Nine tools cover source discovery, search, reading, text finding,
  references, versions and geographic lookup.
- [Download the U.S. Code starter](starter.md): 284 MiB, 58 documents and 63,475
  mixed retrieval records, with original source bytes and dates. The larger
  working corpus is local, not bundled into the software or public download.
- The released wheel's `psephos sources` lists 19 acquisition commands; the
  current checkout adds `south-carolina-code`, `minnesota-statutes`,
  `idaho-statutes`, `new-york-laws` and `illinois-statutes`, totaling 24.
  Georgia, Virginia, Oregon, Washington, Nebraska and Portland Charter now have
  installed entrypoints; historical collector directories are not the public interface.
- `export` / `import` transfer explicitly selected collections. Import verifies
  content hashes, fixed-schema rows, source relationships and spatial-index
  membership into a new store; it never overwrites an existing store.
- MIT covers original software, not the source corpus. [Data notices](data-notices.md)
  record why the public starter excludes other retained archives.

## Coverage and demonstrated use

At the v0.1.0 checkpoint, the local catalog contained **87 collections, 21,037 documents, 21,285 versions
and 1,523,925 mixed retrieval records**. This continuation adds Nebraska's
66-document completion and all 15 Portland Charter chapters, preserving the
prior corpus. These additions total 28,018 retrieval records; they are not
28,018 new laws. The public starter remains the smaller federal-only dataset.

The [coverage ledger](coverage-ledger.json) is dated source-by-source
accounting, with later family updates explicitly marked. It separates completed publisher inventories, partial bodies,
blocked sources and absent families. A completed inventory can be an older or
narrowed edition; population living in those jurisdictions is not comprehensive
legal coverage. Counts of sections, PDF pages, context records and versions must
not be presented as interchangeable counts of laws.

The September 17 [Washington continuation](washington-rcw-continuation-20260917.json)
adds **956 chapter exports** across two passes, bringing RCW to **1,381 / 2,785**
retained inventory entries, with 1,403 pending and one budget-deferred export.
Exact replay verified 16,715 section occurrences, 956 chapter notes and 22
subchapter headings; eight actual MCP calls verified reading, source receipts,
date exclusions and contents links. The original 424 versions / 8,764 records
are unchanged. The original 250 MiB download allowance is exhausted at the next
chunk reservation; more network acquisition requires an explicit allowance
increase. The [earlier 602-export checkpoint](washington-rcw-completion-20260917.json)
remains dated evidence. Unknown legal clocks, pointer-only chapters and remaining
gaps stay explicit; population reach is unchanged by this partial family.

The [South Carolina continuation](south-carolina-code-completion-20260917.json)
closes the retained **1,308-export / 63-title** inventory, adding 986 exports with
23,631 section occurrences, 974 contexts, ten whole-chapter units, two reserved
inventory notices and one publisher numbering anomaly. Full-text reconstruction,
exact field/reference replay and nine real MCP calls passed. The source's wrong
section number is retained under a chapter-specific key, not silently corrected;
the two empty RESERVED exports are not counted as statutes. All 448 earlier SC
versions / 16,343 records are unchanged. The original 200 MiB ledger remains shared
with the historical collector: 135,697,707 bytes charged, including prior work.
The retained 2025-session notice and mixed observation dates do not establish
complete current law; this command is not on the automatic-refresh schedule.

The [Minnesota continuation](minnesota-statutes-continuation-20260917.json)
adds **859 chapters**, reaching **901 / 1,133** publisher HTML exports and
79 closed subject parts out of 105. Exact replay and full-text reconstruction
verified 21,106 section records, 21,284 disposition records and 859 contexts;
seven actual MCP calls passed, including retained section-to-section navigation.
All 50 original Minnesota versions / 3,358 records and references are unchanged.
The original 200 MiB ledger reached 209,633,515 bytes; the next declared body
exceeds the remaining 81,685. The 231 pending chapters and one budget-deferred
chapter remain visible. These HTML projections are not authenticated PDFs or a
complete current-law collection, and new PDF companions were not downloaded.
The maintained-adapter CI gate passed 220 tests with one skip and one intentional
deselection, plus Ruff and strict mypy across 32 modules.

The [Idaho inventory verification](idaho-statutes-inventory-20260917.json) now
retains **1,444 of 1,471 linked chapter PDFs**, with 59 of 74 title inventories
closed. All **1,365 added PDFs / 11,647 pages / 23,662 publisher links** replay
exactly; 15 real MCP calls passed, including document-scoped search, source
heading inspection and visible missing-export records. All 79 original PDFs /
610 records and references remain unchanged. Short-page repairs preserve narrow
history-note labels and visually checked repeal notices. Poppler is required;
an observed pypdf fallback reversed reading order.

Remaining PDF gaps are 15 draft placeholders, six chapter-number mismatches, one
404, one failed transfer and four pending files. The 279 native nonexport notices
remain separate, and T15CH15 still has no listed PDF. Its actual HTML link also
hit a connection reset; no body or alternate access is claimed. The shared
200 MiB ledger has 111,992,623 bytes charged, including an interrupted transfer's
65,536-byte reservation; 96,674,001 usable bytes remain after the original 1 MiB
reserve. This is partial page-level coverage, not complete current law, verified
table reconstruction or additional closed-inventory population reach.

The [New York continuation](new-york-laws-continuation-20260917.json) adds
**28 whole-law PDFs / 7,270 indexed pages**, reaching 77 selected volumes and
51,934 page records. All added projections replay exactly; the original 49
versions / 44,664 records and 59 receipts are unchanged. Fifteen real MCP calls
verify retrieval, corrected labels, receipts and date exclusions. Eight text-empty
pages remain in the raw PDFs, not certified visually blank.

Four candidate titles were corrected against the PDFs: REL is Rural Electric
Cooperative, CAN Cannabis, PBG Public Housing and PBL Public Lands. The erroneous
330-page PBG projection created during this continuation was archived and withdrawn;
its replacement preserves every source-text and markup byte. Eight unsupported
legacy alias assertions now remain unverified candidates, not accepted equivalences.
Five selected IDs returned 404. There is still no authoritative statewide inventory,
regulations collection or complete-current-law claim. The original 180 MiB ledger
has 70,642,447 bytes charged; the API/index denials were not retried.

The [Illinois preflight](illinois-statutes-preflight-20260917.json) adds no legal
bodies. Its maintained adapter replays all 12 retained acts / 1,297 units, preserving
13 original versions including the constitution. Live acquisition stopped at TLS
issuer validation; a system-trust diagnostic HEAD returned 403, without a body.
TLS verification was never disabled. The retained denial remains enforced, the
38,099,825-byte spending counter is unchanged, and statewide live validation is
not claimed. The combined offline code gate passed **246 tests**, with one skip
and one intentional deselection; Ruff and strict mypy passed across 35 modules.

[Six research checks](research-checks.md) used 68 actual MCP calls on retained
Portland, NYC, Florida, Virginia and federal sources, plus an unsupported Ann
Arbor question. They found and fixed Virginia URL navigation and verified federal
subsection navigation. The report preserves unanswered questions, table-reading
limits and date exclusions; it is not a benchmark accuracy score.

Fresh-environment verification installed the package, imported the full starter,
discovered all nine tools and exercised eight real stdio calls. Reproduce the
small installed-package check with `scripts/verify_release.py`. CI exercises
offline fixtures and lint, without downloading the legal corpus.

The v0.1.0 integrated gate passed 168 tests with one skip and one intentionally
deselected full-payload check; Ruff and strict mypy passed. All 28 package modules
matched the released wheel. A separate local full-data backup preserves the working
catalog, original objects and collector evidence; it is not an off-site backup.

The post-release refresh gate passed **198 tests**, with the same skip and
deselection; Ruff and strict mypy passed. Live checks completed U.S. Code
release 119-103, D.C.'s two collections and the two selected Portland guides.
On September 17, the macOS timer completed the eCFR September 15 snapshot:
**49 active titles indexed, one reserved title, 245,166 mixed retrieval records**.
The final pass reused completed title bodies, downloaded the remaining 11, and
finished at 06:21:51 UTC. Each active title has a successful acquisition receipt,
a retained source file matching its recorded size and a nonempty indexed version.

The unattended schedule is now installed and enabled, with a verified background
run exiting successfully. The fix was operator-approved Documents access for the
timer's actual Homebrew Python executable; no Full Disk Access was granted.
It wakes hourly, checks due sources weekly, and retains the 512 MiB per-pass and
8 GiB per-month allowances. All four scheduled sources have completed checks;
this does not certify legal effectiveness or freshness of unscheduled sources.

The **v0.2.0 gate passed 201 tests**, with one skip and one intentional deselection,
plus Ruff and strict mypy across 30 modules. All 30 modules matched the wheel.
Installed outside the checkout, the wheel exercised all nine MCP tools with 11
calls against the retained starter and 12 against the working corpus, including
source receipts, version pagination and geographic discovery. The working-store
Portland probe returned three Census entities and one retained zoning polygon;
the federal-only starter correctly returned no geometry.

The September 17 integrity audit rehashed **15,795 artifacts / 6,939,024,826 bytes**
and passed SQLite, foreign-key, source-provenance, key and spatial-index membership
checks. That audit, before the RCW continuation above, measured 26,036 retained versions and 2,060,152 mixed
retrieval records across all versions, versus 1,494,554 latest projected records.
These are storage measures, not counts of unique laws or a coverage percentage.
Detailed local receipts are retained under `data/verification/20260917-v0.2.0/`.

This release also makes failed CLI audits exit nonzero, serializes offline
reindexing with other public writers, and reduces one measured Georgia version
listing from 1,893,863 bytes to 8,218 bytes by omitting repeated bulk lists from
the response only. The original metadata, files and immutable IDs remain intact.

## Remaining boundaries

- Many states and nearly all municipalities lack complete retained families.
  Source access restrictions remain recorded, not bypassed or counted as data.
- Unknown snapshot/effective dates stay unknown. A latest local result may be
  stale; a missing result is not evidence that a restriction does not exist.
- Printed citations in some PDFs/HTML still require manual navigation. External
  standards, image transcription, complex PDF layout and complete amendment
  histories are not solved by text extraction.
- Zoning polygons and Census identities do not determine parcel applicability
  or permission to build. Invalid geometry is reported, not silently repaired.
  The September 17 audit found 51 invalid publisher polygons among 99,787 latest
  geometry features; these are warned about and excluded from point conclusions.
- [Automatic refresh](refresh.md) now supports four reviewed source commands,
  with local scheduling, persistent allowances and failure/overdue reporting.
  Other sources remain manual or fixed-edition. Portable snapshots do not
  transfer campaign or refresh allowances.
- Only the starter's specific artifacts received this release's redistribution
  review. No blanket right to publish the larger corpus is asserted.

Earlier [integrity](integrity.json), [collection-wave](collection-wave.md),
[navigation](navigation.md), [source-specific](sources.md) and
[offline replay](../replay/README.md) receipts remain dated evidence. This page
replaces the old chronological status narrative; it does not rewrite those
measurements or claim they were all rerun for this release.
