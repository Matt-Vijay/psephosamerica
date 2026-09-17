# Active source completion

The broader project is not complete. Release readiness below is a finished
milestone, not the stopping condition for the legal-information-library goal.

Current work: South Carolina's retained inventory is closed; continue
through the evidence-backed queue in `docs/coverage-ledger.json`. Washington's
original download allowance is exhausted; no additional allowance is assumed.

- [x] Reconcile the 559 original Washington receipts with the canonical store.
  Restore the original 250 MiB lifetime cap with 95,764,815 bytes already charged
  across 598 retained Washington receipts, including later source work.
- [x] Preserve the publisher's synthetic centered subchapter heading in 47.26
  without inventing a numbered statutory section. Separate the actual contents
  column from cross-references in headings: 85.05.510 is a cited reference, not a
  missing inventory body. Genuine missing section entries still fail closed.
- [x] Add 602 accepted RCW chapter exports: 600 newly downloaded and two recovered
  from earlier retained failures. Reconcile heading citations, multiline article
  headings and two pointer-only chapters without inventing statutory sections.
- [x] Exactly replay the 602 new projections (9,980 section occurrences, 602
  chapter notes, nine subchapter headings), verify eight real MCP calls including
  contents-to-section navigation, and preserve all 424 earlier versions / 8,764
  provision records. See `docs/washington-rcw-completion-20260917.json`.
- [x] Add another 354 RCW exports within the original allowance, including two
  pointer-only notices recovered from cache. Exactly replay all 956 maintained
  projections and eight real MCP calls; see
  `docs/washington-rcw-continuation-20260917.json`.
- [ ] Close the remaining RCW inventory: 1,381 of 2,785 accepted, 1,403 pending,
  one budget-deferred export; 44 retained title inventories closed. The original
  250 MiB allowance has 262,096,013 bytes charged and 47,987 bytes remaining,
  below one response-chunk reservation. Await an explicit allowance increase;
  do not reset spending or start another campaign to bypass this limit.
- [x] Close South Carolina's retained 1,308-export inventory across 63 titles:
  986 added exports, 23,631 section occurrences, 974 contexts, ten whole chapters,
  two reserved inventory notices and one verbatim source-number anomaly. Full
  text/field/reference replay and nine real MCP calls passed. All 448 earlier SC
  versions / 16,343 records remain unchanged. See
  `docs/south-carolina-code-completion-20260917.json`.
  The shared original 200 MiB ledger is at 135,697,707 bytes; cache-only repairs
  downloaded zero bytes. No scheduled refresh or current-law guarantee is implied.
- [ ] Continue the remaining state/local source queue; don't relabel another
  release, partial tranche or source restriction as nationwide completion.
  Minnesota now retains 901/1,133 chapter exports across 79 closed subject parts
  out of 105. All 859 added projections reconstruct full source text; seven real
  MCP calls verify reading, receipts, section-link navigation and date exclusions.
  All original 50 versions / 3,358 records and references remain unchanged.
  See `docs/minnesota-statutes-continuation-20260917.json`. The next declared body
  exceeds the 81,685 bytes remaining in the original 200 MiB ledger; do not reset
  or increase the allowance without authorization.
- [ ] Finish Idaho's remaining linked chapter PDFs with the maintained
  `idaho-statutes` command. All 74 title inventories are expanded: 1,471 PDF links,
  279 nonexport notices and one chapter lacking a listed PDF. The verified first
  batch adds 49 PDFs / 212 pages, bringing retention to 128 PDFs; all original 79
  versions / 610 records remain unchanged. Five real MCP calls passed. There are
  1,342 pending exports; T5CH10 is a rejected DRAFT placeholder, and T15CH15 has no
  listed PDF. The shared 200 MiB ledger has 15,516,405 bytes charged and
  193,150,219 usable bytes left after the original 1 MiB reserve. Do not treat
  notices, missing exports or projected pages as new laws. See
  `docs/idaho-statutes-continuation-20260917.json`.

## Completed release milestone

September 17 goal: finish the usable local legal-source product, not claim all
U.S. law has been collected. The active checklist is below; dated release and
source evidence stay in the linked documents.

- [x] Inspect the current code, release, live refresh and MCP workflows.
- [x] Close operational/retrieval defects: audit exit status, reindex writer
  serialization, paginated retained-version history and repeated bulk metadata.
- [x] Verify current federal/local retrieval and geographic discovery through
  actual MCP calls; retain concrete evidence and unresolved source limits.
- [x] Run focused regressions and one integrated offline gate; build and exercise
  the new wheel outside the checkout without downloading the corpus again.
- [x] Publish the current software release, verify its artifacts and CI, and
  leave the repository clean with accurate installation and operational docs.

Published [v0.2.0](https://github.com/Matt-Vijay/psephosamerica/releases/tag/v0.2.0)
from `7ac027b`; [release CI passed](https://github.com/Matt-Vijay/psephosamerica/actions/runs/35195141531).
The public wheel returned HTTP 200 and its GitHub SHA-256 matched the tested local
artifact. The upgraded macOS timer exited 0 with unchanged spending when no source
was due. This release milestone is finished. The active source-completion goal
above remains open.

Completion requires the checks above, not new architecture or a collection
fleet. Expand unattended sources only after their exact inventory, original
budgets, date semantics and failure behavior have been reviewed. Existing manual
and blocked sources must remain visibly manual/blocked otherwise.

## Preserved boundaries

The current release account is [docs/status.md](docs/status.md); exact source
coverage and limitations live in [docs/coverage-ledger.json](docs/coverage-ledger.json).
Preserve the existing corpus, immutable bytes, clocks and dated research receipts.

September 16 source work: maintained GA/VA/OR/WA/NE acquisition entrypoints are
integrated. Nebraska's 66-document completion is imported and independently
verified; all prior 50 chapter versions remain unchanged. Original operator
budgets are required for further acquisition into existing stores. Portable
read snapshots do not grant fresh campaign allowances.

Portland Charter's complete 15-chapter publisher family is verified and
canonically published. This source/release continuation is complete. A local
full-data backup and installed-package checks preserve a recoverable checkpoint.
Source receipts remain under `data/nebraska-publication-20260916` and
`data/collectors/portland-charter`. Massachusetts/Nevada remain blocked by their
runtime permission checks. Do not restart the historical fleet, reset source
allowances or retry denied access to inflate coverage.

[Refresh operations](docs/refresh.md) cover the first four reviewed source
commands. The macOS timer now has a verified successful run and is enabled.
Existing access denials and original campaign budgets remain in force; this
does not reactivate the historical collection fleet.
