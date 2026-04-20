# Open Pact

Open Pact is an open-source, evidence-first public ledger of score-changing events for Members of Congress. It links official public records, applies deterministic rules, and publishes evidence cards and per-member profiles that show what changed, why it changed, and which records support the change.

## v1 Scope

Version 1 is intentionally narrow:

- Federal Congress only
- Current Members of Congress only at launch
- Current Congress legislative data
- Current-term financial disclosures and PTRs for current members
- FEC committee and contribution context for the most recent two election cycles
- One score family: `conflict_of_interest_risk`
- Public, read-only web product with ZIP entry, homepage/feed, member pages, evidence cards, methodology, and a deferred compare placeholder
- Batch recomputation with frozen published snapshots

Anything outside `ENGINEERING_SPEC_V1.md` is deferred.

## Current State

The backend oracle is real enough to run locally:

- Postgres is the canonical store
- the repo has a DB-backed runtime layer under `src/runtime/`
- canonical ingest, normalization, rule execution, scoring, snapshot planning, and publish plumbing all exist
- disclosure artifact discovery and download now use the official House and Senate portals
- local publish and local readback paths both exist

The remaining gap is depth, not shape:

- Congress live loading is still missing member-detail enrichment, committee memberships, primary sponsors, and vote orchestration
- disclosure parsing is still at the artifact/provenance boundary, not full PDF extraction over real stored artifacts
- the public frontend is not started yet

## Runtime Entry Surface

The operator entrypoint is:

```bash
python3 -m src.runtime.main <command>
```

Current commands:

- `bootstrap-db`
- `status`
- `load-congress`
- `load-disclosures`
- `parse-disclosures`
- `process-disclosures`
- `recompute`
- `publish`
- `load-congress-local`
- `process-disclosures-local`
- `run-oracle-local`
- `plan-history-backfill`
- `run-history-backfill-local`
- `aggregate-history`
- `verify-publish`
- `verify-publish-roundtrip`
- `verify-history-aggregate`

Examples:

```bash
python3 -m src.runtime.main status
python3 -m src.runtime.main load-congress --congress 119
python3 -m src.runtime.main load-disclosures --chamber both --year 2025
python3 -m src.runtime.main parse-disclosures --chamber both --local-root data/artifacts
python3 -m src.runtime.main process-disclosures --chamber both --local-root data/artifacts
python3 -m src.runtime.main recompute --snapshot-date 2026-04-14
python3 -m src.runtime.main publish --snapshot-date 2026-04-14 --zip-bundle data/zip_bundle.json
python3 -m src.runtime.main process-disclosures-local --bundle data/disclosures_bundle.json
python3 -m src.runtime.main plan-history-backfill --congress 119 --target-root out/history
python3 -m src.runtime.main run-history-backfill-local --congress-archive data/congress_119 --disclosures-bundle data/disclosures_bundle.json --congress 119 --target-root out/history --aggregate-root out/history-aggregate
python3 -m src.runtime.main aggregate-history --source-root out/history/2025-01-06 --source-root out/history/2025-01-13 --target-root out/history-aggregate
python3 -m src.runtime.main verify-publish --publish-root out/publish
python3 -m src.runtime.main verify-publish-roundtrip --publish-root out/publish
python3 -m src.runtime.main verify-history-aggregate --publish-root out/history-aggregate
```

Notes:

- `bootstrap-db` applies the canonical bootstrap SQL from `db/schema.sql`; `--dry-run` reports the plan instead of dumping SQL.
- `status` returns summary counts plus the latest ingestion run, latest parse run, latest artifact, and active data sources.
- `load-congress` treats `--house-vote-year` or `--senate-session` as an explicit vote request, even if `--include-votes` is omitted.
- `publish` requires `--zip-bundle`; there is no implicit default.
- `process-disclosures-local` only accepts `--bundle`; unused `--chamber` and `--limit` flags are gone.
- verification commands still emit structured JSON on stdout, print a one-line failure summary to stderr when checks fail, and exit nonzero on verification failure.
- `aggregate-history` now verifies the aggregate root it just wrote and returns the nested history-verify summary.
- `run-history-backfill-local` now verifies the aggregate root when `--aggregate-root` is provided; a failed aggregate verify keeps the overall run non-green even if snapshot replays succeeded.

## Repository Layout

- `ENGINEERING_SPEC_V1.md` - locked v1 contract and source of truth
- `README.md` - project overview and repo orientation
- `METHODOLOGY.md` - public methods, source policy, and limitations
- `data/` - taxonomy and crosswalk artifacts
- `db/` - canonical schema and migrations
- `src/core/` - settings and structured logging
- `src/db/` - DB connection, SQL building, FK resolution, lookup loading, write execution
- `src/ingest/` - typed source clients and source-to-canonical transforms
- `src/load/` - canonical load-plan builders
- `src/normalize/` - taxonomy, member, and issuer normalization
- `src/parse/disclosures/` - disclosure acquisition, index discovery, artifact download, normalization, and transform layers
- `src/provenance/` - ingestion, artifact, and parse-run lifecycle helpers
- `src/query/` - persisted row fetchers and read-model assembly helpers
- `src/rules/` - rule contracts, loaders, contexts, evaluator, and engine
- `src/scoring/` - score snapshot and delta builders
- `src/export/` - published artifact contracts, planning, writing, and local reads
- `src/pipeline/` - DB-backed orchestration
- `src/runtime/` - operator-facing runtime surface and entrypoints
- `tests/` - behavioral proof for each layer

## What v1 Means

v1 is a deterministic, inspectable, frozen launch. Every score change must be decomposable into explicit rule fires, every evidence object must separate fact from inference from normative judgment, and every published snapshot must remain immutable after release.

## Development Standard

This repo is intentionally backend-first. The goal is a codebase that is legible at every zoom level:

- the top-level directories should describe the system without explanation
- each package should have one obvious job
- each file should feel like one unit of responsibility
- each function should read like one mechanical step

Tests, naming, and runtime topology are treated as product quality, not cleanup work.
