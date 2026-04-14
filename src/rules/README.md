# Rule Scaffolding

This directory holds declarative rule definitions only. There is no executor here.

## Layout

```text
src/rules/
  README.md
  conflict_of_interest_risk/
    committee_sector_trade/
      v1.yaml
    repeated_committee_linked_trading/
      v1.yaml
    late_or_amended_disclosure/
      v1.yaml
    sector_holdings_overlap/
      v1.yaml
```

## Versioning

- Each rule family gets its own directory.
- Each version is stored as a separate immutable YAML file.
- Historical versions are never edited in place.
- New behavior should be introduced as a new `vN.yaml` alongside prior versions.

## v1 behavior notes

- Missing data means no fire.
- Rule definitions are deterministic and human-readable.
- Repeated-trade logic is threshold-based, not statistical.
- Amended disclosures are recorded as new immutable fires, not mutations of prior fires.

