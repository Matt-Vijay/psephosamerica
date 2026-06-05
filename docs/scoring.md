# Scoring Formulas

OpenPact's v1 launch dimension is `conflict_of_interest_risk`. Scoring is
deterministic and decomposable: a member's score is the baseline minus the
sum of explicit `rule_fire` deltas. This page is the public, authoritative
source for the formulas; the values it cites live in `src/scoring/semantics.py`
and the rule YAMLs.

## Baseline

```
baseline_score(conflict_of_interest_risk) = 100.0
```

## Per-fire delta

A `rule_fire`'s contribution is determined by its `severity` (rule-authored
ahead of time and not editorially adjusted after firing):

| Severity | Score delta |
|---|---|
| `low` | −1.0 |
| `medium` | −2.0 |
| `high` | −4.0 |
| `critical` | −8.0 |

Source: `_SEVERITY_SCORE_DELTAS` in `src/scoring/semantics.py`. A blank or
unknown severity raises `ValueError` — no defaults.

## Aggregate member score

```
score(member, dimension) = BASELINE_SCORE + Σ score_delta(fire)
                         for every rule_fire in the snapshot whose
                         dimension == dimension and member == member
```

`recompute` materialises this as `score_snapshot` rows alongside the
generating `rule_fire` rows, so any score change is traceable to a specific
set of fires and their sources.

## Launch rule families and their severities

The four conflict-of-interest rule families ship at launch
(`src/rules/conflict_of_interest/`):

| Rule family | Severity | Per-fire delta |
|---|---|---|
| `committee_sector_trade` | medium | −2.0 |
| `late_or_amended_disclosure` | low | −1.0 |
| `repeated_committee_linked_trading` | high | −4.0 |
| `sector_holdings_overlap` | medium | −2.0 |

Rule conditions, parameters, source-type requirements, and explanation
templates are documented in each family's YAML. A worked end-to-end example
of all four families firing on a single seeded member, producing an evidence
card and a manifest, is in `src/demo/conflict_demo.py` and exercised by
`tests/rules/test_recompute_determinism.py`.

## Properties guaranteed by the recompute pipeline

- **Deterministic**: the same canonical inputs produce the same rule fires
  and the same score delta (modulo wall-clock `fired_at` audit timestamps
  and random `fire_id` provenance identifiers). Locked by
  `tests/rules/test_recompute_determinism.py`.
- **Decomposable**: a score change is always the sum of explicit, sourced
  rule fires; missing data does *not* impute a fire.
- **Frozen**: published `score_snapshot` rows are immutable; corrections
  flow through a fresh dated snapshot rather than mutation (see
  [ADR 0002](adr/0002-artifact-first-immutable-publishing.md) and
  [docs/corrections.md](corrections.md)).
