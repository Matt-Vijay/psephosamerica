# The honest cross-pressure slice — definition, prevalence, and why it is honest

Track B v4, item (1). This fixes the metric the earlier sessions optimized.

## The problem with the old slice

Earlier sessions defined the cross-pressured slice **from the realized label**:

```python
is_cross_pressured = (is_yea != party_leans_yea)   # reads the vote being scored
```

A pair was "cross-pressured" iff the member *did, in fact,* vote against their
party's majority on that very roll-call. Any reported "accuracy on the
cross-pressured slice" was therefore partly tautological: a model that simply
predicts *the opposite of the party line* scores ~100% on that slice and is
useless in production. You cannot know `is_yea` the morning before the vote, so
a slice keyed on it cannot be served. The lift was an artifact of the metric.

## The honest slice (ex ante, features only)

A `(member, bill)` pair is **defection-prone** when, using **only information
available at the cutoff**, the member's own track record breaks with their
party on the bill's policy area. Concretely (`src/prediction/defection.py`):

1. **Party loyalty is directional.** For every *pre-cutoff* vote we record
   whether the member voted **with** their party's majority lean
   (`party_alignment`). A member's loyalty is the fraction of past votes cast
   with the party — overall, and within each policy **sector** the bill touches.
2. **Defection-proneness = `1 − loyalty`**, taken as the **max over the bill's
   sectors** (the member's least-loyal relevant sector), using the sector rate
   when they have `≥ 3` votes there, else their overall loyalty, else a default
   of 0.9 (an unknown member is assumed loyal, never flagged).
3. A pair is **in the slice** when that proneness is at least `τ` (default
   `0.25`).

Crucially this is **directional**, not a distance from the party mean: a
super-loyalist (loyalty 1.0) scores 0 and is *never* flagged, whereas a
symmetric `|member_rate − party_rate|` metric would wrongly flag party
hard-liners as "cross-pressured". Only members whose history actually points
*against* the party enter the slice.

### Why it is honest

- **No label leakage.** The slice reads the member's *past* votes and the
  *bill's* sectors. It never reads `is_yea` for the bill being scored.
  `test_sector_divergence_is_ex_ante_and_label_free` flips the realized vote of
  the scored bill and asserts slice membership is **unchanged** — the
  non-tautology proof.
- **Servable.** Every input is knowable at the cutoff, so the slice can be
  published the morning before a vote (this is what the defection-watch page
  serves).
- **Strict cutoff.** Loyalty profiles are built only from `vote_date ≤ cutoff`
  (`split_by_cutoff`); the eval window is `(cutoff, eval_end]`. No future vote
  enters a feature.

## Prevalence and enrichment (real corpus)

Measured on Track A's real House roll-call corpus
(`data/real/house_118_rich.jsonl`, 508,382 member-votes, 14 policy sectors),
trained through `2023-12-31`, evaluated on 2024 (`benchmarks/defection_experiment.json`):

| τ | slice prevalence | defection rate **in** slice | defection rate **overall** | enrichment |
|---|---|---|---|---|
| 0.15 | 7.3% | 15.1% | 6.3% | **2.39×** |
| 0.25 | 0.3% | 27.3% | 6.3% | **4.31×** |
| 0.35 | 0.2% | 26.4% | 6.3% | **4.16×** |

The honest slice concentrates real defections **4.3× above base rate** at
`τ=0.25` — using only ex-ante features. The slice is genuinely predictive of who
breaks ranks, and it earns that lift without ever reading the outcome.

## The honest product metric: ranking, not slice accuracy

Because "accuracy on the slice" was the gameable quantity, the product metric is
**ranking** (`src/prediction/defection_head.py`): score
`P(member defects from their party)` over all eval pairs and rank them. ROC
**AUC** measures only the *ordering* of scores, so it is immune to the
predict-opposite-of-party degeneracy (a constant predictor scores AUC 0.5, which
the old accuracy hid). The realized `defected` label is used **only to score**
the ranking, never to define the slice or any feature.

Pinned baseline (`benchmarks/defection_auc_baseline.json`, gated at ≤0.005
regression by `src/prediction/defection_gate.py`):

| slice | AUC | precision@50 | precision@100 | eval pairs | defections |
|---|---|---|---|---|---|
| congress-118 | **0.6775** | 0.340 | 0.310 | 211,718 | 13,419 (6.3%) |

AUC 0.68 on a non-tautological label, with precision@50 = 0.34 (5.4× base rate):
a real, servable signal for *who* will break with their party.
