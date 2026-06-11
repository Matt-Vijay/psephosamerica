# Architecture: the three working domains

OpenPact's `src/` tree is developed along three parallel domains. Each domain
has a clear owner-track so concurrent work does not collide; the layering rules
in `tests/architecture/test_layering.py` enforce the hard boundaries.

## Track A — data, graph, ingestion

Owns the path from public records to canonical entities:

- `src/ingest/` — fetchers for official sources (Congress.gov, FEC, GovInfo, …)
- `src/parse/` — format parsers (PDF disclosures, XML records, …)
- `src/normalize/` — canonicalization of raw rows
- `src/load/` — database writers
- `src/pipeline/` — multi-step ingestion orchestration
- `src/export/` — published artifact writers
- `src/provenance/` — source/run provenance tracking
- `src/identity/` — entity resolution
- `src/ontology/` — ontology edge derivation
- `src/graph/` — the canonical entity graph and the Track A↔B output contract

## Track B — prediction, API, benchmarks

Owns model training, inference, and the serving surface:

- `src/prediction/` — vote-prediction datasets, models, calibration, eval
- `src/api/` — the public API contract and serving layer
- `benchmarks/` — frozen benchmark definitions and no-regression gates
- Experiment runners living under `src/runtime/` whose names match
  `*_experiment.py`, `defection_*`, `continuous_learning_*`, `corpus_watch.py`,
  and similar research entry points are Track B's, even though they sit in the
  runtime package.

## Track C — shared infrastructure

Owns everything the other two tracks stand on:

- `src/runtime/` — operator commands, CLI, runners, checkpointing
  (`src/runtime/commands/` is the operator command package; `cli.py` builds the
  argument parser; `main.py` dispatches)
- `src/query/` — read-side queries over published artifacts
- `src/db/` — connection/schema management
- `src/core/` — pure foundation utilities (must not import any other `src.*`)
- `src/rules/` — conflict-of-interest rule engine
- `src/scoring/` — conflict scoring
- `src/evidence/` — source-anchor policy shared by rules and prediction
- `src/feed/`, `src/homepage/`, `src/demo/` — presentation surfaces
- `src/zip/` — bundle reading/writing
- `tests/support/`, `tests/conftest.py` — shared test fixtures

## Layering invariants

- Foundation/domain packages never import the orchestration layer
  (`runtime`, `api`, `pipeline`).
- `src/core` imports nothing outside itself.
- `src/prediction` and `src/rules` stay mutually decoupled; the shared
  source-anchor policy lives in `src/evidence/`.

## The runtime commands package

`src/runtime/commands.py` was a 23.9k-line monolith; it is now the
`src/runtime/commands/` package, split along command families (core ops,
disclosures, FEC, history, bill semantics, prediction eval/benchmark/readiness,
operator reports/packet). `src/runtime/commands/__init__.py` re-exports every
top-level symbol, so all historical import paths (`from src.runtime.commands
import X`) keep working, and `registry.py` holds `COMMAND_REGISTRY` plus
`dispatch_command` — the single dispatch point used by `src/runtime/main.py`.
`src/runtime/cli.py` got the same treatment (`src/runtime/cli/`, with
`parser.py` assembling the subcommands).

**Patching in tests.** The commands package installs a small
`_PatchForwardingModule` shim: assigning an attribute on
`src.runtime.commands` (which is what `mock.patch` / `monkeypatch` do) is
forwarded to every submodule that defines that name, so the historical patch
targets like `mock.patch("src.runtime.commands.build_runtime")` still reach
the real call sites. New tests should prefer patching the owning submodule
directly (e.g. `src.runtime.commands.history._verify_local_history_aggregate`);
once all tests do, the shim can be deleted.
