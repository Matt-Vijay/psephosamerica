# Track A source-coverage snapshot

Auto-generated from `src/graph/coverage.py` (pinned by
`tests/graph/test_coverage.py`). Counts are demonstrated on real runs;
access is keyless (no credential), local-bulk (committed/local file),
or derived (computed from ingested data). Credential-gated sources are in
[`ingestion-credentials.md`](ingestion-credentials.md).

| Tier | Source | Produces | Access | Demonstrated |
|---|---|---|---|---|
| federal | Congressional Record (CREC) floor speeches | floor_speech edges (bioguide-linked) | keyless | 2,762 edges / 537 members |
| federal | FEC candidate-committee linkage (ccl.txt) | Person<->Org edges | local-bulk | 624 affiliation edges |
| federal | FEC committee master (cm.txt) | Org IDs | local-bulk | 20,941 committees |
| federal | FEC contributions (itcont.txt) | donation edges | local-bulk | 162,723 edges ($14M) |
| federal | FEC member crosswalk | Person IDs | local-bulk | 535 members |
| federal | House Clerk roll-call votes | Person IDs + vote edges | keyless | 440 members, 17,045 edges |
| federal | Senate LDA federal lobbying | Org IDs + lobbying edges | keyless | 248 orgs |
| federal | Senate.gov roll-call votes | Person IDs + vote edges | keyless | 100 senators, 4,100 edges |
| state | OpenStates people (all 50 states) | Person IDs | keyless | 7,359 legislators |
| county | Legistar county clients | Person IDs + bills + votes | keyless | 21 counties (in 24,368 officials) |
| city | Legistar city clients (officials) | Person IDs | keyless | in 24,368 municipal officials |
| city | Legistar event-item votes | vote edges | keyless | 1,161 municipal vote edges |
| city | Legistar matters (municipal bills) | Bill IDs | keyless | 2,000 bills |
| city | Legistar registry (cities + counties) | Person IDs across governments | keyless | 124 governments (44,600 officials) |
| city | Text-PDF municipal minutes (pdfplumber) | minutes text + official links | keyless | text-layer PDFs (image-only = OCR gap) |
| cross-cutting | Bluesky public posts | social-post edges | keyless | 78 posts |
| cross-cutting | GDELT news | news-mention edges | keyless | 75 edges / 56 outlets |
| cross-cutting | ProPublica 990s | Org IDs | keyless | 175 nonprofits |
| cross-cutting | Public statements -> sectors | stance edges | local-bulk | 354 edges |
| cross-cutting | Wayback CDX (campaign sites) | snapshot edges | keyless | 363 snapshots 2007-2026 |
| enrichment | entity-linker (NER -> canonical_person_id) | text->Person links | derived | P 1.00 / R 0.75 on real sample |
| enrichment | regenerate driver (contract corpus) | ready rows w/ embeddings | derived | 7,918 rows, 100% enriched |

**22 sources** across 6 tiers.
