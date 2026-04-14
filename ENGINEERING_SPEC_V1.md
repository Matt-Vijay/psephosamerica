# Open Pact Engineering Spec v1

Status: locked working spec for the first public release.

This document synthesizes `research/01` through `research/12` into one v1 plan. Where the research docs left options open, this file makes the decision.

## 1. Product Definition

Open Pact is an open-source, evidence-first public ledger of score-changing events for Members of Congress. It does not decide who is corrupt. It links official public records, applies deterministic rules, and publishes evidence cards and per-member profiles that show exactly what changed, why, and which records support the change.

Working line: **We make public records impossible to ignore.**

## 2. Locked v1 Scope

### In scope

- Federal Congress only.
- Current Members of Congress only at launch.
- Current Congress legislative data.
- Current-term financial disclosures and PTRs for current members.
- FEC committee and contribution context for the most recent two election cycles.
- One score family at launch: `conflict_of_interest_risk`.
- Public, read-only web product with ZIP entry, homepage/feed, member pages, evidence cards, methodology, and a deferred compare placeholder.
- Full batch recomputation with frozen published snapshots.

### Out of scope for launch

- State or local officials.
- Promise tracking.
- Broad donor-dependence and legislative-effectiveness scoring.
- Real-time alerts or live recomputation.
- User accounts.
- Mobile app.
- Cross-member rankings like "most corrupt."
- Automated social posting.
- Unofficial sources in the scoring path.
- Pre-2013 historical disclosure cleanup.

## 3. Product Principles

Every public output must be:

- `Decomposable`: score changes are the sum of explicit rule fires.
- `Inspectable`: every penalty or bonus links to a source record.
- `Deterministic`: same inputs, same outputs.
- `Open`: rules, mappings, weights, and code are public.
- `Frozen`: published snapshots are immutable.

Every evidence object must separate:

- `Fact`
- `Inference`
- `Normative judgment`

Tone rule: describe overlap, timing, concentration, lateness, and risk. Do not imply guilt.

## 4. Official Source Set

The launch source set is intentionally narrow.

| Domain | Launch source | Role in v1 |
|---|---|---|
| Member identity, terms, bills, sponsorships, committees | `Congress.gov API` | Canonical legislative source |
| Member ID crosswalks | `unitedstates/congress-legislators` | Supporting crosswalk only |
| House votes | `clerk.house.gov` XML | Canonical House vote source |
| Senate votes | `senate.gov` roll call XML | Canonical Senate vote source |
| Campaign finance | `FEC bulk data + API` | Committee and contribution context |
| Financial disclosures and PTRs | `efdsearch.senate.gov`, `disclosures.house.gov` | Canonical conflict data source |
| Company/ticker reference | `SEC company_tickers` / EDGAR | Issuer normalization |
| ZIP to House district mapping | Census ZIP/ZCTA crosswalk artifacts | ZIP entry support |

Launch notes:

- `bioguide_id` is the main cross-source member identifier.
- `lis_member_id` must be mapped to `bioguide_id` for Senate votes.
- FEC data is useful but incomplete for small-dollar donor identity because unitemized contributions are aggregate only.
- Lobbying data is worth ingesting later, but it is not a launch dependency and does not identify specific member contacts.

## 5. Canonical Data Model

The canonical layer is relational and row-based. The product value comes from joins across members, committees, votes, contributions, disclosures, holdings, transactions, and rule fires.

### Core canonical tables

- `member`
- `member_term`
- `committee`
- `committee_membership`
- `bill`
- `bill_sponsor`
- `vote_event`
- `vote_cast`
- `fec_committee`
- `contribution`
- `financial_disclosure`
- `holding`
- `transaction`
- `rule_fire`
- `evidence_card`
- `score_snapshot`

### Provenance and review tables

- `data_source`
- `ingestion_run`
- `source_artifact`
- `parse_run`
- `match_decision`
- `review_queue`

### Read models and exported artifacts

These are generated after each recompute and are not sources of truth:

- per-member profile JSON
- per-ZIP feed JSON
- evidence-card JSON
- pre-rendered OG images
- dated public snapshot manifests

Decision: `Postgres` is the canonical store. JSON is for exported read models, not raw truth.

## 6. Entity Resolution Policy

### Canonical identifiers

- `bioguide_id` for members
- `fec_candidate_id` and `fec_committee_id` for campaign finance
- `sec_cik` for public companies

### Confidence model

- `HIGH`: deterministic ID joins or exact alias hits; auto-accept
- `MEDIUM`: strong fuzzy match with supporting signals; auto-accept plus audit sample
- `LOW`: ambiguous or LLM-assisted candidate; human review required

### LLM policy

Allowed:

- employer clustering
- occupation normalization
- sector suggestion for unresolved donor or company entities

Not allowed as final authority:

- member identity resolution
- asset classification that directly affects scoring
- lobbying relationship inference

Rule: the model may propose; the system or reviewer decides.

### Review priority

1. member identity conflicts
2. high-value unresolved assets and issuers
3. large unclassified PACs and donors
4. large-scale employer cluster merges

## 7. Taxonomy and Committee Mapping

Open Pact uses a public, versioned sector taxonomy with 15 canonical sectors. OpenSecrets categories are a crosswalk, not the canonical taxonomy.

Decision:

- canonical taxonomy lives in `data/taxonomy/`
- crosswalks live in `data/crosswalks/`
- committee mappings are effective-dated or versioned per Congress, not one living undifferentiated file
- review-required committees must have a designated reviewer, not ad hoc approval

Recommended artifacts:

- `data/taxonomy/sectors.yaml`
- `data/taxonomy/committee_sector_map.csv`
- `data/crosswalks/crp_to_sector.csv`

Mapping tiers:

- `deterministic`: clear single-sector jurisdiction
- `review_required`: broad or multi-sector committees like Finance, Appropriations, Judiciary
- `out_of_scope`: select and special committees in v1

Every committee mapping row must include a `jurisdiction_basis` citation.

## 8. Disclosure Parser Pipeline

Launch parser scope is limited to:

- annual financial disclosures
- periodic transaction reports

### Pipeline

1. acquire PDFs from official House and Senate portals
2. store raw artifacts in immutable object storage
3. classify text-based vs image-based PDFs
4. OCR only when text extraction fails
5. detect sections and tables
6. extract holdings, transactions, and outside positions
7. normalize values and owner fields
8. generate issuer/ticker candidates
9. route low-confidence items to review

### Chamber-specific decision

- Senate filings are generally machine-readable and can flow through the default parser.
- House filings require OCR fallback and stricter review thresholds.
- Pre-2013 messy House disclosure cleanup is deferred.

### Failure handling

The parser must detect:

- low OCR confidence
- missing section headers
- duplicate header rows interpreted as data
- non-standard amount values
- amendment filings
- impossible dates
- unresolved options or trusts
- member identity mismatches

Decision: use `Tesseract` first with confidence thresholds. Escalate only low-confidence pages to manual review. Cloud OCR is an optional later optimization, not a launch dependency.

## 9. Rules and Evidence Engine

Launch rule families:

- `committee_sector_trade`
- `repeated_committee_linked_trading`
- `late_or_amended_disclosure`
- `sector_holdings_overlap`

### Rule requirements

Every rule must include:

- `rule_id`
- `dimension`
- `version`
- `inputs`
- `conditions`
- `parameters`
- `severity`
- `source_types_required`
- `explanation_template`

Every `rule_fire` must capture:

- the exact sourced facts used
- derived values
- parameters used at fire time
- rule version
- recompute run id

### Locked decisions

- `repeated_committee_linked_trading` uses a simple threshold rule in v1, not a statistical model
- missing data means no fire; no imputation
- amended disclosures create a new immutable rule fire linked to the superseded filing
- severity is rule-authored, not editorially changed after the fact

### Evidence cards

Each evidence card must include:

- member identity
- impacted dimension
- score delta
- short explanation
- source anchors
- confidence label
- fact / inference / normative judgment sections
- link to the full member page

## 10. Public Product Surfaces

### Routes

| Route | Purpose | Render mode |
|---|---|---|
| `/` | ZIP entry | static |
| `/zip/:zip` | federal feed for one House member + two senators | batch-regenerated |
| `/member/:slug` | member profile | batch-regenerated |
| `/evidence/:id` | evidence card permalink | batch-regenerated |
| `/methodology` | public methods and limitations | static |
| `/compare` | deferred placeholder | static |
| `/api/v1/*` | read-only JSON | dynamic over precomputed artifacts |

### ZIP decision

Launch input is 5-digit ZIP only. If a ZIP maps to multiple congressional districts, use the plurality district and show a clear ambiguity note. Street-address resolution is deferred.

### Viral primitives in v1

- permanent shareable member URLs
- permanent evidence-card URLs
- pre-rendered OG images for evidence cards, members, and ZIP feeds
- "look up your ZIP" CTA on every page
- lightweight card embed for media and civic sites

Virality is a launch requirement. Sensational breadth is not.

## 11. Batch Architecture and Deployment

Open Pact is a batch-computed publishing system, not a live transactional app.

### Recommended launch stack

- `Python` for ingestion, parsing, normalization, rules, and exports
- `Postgres` as canonical database
- `Cloudflare R2` for raw files, exported JSON, OG images, and archives
- `Cloudflare Pages` for the public site
- `Cloudflare Worker` for a thin read-only API over precomputed artifacts
- `GitHub Actions` for scheduled recompute while the repo is public, with a clean path to `Fly Machines` or similar if compute or privacy needs change

### Recompute cadence

Launch cadence: `weekly full recompute`.

Rationale:

- lowers operational and audit burden
- matches the non-real-time character of the product
- is easy to increase to daily later without redesign

### Snapshot policy

- raw source files are immutable
- published snapshots are immutable
- each published snapshot has a SHA-256 manifest
- `pg_dump` exports are archived regularly

Read-path rule: public traffic should hit the CDN and object storage, not Postgres.

## 12. Governance and Trust

The governance layer is part of the product.

Launch requirements:

- public methodology page
- public scoring formulas and thresholds
- public taxonomy and mapping artifacts
- public changelog for rule or mapping changes
- public correction channel
- correction SLA
- immutable monthly or dated published snapshots

Public claims must stay narrow:

- we show disclosed records, linked official context, and deterministic rule fires
- we do not adjudicate guilt
- we do not claim complete coverage of all money or all conflicts

## 13. Release Cutline

### Must ship

- canonical ingest for members, committees, bills, votes, disclosures, and FEC contributions
- disclosure parser for current House and Senate filings
- sector taxonomy and committee mapping artifacts
- four conflict-risk rule families
- evidence-card generation
- member pages
- ZIP feed
- methodology and correction process
- batch export, archive, and public snapshot manifest

### Can slip

- lobbying ingestion
- deep donor employer normalization
- historical backfill
- search
- compare implementation
- more advanced parent-subsidiary mappings

### Explicitly out of scope

- promise tracking
- donor-dependence scoring
- legislative-effectiveness scoring
- rankings/leaderboards as the main product
- state/local expansion
- automated social posting
- user accounts

## 14. Minimal Repo Shape

```text
openpact/
  ENGINEERING_SPEC_V1.md
  README.md
  METHODOLOGY.md
  .github/workflows/
  db/
    schema.sql
    migrations/
  src/
    ingest/
    parse/
    normalize/
    rules/
    export/
    api/
    site/
  data/
    taxonomy/
    crosswalks/
  research/
```

## 15. Immediate Build Order

1. create schema and provenance tables
2. ingest member, term, committee, bill, and vote data
3. ingest Senate and House disclosure filings for current members
4. build the disclosure parser and review queue
5. create sector taxonomy and committee mapping artifacts
6. ingest FEC committee and contribution context
7. implement the four launch rule families
8. export member, ZIP, and evidence read models
9. ship the static site and methodology page
10. run a manual audit before public launch

This is the v1 contract. Anything that conflicts with it should be treated as deferred unless this file is intentionally revised.
