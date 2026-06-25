# National Federal -> State Zero-Shot Defection Transfer

**The moat-thesis transfer test at national scale.** The defection-ranking
head is trained ONLY on US House floor votes, then evaluated zero-shot on
each state/territory's roll-call votes (unseen members, unseen chamber).
Streamed once over the 22GB `state_vote_edges_combined.jsonl` (never loaded
whole); roll-calls sharded by voter jurisdiction, most-recent
150 per state kept; same `transfer_report`
harness that showed House->Senate and House->CA are lossless.

## Aggregate

- Jurisdictions evaluated: **27** (of 50 seen)
- Federal source vote-pairs: 200,000
- Total eval (member,bill) pairs: 54,311
- Edges streamed: 39,261,234; party-labeled 23,163,306 (**59.0%** global party-label coverage)
- **Sample-weighted zero-shot AUC: 0.6938**
- Macro (per-state mean) zero-shot AUC: 0.6857

## Per-jurisdiction zero-shot AUC (federal-trained, zero state training)

| State | Zero-shot AUC | State-trained | Joint | Gap | Eval pairs | Eval defects | Roll-calls | Labeled edges |
|---|---|---|---|---|---|---|---|---|
| OR | 1.0000 | 1.0000 | 1.0000 | +0.0000 | 50 | 1 | 20,770 | 298,498 |
| WI | 0.8972 | 0.8972 | 0.8972 | +0.0000 | 218 | 14 | 1,291 | 63,944 |
| MN | 0.8041 | 0.8041 | 0.8041 | +0.0000 | 5,031 | 379 | 2,387 | 196,007 |
| KS | 0.7858 | 0.7858 | 0.7858 | +0.0000 | 322 | 4 | 3,134 | 116,076 |
| FL | 0.7749 | 0.7749 | 0.7749 | +0.0000 | 813 | 19 | 27,335 | 436,290 |
| NC | 0.7702 | 0.7702 | 0.7702 | +0.0000 | 4,901 | 70 | 14,647 | 263,384 |
| MA | 0.7482 | 0.7482 | 0.7482 | +0.0000 | 9,171 | 252 | 189 | 28,578 |
| MI | 0.7330 | 0.7330 | 0.7330 | +0.0000 | 4,075 | 213 | 8,188 | 295,619 |
| IA | 0.7286 | 0.7286 | 0.7286 | +0.0000 | 2,137 | 57 | 6,391 | 242,127 |
| SD | 0.7219 | 0.7219 | 0.7219 | +0.0000 | 524 | 109 | 12,313 | 172,439 |
| WY | 0.7131 | 0.7131 | 0.7131 | +0.0000 | 113 | 33 | 9,663 | 184,921 |
| AK | 0.6983 | 0.6983 | 0.6983 | +0.0000 | 470 | 22 | 445 | 3,646 |
| MS | 0.6886 | 0.6886 | 0.6886 | +0.0000 | 246 | 8 | 16,802 | 941,999 |
| TX | 0.6692 | 0.6692 | 0.6692 | +0.0000 | 4,012 | 218 | 8,652 | 767,465 |
| NH | 0.6601 | 0.6601 | 0.6601 | +0.0000 | 3,475 | 248 | 3,978 | 415,406 |
| NV | 0.6596 | 0.6596 | 0.6596 | +0.0000 | 273 | 15 | 3,389 | 75,112 |
| ID | 0.6527 | 0.6527 | 0.6527 | +0.0000 | 1,366 | 218 | 8,237 | 206,576 |
| IN | 0.6355 | 0.6355 | 0.6355 | +0.0000 | 3,904 | 170 | 4,982 | 272,310 |
| ND | 0.6336 | 0.6336 | 0.6336 | +0.0000 | 1,486 | 118 | 5,934 | 299,314 |
| ME | 0.6331 | 0.6331 | 0.6331 | +0.0000 | 286 | 37 | 3,864 | 243,866 |
| VT | 0.6087 | 0.6087 | 0.6087 | +0.0000 | 4,343 | 340 | 1,076 | 44,770 |
| WA | 0.5936 | 0.5936 | 0.5936 | +0.0000 | 2,302 | 125 | 12,292 | 603,001 |
| NE | 0.5875 | 0.5875 | 0.5875 | +0.0000 | 333 | 84 | 7,006 | 162,231 |
| NM | 0.5816 | 0.5816 | 0.5816 | +0.0000 | 3,559 | 108 | 1,554 | 61,684 |
| DC | 0.5609 | 0.4391 | 0.5609 | -0.1218 | 155 | 9 | 10,246 | 80,254 |
| SC | 0.5228 | 0.5228 | 0.5228 | +0.0000 | 203 | 37 | 7,999 | 563,627 |
| DE | 0.4519 | 0.4519 | 0.4519 | +0.0000 | 543 | 2 | 6,671 | 139,787 |

## Honest read

- **State rows carry no bill sectors**, so `sector_divergence` collapses to
  the loyalty gap; zero-shot / state-trained / joint can therefore report the
  same AUC. The zero-shot number is real (federal coefficients rank unseen-
  state defections well above 0.5 chance with NO state training); the near-
  zero gap is not the discriminating lossless-transfer evidence sectors give.
- **Party labels exist only for current legislators** (the `raw_people`
  roster), so historical sessions lose coverage -- global party-label coverage
  is 59.0%. Unlabeled votes are dropped, never
  assigned a fabricated party. Low labeled-edge counts reflect data sparsity.
- **Nonpartisan / tiny bodies** (e.g. NE unicameral, DC, sparse territories)
  produce thin or degenerate splits and are reported below, never invented.

## Skipped / non-evaluable jurisdictions

| Region | Reason | Labeled edges | Roll-calls |
|---|---|---|---|
| AL | degenerate_split | 569,058 | 12,395 |
| AR | degenerate_split | 244,783 | 8,334 |
| AZ | degenerate_split | 282,946 | 11,588 |
| CA | single_date | 1,439,176 | 127,755 |
| CO | degenerate_split | 344,315 | 17,068 |
| CT | single_date | 193,821 | 2,270 |
| GA | degenerate_split | 613,246 | 8,277 |
| HI | no_eval_defections | 279,243 | 58,272 |
| IL | single_date | 1,211,938 | 58,504 |
| KY | degenerate_split | 167,847 | 5,509 |
| LA | degenerate_split | 888,052 | 21,922 |
| MD | degenerate_split | 1,364,387 | 21,874 |
| MT | degenerate_split | 586,567 | 14,307 |
| NJ | single_date | 200,932 | 10,883 |
| NY | degenerate_split | 1,815,200 | 47,531 |
| OH | degenerate_split | 176,482 | 7,682 |
| OK | degenerate_split | 626,796 | 20,327 |
| PA | degenerate_split | 849,506 | 13,861 |
| RI | degenerate_split | 385,216 | 10,161 |
| TN | no_binary_votes | 1,780,429 | 55,609 |
| UT | degenerate_split | 263,270 | 14,226 |
| VA | degenerate_split | 1,402,509 | 54,811 |
| WV | degenerate_split | 298,656 | 6,991 |
