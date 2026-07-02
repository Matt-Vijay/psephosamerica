# Dataset Acquisition Map

> **Acquisition status (2026-07-02):** Tier-1 CC0/PD sources + gettable Tier-2 all downloaded to `data/raw/acquisitions/` (1.2GB, see MANIFEST.md). Remaining: LegiScan (login), ICPSR (institutional).


Verified survey (2026-07-02) of existing datasets that can be legally ingested and remolded into the Psephos America graph. Every claim below survived 3-vote adversarial verification against primary sources (deep-research run `wf_78d2f9a9-4d6`: 24 sources fetched, 120 claims extracted, top 25 verified 25/25).

The dominant risk dimension is **license clarity**, which splits everything into three tiers.

## Tier 1 — Verified public-domain / CC0: ingest, remold, redistribute freely

| Dataset | What it adds | Coverage | Access | License |
|---|---|---|---|---|
| **unitedstates/congress-legislators** | **The master identity crosswalk**: every member of Congress mapped to Bioguide, THOMAS, GovTrack, OpenSecrets, VoteSmart, FEC, ICPSR, C-SPAN, Wikipedia, Ballotpedia, MapLight IDs | 1789–present | GitHub (YAML/JSON/CSV) | **CC0** |
| **Klarner State Legislative Election Returns** | State-legislative election results, candidate-level (418,894 contest-candidate-party rows + aggregations) | All 50 states, **1967–2022** (doi:10.7910/DVN/FJOGJB) | Harvard Dataverse bulk | **CC0** |
| **Klarner State Partisan Balance** | Partisan composition of both chambers + governor, supermajority control | **1937–2011** | Harvard Dataverse (hdl:1902.1/20403) | CC0-family (verify edition) |
| **Shor–McCarty state-legislator ideology** | Ideal-point scores for **27,629 state legislators** — entity spine + ideology enrichment | 1993–2020 (Apr 2023 update, doi:10.7910/DVN/NWSYOS) | Harvard Dataverse bulk | **CC0** |
| **CourtListener / Free Law Project bulk** | Courts, dockets, opinion clusters, opinions, **citation map**, judges/people, **judicial financial disclosures**, oral arguments — quarterly PostgreSQL CSV exports | Comprehensive; disclosures thin recent years | Bulk CSV (quarterly regen) | **Public Domain Mark** |
| **Open States / Plural bulk** | Legislator data, per-session bill+vote CSV/JSON incl. **full bill text**, district polygons, near-complete PostgreSQL dumps | 50 states + DC | Bulk (login) + PG dumps | **Public-domain dedication** (attribution appreciated) |

## Tier 2 — Ambiguous / conditional: confirm terms before redistributing

| Dataset | What it adds | Coverage | Issue |
|---|---|---|---|
| **Voteview / DW-NOMINATE** | **The entire pre-2013 federal roll-call gap**: all four datasets (member ideology, votes, members' votes, parties), 1st–119th Congress, bulk CSV/JSON + ~500MB MongoDB dump, live-updated | **1789–present** | Citation required (Lewis, Poole, Rosenthal et al.); **no explicit data license** — the 2018 MIT license covers the software, not unambiguously the data. Confirm with maintainers before redistribution (2-1 verifier split). Internal ingestion + derived features are lower-risk than re-releasing the raw data. |
| **Correlates of State Policy (CSPP)** | 3,000+ state-level policy/political variables — context/feature layer | All 50 states, ~1900–2020 | Free bulk CSV + R package, but **no explicit license**; aggregates thousands of differently-licensed upstream variables with per-source citation conditions. Isolate a redistributable subset if re-releasing. |
| **LegiScan weekly snapshots** | Bill/vote/legislator JSON/XML/CSV per session — complementary/gap-check feed vs OpenStates | 50 states + DC + Congress, weekly regen | Free registration to download; **redistribution license not established** — check terms. |

## Tier 3 — Access-gated / not openly licensed

| Dataset | What it adds | Issue |
|---|---|---|
| **ICPSR Study 1 — US Historical Election Returns** | County-level returns for 90%+ of president/governor/senator/House elections **1824–1968**, standardized county IDs + congressional-district numbers (identity crosswalk value) | ICPSR **membership-gated**; redistribution restricted without written agreement. Check for an openICPSR mirror with permissive terms. |
| **LOCUS-v1** (already ingested) | 2.2M local ordinances | **CC-BY-NC** — non-commercial only. |

## Priority ingestion order (given current graph state)

1. **congress-legislators crosswalk (CC0)** — the single highest-leverage item: it is the identity spine that makes Voteview ↔ FEC ↔ OpenSecrets ↔ existing federal votes resolvable as one entity set. Ingest first; everything else joins through it.
2. **Voteview (internal use)** — extends federal roll-calls from 113th–119th back to **1789**, plus DW-NOMINATE ideology as features. ~500MB dump, one download.
3. **Klarner election returns + Shor–McCarty (CC0)** — state-legislator election results 1967–2022 + ideology 1993–2020: pre-2015 state depth, election-results pillar, and a second identity spine for state legislators.
4. **CourtListener bulk (PD)** — replaces the proof-scale API ingest with the full quarterly dump: judges (FJC-keyed → confirmation votes), opinions, citation graph, financial disclosures.
5. **CSPP (internal features)** — state-year context panel for models.
6. **LegiScan** — as a completeness cross-check against the OpenStates 39M-vote corpus.

## Known gaps this survey did NOT resolve (next research pass)

- **Local government** (the biggest hole): LocalView council-meeting corpus, Big Local News, school-board and special-district datasets, Census of Governments — searched but not in the verified top-25; needs a dedicated pass.
- **Election results (modern)**: MIT Election Data + Science Lab bulk terms.
- **State campaign finance**: OpenSecrets/FollowTheMoney bulk terms post-merger.
- **Influence mapping**: LittleSis API/bulk license.
- **LLM-era corpora**: Pile-of-Law, COLD Cases license fit.

## Open questions (from verification)

- Does Voteview's 2018 MIT license extend to the roll-call *data* or only the software?
- Are LegiScan bulk snapshots redistributable/commercially reusable?
- Which CSPP upstream variables carry restrictive licenses; can a clean subset be isolated?
- Does an openICPSR version of the 1824–1968 returns exist under permissive terms?

*Caveat: CC0/PD dedications address copyright only — PII/privacy/sealed-record constraints still apply (esp. judicial financial disclosures).*
