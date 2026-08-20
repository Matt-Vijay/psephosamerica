# Psephos America · ψ

The bounded executable-transfer pilot is documented in
[`src/transfer_eval/README.md`](src/transfer_eval/README.md): one OpenStates normalization task,
one public case, three hidden real-data cases, a deterministic continuous score, and a cleanroom
Deno runner backed by the American Legislative Time Machine V1 oracle.

**A connected, cited, queryable knowledge graph of US governance — federal, all 50 states, and local — with calibrated vote prediction and plain-English reasoning over the whole thing.**

The name is from the Greek *psêphos* (ψῆφος), the pebble Athenians dropped to cast a vote — the root of *psephology*, the science of elections. ψ is also the wavefunction in physics: a probability amplitude. One glyph for *the vote* and *the probability* — which is exactly what this is: **a calibrated probability on every official's vote, everywhere, grounded in public record.**

---

## What it is

Psephos America ingests public-record governance data from many sources into **one entity-resolved graph** — officials ↔ bills/ordinances ↔ votes ↔ donors ↔ lobbying ↔ awards ↔ floor speeches ↔ jurisdictions — where **every node and edge carries its source URL, a content hash, and a `known_at` timestamp** (strict, leakage-safe provenance). On top of that substrate sit three things:

1. **A cited query API + lenses** — filters, joins, multi-hop paths, redundancy/waste detection, accountability rankings.
2. **GraphRAG reasoning** — ask a question in plain English, get an answer grounded in a cited subgraph.
3. **Calibrated vote prediction** — a defection-ranking model that generalizes across chambers and jurisdictions.

## What's in the graph

| Layer | Coverage |
|---|---|
| **State legislative votes** | **39,261,234** per-legislator vote edges · 50 states + DC · 12,467 legislators · 354,220 bills · 955,979 roll calls |
| **Federal floor speeches** | **52,004,494** member→bill/record edges (full 113th–119th Congress) |
| **Federal roll-call votes** | House **3,138,375** + Senate **494,465** (113th–119th) |
| **Local ordinances** | **2,207,679** enacted ordinances · 2,287 jurisdictions · 50 states (LOCUS-v1, CC-BY-NC) |
| **Federal bills** | **106,592** local BILLSTATUS metadata/dossier rows (113th–119th); **106,536** are in the historical graph/embedding contract (titles + CRS metadata/summaries, not full bill text) |
| **Lobbying** | **1,070,335** client→registrant / client→bill edges (Senate LDA, 1999–2020) |
| **Federal spending** | Federal awards covering **$5.1T** in obligations (USASpending, top-dollar slice) |
| **Campaign finance** | FEC donor→member contributions |
| **Courts** | Judges / opinions / citations (CourtListener, proof-scale) |

Together this closes the loop: **money in (donors, lobbying) → power (votes, speeches) → money out (federal awards).**

## Headline result: does the signal generalize?

A defection-ranking model trained **only on US House floor votes**, evaluated **zero-shot on all 50 states + DC**:

- **Sample-weighted AUC 0.700** (macro 0.705) over 3.16M eval pairs — **every jurisdiction clears chance.**
- Strong: HI 0.88, NJ 0.86, MD 0.85, IL 0.84. Weak (sparsity-driven): DC 0.55, AR 0.56, MI 0.58.
- Reproduces the earlier lossless federal→California and House→Senate transfers.

**Honest caveat:** this shows real cross-jurisdiction *skill*, not yet rigorous *losslessness* — state bills currently lack sector features, so the near-zero train-vs-transfer gap reduces to party-loyalty transfer. Adding state bill sectors is the open capstone. See [`benchmarks/STATE_TRANSFER_REPORT.md`](benchmarks/STATE_TRANSFER_REPORT.md).

---

## Quickstart

Requires Python 3.12+ and the project dependencies (`pip install -e .`).

**Canonical local analytical path:** inventory the existing immutable inputs,
build the Parquet/DuckDB substrate, and query it with real point-in-time cutoffs.
See [`docs/time-machine-v1.md`](docs/time-machine-v1.md). The graph, model, and
explorer commands below are legacy product surfaces layered beside that substrate.

**Ask a question (cited GraphRAG):**
```bash
python -m src.query ask "Which organizations received the most federal award money?"
python -m src.query ask "Which congressional bill dossiers have near-duplicate summaries?"
```
Every answer cites its sources (URL + content hash + `known_at`) and prints which model served it. With no LLM key it returns a deterministic, never-hallucinating grounded-evidence stub.

**Run the explorer (browsable web UI):**
```bash
python -m src.api.serve         # → http://127.0.0.1:8000
```
Landing page with search + ask boxes; per-official and per-jurisdiction pages, cited.

**Analytical lenses:**
```bash
python -m src.query lens copied-bills --scan 20000 --threshold 0.85 --cite
python -m src.query lens accountability --limit 25
python -m src.query said-vs-voted ce-<official-id>
```

Full command + HTTP API reference (query endpoints, redundancy/waste, LOCUS opacity/diffusion, the state-vote index): [`USAGE.md`](USAGE.md).

## LLM backend

GraphRAG uses an **OpenAI-compatible endpoint (OpenRouter by default)**, configured via env vars / a gitignored `.env`:

| Var | Purpose |
|---|---|
| `PSEPHOS_LLM_MODEL` | Primary model (e.g. `nvidia/nemotron-3-ultra-550b-a55b:free`) |
| `PSEPHOS_LLM_FALLBACKS` | Comma-separated fallbacks, tried on 429/5xx |
| `OPENROUTER_API_KEY` / `OPENROUTER_BASE_URL` | Endpoint + key |
| `ANTHROPIC_API_KEY` | Optional alternate backend |

Rate-limited models retry with backoff, fall through the chain, then degrade to the grounded stub — never crash, never hallucinate.

## Data sources & ingestion

Adapters live under `src/graph/ingest/` and write sidecars to `data/exports/<source>/` (large `.jsonl` gitignored; manifests + `ingest_meta.json` tracked as provenance):

- **govinfo BILLSTATUS** — federal bill metadata + CRS summaries (keyless bulk; GovInfo BILLS version text was not part of this legacy ingest)
- **House Clerk / Senate** — federal roll-call votes
- **Congressional Record** — floor speeches linked to bills
- **OpenStates** — 50-state legislators/bills/votes (bulk session CSVs; `openstates_bulk.py`)
- **LOCUS-v1** — local ordinance corpus (Hugging Face, CC-BY-NC)
- **USASpending** — federal awards (cursor-paginated)
- **Senate LDA** — lobbying (bulk archives via Wayback)
- **FEC** — campaign finance
- **CourtListener** — court opinions/judges

## Honest limitations

- **State transfer** shows skill, not proven losslessness (needs state bill sectors) — see above.
- **Courts** are proof-scale only (CourtListener keyless API throttles hard; full opinion text is gated).
- **Lobbying** covers 1999–2020; 2021–2026 has no public bulk source (API-only).
- **LOCUS** is CC-BY-NC (non-commercial) and is a snapshot of *enacted* law, not proposals.
- Party labels for state legislators cover current members (~59%); historical sessions lose coverage.

## Repository layout

- `src/graph/` — knowledge-graph schema, ingestion adapters, entity resolution, canonical entities
- `src/query/` — connected-graph store, cited query layer, GraphRAG, analytical lenses, state-vote index
- `src/api/` — HTTP/WSGI serving: query endpoints + explorer
- `src/prediction/` — numpy models: defection head, calibration, transfer harness (frozen pins)
- `src/runtime/` — operator entrypoints, ingestion runners, backfills
- `src/time_machine/` — canonical local inventory, normalization, DuckDB, and integrity path
- `src/ingest/ · src/parse/ · src/normalize/ · src/load/ · src/identity/ · src/provenance/` — the legacy federal pipeline (Postgres-backed) that seeded the graph
- `data/exports/` — source sidecars + tracked provenance manifests
- `benchmarks/` — pinned baselines + session reports (incl. `STATE_TRANSFER_REPORT.md`)
- `tests/` — behavioral proof for each layer

## Documentation

- Canonical local point-in-time substrate: [`docs/time-machine-v1.md`](docs/time-machine-v1.md)
- Usage & command reference: [`USAGE.md`](USAGE.md)
- North-star scope: [`OVERALL_GOAL.md`](OVERALL_GOAL.md)
- Methodology & source policy: [`METHODOLOGY.md`](METHODOLOGY.md)
- Docs index: [`docs/README.md`](docs/README.md) · architecture map: [`docs/architecture-domains.md`](docs/architecture-domains.md)
- Changelog: [`CHANGELOG.md`](CHANGELOG.md)

## Ethics

Public-record data on officials' public conduct only. No logins, no scraping behind auth, robots.txt respected. `known_at` on every feature; strict cutoff discipline so predictions never see the future; every served fact cited to its source; access gates documented rather than faked.
