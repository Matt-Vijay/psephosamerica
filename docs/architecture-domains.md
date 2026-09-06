# Code ownership and dependency boundaries

The repository contains a current executable-evaluation path and several older
research paths. They share source/identity foundations; they are not one product
pipeline. See the root README for what is measured versus still exploratory.

## Start at a boundary, then follow its implementation

| Path | Entry point | Responsibility |
|---|---|---|
| Regulatory patch evaluation | `src/regpatch/cli.py` | Acquisition → causal compiler → isolated execution → deterministic grading |
| Local legislative evidence | `src/time_machine/cli.py` | Inventory → immutable sources → Parquet/DuckDB → cutoff-safe queries |
| Format-transfer pilot | `src/transfer_eval/cli.py` | Fixed OpenStates task, bundles, executable scoring |
| Legacy operator workflows | `src/runtime/main.py` | Parse arguments, dispatch one command, serialize its result |
| Legacy read/serving layer | `src/api/`, `src/query/`, `src/export/` | Assemble and publish typed read models |

RegPatch's detailed map is in its [operator guide](../src/regpatch/README.md).
None of these paths should acquire new product responsibilities during cleanup.

## Legacy domain ownership

- `ingest`, `parse`, `normalize`, `identity`: source formats and canonical identities.
- `graph`, `ontology`: graph representation, source-backed edges and enrichment.
- `db`, `provenance`, `load`: persistence, source/run receipts and write planning.
- `pipeline`, `runtime`: orchestration of those domain operations.
- `prediction`: historical datasets, models and evaluation; not a dependency of
  the conflict-rule engine or the current RegPatch grader.
- `rules`, `scoring`, `evidence`: conflict rules, score assembly and source-anchor policy.
- `feed`, `homepage`, `zip`, `demo`: retained historical presentation and bundle paths.
- `core`: foundation utilities; no imports from another `src` package.

`tests/architecture/test_layering.py` enforces domain → foundation dependencies:
domain packages may not import `runtime`, `api` or `pipeline`; `prediction` and
`rules` remain mutually decoupled. The existing export/API contract exception is
explicit in that test, not permission to add more cycles.

## One command route, no forwarding magic

`runtime/cli/parser.py` assembles argument groups. `runtime/commands/registry.py`
maps each command to a handler in its owning family: core operations,
disclosures, FEC, history, bill semantics, prediction or operator reports.
`runtime/main.py` is the single execution/exit-code boundary.

The command package exports public command functions and dispatch only. It does
not export every imported dependency, expose private helpers, or mutate sibling
modules when an attribute is assigned. CLI exports are only `build_parser` and
`parse_args`. The supported command names and public functions remain intact.

Tests patch the name where it is used, for example
`src.runtime.commands.core.open_connection`. Imports of private helpers likewise
name the owning module. A shared test harness accepts its command family
explicitly; production has no special behavior for monkeypatching.

## Reuse behavior at its existing owner

- `core/files.py`: streaming file hashes and atomic byte/text replacement. Callers
  own serialization and directory creation; fsync-backed source stores retain
  their separate durability policies.
- `core/text_hash.py`: one feature-hashing space for indexed documents and queries;
  the old embedder import names remain aliases for compatibility.
- `ingest/congress/archive_manifest.py`: Congress manifest serialization and
  exact-file receipt validation.
- `db/repositories.py`: SQL execution and transaction boundaries, including
  checked `INSERT … RETURNING id` handling and relation-existence queries.
- `graph/provenance.py`: shared public-statement provenance construction.
- `query/member_profile.py`: member-row normalization shared by history queries.
- `db/load_report.py`: load-summary aggregation for FEC, recomputation and runners.
- `runtime/http_client.py`: injectable clients and bounded transient retry policy.
- `runtime/json_artifacts.py`: atomic JSON writes and optional output receipts.
- `prediction/dataset.py`: shared training/evaluation window validation.
- `runtime/flat_corpus.py`: one `(bill, date)` grouping for both linked and unlinked
  roll-call views.

Prefer a direct import over another forwarding function. Consolidate only when
semantics agree: clock meanings, identity namespaces, transaction ownership and
candidate/evaluator isolation must not be hidden behind a generic helper.
