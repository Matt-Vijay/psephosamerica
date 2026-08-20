# govinfo BILLSTATUS metadata ingest — the Track B data unlock

**Historical legacy-run record:** the figures below describe the 106,536-row
graph/embedding contract used by the recorded experiments. The current local
`bill_content_full.jsonl` contains 106,592 BILLSTATUS metadata/dossier rows;
the Time Machine integrity report is authoritative for the canonical build.

Track B proved on real data that its cross-pressured/defection ceiling was a
*data* problem: the corpus had 19 bills and 0 that were vote-linkable, so
bill-embedding retrieval (RAG) gave ΔAUC = 0 — the sector-bag stand-in collapsed
every same-sector bill to one point. This ingest fixes that at the source.

## What was ingested

Keyless **govinfo bulk** (`www.govinfo.gov/bulkdata/BILLSTATUS/<congress>/<type>`,
no API key), every House + Senate bill and resolution for **congresses 113–119**:
title, CRS **policy area** + **legislative subjects**, sponsors, cosponsors,
committees, and the CRS **summary** text.

This legacy ingest did **not** fetch GovInfo's `BILLS` collection or any actual
bill-text version package. Its derived `text` field is a search/embedding dossier
assembled from the title, policy area, subjects, and CRS summary; it must not be
interpreted as statutory or legislative text.

| Congress | Bills | Congress | Bills |
|---|---|---|---|
| 113 | 10,637 | 117 | 17,828 |
| 114 | 12,063 | 118 | 19,315 |
| 115 | 13,556 | 119 | 16,536 |
| 116 | 16,601 | **Total** | **106,536** |

The background run fetched all **106,536** bills (8 bill/resolution types per
chamber), **0 skipped** — every BILLSTATUS file parsed.

## Why the bills are vote-linkable

Each bill's canonical id is minted by the **same** `BillRef` the House/Senate
roll-call adapters resolve their votes to (`congress` + `type` + `number` →
`hr-1` → `cb-<digest>`). So a govinfo bill is, by construction, already the
`dst_id` of its `vote` edges — no fuzzy matching. Every bill embeds its assembled
BILLSTATUS dossier to a 256-d point, so cosine retrieval can discriminate beyond
the earlier sector-only stand-in.

## Delivered to the watched corpus

`merge_bill_corpus_into_main` folded the bills into the corpus Track B's
hot-swap watcher reads (`data/exports/contract_records/`): persons preserved,
the 19 pre-existing bills superseded, the rest added — with a delta-CDC feed for
the hot-swap.

```
contract corpus: 7,918 -> 114,435 rows  (7,899 persons + 106,536 dense bills)
delta-CDC: 100,136 `created` bill deltas appended
```

## Linkage report (the >95% success metric)

Measured on a real sample of 2023 House roll-calls (rolls 1–120, 52,401
member-votes), checking each vote's `BillRef.canonical_id` against the 106,536
embedded bills:

| metric | value |
|---|---|
| substantive (bill-bearing) member-votes | 43,720 (83.4% of all) |
| **linked to an embedded bill** | **43,720 — 100.0% of substantive** |
| unlinked | the 16.6% procedural votes (quorum calls, motions) — no bill to link |

**100% of substantive votes resolve to an embedded bill.** The only unlinked
votes are procedural ones with no bill, so the >95% target is met for every vote
that *has* a bill.

## Edges available from the same records

`src/graph/ingest/billstatus_edges.py` turns each parsed bill into:

- `bill → policy_area` + `bill → legislative_subject` — a real CRS topic for
  **every** bill (kills the ~10% sector-coverage gap), which also flows into the
  bill's dossier context to enrich its embedding.
- `bill → committee` referral edges (canonical `CommitteeRef`).
- sponsor / cosponsor `member → bill` edges (via a bioguide → canonical resolver).

## Reproduce

```
python -m src.runtime.govinfo_bills_run --congresses 113-119 \
    --directory data/exports/govinfo_bills --max-fetch 400      # resumable
python -c "from datetime import UTC, datetime; \
  from src.runtime.bill_corpus_merge import merge_bill_corpus_into_main; \
  merge_bill_corpus_into_main(main_directory='data/exports/contract_records', \
  bills_directory='data/exports/govinfo_bills', as_of=datetime.now(UTC))"
```
