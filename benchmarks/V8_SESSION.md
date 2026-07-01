# Psephos America V8 — Expose the Graph (query API + GraphRAG + explorer)

**Track B v8 — the platform-first pivot.** The prediction frontier is paused
(all pins remain FROZEN and servable — no regression); this session turns the
unified, provenance-carrying data graph into a **queryable, downloadable, and
reasoned-upon substrate**. Everything below builds on Track A's existing export
artifacts, so it sharpens automatically when richer data (LOCUS municipal
ordinance text, FEC donor edges) lands in the contract.

Written for the LOCUS authors: LOCUS stopped at the ordinance *text* across
9,239 jurisdictions. We add the **relational layer on top** — officials ↔ votes
↔ bills/ordinances ↔ CRS topics ↔ jurisdictions — and the reasoning that layer
unlocks: cross-jurisdiction near-duplicate detection, federal redundancy/waste
queries, donor→vote paths, and a grounded GraphRAG question-answerer. Every
served answer is cited (source_url + content_sha256 + known_at).

## What shipped

| # | Deliverable | Module(s) | Status |
|---|-------------|-----------|--------|
| 1 | Query API over the connected graph | `src/query/graph_store.py`, `src/query/graph_query.py`, `src/api/graph_http.py` | shipped |
| 2 | GraphRAG reasoning (retrieval + grounding; **OpenRouter LLM primary**) | `src/query/graph_rag.py`, `src/query/query_embedder.py` | shipped |
| 3 | Public explorer (per-official / per-jurisdiction pages) | `src/api/graph_http.py` (render_*), wired in `src/api/wsgi_app.py` | shipped |
| 4 | Flagship: cross-jurisdiction near-duplicate ordinances | `src/query/redundancy.py`, `src/query/locus.py` | shipped + verified on real 2.2M-ordinance LOCUS corpus |
| 5 | Federal waste/redundancy queries | `src/query/redundancy.py` | shipped, verified on real corpus |
| 6 | This writeup | `benchmarks/V8_SESSION.md` | here |

### V8.1 — usability + analytical lenses (this session)

| # | Deliverable | Module(s) | Status |
|---|-------------|-----------|--------|
| U1 | **Ask-anything CLI** (`python -m src.query ask "…"`) | `src/query/__main__.py` | shipped, live-verified |
| U2 | **One-command explorer launcher** + landing page (search box, ask box, jurisdiction links) | `src/api/serve.py`, `render_home_page`/`render_search_results`/`render_ask_page` in `src/api/graph_http.py` | shipped |
| U3 | **Analytical lenses**: copied/model-legislation across bills; per-official + per-jurisdiction accountability/opacity; said-vs-voted stub | `src/query/lenses.py`, served in `src/api/graph_http.py` | shipped |
| U4 | **OpenRouter LLM backend** (OpenAI-compatible, env/.env-driven) with comprehensive fallback detection + `served_by` observability | `src/query/graph_rag.py` | shipped, live-verified |
| U5 | Usage doc (run commands + endpoints + examples) | `USAGE.md` | shipped |

Run commands and the full endpoint list live in **`USAGE.md`**. Highlights:

```sh
# Ask a cited question (offline grounded stub, or live OpenRouter when keyed)
python -m src.query ask "What did the budget reconciliation bill cover?"

# Launch the explorer (landing page with search + ask + jurisdiction links)
python -m src.api.serve            # http://127.0.0.1:8000/

# Analytical lenses from the CLI
python -m src.query lens copied-bills --threshold 0.85 --cite
python -m src.query lens accountability
python -m src.query said-vs-voted ce-<id>
```

New JSON/HTML routes (all cited):
`GET /` (landing), `/v1/explorer/search`, `/v1/explorer/ask`,
`/v1/graph/copied_bills`, `/v1/graph/accountability`,
`/v1/graph/jurisdiction_accountability`, `/v1/graph/said_vs_voted`.

**GraphRAG LLM path (U4).** The primary backend is **OpenRouter** (OpenAI-compatible
`chat/completions`, httpx, no SDK), configured entirely via env / the gitignored
`.env` (`OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL`, `PSEPHOS_LLM_MODEL`,
`PSEPHOS_LLM_FALLBACKS`). The client tries `[primary, *fallbacks]` in order and
detects, at each step: HTTP 429/5xx/timeout/connection error (retry with backoff
honoring `Retry-After`, then fall through); 200-responses carrying a body-embedded
`error`; empty / whitespace / reasoning-only content; and obvious refusals — then
falls through to the next model, and finally to the never-hallucinating grounded
**stub** if all fail. The serving model is recorded as `served_by` on the result
(surfaced in the CLI and the HTML ask page). Anthropic (`claude-opus-4-8`, adaptive
thinking) remains an optional alternate behind `ANTHROPIC_API_KEY`.

**Live verification (this session):** `python -m src.query ask "What do the
retrieved congressional resolutions concern, and which are duplicates?"` →
`Mode: live LLM` · `Served by: nvidia/nemotron-3-ultra-550b-a55b:free` — a real
grounded answer citing seven `govinfo.gov` BILLSTATUS records with `known_at`
dates. The fallback chain + all detection modes (429, body-error, empty,
reasoning-only, refusal, transport error, all-fail→stub) are unit-tested with an
injected mock poster in `tests/query/test_graph_rag.py`.

Commits (branch `consolidate-prediction-and-quality-pass`):
`cb0bb17` (#1 store+query) · `131d008` (#2 GraphRAG) · `7e308c6` (#1+#3 HTTP+explorer) ·
`0464d59` (#4+#5 redundancy apps) · `164e992` (#4 LOCUS flagship).

## The substrate (`graph_store.py`)

An in-memory, indexed view built from Track A's on-disk exports — **no DB, no
new dependency, numpy-only**. It joins:

- **Nodes**: 185,830 canonical entities from `data/exports/contract_records`
  (132,075 bills + 53,755 people), plus 44,564 municipal persons. Each keeps
  its source anchors.
- **Edges** (every one carries `source_url` + `content_sha256` + `known_at`):
  - 137,219 bill edges (CRS `policy_area` + `legislative_subject`)
  - 494,465 federal Senate `vote` edges
  - 24,191 municipal `vote` edges
  - (floor speeches, news mentions available in the exports for later wiring)

Build cost: ~13s for nodes+bill-edges (no embeddings); ~0.3s for the municipal
slice. Jurisdiction is inferred from external-id prefixes (`legistar:chicago:*`
→ `chicago`; `bioguide`/`congress` → `us-congress`; `ca_leginfo` →
`ca-legislature`).

## #1 — Query API (cited filters / joins / multi-hop paths)

`src/query/graph_query.py` primitives, each returning rows with provenance:
`find_entities` (type/jurisdiction/substring), `votes_of` / `voters_on`
(person↔bill, both directions), `bill_policy_areas` / `bills_in_policy_area`
(bill↔topic), and `path` (bounded multi-hop BFS, e.g. official → vote → bill →
policy_area, returning fully-cited hop chains; visited-set prevents loops).

Served (framework-neutral, wired into the existing WSGI app, no new dependency):

```
GET /v1/graph/entities?type&jurisdiction&q&limit
GET /v1/graph/votes?person_id | ?bill_id
GET /v1/graph/policy_area?bill_id | ?name
GET /v1/graph/path?from&to&max_hops&edge_types
GET /v1/graph/jurisdictions
```

## #2 — GraphRAG reasoning (`graph_rag.py`)

NL question → **numpy cosine retrieval** over the contract's 256-d
`dossier_embedding` vectors → one-hop **cited subgraph** expansion (votes /
policy areas) → **grounded answer**. Two cleanly-separated halves:

- **Retrieval + grounding (always on).** `query_embedder.py` reproduces Track
  A's feature-hashing embedder byte-for-byte (a parity test pins this against
  `src/graph/enrichment/local_embedder.py`), so the question vector is
  cosine-comparable to the stored vectors without importing the graph package.
- **Answer generation (key-gated, injected).** `GraphRagAnswerer.from_environment`
  selects `stub_generate` (deterministic, offline, never-hallucinating, fully
  tested) when no key is present, and `anthropic_generate` (httpx → Messages
  API, **`claude-opus-4-8` + adaptive thinking**, no SDK dependency added) when
  `ANTHROPIC_API_KEY` is set. Both obey the same contract: answer ONLY from the
  cited evidence; every result ships its de-duplicated citation list.

Served: `GET /v1/graph/ask?q=...`.

## #3 — Public explorer

`GET /v1/explorer/official/<person_id>` renders an official's roll-call votes
with an inline source link + `known_at` on every row;
`GET /v1/explorer/jurisdiction/<slug>` lists that jurisdiction's officials
(each linking to their page) and measures. Citations everywhere.

## #5 — Federal waste/redundancy (verified on the real 185,830-entity corpus)

**Top CRS policy areas by bill volume** (`redundant_bills_by_policy_area`,
min_bills=50) — topical over-legislation:

| bills | CRS policy area |
|------:|-----------------|
| 1658 | Health |
| 1383 | Taxation |
| 1369 | Armed Forces and National Security |
| 1056 | Government Operations and Politics |
|  888 | Public Lands and Natural Resources |
|  828 | International Affairs |
|  816 | Education |
|  763 | Crime and Law Enforcement |

**Reauthorization / re-introduction clusters** (`reauthorization_clusters`,
title-normalized, min_count=3) — the "passed/re-introduced N times" pattern:

| count | normalized title |
|------:|------------------|
| 485 | budget act of … |
| 106 | electing members to certain standing committees of the house |
|  37 | tribal gaming compact ratification |
|  28 | national defense authorization act for fiscal year … |
|  28 | intelligence authorization act for fiscal year … |
|  26 | veterans compensation cost of living adjustment act of … |

**Donor → vote paths** (`donor_to_vote_paths`): a donor-context term (employer /
occupation) is matched against official dossiers, returning each matched
official and their cited votes. The donor→official linkage currently rides
dossier-embedding similarity; it sharpens to a hard graph edge automatically
when Track A lands FEC donor→official edges in the contract.

Served: `GET /v1/graph/{redundant_policy_areas,reauthorizations,donor_paths}`.

## #4 — Flagship: cross-jurisdiction near-duplicate detection

Two implementations, both shipped and cited:

**(a) Bill-embedding near-dup** (`redundancy.near_duplicate_ordinances`): pairwise
numpy cosine over `dossier_embedding`, attaching each side's jurisdiction.
Verified on the real CA corpus — correctly surfaces companion/duplicate measures
(`SB2 ↔ AB2: Budget Act of 2024` sim 0.944; `AB96 ↔ SB96: Emergency Telephone
Users Surcharge Act` sim 0.942). Served: `GET /v1/graph/duplicate_ordinances`.

**(b) LOCUS text near-dup — the real cross-city flagship** (`src/query/locus.py`).
Track A landed **LOCUS v1**: **2,207,679 municipal/county ordinance provisions**
across **2,287+ jurisdictions** (in just the first 300k rows), each with raw
`text`, LOCUS `topic`/`function`, canonical `jurisdiction_id`
(`us-ca-city-oakland`), and four `dimension_scores` (opacity, paternalism,
enforcement_discretion, problem_salience). `near_duplicate_ordinances` finds
near-identical ordinance **text across different jurisdictions** with
**MinHash + LSH banding** over token shingles — **numpy-only, no embeddings**, so
it works on the `pending` LOCUS rows *today*. This is the model-legislation
diffusion LOCUS stopped short of.

Verified on a real 150k-row scan (`threshold=0.8`, cross-jurisdiction only):

| jaccard | jurisdiction A | jurisdiction B | shared text |
|--------:|----------------|----------------|-------------|
| 0.98 | us-mo-city-dixon | us-mo-city-marshall | Section 125.160 Duties of the City's Prosecuting Attorney |
| 0.97 | us-mo-city-chillicothe | us-mo-city-kimberling_city | Section 310.060 Emergency Vehicles — Use of Lights and Sirens |
| 0.97 | us-mo-city-marshall | us-mo-city-monett | Section 340.125 Riding Bicycles, Sleds, Roller Skates |
| 0.92 | us-al-city-hartselle | us-ms-city-pascagoula | Sec. 1-11 Code does not affect prior offenses or rights |

A whole cluster of Missouri cities share a model municipal code verbatim (matching
section numbers and text), and a cross-*state* AL↔MS hit appears — exactly the
"who copied whose law" signal that needs cross-jurisdiction comparison, not text
scoring alone.

**Opacity / paternalism map** (`opacity_paternalism_map`, real LOCUS, 300k scan,
min 50 ordinances) — which jurisdictions write the most opaque law:

| mean opacity | jurisdiction | n |
|-------------:|--------------|--:|
| 0.86 | us-ca-city-san_francisco | 97 |
| 0.82 | us-fl-county-martin_county | 71 |
| 0.79 | us-va-county-fairfax_county | 80 |
| 0.78 | us-fl-county-sarasota_county | 345 |

**Topic diffusion** (`topic_diffusion`, real LOCUS, 300k scan): Nuisance spans
2,276 jurisdictions, Buildings 2,272, Zoning 2,242 — the breadth of which local
policy areas are near-universal.

Served (CC-BY-NC-4.0 attribution on every payload):
`GET /v1/graph/locus/{opacity_map,duplicates,topic_diffusion}`.

**Honest limitation (auto-sharpens with the other track):** LOCUS rows are
`pending` — no `dossier_embedding` yet — so the LOCUS flagship uses **text
MinHash** rather than semantic embeddings (which actually makes it robust *today*
without enrichment). When Track A enriches LOCUS rows with embeddings, the
embedding near-dup path (a) extends to all 9,239 jurisdictions automatically. The
federal/CA `dossier_embedding` is a feature-hash of a (currently sparse) dossier
summary, so embedding-based similarity reflects title/metadata more than body
text until the semantic re-embed (`src/runtime/semantic_reembed.py`, Track A)
lands.

## Tests

All new code is tested and green (run under a py3.13 venv with the project's
core deps):

```
tests/query/test_graph_store.py        15
tests/query/test_graph_query.py        (joins, paths, citations)
tests/query/test_query_embedder.py     incl. byte-parity vs the contract embedder
tests/query/test_graph_rag.py          15  (retrieval, grounding, stub, key-gating)
tests/query/test_redundancy.py          7
tests/query/test_locus.py               7  (opacity map, MinHash near-dup, topic diffusion)
tests/api/test_graph_http.py           handlers (entities/votes/path/ask/explorer/redundancy/locus)
tests/api/test_wsgi_graph_routes.py    WSGI routing + existing prediction route unaffected
```

Full `tests/query` + `tests/api` run: **all green, no regression** to the frozen
prediction pins or existing read-service/explorer code.

ruff + ruff-format clean; mypy clean on every new module (the only mypy errors
in the touched import graph are pre-existing `pypdf` stubs in `src/parse/`,
outside this track's domain).

## What remains / blocked

- **LOCUS landed** (2.2M ordinances): the cross-city flagship now runs on real
  data via text MinHash (no embeddings needed). When Track A enriches LOCUS rows
  with `dossier_embedding`, the embedding near-dup path extends across all 9,239
  jurisdictions automatically — no code change.
- **Hard donor→official edges**: gated on FEC donor edges in the contract; the
  donor→vote path falls back to embedding similarity until then.
- **Live LLM GraphRAG answers**: gated on `ANTHROPIC_API_KEY`; the stub serves
  honest grounded answers in the meantime and the real path is wired + ready.
- **Richer bill-text embeddings** (`semantic_reembed`, Track A): will sharpen
  both GraphRAG retrieval and near-duplicate discrimination.
- The prediction pins/baselines remain FROZEN and servable; resume the frontier
  after the substrate is consumed by the LOCUS pitch.
