# Graph data layer (`src/graph/`)

The universal data layer for vote-prediction: a heterogeneous
Person × Bill × Org knowledge graph where every fact carries provenance
and is replayable to any past time `t`. It generalizes the federal,
congress-only ontology into one homogeneous model spanning
federal → state → county → city → special-district. See the repo
blueprint `OVERALL_GOAL.md`.

This layer is the data/graph/entity-resolution half of the system; it
hands off to the model/inference half across a single typed contract
(`EntityResolutionOutput`), so the two can be built in parallel.

## The spine: provenance and time

- [`provenance.py`](../src/graph/provenance.py) — `ProvenanceEnvelope`,
  the bitemporal record every node and edge carries:
  `(source_url, content_sha256, first_observed_at, valid_from, valid_to,
  known_at)`. Two time axes:
  - **valid time** `[valid_from, valid_to)` — when the fact was true in
    the world (`covers(date)`);
  - **known time** `known_at` — the earliest instant the fact was
    publicly knowable. `known_as_of(cutoff)` is the strict-cutoff
    leakage gate that ties into `src/prediction/backtest.py`. A fact can
    never be observed before it is knowable (`known_at <=
    first_observed_at`).
  - `content_address()` renders the sharded content-addressed lake path.

## Entity resolution (`src/graph/entity_resolution/`)

The central hard problem — collapsing many fragmented source records
into one canonical entity without losing the per-source audit trail.

| Module | Responsibility |
|---|---|
| `names.py` | Parse + normalize person names (comma/space order, suffixes, particles, nicknames); diminutive folding (Bob → robert); comparison features. |
| `records.py` | `SourceRecord` — one source's observation, with `ExternalId` strong keys and a provenance envelope. |
| `scoring.py` | `score_pair` — Fellegi-Sunter log-odds + 3-way decision (match / **possible** / no_match). Hard rules: shared external ID → match; same-namespace conflict → no_match. Weights/thresholds injectable (a learned Splink model swaps in). |
| `linker.py` | `resolve()` — block (name + external-id), union-find cluster the match edges, retain merge edges as the audit trail, route **possible** edges to an automated review queue (disputed pairs persist, never force-collapsed). |
| `canonical.py` | `build_canonical_entity(..., as_of=t)` — the typed canonical entity with a leakage-safe time-travel projection. |
| `assignment.py` | `assign_canonical_ids` — **stable** persistent IDs across runs (growth / merge / split handled deterministically); content-addressed cluster IDs alone are not stable. |
| `ids.py` | `stable_id` — deterministic content-addressed ID minting. |

## The graph

- [`edges.py`](../src/graph/edges.py) — `GraphEdge`: one atomic,
  provenance-carrying relation (vote, sponsorship, donation, …) between
  two canonical IDs. Open edge-type taxonomy; identity over
  `(type, src, dst, valid_from, external_key)`; `edges_known_as_of` is
  the relational leakage projection.
- [`bills.py`](../src/graph/bills.py) — `BillRef` + deterministic
  `canonical_bill_id` (bills resolve deterministically, not fuzzily).
- [`jurisdictions.py`](../src/graph/jurisdictions.py) — `Jurisdiction`,
  the hierarchical `jurisdiction_id` vocabulary
  (`us` → `us-ca` → `us-ca-city-los_angeles`).
- [`knowledge_graph.py`](../src/graph/knowledge_graph.py) —
  `KnowledgeGraph`: an in-memory read-model with adjacency queries and
  an `as_of(t)` leakage-safe snapshot — the queryable surface the live
  graph store backs.

## Ingestion (`src/graph/ingest/`)

Adapters mapping parsed public records into the graph, attaching
provenance with correct leakage semantics:

- `congress_members.py` — member lookup → `SourceRecord` (bioguide as
  the strong key).
- `votes.py` — roll-call → vote edge; `known_at` at the vote day.
- `donations.py` — contribution → donation edge; `known_at` at the
  **report-filing** date, not the contribution date (a donation is not
  public until disclosed — the disclosure-lag rule).

## Output contract and feed

- [`contracts.py`](../src/graph/contracts.py) —
  `EntityResolutionOutput`, the typed Track A → Track B seam:
  `canonical_id` / `canonical_person_id`, `known_at`, `source_anchors[]`
  (full provenance per source). The `dossier_json` /
  `dossier_embedding` / `structural_embedding` fields are present but
  `enrichment_status="pending"` until the LLM-dossier and GNN slices
  fill them.
- [`cdc.py`](../src/graph/cdc.py) — `diff_outputs`, the change-data-
  capture delta feed: one `EntityDelta` per created / removed / changed
  entity (keyed by stable canonical ID), so consumers update
  incrementally.

## Invariants

- Every node and edge carries a `ProvenanceEnvelope`; every emitted fact
  is replayable from raw artifacts to any time `t`.
- Nothing observed after `t` can appear in an `as_of(t)` snapshot — the
  leakage discipline in `src/prediction/backtest.py` holds end to end.
- The layer is pure and deterministic (foundation tier in
  `tests/architecture/test_layering.py`); every slice ships with tests
  first and full coverage.
