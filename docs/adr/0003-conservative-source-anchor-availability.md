# ADR 0003: Conservative source-anchor availability for prediction features

Status: accepted

## Context

Prediction feature snapshots draw signals from ontology edges (committee
assignments, contributions, transactions, statements, holdings, …) and from
member/bill/contribution rows. Whether a given signal counts as "available at
the feature cutoff" governs whether it can be used to predict labels — getting
this wrong silently leaks future information into features and invalidates
every reported metric.

Edges and rows carry availability dates under known keys (e.g.
`transaction_date`, `contribution_date`, `start_date`, `committee_start_date`,
`filing_date`, `filed_at`, `report_date`, `statement_date`, `effective_date`,
`as_of_date`, `date`). In real data, those fields are frequently missing,
mistyped, or unparseable.

## Decision

When availability cannot be **proven** to predate the feature cutoff, exclude
the item rather than admit it.

1. **Edges** (`_edge_available_at_or_before` in `src/prediction/backtest.py`)
   require `_edge_has_known_availability`: at least one of
   `_EDGE_AVAILABILITY_DATE_KEYS` must parse to a real `date`. Edges with no
   parseable availability date are dropped — not assumed current.
2. **Signal rows** (`_signal_row_available_at_or_before`) walk a row's date
   keys in priority order and decide using the **first parseable** value. A
   row whose date keys are absent or unparseable is dropped.
3. **Bill sponsors** with a sponsor date after the cutoff are filtered out
   (`_bill_signal_index`). A `None` / unparseable sponsor date is treated as
   "not available" rather than "available now".

The direction is asymmetric on purpose: undated provenance under-counts
features (a safe, recoverable error) rather than over-counts them (a leakage
that silently inflates accuracy).

## Consequences

- The cutoff-safety guarantee survives noisy upstream data. Any future change
  that flips one of these "exclude" branches to "include" must come with a
  reason and a regression test.
- The invariant is locked by both unit tests
  (`tests/prediction/test_backtest.py::*filters_unknown_availability*`,
  `*filters_undated_*`) and seeded fuzz tests
  (`tests/prediction/test_cutoff_safety_properties.py`), which sweep thousands
  of date/cutoff combinations and confirm available iff *every* parseable date
  is at or before the cutoff (and "no parseable date" ⇒ unavailable).
- The same conservative rule is jurisdiction-neutral: it is expressed entirely
  in dates and parseability, so it holds unchanged for non-Congress
  jurisdictions (see [ADR 0001](0001-cutoff-safe-no-leakage-prediction.md)).
