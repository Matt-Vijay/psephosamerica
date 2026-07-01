# Psephos America — External Data Catalog & Acquisition Roadmap

A comprehensive census of existing public datasets we can legally ingest/remold into the
governance graph (person ↔ bill/ordinance ↔ vote ↔ org ↔ donor ↔ lobby ↔ award ↔ speech ↔
jurisdiction, provenance + `known_at` on every edge). Compiled from a 6-domain research sweep.

## License posture

Psephos America is a **public-interest, non-commercial** project. That means **CC-BY-NC and
academic-use-with-citation sources are usable** (DIME, FollowTheMoney, OpenSecrets bulk, SCDB,
Martin-Quinn, Oyez, LOCUS). We still **exclude**: OpenSecrets *Revolving Door* (Columbia Books
contract — no redistribution), commercial voter files (L2/Catalist/TargetSmart), and paid APIs
(Ballotpedia, Cook PVI, Dave Leip, CQ, QuiverQuant, LegiStorm). Attribution + `known_at` on
every ingested record; access gates documented, never faked.

## What we already have

Federal bills + full text + CRS (govinfo, 113–119) · House + Senate roll-calls · Congressional
Record floor speeches · OpenStates 50-state bulk votes (39M edges) · LOCUS local ordinances
(2.2M) · FEC individual contributions · Senate LDA lobbying 1999–2020 · USASpending awards ·
CourtListener (proof-scale) · 50-state legislator rosters.

---

## Prioritized acquisition roadmap (the power-law order)

### Tier 0 — the entity-resolution spine (do FIRST; makes everything else joinable)
The graph's value is superlinear in how well entities connect. These are the crosswalks.

| Source | Why it's the spine | Access / license |
|---|---|---|
| **unitedstates/congress-legislators** | Canonical person roster 1789–present + the ID crosswalk (Bioguide↔ICPSR/Voteview↔FEC↔OpenSecrets↔Wikidata↔C-SPAN↔OpenStates↔Ballotpedia) — the federal join key | GitHub bulk, **CC0** |
| **Wikidata** | Universal cross-ID + bio hub for officials/orgs/agencies at every level; SPARQL + weekly dumps | **CC0** |
| **Census of Governments (Organization)** | Authoritative registry of all **90,837** local units + GID master — the local denominator/target universe | Census bulk, **public domain** |
| **SAM.gov Entity / Exclusions extracts** | Vendor/org master (UEI, ownership) for money-out entity resolution + debarment | GSA API, public domain |
| **BICAM** | Pre-entity-keyed federal subgraph (11 components, standardized cross-dataset IDs) — backbone + validation set | GitHub, open academic |

### Tier 1 — biggest coverage/data unlocks
| Source | Adds | Access / license |
|---|---|---|
| **DIME (Bonica, Stanford)** | 850M+ contributions **federal+state** in one schema + **CFscores (ideology) for donors AND candidates** — the "money-in everywhere" backbone | Free download, academic-cite |
| **FollowTheMoney / NIMSP** | Canonical **50-state campaign finance** + 2M+/yr state lobbyist relationships (through 2024) | Free API (account), **CC-BY-NC** |
| **Legistar Web API** (`webapi.legistar.com`) | **Local votes + ordinances** for ~70% of big US cities/counties — shared multi-tenant read API, **no login**; one integration → hundreds of governments | Public read API |
| **LegiScan** | Full state **bill text** + complete roll-calls, all 50 states, weekly snapshots (great `known_at`); cross-check on OpenStates | Free API + bulk (free tier) |
| **Voteview / DW-NOMINATE** | All roll calls back to **1789** + member **ideology ideal points** | Free bulk, academic-cite |
| **MEDSL + OpenElections** | Election-returns backbone (precinct→county→district), all offices | Free bulk (Dataverse/GitHub) |
| **FEC bulk — missing pieces** | Committees, candidate master, disbursements, **independent expenditures + electioneering** (money spent *for/against*) | Free bulk, public domain |
| **Caselaw Access Project** | 6.7M cases / 40M pages full text — **fully open since Mar 2024** | Bulk (HF/Dataverse), open |
| **IRS 990 e-file** | Dark-money / 501c4 / nonprofit org financials | AWS S3, public domain |

### Tier 2 — high-value enrichment + new edge types
| Source | Adds | Access / license |
|---|---|---|
| **US Policy Agendas / Comparative Agendas Project** | Gold-standard human policy-topic codes across bills/hearings/laws/EOs — crosswalk + validation for CRS areas | Free, academic-cite |
| **Correlates of State Policy** | 3,000+ variables × 50 states × year (policy adoptions + outcomes) — state-outcome feature substrate | Free R pkgs |
| **Regulations.gov API (v4)** | **Rules ↔ dockets ↔ public comments ↔ commenter** — a unique influence layer | Free key, 1,000 req/hr |
| **Federal Register + eCFR + Unified Agenda + EO bulk** | Executive/regulatory node core: rules, codified regs, RIN lifecycle, EOs; `/agencies` crosswalk | Free (mostly keyless) |
| **SCDB (Spaeth) + Martin-Quinn + Judicial Common Space** | Every SCOTUS vote + issue coding + judicial ideology (extends to all fed judges via appointing-senator NOMINATE) | Free, academic-cite |
| **Shor-McCarty** | State-legislator ideology ideal points (27k legislators, 1993–2020) | Free, academic |
| **Senate/House LDA 2021–2026 + LD-203** | Closes our lobbying time gap + adds lobbyist political contributions | `lda.gov` REST API |
| **FARA** | Foreign-agent → principal → US-official influence edges | New API v1, public domain |
| **Congressional PTRs / financial disclosures** | Member stock trades + assets → conflict-of-interest edges | House bulk ZIP + Senate EFD |
| **TIGER/Line + VEST + ALARM** | District/precinct **geometry** for spatial person↔jurisdiction joins | Free, public domain |
| **Comparative Legislators DB (`legislatoR`)** | Person enrichment: career, sociodemographics, Wikipedia attention, IDs | R pkg, academic |
| **EveryCRSReport · Congressional Bills Project · GovInfo non-bill (CHRG/CRPT/PLAW/US Code)** | Better CRS metadata; PAP-coded bills; hearings/reports/laws/statute nodes | Free, mostly public domain |

### Discovery meta-layers (for scaling local at large)
Socrata Discovery API · ArcGIS Hub · CKAN (data.gov + state/city) · DataPortals.org seed CSV ·
Google Dataset Search · Harvard Dataverse Search API — used to *find and harvest* the long tail
of city/county datasets and boundaries beyond the vendor APIs.

---

## Dead sources (do NOT build against)
- **ProPublica Congress API** — retired Jul 2024 (use unitedstates/congress + Congress.gov)
- **GovTrack bulk/API** — retired 2017 (use congress-legislators)
- **OpenSecrets API** — discontinued Apr 2025 (use their bulk data)

## Time-sensitive migrations
- **FPDS → SAM.gov Contract Awards API** — FPDS decommissioned Feb 24 2026
- **Senate LDA `lda.senate.gov` → `lda.gov`** — legacy host retires **2026-06-30**; ingest via lda.gov REST going forward (our 1999–2020 backfill came from Wayback archives of the old host)

## Restricted / paid (flagged; excluded under non-commercial posture or by cost)
Ballotpedia API (paid) · Cook PVI (paid) · Dave Leip Atlas (paid) · CQ Elections (subscription) ·
ICPSR (membership; cherry-pick free series) · L2/Catalist/TargetSmart voter files (commercial,
partisan-gated) · OpenSecrets Revolving Door (**contractually non-redistributable**) · Charles
Stewart committee data (non-commercial — OK for us, flag for any commercial fork) · C-SPAN video
(only floor proceedings are public domain) · Stanford Congressional Record corpus (HeinOnline
redistribution limits — internal features only) · QuiverQuant / Unusual Whales / LegiStorm (paid).

---

## Mapping to the three goals

- **Amount** → Legistar (local at scale), DIME + FollowTheMoney (money everywhere), LegiScan
  (state bill text), Caselaw Access Project, IRS 990, MEDSL/OpenElections.
- **Organization / quality** → the Tier-0 identity spine (congress-legislators, Wikidata,
  Census of Governments, SAM.gov, BICAM) — the crosswalks are the keystone.
- **Insights / simulation** → Voteview + DIME CFscores + SCDB/Martin-Quinn (ideology),
  Comparative Agendas + Correlates of State Policy (policy topics + outcomes as causal/sim
  features), Regulations.gov (influence edges).
