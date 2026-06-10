# Does the combined SOTA generalize? — honest cross-congress check

Ran the four-arm combined experiment (base / bill-RAG+proj / CRS-only / combined) on
two congresses. **The combined SOTA does NOT generalize cleanly; CRS policy-area is
the robust component, bill-RAG's marginal value is congress-dependent.**

| arm | 118th AUC | 113th AUC |
|---|---|---|
| base (loyalty + sector) | 0.7064 | 0.6696 |
| bill-RAG + projection | 0.7707 | 0.7179 |
| **CRS policy-area only** | 0.7764 | **0.7499** |
| combined (bill-RAG + CRS) | **0.7998** | 0.7433 |
| best arm | combined | **CRS-only** |

## Reading

- **CRS policy-area is the robust win**: best or near-best on both congresses
  (+0.070 over base on the 118th, +0.080 on the 113th). It is the generalizable
  signal.
- **bill-RAG is congress-dependent**: it *adds* on the 118th (combined 0.7998 >
  CRS-only 0.7764) but *hurts* on the 113th (combined 0.7433 < CRS-only 0.7499).
  bill-RAG's near-random-retrieval member-rate proxy is partly redundant with — and
  on the 113th noisier than — the CRS-policy-area defection rate.
- The **0.7998 combined is the 118th SOTA, not a universal one.** The honest,
  deployable recommendation is **CRS policy-area as the primary bill-content signal**
  (robust across congresses), with bill-RAG added only where it demonstrably helps.

## Why this matters

It corrects an over-claim: the +0.029 combined lift on the 118th is real but
congress-specific. The durable result of the whole effort is that **coarse,
structured CRS metadata (policy_area) is the reliable lever for defection
prediction** — consistent with the semantic-A/B and subjects-ablation findings that
coarse topical grouping beats fine-grained text representations at this data scale.

Artifacts: `combined_sota.json` (118th), `combined_sota_113.json` (113th).
