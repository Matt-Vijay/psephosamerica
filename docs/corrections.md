# Corrections

Psephos America is built from official public records and deterministic joins, not
from perfect data. We accept and act on sourced corrections to the underlying
records. See `METHODOLOGY.md` for the policy rationale.

## What counts as a correction

A correction is **narrow and source-based**: it identifies a specific record
or mapping that conflicts with an official source, and cites that source.

Accepted:

- A member identity / district / committee assignment that disagrees with
  `Congress.gov`.
- A vote, sponsor, or bill detail that disagrees with the official House or
  Senate roll-call XML or `Congress.gov`.
- A holding, transaction, or filing field that disagrees with the official
  Senate (`efdsearch.senate.gov`) or House (`disclosures.house.gov`)
  disclosure record.
- An FEC committee or contribution detail that disagrees with FEC bulk data
  or the FEC API.
- An issuer/ticker normalization that disagrees with SEC `EDGAR`.

Out of scope:

- Reinterpretation of a rule after the fact ("this should not have fired").
- Disputes about the rule families, weights, or thresholds themselves —
  those are governance changes, proposed through a separate channel.
- Anything not grounded in one of the official sources above.

## How to submit

Open a GitHub issue using the **Correction** label and include:

1. The affected record(s) (member slug, evidence-card id, snapshot date, …).
2. A link to the official source the record should match.
3. A short description of the conflict.

Sensitive submissions can also be emailed to the maintainers; use the
GitHub-issue template's structure in the email.

## SLA

| Stage | Target |
|---|---|
| Initial triage / acknowledgement | within **5 business days** |
| Decision (accept / decline / needs more info) | within **14 business days** |
| Accepted fix applied to the data or mapping layer, recomputed, and reflected in the next published snapshot | within **30 days of acceptance** (or the next regular weekly recompute, whichever is later) |

The SLA covers maintainer response time, not data freshness; published
snapshots are recomputed on the schedule documented in
[`docs/operations.md`](operations.md).

## Processing flow

1. Correction is filed and triaged.
2. Maintainer confirms the official source supports the proposed fix.
3. Fix is applied **at the data or mapping layer** (e.g.
   `data/crosswalks/`, the canonical row, a rule's `committee_sector_map.csv`
   entry) — never by editing a released snapshot.
4. The affected recompute is rerun (see operations runbook).
5. A new, dated public snapshot is published.
6. A line is added to the project changelog (`CHANGELOG.md`) noting the
   record(s) corrected and the source.

This mirrors the immutability contract in
[ADR 0002](adr/0002-artifact-first-immutable-publishing.md): released
snapshots are frozen; corrections appear as a *new* snapshot, not as an edit.
