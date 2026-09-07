# Florida: publisher-listed 2026 statutes

This source driver extends `florida-statutes-2026` from the Florida Senate's
[statutes index](https://www.flsenate.gov/Laws/Statutes). The retained native
inventory lists **49 titles and 638 chapters**. Those are actual linked members,
not expanded numeric ranges or a count of acquired laws. Each title's expanded
index supplies its chapter membership; every index and chapter must explicitly
identify the **2026 Florida Statutes** edition.

The scope is that publisher-listed statutory edition, not all Florida law. It
excludes the separate constitution, administrative rules, session laws, local
ordinances and case law. Auxiliary PDF indexes/tracing tables and external media
are not acquired by this driver. Inventory membership alone does not establish
complete acquisition: pending/failed chapters remain visible until accepted.

## Operate and resume

Use one acquisition writer at a time. These commands acquire sources; reading an
already retained store remains offline:

```sh
.venv/bin/psephos --data data sync florida --limit 2
.venv/bin/psephos --data data sync florida
.venv/bin/psephos --data data status --collection florida-statutes-2026
.venv/bin/psephos --data data status --collection florida-statutes-2026 --view inventory --status pending
.venv/bin/psephos --data data status --collection florida-statutes-2026 --view inventory --status failed
```

Choose the bounded or full sync command; do not run them concurrently. `--limit`
bounds attempted new chapter imports **after the whole title/chapter inventory**.
Exact retained projections can be reused without consuming that budget, so it is
not an HTTP-request limit. A repeat run reuses cached sources and accepted matching
projections; `--refresh` conditionally revalidates sources. No `--as-of` acquisition
is supported for this pinned edition.

Requests are paced at least 10.1 seconds apart on the source host, with robots
policy still enforced. The run is capped at 256 MiB, each chapter at 8 MiB, and
each index at 2 MiB. Edition, chapter identity and catchline-index/body membership
checks reject inconsistent chapters before ingestion. Acquisition errors stop the
run; parser rejections are recorded and other chapters can continue.

## Read native sections, not guessed links

```text
legal_coverage(collection="florida-statutes-2026", view="documents", limit=2)
legal_search(query="landlord tenant", collection="florida-statutes-2026", limit=3)
legal_read(key_or_id="Fla. Stat. § 83.43 (2026)", length=1200)
legal_read(key_or_id="fl:stat/83.43", length=1200)
```

Printed citations with or without the edition are supported; a subsection suffix
is not a separately acquired section identity. Use `legal_find` and read around
the match within the whole section. Duplicate publisher section numbers remain
separate `/occurrence/N` keys and require an explicit choice, not an arbitrary
winner. Use the stable key with `legal_versions`, the returned immutable provision
ID for version-specific reading and `legal_references`, and `source_receipt` for evidence.

Many sections legitimately share their chapter's source URL, such as
[Chapter 83 /All](https://www.flsenate.gov/Laws/Statutes/2026/Chapter83/All).
Only a native section anchor actually present in the retained HTML is appended;
no section fragment is invented. A shared URL or unmatched footnote link does not
identify one acquired section.

New projections retain the **whole publisher Section node**, including numbering,
history, footnotes and future/conditional replacement text—not just `SectionBody`.
They do not apply amendments or choose which alternative is legally effective.
Chapter hierarchy/index material is a separate `scope_notes` unit at
`fl:chapter/83/_context`, not another statutory section. Source markup and retained
bytes remain the fidelity authority; media descriptors are not transcriptions.

The reading proof follows three concrete paths:

- Tenancy: §83.51 obligations and qualifications, §83.43 scoped definitions, its
  printed reference to §250.01, and §83.505 email-notice text.
- Environmental permitting: §403.087 retains “unless exempted by department rule.”
  This import does not supply or decide those administrative-rule exemptions.
- Business obligations: §560.602 retains its March 1, 2027 effective-date note and
  printed reference to §560.103. A footnote marker can adjoin the section number in
  plain text; the native section identity and original markup remain separate.

These are navigation/fidelity checks, not legal conclusions. Printed references
are followed by exact citation; ordinary prose is not turned into invented links.

## Clocks, frozen history and verification

The edition year is known; exact publication, incorporation/snapshot and legal
effective dates remain unknown. Observation records when source bytes were
actually acquired, not when the law took effect. Date-based `as_of` queries exclude
unknown snapshots; `observation_cutoff` is an independent availability filter.
Even an exact immutable ID does not bypass either cutoff.
Pages are acquired sequentially and older matching pages are reused: this is not
a transactionally frozen publisher snapshot or a promise of live currency.

The **52 previously accepted statutory chapter versions** remain frozen with
their original `fl-html-1/text-2` projections and IDs. Matching source hashes reuse
them; this expansion does not silently reproject that baseline. New projections
use `florida-edition-1/text-3` and are distinct from historical replay evidence.

The [Florida edition verification receipt](florida-edition-verification.json)
records the completed import:

| Measured scope | Result |
| --- | ---: |
| Complete publisher-listed titles / chapters | 49 / 638 |
| Native section nodes / matching catchline entries | 24,993 / 24,993 |
| Separate chapter context units | 638 |
| New chapters / unchanged original versions | 586 / 52 |
| Missing chapters / remaining parser refusals | 0 / 0 |
| Distinct index/body objects rehashed | 688 / 150,262,942 bytes |
| New retained objects, including title preflight | 628 / 138,404,518 bytes |

All chapter index/body/store memberships agree. Five representative chapters
(14, 27, 83, 403 and 560) reproduce 582 units exactly, including identity, text,
markup, metadata and first-write reference evidence. The original 1,831 provision
IDs and original version rows are unchanged; the earlier collection review is
preserved intact as `historical_discovery_review`.

The cache-only rerun reused all 638 versions with zero new acquisitions, versions
or downloaded bytes and a transport that fails every network attempt. Seventeen
real stdio MCP calls exercised the reading paths, source receipt and temporal
exclusions in 2.228076 seconds, returning 49,366 structured-JSON bytes. This is one
observed run, not a latency distribution or certification of legal completeness.

Two read timeouts are retained as acquisition evidence: an optional Chapter 83
index probe was not needed/retried; Chapter 215's whole-page request succeeded on
the single resumed import. No chapter body remains missing. The receipt separates
successful decoded source bytes from requests and records the acquisition clocks;
run wall-clock intervals include interruptions, not just active downloading.

Local acquisition evidence is written under `<data-root>/florida-edition/` as
`inventory-plan.json`, unique timestamped `run-*.json` and `latest-run.json`.
Raw publisher bytes stay in the ignored content-addressed store, not in Git.

To reproduce this checkpoint against its retained local store, after the writer exits:

```sh
.venv/bin/python scripts/verify_florida.py --data data --check-resume --out /path/to/new-receipt.json
.venv/bin/pytest -q tests/test_florida.py tests/test_florida_reading.py tests/test_acquire.py tests/test_store_retrieval.py tests/test_sync_audit_summary.py
```

The verifier requires the retained `inventory-plan.json` and pre-expansion
`before.json`; it does not recreate that historical baseline for a fresh store.
Its resume transport refuses network requests. Later refreshes can legitimately
require a new reviewed checkpoint; do not rewrite the old receipt to fit them.
Final focused gates passed: 34 tests from the command above, Ruff check/format on
the runtime and changed verification/tests, and strict mypy on `src/psephos` plus
`scripts/verify_florida.py`. No legacy/full-catalog test or replay campaign was run.
