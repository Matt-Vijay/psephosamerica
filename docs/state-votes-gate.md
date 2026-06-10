# State-legislature roll-call votes — direct-keyless gate (honest report)

Track A v5 #2 asked for ≥5 states' roll-call votes via **direct-keyless** ingest,
CA leginfo bulk first. Probed 2026-06-10. **California is now SHIPPED** (see
below); the other states remain key/JS/PDF-gated. No fabrication — this documents
exactly what each route does, with the path to unblock the rest.

## California — `leginfo` bulk — ✅ SHIPPED via partial-ZIP range extraction

`https://downloads.leginfo.legislature.ca.gov/` is keyless and serves the full
legislative database as `pubinfo_<year>.zip`. Roll-call votes live in
`BILL_DETAIL_VOTE_TBL.dat` (one tab-delimited, backtick-quoted row per legislator
per motion) inside the zip.

**The gate that *was*:** every snapshot is a *full* DB dump — `pubinfo_2025.zip`
is **975 MB**, and even the daily zip is 850 MB. There is no per-vote endpoint, so
the prior probe marked CA "keyless but impractical (can't stage a 1 GB download)".

**How it was cracked (`src/runtime/ca_leginfo_votes.py`):** the server honours
**HTTP range requests** (`Accept-Ranges: bytes`, 206 responses) and a ZIP's
central directory sits at the *end*. Backing `zipfile.ZipFile` with a
range-request reader (`HttpRangeReader`) lets us pull **only**
`BILL_DETAIL_VOTE_TBL.dat` (~3.6 MB compressed) out of the 975 MB archive —
the rest is never downloaded. Grouping member rows by
`(bill_id, location, motion_id)` reconstructs each roll-call; tallies are counted
directly from the member rows (more reliable than joining the summary table).
Output is Track B's rich roll-call shape in `data/real/`.

**Delivered (2025 session, floor-only):** 5,317 roll-calls (assembly + senate),
**~328K member-votes** — the first state in the corpus. Multi-year (2013→2025,
the full 113-119 window) extracts the same way via `--year`; each session-year
archive is a single ranged fetch.

**Track-B-ready format:** votes are emitted as the **4-tuple
`[member, party, "CA", choice]`** matching the Senate/House rich roll-calls, with
`AYE/NOE → yea/nay` normalization, so Track B's existing `build_vote_records`
consumes CA unchanged. Party is joined from `LEGISLATOR_TBL.dat` (extracted from
the *same* zip via one more ranged fetch) by `(surname, house)` — **100% resolved
on 2025** (DEM 247,166 / REP 81,139, 4 unknown). An internal-consistency audit
(member count vs chamber size) caught and fixed a grouping bug where CA reuses a
`motion_id` across separate vote events (split by `motion_seq` + timestamp).

**This technique generalizes** to any large keyless ZIP behind a range-capable
host (it does *not* help the JS/PDF/key-gated states below — those have no bulk
zip at all).

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

**California is shipped** (5,299 floor roll-calls / 328K member-votes for 2025
alone, multi-year extracting via the same path) — the partial-ZIP range trick
turned the "impractical 1 GB download" into a ~3.6 MB ranged fetch. That is the
first state vote source in the corpus.

The remaining target states (NY/TX/FL/IL/PA/OH) have **no bulk zip at all** — they
are API-key-, JS-, or PDF-gated, so the range trick does not reach them. State
*legislators* (rosters) are already ingested keyless (OpenStates people, 7,359
across 50 states). Those vote tiers unblock with any of: `OPENSTATES_API_KEY` (the
adapter shape is identical to House/Senate), a LegiScan key, or per-state
headless-browser / OCR scrapers.

**Decision:** CA delivered keyless; remaining states documented and deferred
(per the v5 directive), prioritizing the keyless, tractable items — Senate
backfill, the bill-edge pass, CREC, and now CA state votes (all shipped).
