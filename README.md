# Open Pact

Open Pact is an open-source, evidence-first public ledger of score-changing events for Members of Congress. It links official public records, applies deterministic rules, and publishes evidence cards and per-member profiles that show what changed, why it changed, and which records support the change.

## v1 Scope

Version 1 is intentionally narrow:

- Federal Congress only
- Current Members of Congress only at launch
- Current Congress legislative data
- Current-term financial disclosures and PTRs for current members
- FEC committee and contribution context for the most recent two election cycles
- One score family: `conflict_of_interest_risk`
- Public, read-only web product with ZIP entry, homepage/feed, member pages, evidence cards, methodology, and a deferred compare placeholder
- Batch recomputation with frozen published snapshots

Anything outside `ENGINEERING_SPEC_V1.md` is deferred.

## Repository Layout

- `ENGINEERING_SPEC_V1.md` - locked v1 contract and source of truth
- `README.md` - project overview and repo orientation
- `METHODOLOGY.md` - public methods, source policy, and limitations
- `.github/` - repository automation
- `data/` - taxonomy and crosswalk artifacts
- `db/` - canonical schema and migrations
- `src/` - ingestion, parsing, normalization, rules, export, API, and site code

## What v1 Means

v1 is a deterministic, inspectable, frozen launch. Every score change must be decomposable into explicit rule fires, every evidence object must separate fact from inference from normative judgment, and every published snapshot must remain immutable after release.
