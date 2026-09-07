# Completed state-collection wave

The interrupted collectors resumed their existing stores and finished. One writer
published **45 reviewed batches: 44 new state batches and two Portland guide pages**.
Texas was already present, so 45 states now have some accepted source material.
This is not 45 complete, current state codes. All source bytes remain local and
ignored by Git; no public corpus redistribution was performed.

## Measured result

The [portable receipt](collection-wave.json) records per-collection counts and
inventory states. Its hashed local evidence contains the complete source manifest,
batch acceptance receipts and reconciliation. Measurements were taken September 7,
2026 UTC after publication stopped.

| Measure | Accepted canonical catalog |
| --- | ---: |
| Collections | 77 |
| Documents / acquired versions | 17,926 / 17,929 |
| Retrieval units in latest documents | 1,383,595 |
| Units across all acquired versions | 1,385,630 |
| Source reference records, all versions | 2,624,501 |
| Distinct raw objects / bytes | 9,560 / 4,842,876,143 |
| SQLite bytes at audit | 13,299,683,328 |
| Municipal polygon features | 32,604, unchanged |

All 9,560 objects rehashed successfully. SQLite integrity, foreign keys, duplicate
version keys, required text/URLs, source-version validity and geometry-index checks
passed. Publication deltas reconcile exactly with the baseline plus every accepted
batch. The audit took 99.029 seconds; it measures storage integrity, not legal
correctness. Focused tests, Ruff and strict mypy also passed; exact commands and
software versions are in the receipt and [navigation execution record](navigation-execution.json).

## Boundaries that matter

- **Held, not published:** Hawaii and Alabama returned HTML instead of a usable
  robots policy. Their isolated evidence was not promoted.
- **Blocked, not published:** Ohio, Tennessee and Vermont have no accepted corpus
  from this wave. Access failures/restrictions were not bypassed.
- Accepted sources vary from substantial code collections to bounded tranches,
  historical material, court rules and publisher guidance. Indiana's accepted text,
  for example, is historical constitutional material, not its current code.
- Units include sections, PDF pages, headings, notes, indexes, notices and source
  blocks. Counts are not a count of operative legal provisions. Parser quality
  labels are not whole-corpus independent fidelity certification.
- 12,699 acquired versions lack an exact snapshot day and are excluded from dated
  as-of queries. Known dates describe publisher states, not inferred effectiveness.
  California's bulk archive does not incorporate unacquired weekday deltas;
  Oregon's bounded 2025 edition excludes subsequent session changes.
- The original 51 invalid polygons remain warned about, not repaired. GIS and text
  vintages differ; no automatic geographic applicability or legal-precedence engine
  is implied.
- Regional adapters, runtime details and frozen manifests remain in the local
  ignored collector stores, with acceptance hashes. The portable package includes
  the common library and California/Oregon/Washington adapters, not every regional
  acquisition script. Cloning Git alone does not recreate this wave.
- Publisher rights and access conditions are source-specific. No blanket software
  or corpus redistribution license, complete-history claim or population-coverage
  percentage is asserted.

## Recheck the retained library

```sh
.venv/bin/psephos --data data status
.venv/bin/psephos --data data audit
.venv/bin/python scripts/verify_corpus.py --data data --out verification.json
```

These checks use the retained store; they do not restart collectors. Full manifests
and original acceptance ledgers are under `data/collectors/leads/publish/`. Revalidating
an imported batch preserves its original publication ledger and records a separate
verification receipt, including recovery after a post-commit process interruption.
