# THE cross-pressured experiment — before/after + diagnosis (real 118th House)

**Question:** does a per-member sector signal let the model predict cross-pressured
defections (votes where a member breaks from their party majority), lifting
accuracy from ~0% toward >50%?

**Setup (real, no-leakage):** 1,237 real House roll-calls (118th, 2023–2024);
bills tagged to the 15-sector taxonomy from their `vote-desc`; `sector_lean` =
the member's pre-cutoff yea-rate deviation on the bill's sector(s) vs their
overall rate; per-member model, strict cutoff 2024-03-31.

## Result: hypothesis REFUTED with this feature
| slice | BEFORE (party only) | AFTER (+ sector_lean) |
|---|---|---|
| overall | acc 0.932, Brier 0.062 | acc 0.932, Brier 0.062 |
| cross_pressured (n=11,459) | **acc 0.000, Brier 0.848** | **acc 0.001, Brier 0.842** |
| party:D | acc 0.952 | acc 0.952 |
| party:R | acc 0.913 | acc 0.913 |

The sector signal moved cross-pressured accuracy by **+0.001** — essentially nothing.

## Diagnosis (why it failed — the real, useful finding)
1. **Coverage:** only **10%** of cross-pressured eval votes are on sector-tagged
   bills. Keyword tagging of `vote-desc` is far too sparse; 90% of defections get
   no sector signal at all. → needs dense bill features (the LLM bill encoder /
   Track A's bill `dossier_embedding`), not keyword tags.
2. **The metric is partly tautological.** `cross_pressured := (is_yea !=
   party_lean)`. A model trained *only* on cross-pressured votes learns
   `party_alignment` coefficient **−2.8** (predict the opposite of party) and
   scores **100%** on the slice — but that rule is catastrophic on the 93%
   party-aligned majority, i.e. useless in production. You cannot identify a
   defection a priori from party; the slice can only be cracked by a signal that
   is *high exactly on the to-be-defected votes*.
3. **Weak signal.** Even where present, `sector_lean` barely separates defection
   direction (mean −0.044 for yea-defections vs +0.155 for nay-defections).
4. **Minority-slice tension.** Cross-pressured is ~7% of votes; a loss-minimizing
   model keeps party dominant and accepts being wrong on the minority. Gains here
   require per-(member,bill) defection signals, not a stronger party prior.

## Conclusion / next experiment
Cross-pressured accuracy is a **per-(member, bill) interaction** problem. The
honest path is the **bill encoder + RAG over the K-nearest past (member, bill)
pairs by bill embedding** — retrieving how the member voted on *similar* bills,
which carries defection direction. That needs dense bill embeddings (sparse in
the corpus today: 19 enriched bills). This experiment quantifies, on real data,
exactly why the party-only model fails and what is actually required — the
strongest possible motivation for the bill-encoder slice.
