# OpenPact — Usage (ask anything + run the explorer)

OpenPact is a public-interest governance-intelligence tool: a cited, queryable
knowledge graph of US public officials, bills/ordinances, roll-call votes, and
policy topics. **Every served fact carries its source URL, content hash, and
`known_at` timestamp.** This page is the canonical run reference.

A project venv with deps lives at `/tmp/opvenv`; substitute your own Python if
you have one. All commands run from the repo root.

---

## 1. Ask anything (CLI)

Run a cited GraphRAG answer from the terminal:

```sh
/tmp/opvenv/bin/python -m src.query ask "What did the budget reconciliation bill cover?"
```

- With **no key**, you get a deterministic, never-hallucinating **grounded
  stub**: the most relevant source-anchored evidence in the graph, with
  citations. Honest and offline.
- With a key set, you get a **live synthesised answer** over the same cited
  evidence. The CLI auto-loads the gitignored repo `.env`, so you don't need to
  export anything.

LLM backend (primary = **OpenRouter**, OpenAI-compatible):

| Env var | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | Secret bearer key (read from env / `.env`; never committed). |
| `OPENROUTER_BASE_URL` | Default `https://openrouter.ai/api/v1`. |
| `OPENPACT_LLM_MODEL` | Primary model, e.g. `openai/gpt-oss-120b:free`. |
| `OPENPACT_LLM_FALLBACKS` | Comma-separated fallback models, tried in order on 429/5xx. |
| `ANTHROPIC_API_KEY` | Optional alternate backend (`claude-opus-4-8`, adaptive thinking). |

Free models rate-limit intermittently: each model is retried with backoff
(honoring `Retry-After`), then the fallback chain is tried in order; if every
model fails the answer degrades to the grounded stub (never crashes, never
hallucinates). Add `--json` for machine-readable output, `--top-k N` to widen
retrieval.

### Analytical lenses from the CLI

```sh
# (a) copied / model-legislation detection across bills (text MinHash)
/tmp/opvenv/bin/python -m src.query lens copied-bills --scan 20000 --threshold 0.85 --cite

# (c) per-official accountability / opacity ranking (transparent composite)
/tmp/opvenv/bin/python -m src.query lens accountability --limit 25

# (c) per-jurisdiction accountability surface
/tmp/opvenv/bin/python -m src.query lens jurisdictions

# (d) said vs voted — an official's floor speeches lined up against their votes
/tmp/opvenv/bin/python -m src.query said-vs-voted ce-<id>
```

---

## 2. Run the explorer (one command)

```sh
/tmp/opvenv/bin/python -m src.api.serve            # http://127.0.0.1:8000
/tmp/opvenv/bin/python -m src.api.serve --port 9000 --host 0.0.0.0
```

Then open **`http://127.0.0.1:8000/`** — a landing page with:

- a **search box** (officials & bills, by name/external id),
- an **ask box** (GraphRAG, cited),
- links into every **jurisdiction** and the analytical lenses.

Key URLs:

| URL | What |
|---|---|
| `/` | Landing page (search + ask + jurisdiction links) |
| `/v1/explorer/search?q=Klobuchar&type=person` | HTML search results |
| `/v1/explorer/ask?q=<question>` | HTML cited answer |
| `/v1/explorer/official/<person_id>` | Per-official roll-call votes, each cited |
| `/v1/explorer/jurisdiction/<slug>` | Per-jurisdiction officials + measures |

The store builds lazily on the first graph/explorer hit (~13s), so startup is
instant and the first request takes a few seconds to index.

---

## 3. Query API + lens endpoints (JSON, cited)

```
GET /v1/graph/entities?type&jurisdiction&q&limit      # cited entity list
GET /v1/graph/votes?person_id | ?bill_id              # the vote join
GET /v1/graph/policy_area?bill_id | ?name             # bill<->topic join
GET /v1/graph/path?from&to&max_hops&edge_types        # cited multi-hop paths
GET /v1/graph/jurisdictions                           # jurisdiction index
GET /v1/graph/ask?q=...                               # GraphRAG grounded answer

# Analytical lenses (V8 #3)
GET /v1/graph/copied_bills?scan&threshold&cross_jurisdiction_only
GET /v1/graph/accountability?limit                    # per-official ranking (cited)
GET /v1/graph/jurisdiction_accountability?limit       # per-jurisdiction rollup
GET /v1/graph/said_vs_voted?person_id                 # speeches vs votes

# Redundancy / waste (V8 #4/#5)
GET /v1/graph/redundant_policy_areas?min_bills
GET /v1/graph/reauthorizations?min_count
GET /v1/graph/duplicate_ordinances?threshold
GET /v1/graph/donor_paths?term
GET /v1/graph/locus/{opacity_map,duplicates,topic_diffusion}
```

Every JSON payload that asserts a fact includes a `citation`
(`source_url` + `content_sha256` + `known_at`).

---

## 3a. State legislator votes at scale (39M edges, lazy & cited)

The 50-state + DC per-legislator vote corpus
(`data/exports/openstates/state_vote_edges_combined.jsonl`) is **39,261,234
edges / 22GB** — far too large to fold into the in-memory `GraphStore` that serves
the explorer, so it is deliberately **not** in `build_store`'s default
`edge_paths`. Instead a compact **byte-offset index** lets the query layer answer
state-legislator vote questions *lazily*: seek straight to one entity's edge lines
in the big file and parse only those.

```bash
# Build the index once (single streaming pass; ~957MB sidecar, 23x smaller than
# the corpus). Records each edge line's byte offset keyed by person id + bill id.
/tmp/opvenv/bin/python -m src.query.state_vote_index
# -> indexed 39,261,234 edges across 12,467 persons and 353,808 bills
```

```python
from src.query.state_vote_index import StateVoteIndex

idx = StateVoteIndex.load()          # loads only the offset table (~4s), not the 22GB
idx.vote_count_for_person("ce-oevmujkhtk3yagqm")   # 46,261  (legislator "Bates", CA)
votes = idx.votes_for_person("ce-oevmujkhtk3yagqm", limit=3)  # 0.6ms — seeks, never scans
votes[0].as_citation()
# {'bill_id': 'cb-wwgdypefilohb5bo', 'choice': 'yea',
#  'motion': 'Consent Calendar SB629',
#  'source_url': 'https://openstates.org/ca/?session=California_2021_2022_Regular_Session',
#  'content_sha256': 'bf1dfbfbf604...', 'known_at': '2021-05-13T00:00:00Z', ...}
```

Every returned vote keeps full provenance (`source_url` + `content_sha256` +
`known_at` + `valid_from`), so state-vote facts stay citable exactly like the
in-memory store's. The in-memory store still serves browse/ranking over the
federal + municipal + bounded slices; this index is the query-on-demand path for
"show me state legislator X's full voting record" without paying the 22GB RAM cost.

---

## 4. Example questions + answers

**Q:** `What did the budget reconciliation bill cover?`
**A (live, OpenRouter `openai/gpt-oss-120b:free`):** "The budget-reconciliation
measures were designed to implement the provisions of the congressional budget
resolutions… Title II … and Title V … for fiscal year 2018【1】【2】【3】" — cited to
four `govinfo.gov` BILLSTATUS records with their `known_at` dates.

**Q (no key, grounded stub):** `Who supports affordable housing?` → returns the
top source-anchored entities + one-hop facts from the graph, each with its
source URL and `known_at`, and an explicit note that no external knowledge was
used.

**Lens (copied bills):** scanning 20k federal bill texts at jaccard ≥ 0.85
surfaces near-identical bill text (recycled boilerplate / companion bills),
each side cited to its canonical `govinfo` BILLSTATUS record.

**Lens (accountability):** ranks officials by an explained composite of
`record_depth` (log-scaled vote count), `source_coverage` (fraction of votes
with a real source URL), and `voice` (floor speeches / news). Every row exposes
its component scores and raw observation counts — no black box.

---

## Notes

- **numpy/stdlib only** — no DB, no heavy frontend, no LLM SDK. The explorer is
  served HTML over stdlib `wsgiref`.
- Builds on Track A's on-disk exports; new data (federal awards, lobbying, CREC
  speeches) flows in automatically. The **said-vs-voted** lens and the
  accountability **voice** component light up the moment Track A wires
  `floor_speech` / `news_mention` edges into the store.
- Frozen prediction pins are unaffected; the prediction read API is mounted by
  the launcher but starts with an empty served snapshot.
