# Track A source-coverage snapshot

Auto-generated from `src/graph/coverage.py` (pinned by
`tests/graph/test_coverage.py`). Counts are demonstrated on real runs;
access is keyless (no credential), local-bulk (committed/local file),
or derived (computed from ingested data). Credential-gated sources are in
[`ingestion-credentials.md`](ingestion-credentials.md).

| Tier | Source | Produces | Access | Demonstrated |
|---|---|---|---|---|
| federal | BILLSTATUS bill edges (sponsor/subject/committee) | graph edges | derived | 1,673,411 edges / 106,999 bills |
| federal | Congressional Record (CREC) floor speeches | floor_speech edges (bioguide-linked) | keyless | 380,119 edges / 2,352 issues (113-119) |
| federal | FEC candidate-committee linkage (ccl.txt) | Person<->Org edges | local-bulk | 624 affiliation edges |
| federal | FEC committee master (cm.txt) | Org IDs | local-bulk | 20,941 committees |
| federal | FEC contributions (itcont.txt) | donation edges | local-bulk | 162,723 edges ($14M) |
| federal | FEC donor-industry profiles | dossier_json.donor_profile | derived | 518 members, $1.21B itemized |
| federal | FEC member crosswalk | Person IDs | local-bulk | 535 members |
| federal | House Clerk roll-call votes | Person IDs + vote edges | keyless | 440 members, 17,045 edges |
| federal | Senate LDA federal lobbying | Org IDs + lobbying edges | keyless | 248 orgs |
| federal | Senate.gov roll-call votes | Person IDs + vote edges | keyless | 286 senators (LIS), 494,265 vote edges 113-119 |
| federal | congress-legislators roster | Person IDs (bioguide+LIS, real names) | keyless | 1,132 members (440 renamed + 692 added) |
| federal | govinfo BILLSTATUS bulk (113-119) | Bill IDs + CRS content + embeddings | keyless | 106,536 bills, 100% of substantive votes linkable |
| state | CA leginfo bulk (partial-ZIP range reads) | Bill IDs + titles + legislators + rich roll-calls | keyless | 25,539 bills, 720 seats, 48,676 roll-calls / 2.96M votes |
| state | OpenStates people (all 50 states) | Person IDs | keyless | 7,359 legislators |
| county | Legistar county clients | Person IDs + bills + votes | keyless | 21 counties (in 24,368 officials) |
| city | LOCUS jurisdiction linkage (folded-key) | LOCUS jurisdiction -> canonical jurisdiction | derived | 59 linked (P 1.00 / R 1.00, 0 false merges), 2,228 minted |
| city | LOCUS-v1 ordinances (CC-BY-NC; HF LocalLaws/LOCUS-v1) | ordinance Bill IDs + function/topic + 4 dimension scores | keyless | 2,207,679 ordinances / 2,287 jurisdictions (50 states), full corpus |
| city | Legistar city clients (officials) | Person IDs | keyless | in 24,368 municipal officials |
| city | Legistar event-item votes | vote edges | keyless | 1,161 municipal vote edges |
| city | Legistar matters (municipal bills) | Bill IDs | keyless | 2,000 bills |
| city | Legistar registry (cities + counties) | Person IDs across governments | keyless | 134 governments (44,600+ officials) |
| city | Text-PDF municipal minutes (pdfplumber) | minutes text + official links | keyless | text-layer PDFs (image-only = OCR gap) |
| cross-cutting | Bluesky public posts | social-post edges | keyless | 78 posts |
| cross-cutting | GDELT news | news-mention edges | keyless | 539 edges (API throttles bulk) |
| cross-cutting | Prediction markets (Polymarket + Kalshi) | market entities + bill links + price history | keyless | 1,275 markets, 49 bill-linked, 5,322 price obs |
| cross-cutting | ProPublica 990s | Org IDs | keyless | 175 nonprofits |
| cross-cutting | Public statements -> sectors | stance edges | local-bulk | 354 edges |
| cross-cutting | Wayback CDX (campaign sites) | snapshot edges | keyless | 363 snapshots 2007-2026 |
| enrichment | entity-linker (NER -> canonical_person_id) | text->Person links | derived | P 1.00 / R 0.75 on real sample |
| enrichment | regenerate driver (contract corpus) | ready rows w/ embeddings | derived | 141,266 rows; 139,974 w/ 384-d semantic |

**30 sources** across 6 tiers.
