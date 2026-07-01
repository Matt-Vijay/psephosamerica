# Methodology

This page describes the launch methodology for Psephos America. It follows `ENGINEERING_SPEC_V1.md` and only covers the v1 system.

## Source Set

Launch scoring uses a narrow set of official or supporting sources:

- Congress.gov for member identity, terms, bills, sponsorships, and committees
- `unitedstates/congress-legislators` for supporting crosswalks
- House vote XML from `clerk.house.gov`
- Senate roll call XML from `senate.gov`
- FEC bulk data and API for committee and contribution context
- Senate and House financial disclosure portals for disclosures and PTRs
- SEC company ticker and EDGAR data for issuer normalization
- Census ZIP and district crosswalk artifacts for ZIP entry support

Psephos America does not use unofficial sources in the scoring path.

## Current Runtime Shape

The current implementation follows an artifact-first path:

1. discover official disclosure listings from the House and Senate portals
2. download raw disclosure artifacts
3. store immutable source-artifact rows with provenance
4. parse and normalize disclosures into canonical rows
5. recompute rule fires and score snapshots from canonical data
6. publish immutable read-side snapshots

This is deliberate. Public pages should read from frozen exported artifacts, not from live canonical tables.

## Rule Philosophy

The system is deterministic, not interpretive. Rules are authored ahead of time, versioned, and executed against official records. A score change is the sum of explicit rule fires, not a model output and not an editorial judgment.

Each rule must define its inputs, conditions, parameters, severity, source types, and explanation template. Each fire must preserve the exact facts used, the derived values, the parameters in force, and the rule version that produced it.

## Fact, Inference, Judgment

Psephos America keeps three layers separate:

- Fact: a sourced record or directly observable field
- Inference: a derived value produced from rules, matching, or normalization
- Normative judgment: the meaning assigned to a pattern, such as whether it represents risk

This separation is visible in evidence cards and member pages. The product may describe overlap, timing, concentration, lateness, and risk, but it does not imply guilt.

## Known Limitations

Launch limitations are intentional:

- Coverage is federal-only and current-member-only at launch
- The v1 score family is limited to `conflict_of_interest_risk`
- Missing data does not produce a rule fire
- Amended disclosures create new immutable rule fires linked to the superseded filing
- House disclosures are more likely to need OCR fallback and manual review than Senate filings
- FEC data is useful but incomplete for some donor identity detail
- Historical cleanup before 2013 is deferred
- House and Senate disclosure listing pages do not provide reliable `bioguide_id` values directly; name-to-member resolution remains a downstream normalization step
- Current live Congress loading still reaches core members, committees, bills, and cosponsors before deeper enrichment like memberships and votes

## Correction Policy

Psephos America includes a public correction channel because the product is built from public records and deterministic joins, not from perfect data.

Corrections should be narrow and source-based. When a sourced error is confirmed, the record is corrected at the data or mapping layer, the affected recompute is rerun, and the change is reflected in a new published snapshot and changelog entry. The goal is to fix the record, not to reinterpret the rule after the fact.

## Snapshot Policy

Published output is immutable once released.

- Raw source files are stored immutably
- Published snapshots are frozen
- Each published snapshot has a SHA-256 manifest
- `pg_dump` exports are archived regularly

Public traffic should read from precomputed artifacts rather than the canonical database directly. This preserves auditability and keeps the public product stable across recomputes.

## Operator Model

Psephos America is built as a batch publishing system, not a live scoring dashboard.

- the runtime layer loads source data into canonical Postgres tables
- recompute creates deterministic rule fires, evidence rows, and score snapshots
- publish writes immutable public artifacts from persisted rows
- status surfaces are operator-facing, not part of the public product contract
