# Active source completion

The broader project is not complete. Release readiness below is a finished
milestone, not the stopping condition for the legal-information-library goal.

Current work: New York's original selected candidate pass is reconciled, but no
statewide inventory is established. Continue through the evidence-backed queue in
`docs/coverage-ledger.json`. Washington and Minnesota have exhausted their original
download allowances; no additional allowance is assumed. Illinois remains held
after TLS failure and a diagnostic access denial.

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
  279 nonexport notices and one chapter lacking a listed PDF. Retention is now
  1,444 PDFs across 59 closed title inventories. All 1,365 added projections /
  11,647 pages / 23,662 links replay exactly; 15 real MCP calls passed. All original
  79 versions / 610 records remain unchanged. Remaining: 15 draft placeholders,
  six chapter-number mismatches, one 404, one transport failure, four pending PDFs
  and T15CH15 without a listed PDF. T72CH13 timed out then reset; the exact native
  T15CH15 HTML link also reset. Stop repeated network attempts for now; resume only
  after transport review, then reconcile actual HTML evidence for source gaps.
  The original 200 MiB ledger has 111,992,623 bytes charged (including a 65,536-byte
  uncertain interrupted transfer), and 96,674,001 usable bytes after its 1 MiB
  reserve. See `docs/idaho-statutes-inventory-20260917.json`. Neither pending
  transfers nor defective exports establish inventory closure or new population
  reach. Next useful work can proceed on other reviewed source families meanwhile.
- [x] Add 28 selected New York PDF volumes / 7,270 pages; exactly replay all new
  projections and preserve all 49 original versions / 44,664 records / 59 receipts.
  Verify explicit volume identities and fifteen real MCP calls. Correct four
  candidate title mappings and withdraw eight unsupported legacy alias assertions.
  Archive and replace this continuation's mislabeled PBG projection without changing
  its source text, original PDF or receipt. See
  `docs/new-york-laws-continuation-20260917.json`.
- [ ] Establish New York's remaining source-native inventory. The selected list
  now has 77 acquired volumes and five 404s; eight earlier candidates remain
  unverified, not aliases. Do not infer statewide closure or probe guessed IDs.
  The original 180 MiB ledger has 70,642,447 bytes charged and a 1 MiB reserve.
  Authenticated API and denied index remain unrequested.
- [ ] Resume Illinois only after publisher/runtime access review. The maintained
  adapter and four offline fixtures are ready; 12 original acts / 1,297 units
  replay exactly and all 13 original versions remain unchanged. Fresh robots GET
  failed TLS validation; diagnostic HEAD with system trust returned 403 and no
  body was requested. No new law was acquired, no TLS bypass was used and the
  original 38,099,825-byte counter is unchanged. See
  `docs/illinois-statutes-preflight-20260917.json`.

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
