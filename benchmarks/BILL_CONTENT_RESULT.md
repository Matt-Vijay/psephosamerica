# THE bill-content result — dense bill embeddings lift defection AUC (v4 squeeze)

**The result the whole v4 effort was gated on, now real.** Track A shipped dense
govinfo bill embeddings for the 113th Congress (6,419 bills, 6,400 vote-linkable),
the watcher fired, and the bill-content experiment ran on real held-out data.

## Headline

| model (113th House, train 2013 → eval 2014) | defection AUC |
|---|---|
| base: loyalty_gap + sector_divergence | **0.6696** |
| + dense bill-RAG (k=32) | **0.7017** |
| **ΔAUC** | **+0.0321** |

- 361,571 votes linked to dense bill embeddings; 232,341 held-out eval pairs.
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

## Honest caveats

- The 0.7247 pin is the **118th** Congress; this experiment is the **113th** (the
  only congress with dense bills in the contract today), so `beats_pin=False` is not
  apples-to-apples. The fair comparison is base→best on the *same* 113th data:
  **+0.032**, now pinned as `congress-113-base` (0.6696) and
  `congress-113-bill-content` (0.7017) in `defection_auc_baseline.json`.
- 73% of real-bill roll-calls joined to a dense embedding (6,400 of the 113th's
  bills are embedded). As Track A embeds more congresses (118th included) the same
  harness reruns automatically via `corpus_watch` and we get the 118th number too.

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
