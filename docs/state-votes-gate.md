# State-legislature roll-call votes — direct-keyless gate (honest report)

Track A v5 #2 asked for ≥5 states' roll-call votes via **direct-keyless** ingest,
CA leginfo bulk first. Probed 2026-06-10; each route is gated. No fabrication —
this documents exactly what blocks each, with the path to unblock.

## California — `leginfo` bulk (keyless, but ~1 GB/snapshot)

`https://downloads.leginfo.legislature.ca.gov/` is genuinely keyless and serves
the full legislative database as `pubinfo_<year>.zip` (and `pubinfo_daily_*.zip`).
Roll-call votes live in `bill_detail_vote_tbl.dat` / `bill_summary_vote_tbl.dat`
inside the zip.

**Gate:** every snapshot is a *full* database dump — `pubinfo_2025.zip` is
**975 MB**, and even the daily `pubinfo_daily_Mon.zip` is **850 MB** (a full
snapshot, not an incremental). There is no small/keyless per-bill or per-vote
endpoint. Downloading + extracting ~1 GB per year (≥2 GB for recent coverage) is
impractical in this sandbox (disk + bandwidth + time).

**Path to unblock:** run the (tested-elsewhere) `.dat` vote parser in an
environment that can stage a 1 GB download, or mirror `bill_detail_vote_tbl.dat`
once and parse it offline. The schema is stable and tab-delimited.

## Other states (NY/TX/FL/IL/PA/OH) — JS / PDF / key-gated

Consistent with the prior probe (`docs/ingestion-credentials.md`):

| State | Source | Gate |
|---|---|---|
| NY | Open Legislation API (`legislation.nysenate.gov`) | free **API key** required |
| TX | Texas Legislature Online | roll-calls only in chamber **Journals (PDF)** |
| FL | flsenate.gov / myfloridahouse.gov | vote pages **JS-rendered** |
| IL | ilga.gov | roll-calls published as **PDF** |
| PA | legis.state.pa.us | roll-calls as **PDF** |
| OH | legislature.ohio.gov | vote data **JS/session-gated** |
| (50-state) | OpenStates v3 + bulk S3, LegiScan | **API key** (LegiScan also Cloudflare-403) |

## Conclusion

Direct-keyless state roll-call **votes** are not feasibly ingestible at scale in
this sandbox: CA is keyless but 1 GB/snapshot; every other route is API-key-,
JS-, or PDF-gated. State *legislators* (rosters) are already ingested keyless
(OpenStates people, 7,359 across 50 states). The vote tier unblocks with any of:
`OPENSTATES_API_KEY` (the adapter shape is identical to House/Senate), a staged
CA `leginfo` bulk download, or per-state headless-browser / OCR scrapers.

**Decision:** documented and moved on (per the v5 directive), prioritizing the
keyless, tractable items — Senate backfill (shipped), the bill-edge pass, and
CREC floor-speech backfill.
