# Ingestion credentials — what to provision to unblock more sources

Track A ingests from any source reachable without a credential out of the box
(House Clerk, Senate.gov, OpenStates *people* roster, Legistar municipal/county,
FEC bulk, ProPublica 990s, GDELT, Bluesky public AppView, Wayback). The sources
below are **credential-gated** — provide the env var and the matching adapter
(or the documented adapter shape) ingests them. Set them in the environment /
`.env`; none are committed.

## High-impact (unblock state-level bills + votes)

| Env var | Source | Unblocks | Get a key |
|---|---|---|---|
| `OPENSTATES_API_KEY` | OpenStates v3 API | **State legislators' bills + votes** (all 50 legislatures). The vote adapter shape is identical to `ingest/house_clerk` / `ingest/senate`. | https://openstates.org/accounts/profile/ |
| `LEGISCAN_API_KEY` | LegiScan | Alternative/!redundant state bill + roll-call source (50 states) | https://legiscan.com/legiscan |
| `CONGRESS_GOV_API_KEY` | api.congress.gov (api.data.gov) | Richer federal bill metadata, cosponsors, and text-version metadata/links (beyond the keyless Clerk/Senate vote XML already ingested) | https://api.congress.gov/sign-up/ |
| `GOVINFO_API_KEY` | GovInfo (api.data.gov) | GovInfo API access to BILLS package metadata/text renditions, CBO/JCT scores, and the Bound Congressional Record | https://api.govinfo.gov/docs/ |

The Time Machine V1 representative BILLS XML fetch uses official, keyless
GovInfo package-content URLs; `GOVINFO_API_KEY` is only needed for GovInfo API
endpoints. BILLSTATUS metadata alone is not legislative text.

## Money trail

| Env var | Source | Unblocks | Get a key |
|---|---|---|---|
| `OPENSECRETS_API_KEY` | OpenSecrets / CRP | Donor aggregation, industry codes, dark-money summaries | https://www.opensecrets.org/api/admin/index.php?function=signup |
| `FOLLOWTHEMONEY_API_KEY` | FollowTheMoney (NIMP) | **50-state campaign finance** | https://www.followthemoney.org/our-data/apis/ |
| `OPENCORPORATES_API_TOKEN` | OpenCorporates | State SOS **business registrations** (aggregated) | https://opencorporates.com/api_accounts/new |

## Public record / legal

| Env var | Source | Unblocks | Get a key |
|---|---|---|---|
| `COURTLISTENER_API_TOKEN` | CourtListener / RECAP | **Court filings + dockets** (the `/courts` list is public; dockets need this token) | https://www.courtlistener.com/profile/api/ |
| `REGRID_API_TOKEN` | Regrid (or county vendor) | **County property records / parcels** (no unified public API; vendor-gated) | https://regrid.com/api |

## Stated positions / questionnaires

| Env var | Source | Unblocks | Get a key |
|---|---|---|---|
| `VOTESMART_API_KEY` | Vote Smart | Candidate **questionnaires / NPAT positions** | https://justfacts.votesmart.org/ |
| `BALLOTPEDIA_API_KEY` | Ballotpedia | Officeholder/candidate survey + bio data (partner-gated) | https://ballotpedia.org/API_for_Ballotpedia |

## Social media (official public accounts)

| Env var | Source | Unblocks | Get a key |
|---|---|---|---|
| `X_BEARER_TOKEN` | X / Twitter API v2 | Official X posts (paid tiers) | https://developer.x.com/ |
| `YOUTUBE_API_KEY` | YouTube Data API | Official channel video metadata/captions | https://console.cloud.google.com/ |
| `META_GRAPH_API_TOKEN` | Meta Graph API | Facebook Page / Instagram / Threads public posts | https://developers.facebook.com/ |
| `BLUESKY_IDENTIFIER`, `BLUESKY_APP_PASSWORD` | Bluesky authed API | Higher-rate Bluesky ingestion (the public AppView, already used, needs none) | https://bsky.app/settings/app-passwords |

## News / media archives

| Env var | Source | Unblocks | Get a key |
|---|---|---|---|
| (none) | GDELT | Already ingested keyless (`ingest/gdelt`); 1 req/5s public limit | — |
| `LEXISNEXIS_API_KEY` | LexisNexis | Deep news/litigation archive (enterprise) | sales-gated |
| `NEWSPAPERS_COM_API_KEY` | Newspapers.com | Historical local-paper archive | subscription, no open API |

## Enrichment models (Track B handoff)

| Env var | Source | Unblocks | Notes |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Claude (Sonnet/Opus) | The **LLM dossier** content (`DossierModel` boundary; `dossier_json`). The pipeline + source-anchor guard already exist. | https://console.anthropic.com/ |
| (none) | local embedder | `dossier_embedding` / `structural_embedding` — **already populated** by the in-repo deterministic `LocalTextEmbedder` (no key). | A sentence-transformers MiniLM can replace it for semantic quality; it plugs into the same `DossierEmbedder` boundary. |

> The municipal-vote tier (`ingest/legistar_votes`), House/Senate votes,
> FEC, 990s, GDELT, Bluesky public, OpenStates people, and Wayback are all
> ingested **without any key**. The table above is the remaining surface.

## Probed 2026-06 and found gated (documented, not fabricated)

These were attempted keyless and are blocked by anti-bot, JS-rendering, or
a required key — skipped rather than fabricated:

| Source | Status | Path to unblock |
|---|---|---|
| State-legislature votes (CA/NY/TX/FL/IL/PA/OH/GA/NC/MI) | per-state; LegiScan behind Cloudflare (403), OpenStates v3 + bulk S3 require `OPENSTATES_API_KEY`, state portals (leginfo etc.) are JS-rendered 302s | `OPENSTATES_API_KEY` / `LEGISCAN_API_KEY`, or per-state headless-browser scrapers |
| State campaign finance (top-25 states) | per-state portals are JS/session-gated (CA Cal-Access, NY BOE, TX Ethics, …); no unified keyless API | `FOLLOWTHEMONEY_API_KEY` (50-state) or per-state scrapers |
| C-SPAN transcripts | `c-span.org` search is server-rendered HTML with no public JSON API; transcript pages are JS | HTML scraping with JS rendering (Playwright); no key |
| Court-filing dockets | CourtListener `/search` + `/courts` are keyless, but `/opinions`/`/dockets` detail need `COURTLISTENER_API_TOKEN` | `COURTLISTENER_API_TOKEN` |
| Image-only scanned municipal minutes | text-layer PDFs handled by `ingest/pdf_minutes`; scanned image PDFs have no text layer | a raster OCR engine (Tesseract) — out of the pure-Python path |

**Floor speeches are NOT gated:** the Congressional Record CREC MODS metadata
(`ingest/congressional_record`) is keyless and bioguide-linked — already ingested
(2,762 edges / 537 members).
