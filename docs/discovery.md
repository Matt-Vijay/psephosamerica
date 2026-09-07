# Find the relevant acquired sources first

`legal_coverage` is now a bounded source directory, not a national aggregation of
every stored provision. It keeps one tool and the existing Store/Reader/CLI.
No schema change, materialized cache, new collector or replay dependency is involved.

If the exact jurisdiction or collection is known, start there directly:

```text
legal_coverage(jurisdiction="us-ny-nyc")
legal_coverage(collection="nyc-zoning", view="documents", limit=2)
legal_search(query="qualifying residential site", collection="nyc-zoning", limit=3)
legal_read(key_or_id="nyc-zr:12-10")
```

If it is unknown, `legal_coverage()` returns ten registered jurisdiction records,
with names, exact IDs and numbers of registered collections. Follow `next_offset`
with the same filters to continue. Registration is not acquired legal text or a
claim that all law in the jurisdiction is present. IDs are exact; a state filter
does not silently include cities, federal sources, neighboring states or other
potentially applicable law.

## Four small views

| View | Required scope | What it returns |
| --- | --- | --- |
| `jurisdictions` | None; optional exact jurisdiction | Registered source scopes and collection counts |
| `collections` | Optional exact jurisdiction/collection | Source cards, acquired-document count, scope/currency/notices and mixed inventory status counts |
| `documents` | Exact collection | Latest acquired version per document, clocks, artifact/receipt IDs and `first_key` for `legal_read` |
| `inventory` | Exact collection | Original item IDs, source URLs, statuses/errors; optional exact `status` filter |

Omitting `view` chooses jurisdictions when no scope is supplied, collections when
one is. Pages default to ten rows, at most twenty, and at most 24 KiB of structured
JSON. A byte-limited page may return fewer rows; follow the returned offset, not
`offset + limit`. An oversized individual metadata record is explicitly warned
about, not silently treated as known complete scope. Pages query live metadata,
so concurrent collection changes can shift offset pagination.
An individual entry too large to return is explicitly skipped with
`skipped_entry_offset`; `next_offset` still reaches later records. A skipped entry
is a disclosure of missing output, not an empty source.

No national provision/geometry aggregation runs on the starting or collection
views. Document drill-down reads only the first unit of each displayed version
through its version/order index. This is an entry point, often a heading, context
note or PDF page—not a promise that one unit contains the whole document. Search
within the returned collection to find a particular section.

Collection metadata now preserves scope, publisher notices, omissions and currency
instead of an allowlist that lost those fields. Three bulky machine structures
(`layer_metadata`, `portal`, `title_inventory`) are omitted explicitly; source
artifact references and warning fields remain. Discovery no longer returns
national raw-byte, provision-kind or polygon totals; maintenance audits serve that
separate purpose.
`sync` reports only the requested adapters' exact catalog collections, with an
explicit status for each; `audit` reports its measured `corpus` counts instead of
embedding a paginated discovery page. Neither change alters acquisition or audit
work. The maintenance outputs were checked on offline fixtures, not a live resync
or another whole-corpus audit.

## Historical partial-state example

```text
legal_coverage(jurisdiction="us-fl")
legal_coverage(collection="florida-statutes-2026", view="inventory", status="pending", limit=5)
legal_coverage(collection="florida-statutes-2026", view="documents", limit=2)
legal_read(key_or_id="fl:stat/1.01")
```

At the discovery checkpoint, the Florida card stated which seven titles were accepted: I, II, III, IX,
XI, XII and XIII. Its 101 inventory items mix 49 title indexes with 52 chapter
bodies. **59 indexed items means seven title indexes plus 52 documents**, not 59
legal documents or statewide completeness; 42 title indexes remain pending.
The 2026 edition label supplies no invented exact snapshot/effective date.
This is a frozen measurement, not the live Florida denominator. The subsequent
[Florida edition import](florida-edition.md) expands actual publisher-listed chapter
membership; the old review remains nested as `historical_discovery_review` and the
new `discovery_review` reports current coverage. Query the live card and inventory
for any pending or failed chapters.

Mississippi's existing card now exposes its original scope: 24 statewide court-rule
volumes, not local rules or all Mississippi law. New Jersey's annotation separates
the statute body through P.L.2025 c.405/J.R.22 from the newer TOC through
P.L.2026 c.30/JR1. The TOC does not update the body.

Florida and New Jersey have small additive **current collection annotations**,
reviewed on September 7, 2026 UTC. [The patch/evidence receipt](discovery-annotations.json)
retains before-metadata hashes, accepted batch digests and exact source receipt
IDs. These frozen descriptions are labeled as such; live document/inventory
listings are separate. Only `collections.metadata` changed. Raw data, acquisition
rows, versions, provisions and original acceptance/publication ledgers did not.
Historical replay correctly keeps its earlier metadata, without these later notes.

The historical guarded operator command is idempotent at its own checkpoint and
refuses changed metadata/receipts (including the later Florida scope expansion):

```sh
.venv/bin/python scripts/annotate_discovery.py --data data
```

It is not an MCP mutation tool. Do not rewrite historical ledgers to conceal a
legitimate current-metadata addendum or a future publication conflict.

## Empty and unknown are explicit

- Unknown IDs return `unknown_jurisdiction` or `unknown_collection`, with no rows.
- Conflicting exact scopes return `scope_mismatch`; they never broaden the query.
- A known collection with no acquired documents returns `empty` and its source
  card. Beyond the end of a nonempty result, the status is `page_exhausted`.
- An unmatched inventory status yields `no_inventory_items_with_status`.
- Blank identifiers, unsupported views, missing required collection scopes and
  invalid limits are errors—not national fallback queries.

None of these outcomes means absence of legal restrictions. Discovery is current
catalog navigation, not a point-in-time law opinion; apply the existing independent
snapshot/observation cutoffs when reading sources.

## Receipt headers stay private where needed

`source_receipt` and `psephos receipt` apply the acquisition `SAFE_HEADERS` policy
again when returning stored headers, including imported collector receipts. Header
names are matched case-insensitively and emitted in lowercase. The known internal
`psephos_request_started_at` and `psephos_observed_at_basis` clocks are preserved.
Other values—including cookies and authorization—are omitted; only omitted names
and the projection policy are disclosed. Stored headers, raw artifacts and frozen
replay receipts are unchanged. The focused proof checks actual receipt 6037 for
omission and exact before/after stored-row equality without publishing its values.

## Measured workflow and reproduction

The [focused receipt](discovery-verification.json) records twenty-one real stdio MCP
calls in 1.305 seconds, including startup. One observed run—not a latency
distribution—returned:

| Call | Structured JSON | Local MCP duration |
| --- | ---: | ---: |
| Default jurisdiction page | 1,893 bytes | 8.294 ms |
| NYC exact jurisdiction | 2,847 bytes | 17.463 ms |
| Florida exact jurisdiction | 3,904 bytes | 5.629 ms |
| Florida pending inventory, two rows | 3,780 bytes | 3.104 ms |

The separately reported old unfiltered Reader measurement was 92,232 bytes and
32.699 seconds. It was not repeated. These are different operations and individual
observations, not a controlled before/after benchmark. The receipt also reports
full MCP-envelope byte sizes, source navigation, retained-receipt agreement,
unknown/empty cases, and an undated Florida read correctly excluded by `as_of`.

```sh
.venv/bin/psephos --data data status --jurisdiction us-fl
.venv/bin/psephos --data data status --view inventory --collection florida-statutes-2026 --status pending --limit 5
.venv/bin/python scripts/verify_discovery.py --data data --out /path/to/new-receipt.json
.venv/bin/pytest -q tests/test_discovery.py tests/test_store_retrieval.py tests/test_geography_mcp.py tests/test_sync_audit_summary.py
```

Verification targets the accepted corpus and annotations at this discovery
checkpoint; it is not a generic benchmark. It never fetches data or runs the earlier full-corpus/replay
campaigns. Other collections can still have incomplete scope metadata: cards say
when it is not explicitly described, and counts do not fill that evidentiary gap.
The recorded Florida expectations belong to this discovery checkpoint; do not
rerun its mutating annotation step against a later expanded catalog or rewrite its
frozen receipt. Use the Florida-specific verifier for that later work.
