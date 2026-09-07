# Psephos Legal

A local legal-information library and read-only MCP server: retrieve published U.S.
legal text, its sources and acquired versions, and real municipal zoning geometry.

This is source retrieval, not a legal opinion, a prediction system, or a claim that
all U.S. law has been collected. The implementation is fresh; it does not import the
earlier Psephos research code or corpus.

## Start small

Requires Python 3.12 or newer. Poppler's `pdftotext` is recommended for Portland PDF
layout extraction; the fallback is explicitly labeled `pypdf layout text`.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/psephos --data demo-data sync uscode --limit 1
.venv/bin/psephos --data demo-data search 'words denoting' --collection uscode
.venv/bin/psephos --data demo-data read '1 USC 1'
.venv/bin/psephos --data demo-data serve
```

The small download is the publisher's Title 1 XML archive plus its index and access
policy, not a private fixture. `--limit` leaves the remaining inventory visibly
pending. `serve` speaks MCP on stdout; progress goes to stderr. Use absolute paths
in your MCP client's configuration, for example:

```json
{
  "mcpServers": {
    "psephos": {
      "command": "/absolute/path/to/psephosamerica/.venv/bin/psephos",
      "args": ["--data", "/absolute/path/to/evidence", "serve"]
    }
  }
}
```

Start with `legal_coverage`, then `legal_search` → `legal_read`. Other tools are
`legal_versions`, `legal_references`, `source_receipt`, and `zoning_at`. Tools have
bounded inputs/outputs and cannot execute SQL, fetch URLs, or read arbitrary files.
Publisher text is untrusted content, never an instruction to the client agent.

## Acquired coverage

Measured corpus; counts are from [the integrity receipt](docs/integrity.json), not
estimates or catalog promises. Source dates are not uniformly current legal effect.

| Collection | Retained retrieval coverage | Publisher clock / important qualification |
| --- | --- | --- |
| U.S. Code | 58 XML documents; 60,469 sections, plus rules, notes and appendix material | OLRC release 119-102, current-through July 12, 2026; Title 53 reserved |
| eCFR | 49 non-reserved titles; 227,518 sections and 4,096 appendices | September 3, 2026 snapshots; additionally January 1, 2024 for Titles 1–3 only |
| D.C. Code | 55 title documents; 24,640 sections | Codified May 19, 2026; publisher recency-through April 29, 2026 |
| D.C. law instruments | 1,583 structured texts; 2,961 metadata-only records | Pinned Council archive; metadata/OCR stubs are **not full law text** |
| Texas | All 31 listed code/constitution exports; 4,993 chapter documents and 138,491 section units | Publisher session-level currency; exact snapshot date unknown |
| NYC Zoning Resolution | 117 chapter/appendix pages; 3,636 sections and 416 section headings | Approved text changes through August 13, 2026; approval is not blanket effectiveness |
| Portland Title 33 | Complete 1,855-page PDF, indexed by page | Edition effective July 1, 2026; later ordinances are not silently consolidated |
| NYC zoning GIS | 16,901 polygons in six publisher layers | Publisher description vintage June 2026; no invented exact day |
| Portland zoning GIS | 15,703 polygons, one accepted whole-layer export | Live acquisition interval, not server-side historical snapshot isolation |

The local raw store contains 287 distinct objects / 1,220,587,392 bytes, including
inventories and verification sources. The derived SQLite catalog is approximately
5.2 GiB. Plan for several GiB of free working space beyond the retained data.

## Acquire, query, verify

```sh
# Full supported inventory: substantial downloads, resumable from the local cache.
.venv/bin/psephos sync uscode ecfr dc texas nyc portland nyc-gis portland-gis
.venv/bin/psephos sync ecfr --as-of 2024-01-01 --limit 3
.venv/bin/psephos status
.venv/bin/psephos zoning -122.6765 45.5231 --collection portland-zoning-gis
.venv/bin/psephos read 'dc-code:§42-3505.01'
.venv/bin/psephos audit > integrity.json
.venv/bin/python scripts/verify_corpus.py --data data --publisher-checks --out verification.json

# Offline, small tests; they do not download the corpus.
.venv/bin/pytest -q
.venv/bin/ruff check src scripts tests
.venv/bin/ruff format --check src scripts tests
.venv/bin/mypy src/psephos
```

Rerunning `sync` reuses rehashed objects. Add `--refresh` to conditionally revalidate
mutable sources. Acquisition respects publisher robots rules, rate delays and
Retry-After; it defers long waits and stops on access restrictions. Each run caps
decoded downloads at 4 GiB; each file at 1 GiB. No API key or paid service is used.
The verification script checks the recorded milestone corpus; future publisher
changes can legitimately require updated expectations. Omit `--publisher-checks`
to verify only already-retained alternate-source receipts, with no network.

`audit` reports storage/source integrity separately from content limitations.
It does **not** certify legal accuracy or make an incomplete inventory complete.
See [verification and limitations](docs/status.md) and [publisher notes](docs/sources.md).

## Clocks and fidelity

- `as_of=YYYY-MM-DD` chooses the latest acquired publisher snapshot on or before
  the date. Unknown snapshot dates are excluded. It does not apply intervening
  amendments or resolve future-effective parallel texts.
- `observation_cutoff` independently limits when the source bytes, or complete
  multi-page GIS cohort, became available. It requires a timezone. Exact immutable
  IDs remain readable after updates but cannot bypass either cutoff.
- Legal effective dates, publisher incorporation/currency dates, HTTP timestamps,
  acquisition clocks and parsing metadata remain distinct.
- Source-native sections retain definitions, exceptions, tables, history and
  references within their source containers. Original bytes and markup remain
  available for fidelity checks. External images have visible omission markers;
  they are not silently transcribed. Some PDF pages have no useful extracted text.
- Point matches include polygon boundaries and overlays. Invalid geometries are
  warned about, not silently repaired. Empty results do not mean an absence of law.

## Small architecture

```text
publisher inventories → bounded HTTP receipts → immutable SHA-256 objects
                                             ↓
                        source-specific parsers → SQLite (FTS5 + RTree)
                                                       ↓
                                             read-only CLI / MCP
```

Jurisdictions own publisher collections; collections contain documents; documents
have acquired versions and source-native provisions. References are publisher
evidence with exact identifier matches where possible. GIS features retain their
layer, properties, geometry, version and acquisition receipt. There is no inferred
precedence graph or geographic-to-code applicability engine.

- `src/psephos/store.py`, `acquire.py`: catalog, receipts and immutable storage.
- `sources.py`, `dc.py`, `texas.py`, `municipal.py`, `geography.py`, `parse.py`: explicit publishers and projections.
- `retrieve.py`, `server.py`, `cli.py`: the query surface.
- `audit.py`, `scripts/verify_corpus.py`, `tests/`: real integrity and focused behavior checks.
- `docs/`: measured receipts and source limitations. `data/` and `tmp/` are ignored.

Code and source-data licensing are separate. No blanket license is asserted over
publisher material; retained access notices and source-specific qualifications are
documented. No third-party corpus bytes are committed. A software license has not
yet been selected for this fresh repository.
