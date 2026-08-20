# Semantic A/B — the API-embeddings escalation decision (v5 #1)

**Decision: DO NOT escalate to API embeddings.** Richer BILLSTATUS-dossier
embeddings do not beat the title-only hash for the defection bill-RAG, and the
diagnosis shows why — the bottleneck is per-(member, topic) **data sparsity**, not
embedding quality.

## The A/B (118th pin, train → 2024-04-20, eval → 2024-12-31, 505,897 vote-linked)

| bill embedding | best AUC | best k | ΔAUC vs 0.7707 pin |
|---|---|---|---|
| **hash** (title-only, 256-d) | **0.7707** | 32 | — (the pin) |
| semantic (MiniLM-384, title + CRS policy/subjects/summary) | 0.7613 | 8 | **−0.0094** |
| concat (hash ⊕ semantic, 640-d) | 0.7680 | 8 | −0.0027 |

Semantic ≤ hash. The pin stays at **0.7707 (hash)**; no re-pin.

The semantic input here was the concatenated BILLSTATUS title, policy area,
legislative subjects, and CRS summary. No GovInfo BILLS version text was present
in this benchmark.

## Honest diagnosis (the counterintuitive part)

A separation probe (within- vs across-CRS-policy-area cosine on 2,000 118th bills):

| embedding | within-area cos | across-area cos | **separation** |
|---|---|---|---|
| hash | 0.630 | 0.622 | **0.008** |
| semantic | 0.342 | 0.220 | **0.122** |

Semantic is **15× more topically discriminative** — it is the *better* bill
representation. Yet it loses the defection task. Why:

- **Hash retrieval is near-random** (separation ≈ 0): a member's "k-nearest past
  votes by hash cosine" are effectively a random sample of their history, so the
  bill-RAG signal collapses to the member's **overall defection rate** — a robust,
  low-variance, well-estimated member-level prior. The historical "+0.064
  bill-content" label refers to the available BILLSTATUS dossier; that lift
  from hash bill-RAG was largely a **member-rate-smoothing effect, not genuine
  dossier-content discrimination.**
- **Semantic retrieval is topically precise**: its signal is the member's defection
  rate *on this specific topic*. But members have few votes per fine-grained topic,
  so those estimates are **sparse and high-variance** and generalise worse (hence
  semantic peaks at k=8 — only the very nearest few neighbours carry signal, the
  rest are noise).

**Not pooling/truncation, not leakage.** The probe rules out semantic over-clustering
(it separates topics *better*, not worse). Both arms run an identical pipeline
(pre-cutoff retrieval stores, static embeddings, same cutoff) — no differential
leakage; the hash win is the member-rate-proxy effect above, not a leak.

## The consistent through-line

This matches the CRS multi-task ablation exactly:

| topical granularity | signal | ΔAUC |
|---|---|---|
| CRS policy_area (coarse, ~31) | per-(member, area) defection rate | **+0.071** |
| CRS subjects (fine, 632) | per-(member, subject) defection rate | +0.053 |
| semantic embedding (continuous, fine) | per-(member, topic) bill-RAG | −0.009 vs hash |

**Coarse topical grouping is the sweet spot; finer granularity is too sparse per
member at this data scale.** The cheap, structured **CRS policy_area** signal
(+0.071) is a bigger and more robust lever than any text-embedding upgrade.

## Recommendation

1. **Do not escalate to API embeddings** — the evidence says embedding *quality* is
   not the bottleneck for defection; per-member topical data volume is.
2. Keep the hash bill-RAG (cheap) + add the **CRS policy-area** multi-task signal
   (the real win). Revisit fine-grained semantic retrieval only with far more
   per-member history (e.g. multi-congress pooling) where per-topic estimates
   stabilise.
