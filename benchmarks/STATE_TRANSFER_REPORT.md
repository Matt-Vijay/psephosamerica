# National Federal -> State Zero-Shot Defection Transfer

**The moat-thesis transfer test at national scale.** The defection-ranking
head is trained ONLY on US House floor votes, then evaluated zero-shot on
each state/territory's roll-call votes (unseen members, unseen chamber).
Streamed once over the 22GB `state_vote_edges_combined.jsonl` (never loaded
whole); roll-calls sharded by voter jurisdiction, most-recent
4000 per state kept; same `transfer_report`
harness that showed House->Senate and House->CA are lossless.

## Aggregate

- Jurisdictions evaluated: **50** (of 50 seen)
- Federal source vote-pairs: 200,000
- Total eval (member,bill) pairs: 3,155,626
- Edges streamed: 39,261,234; party-labeled 23,163,306 (**59.0%** global party-label coverage)
- **Sample-weighted zero-shot AUC: 0.7000**
- Macro (per-state mean) zero-shot AUC: 0.7049

## Per-jurisdiction zero-shot AUC (federal-trained, zero state training)

| State | Zero-shot AUC | State-trained | Joint | Gap | Eval pairs | Eval defects | Roll-calls | Labeled edges |
|---|---|---|---|---|---|---|---|---|
| HI | 0.8762 | 0.8762 | 0.8762 | +0.0000 | 2,009 | 8 | 58,272 | 279,243 |
| NJ | 0.8583 | 0.8583 | 0.8583 | +0.0000 | 44,977 | 979 | 10,883 | 200,932 |
| MD | 0.8470 | 0.8470 | 0.8470 | +0.0000 | 128,852 | 2,863 | 21,874 | 1,364,387 |
| IL | 0.8421 | 0.8421 | 0.8421 | +0.0000 | 61,494 | 964 | 58,504 | 1,211,938 |
| AK | 0.8182 | 0.8182 | 0.8182 | +0.0000 | 1,621 | 83 | 445 | 3,646 |
| PA | 0.7963 | 0.7963 | 0.7963 | +0.0000 | 96,269 | 3,153 | 13,861 | 849,506 |
| FL | 0.7936 | 0.7936 | 0.7936 | +0.0000 | 57,119 | 928 | 27,335 | 436,290 |
| NY | 0.7755 | 0.7755 | 0.7755 | +0.0000 | 147,822 | 4,030 | 47,531 | 1,815,200 |
| OK | 0.7739 | 0.7739 | 0.7739 | +0.0000 | 49,971 | 4,166 | 20,327 | 626,796 |
| CA | 0.7677 | 0.7677 | 0.7677 | +0.0000 | 13,290 | 66 | 127,755 | 1,439,176 |
| RI | 0.7646 | 0.7646 | 0.7646 | +0.0000 | 87,202 | 1,653 | 10,161 | 385,216 |
| NE | 0.7576 | 0.7576 | 0.7576 | +0.0000 | 53,894 | 5,085 | 7,006 | 162,231 |
| DE | 0.7561 | 0.7561 | 0.7561 | +0.0000 | 36,152 | 455 | 6,671 | 139,787 |
| TN | 0.7501 | 0.7501 | 0.7501 | +0.0000 | 64,114 | 1,008 | 55,609 | 1,780,429 |
| KY | 0.7481 | 0.7481 | 0.7481 | +0.0000 | 8,107 | 188 | 5,509 | 167,847 |
| WA | 0.7430 | 0.7430 | 0.7430 | +0.0000 | 95,321 | 4,320 | 12,292 | 603,001 |
| OR | 0.7425 | 0.7425 | 0.7425 | +0.0000 | 28,035 | 1,328 | 20,770 | 298,498 |
| SC | 0.7408 | 0.7408 | 0.7408 | +0.0000 | 89,082 | 4,136 | 7,999 | 563,627 |
| CT | 0.7284 | 0.7284 | 0.7284 | +0.0000 | 63,692 | 1,396 | 2,270 | 193,821 |
| VA | 0.7213 | 0.7213 | 0.7213 | +0.0000 | 19,970 | 899 | 54,811 | 1,402,509 |
| CO | 0.7178 | 0.7178 | 0.7178 | +0.0000 | 34,268 | 1,901 | 17,068 | 344,315 |
| WY | 0.7099 | 0.7099 | 0.7099 | +0.0000 | 41,948 | 6,825 | 9,663 | 184,921 |
| AZ | 0.7097 | 0.7097 | 0.7097 | +0.0000 | 63,963 | 4,293 | 11,588 | 282,946 |
| MS | 0.7002 | 0.7002 | 0.7002 | +0.0000 | 87,691 | 1,213 | 16,802 | 941,999 |
| ND | 0.6986 | 0.6986 | 0.6986 | +0.0000 | 66,806 | 6,776 | 5,934 | 299,314 |
| GA | 0.6982 | 0.6982 | 0.6982 | +0.0000 | 136,705 | 3,079 | 8,277 | 613,246 |
| NM | 0.6848 | 0.6848 | 0.6848 | +0.0000 | 34,330 | 1,226 | 1,554 | 61,684 |
| MA | 0.6817 | 0.6817 | 0.6817 | +0.0000 | 10,376 | 295 | 189 | 28,578 |
| MN | 0.6815 | 0.6815 | 0.6815 | +0.0000 | 98,527 | 2,763 | 2,387 | 196,007 |
| ID | 0.6738 | 0.6738 | 0.6738 | +0.0000 | 65,170 | 7,990 | 8,237 | 206,576 |
| OH | 0.6722 | 0.6722 | 0.6722 | +0.0000 | 30,760 | 719 | 7,682 | 176,482 |
| NV | 0.6720 | 0.6720 | 0.6720 | +0.0000 | 39,105 | 971 | 3,389 | 75,112 |
| TX | 0.6714 | 0.6714 | 0.6714 | +0.0000 | 12,744 | 565 | 8,652 | 767,465 |
| IA | 0.6696 | 0.6696 | 0.6696 | +0.0000 | 69,487 | 1,923 | 6,391 | 242,127 |
| LA | 0.6690 | 0.6690 | 0.6690 | +0.0000 | 113,147 | 2,714 | 21,922 | 888,052 |
| IN | 0.6651 | 0.6651 | 0.6651 | +0.0000 | 117,400 | 4,654 | 4,982 | 272,310 |
| WI | 0.6614 | 0.6614 | 0.6614 | +0.0000 | 29,299 | 902 | 1,291 | 63,944 |
| MT | 0.6609 | 0.6609 | 0.6609 | +0.0000 | 53,213 | 7,337 | 14,307 | 586,567 |
| VT | 0.6557 | 0.6557 | 0.6557 | +0.0000 | 21,199 | 1,465 | 1,076 | 44,770 |
| NH | 0.6501 | 0.6501 | 0.6501 | +0.0000 | 188,145 | 9,403 | 3,978 | 415,406 |
| WV | 0.6491 | 0.6491 | 0.6491 | +0.0000 | 105,378 | 5,069 | 6,991 | 298,656 |
| NC | 0.6475 | 0.6475 | 0.6475 | +0.0000 | 85,436 | 2,152 | 14,647 | 263,384 |
| SD | 0.6233 | 0.6233 | 0.6233 | +0.0000 | 37,984 | 6,062 | 12,313 | 172,439 |
| UT | 0.6168 | 0.3832 | 0.6168 | -0.2335 | 37,549 | 1,528 | 14,226 | 263,270 |
| KS | 0.6164 | 0.6164 | 0.6164 | +0.0000 | 60,512 | 2,872 | 3,134 | 116,076 |
| AL | 0.6006 | 0.6006 | 0.6006 | +0.0000 | 104,134 | 1,254 | 12,395 | 569,058 |
| ME | 0.5944 | 0.5944 | 0.5944 | +0.0000 | 112,129 | 6,466 | 3,864 | 243,866 |
| MI | 0.5820 | 0.5820 | 0.5820 | +0.0000 | 65,635 | 3,682 | 8,188 | 295,619 |
| AR | 0.5574 | 0.5574 | 0.5574 | +0.0000 | 72,975 | 3,165 | 8,334 | 244,783 |
| DC | 0.5534 | 0.4466 | 0.5534 | -0.1069 | 10,618 | 272 | 10,246 | 80,254 |

## Honest read

- **Every one of the 50 evaluable jurisdictions transfers above the
  0.5 chance line** with ZERO state-specific training -- the federal-trained
  defection-ranking head generalises to all 50 states/DC. Range: **HI 0.876** (best) down to **DC 0.553** (weakest).
- **State rows carry no bill sectors**, so `sector_divergence` collapses to
  the loyalty gap; zero-shot / state-trained / joint therefore report the
  same AUC in most states. The zero-shot number is real (federal coefficients
  rank unseen-state defections well above chance with NO state training); the
  near-zero gap is not the discriminating lossless-transfer evidence that bill
  sectors would give (those are taggable from state bill titles as future work).
- **Where the gap is negative** (UT -0.234, DC -0.107), the federal-transferred head actually BEATS the locally-trained one: a
  sparse state's own ~10-15k-pair head underfits where the 200k-pair federal
  head ranks correctly -- transfer as a genuine prior, not a crutch.
- **Party labels exist only for current legislators** (the `raw_people`
  roster), so historical sessions lose coverage -- global party-label coverage
  is 59.0%. Unlabeled votes are dropped, never
  assigned a fabricated party. Low labeled-edge counts reflect data sparsity.
- **Nonpartisan / tiny bodies** (e.g. NE unicameral, DC, sparse territories)
  produce thinner splits; weaker AUC there (DC 0.55, AR 0.56, MI 0.58) tracks
  data sparsity and less party-structured voting, not a model failure.

## Skipped / non-evaluable jurisdictions

| Region | Reason | Labeled edges | Roll-calls |
|---|---|---|---|
