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
- `psephos sources` lists 19 maintained acquisition commands and their limits.
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

The [coverage ledger](coverage-ledger.json) is that release's source-by-source
accounting. It separates completed publisher inventories, partial bodies,
blocked sources and absent families. A completed inventory can be an older or
narrowed edition; population living in those jurisdictions is not comprehensive
legal coverage. Counts of sections, PDF pages, context records and versions must
not be presented as interchangeable counts of laws.

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
checks. The working catalog has 26,036 retained versions and 2,060,152 mixed
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
