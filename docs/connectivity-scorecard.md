# Track A connectivity / quality scorecard

Auto-generated from `src.graph.scorecard.compute_scorecard` over the LOCUS
contract corpus + the jurisdiction-linkage P/R. Pinned by
`tests/graph/test_scorecard.py`. This is the v8 definition-of-done: the
graph clears its gates, not merely 'all data exists'.

- **total entities**: 2,207,679
- **unique canonical IDs**: 2,207,679
- **dup rate**: 0.000000
- **orphan-node rate**: 0.000000
- **cross-tier links (LOCUS->canonical jurisdiction)**: 59
- **cross-tier reach (ordinances <-> officials via the join)**: 162,780 ordinances <-> 22,583 officials
- **jurisdiction precision / recall**: 1.0000 / 1.0000
- **jurisdictions linked / minted / total**: 59 / 2,228 / 2,287

## By entity type

| entity_type | count |
|---|---|
| bill | 2,207,679 |

## By tier

| tier | count |
|---|---|
| city | 1,798,445 |
| county | 409,234 |

## Gates

| gate | value | threshold | passed |
|---|---|---|---|
| dup_rate | 0.000000 | <= 0.0 | PASS |
| orphan_rate | 0.000000 | <= 0.0 | PASS |
| jurisdiction_precision | 1.000000 | >= 1.0 | PASS |
| jurisdiction_recall | 1.000000 | >= 0.75 | PASS |

**All gates passed: True**
