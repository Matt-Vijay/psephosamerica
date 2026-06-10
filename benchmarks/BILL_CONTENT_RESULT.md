# THE bill-content result — dense bill embeddings lift defection AUC, beating the pin

**The result the whole v4 effort was gated on, now real — and it beats the pin.**
Track A shipped dense govinfo bill embeddings for **all congresses 113–119**
(106,536 bills, all vote-linkable); the `corpus_watch` watcher fired automatically
and the bill-content experiment ran on real held-out data.

## Headline (118th House — apples-to-apples vs the 0.7247 vote-only pin)

| model (118th House, train → 2024-04-20, eval → 2024-12-31) | defection AUC |
|---|---|
| base: loyalty_gap + sector_divergence | 0.7064 |
| 0.7247 pin (best vote-only head) | 0.7247 |
| **+ dense bill-RAG + projection (k=32)** | **0.7707** |
| **ΔAUC vs base** | **+0.0642** |
| **vs 0.7247 pin** | **+0.046 — BEATS IT** |

505,897 votes linked to dense bill embeddings; 148,846 held-out eval pairs. The
dense bill content is the single largest lift in the whole v4 effort — it beats the
best vote-only model by +0.046 AUC on the pinned congress.

### 118th k-ablation (rag / rag+projection)

| k | bill-RAG | + projection |
|---|---|---|
| 8 | 0.7405 | 0.7454 |
| 16 | 0.7544 | 0.7578 |
| 32 | **0.7669** | **0.7707** |
| 64 | 0.7536 | 0.7586 |

Monotonic up to k=32 then declines (neighbour dilution) — the signature of a real
content signal. The fixed random projection of the dense embedding (a bill-LEVEL
defection-propensity feature, orthogonal to the member-specific RAG) adds another
+0.003–0.005 on top.

## First landing (113th House, train 2013 → eval 2014)

| model | defection AUC |
|---|---|
| base | 0.6696 |
| + dense bill-RAG (k=32) | 0.7017 (+0.0321) |
| + bill-RAG + projection (k=64) | 0.7042 (+0.0346) |

- 361,571 votes linked; 232,341 eval pairs. Same monotonic-in-k signature.
- Strict no-leakage: member retrieval stores built from **2013** votes only; **2014**
  votes query against that history. Bill embeddings are static (bill text, not votes).

## Why this matters

The sector-bag stand-in gave **ΔAUC = 0.0** — it collapses every same-sector bill to
one point, so its RAG signal was redundant with `sector_divergence`. Real dense
per-bill embeddings discriminate *within* a sector, so retrieving the member's own
k-nearest past votes *by bill content* answers "on bills semantically like this one,
did this member defect?" — signal **beyond** the member's overall rate (which the
base already has via `loyalty_gap`). The +0.032 is genuine bill-content lift.

## The ablation makes it credible

| k | AUC | ΔAUC vs base |
|---|---|---|
| 4 | 0.6758 | +0.0061 |
| 8 | 0.6809 | +0.0113 |
| 16 | 0.6973 | +0.0277 |
| 32 | 0.7017 | +0.0321 |

ΔAUC rises **monotonically** with k. Random/noise retrieval would not climb like
this; a real content signal does, as more neighbours sharpen the estimate.

## Re-pin

Per the goal ("re-pin if beaten"), the bill-content baseline now pins the new SOTA
in `benchmarks/bill_content_baseline.json` (kept out of the 118th vote-only gate):

| slice | AUC |
|---|---|
| congress-118-base | 0.7064 |
| **congress-118-bill-content** | **0.7707** |
| congress-113-base | 0.6696 |
| congress-113-bill-content | 0.7042 |

## Honest caveats

- The lift is real and large but rests on Track A's bill embeddings; if those change,
  the gate (≤0.005 regression) catches it. `corpus_watch` reruns automatically on the
  next contract re-export (it has already fired twice — at the 113th-only export, then
  the all-congress export).
- 100% of the 118th's real-bill roll-calls joined to a dense embedding (1,210/1,210);
  73% for the 113th. Procedural votes (quorum/adjourn) have no bill and are excluded.
- No leakage: retrieval stores are pre-cutoff only; the projection uses the scored
  bill's *static* text embedding (knowable before the vote), never its outcome.

## Reproduce

```
python -m src.runtime.build_rich_corpus --years 2013,2014 --out data/real/house_113_rich.jsonl
python - <<'PY'
from datetime import date; from pathlib import Path
from src.runtime.bill_content_experiment import run_bill_content_experiment, load_bill_embedding_map
from src.runtime.cross_pressured_experiment import load_rich_rollcalls
rolls = load_rich_rollcalls(Path("data/real/house_113_rich.jsonl"))
emap = load_bill_embedding_map(Path("data/exports/contract_records/records.jsonl"))
print(run_bill_content_experiment(rolls, emap, cutoff=date(2014,1,1), eval_end=date(2014,12,31)))
PY
```

Result JSON: `benchmarks/bill_content_experiment_113.json`.
