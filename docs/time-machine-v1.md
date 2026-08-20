# American Legislative Time Machine V1

This is the canonical local analytical path for Psephos America. It turns the
already-local federal and state evidence into Parquet tables with a small
DuckDB catalog. It is an evidence substrate, not a UI, graph product,
prediction system, or legal interpretation engine.

The path is deliberately short:

```text
consumed immutable local artifacts -> SHA-256 inventory -> normalized Parquet
                                   -> DuckDB views/time macros -> integrity report
```

Parquet is canonical storage. DuckDB is the query surface. The legacy graph,
GraphRAG, model, explorer, and large CLI paths are not prerequisites.

## Quick start

Run from the repository root with the project environment active. A bounded
one-archive state build is useful for a representative real-data check:

```sh
python -m src.time_machine inventory --state-limit 1
python -m src.time_machine build --state-limit 1
python -m src.time_machine status
python -m src.time_machine integrity
```

Use the same `--state-limit` for inventory and build. Omit it from both commands
to normalize every already-local state-legislature OpenStates session ZIP:

```sh
python -m src.time_machine inventory
python -m src.time_machine build
```

`inventory`, `build`, `status`, `query`, and `integrity` are local-only.
`fetch-text` is the sole network operation. It refuses to fetch until an
inventory exists, downloads only official GovInfo BILLS XML, and refreshes the
inventory afterward:

```sh
# One explicit package
python -m src.time_machine fetch-text --package BILLS-118hr2882ih

# The bounded default slice: introduced and enrolled versions across 113-119
python -m src.time_machine fetch-text

python -m src.time_machine build
```

Fetched bytes are content-addressed and retained under
`data/time_machine/raw/govinfo_bills/`; the manifest records package revisions.
Rerunning the command validates and reuses the cache unless `--refresh` is
given. Existing large source artifacts and OpenStates ZIPs are reused in place,
not copied or downloaded again.

The inventory hashes only artifacts consumed by this canonical build. Unused
large derived or duplicate exports remain untouched and are not hashed merely
because they are present; this includes the roughly 22 GB combined OpenStates
edge JSONL, which the canonical path does not read.

Use `--source-root PATH` and `--output PATH` before the subcommand to override
the defaults. The default output is `data/time_machine`.

## Canonical tables

| Table | Meaning |
|---|---|
| `source_artifacts` | Immutable input inventory: source family, path, URL, hash, bytes, and observation metadata |
| `people`, `person_ids` | People and durable external identifiers; Bioguide is the federal identity spine and LIS is an exact alias |
| `terms` | Federal congressional service intervals from `congress-legislators` |
| `sessions` | Federal Congresses and normalized OpenStates sessions |
| `bills` | Bill identity and metadata, including title, subjects, policy area, and CRS summary where supplied |
| `bill_text_versions` | Genuine cached GovInfo BILLS text versions, or clearly marked state document/version links |
| `actions`, `amendments` | Dated bill actions and defensibly sourced amendments |
| `law_links` | Bill-to-public-law/statute links only when an official identifier supports the link |
| `roll_calls`, `member_votes` | Federal and state roll calls plus member choices |

Every fact row points to a `source_artifact_id` and has fields for its source
family, URL, content hash, and temporal values. A URL remains null when no
defensible upstream URL exists; the integrity report measures those cases. IDs
are joined only through exact official identifiers. The build does not
fuzzy-match names, infer missing OpenStates terms, or fabricate bill/law
relationships. A named state voter without an OCD person ID remains a vote with
a null `person_id` and is counted in the integrity report.

`source_artifact_id` and `content_sha256` always identify the same retained
local artifact. `source_url` may identify the exact official record represented
inside that retained ZIP or JSONL aggregate; it is not necessarily the URL of a
response whose bytes have that hash. A fact/artifact hash mismatch is a hard
integrity failure.

`amendments` and `law_links` may legitimately be empty. V1 leaves them empty
rather than deriving links from titles or prose when the local official inputs
do not contain a defensible identifier.

## Text truth

The three text cases are intentionally different:

- GovInfo `BILLSTATUS` contributes bill metadata: title, CRS policy area,
  legislative subjects, and CRS summary. It is **not bill text**.
- GovInfo `BILLS` XML contributes actual legislative version text. Those rows
  have `is_full_text = true` and nonempty `text_content`.
- Existing OpenStates version rows are official document metadata/links unless
  text was actually acquired. Current normalized link rows have
  `is_full_text = false` and null `text_content`.

The integrity report fails if a row claims full text without text content and
reports federal true-text coverage separately from BILLSTATUS metadata and
state version links.

## Point-in-time semantics

Facts keep separate clocks:

- `event_at`: when the legislative event occurred.
- `available_at`: earliest defensible time the evidence was publicly knowable.
- `observed_at`: when this local installation acquired and hashed the artifact.
- `valid_from`, `valid_to`: the half-open legal/state interval, when applicable.

`tm.<table>_as_of(cutoff)` applies public availability, event time, and the
validity interval. It mechanically excludes rows that were not public yet,
future events, and rows outside their validity interval. When publication time
cannot be established independently, `available_at` falls back conservatively
to local observation time.

```sh
python -m src.time_machine query "
SELECT bill_id, identifier, title, available_at
FROM tm.bills_as_of(TIMESTAMPTZ '2024-01-01 00:00:00+00')
WHERE jurisdiction_id = 'us-congress'
ORDER BY bill_id
LIMIT 20"
```

For a legacy sidecar whose official publication time is unavailable, a cutoff
before local acquisition can therefore return no rows even when its events are
older. That is the conservative result; V1 does not backdate evidence.

`tm.<table>_as_observed(cutoff)` applies the same rules and additionally requires
that this installation had acquired the artifact by the cutoff. Use it to
reproduce local knowledge rather than historical public availability:

```sh
python -m src.time_machine query --format json "
SELECT table_name, count(*) AS records
FROM tm.as_observed(TIMESTAMPTZ '2024-01-01 00:00:00+00')
GROUP BY table_name
ORDER BY table_name"
```

`tm.as_of(cutoff)` and `tm.as_observed(cutoff)` provide the same filtering over
a compact cross-table fact index.

## Outputs and measured status

The output directory contains:

- `inventory.json` — exact consumed artifacts, hashes, sizes, and source URLs.
- `parquet/*.parquet` — canonical tables.
- `time_machine.duckdb` — views and point-in-time table macros.
- `build.json` — input digest, configuration, and table row counts.
- `integrity.json` and `integrity.md` — machine-readable and human-readable
  measured reports.

Run `status` or read the generated integrity report for current counts. Counts
depend on the inventoried corpus and build scope.

The full local verification on 2026-08-20 used input digest
`f3f1791cc2232fcc6b32f22d846d606eedbdc844b13b6989d84698cd038b2163` and
published these measured rows:

| Table | Rows |
|---|---:|
| `source_artifacts` | 697 |
| `people` | 25,234 |
| `person_ids` | 110,879 |
| `terms` | 45,532 |
| `sessions` | 684 |
| `bills` | 1,601,450 |
| `bill_text_versions` | 3,461,312 |
| `actions` | 13,097,175 |
| `roll_calls` | 1,211,109 |
| `member_votes` | 55,161,451 |

That run normalized all 677 state/territory archives selected from the 680
local OpenStates ZIPs; the three U.S.-Congress ZIPs remain inventoried but are
superseded by the federal path. It included 106,592 BILLSTATUS metadata rows
and six genuine GPO BILLS/USLM text versions. A second identical build returned
`unchanged`. All duplicate-key, temporal-order, foreign-key, artifact-hash,
false-full-text, and unsupported-law-link hard failures were zero.

The repository's existing data tree is roughly 77 GB. V1 leaves it in place and
hashes the 18.7 GB of exact artifacts consumed by this canonical build. Large
legacy derivatives such as the redundant combined OpenStates edge export, and
unrelated product corpora, are not copied, redownloaded, or falsely reported as
inputs.

The integrity report covers row counts and time ranges, source-family bytes,
duplicate keys, broken source/relational keys, unmatched person and bill IDs,
missing URLs or hashes, temporal violations, OpenStates manifest collisions,
public-law identifier discipline, and text-version coverage. A build is
published only after its hard integrity invariants pass. A normalization or
hard-check failure leaves the staged build for diagnosis and is not published.

## Current boundaries

- Federal coverage is the modern 113th-through-present slice present in the
  local BILLSTATUS and roll-call artifacts. The exact generated report is the
  authority on the current maximum Congress and dates.
- The legacy House feed contains bill-linked roll calls only, so procedural
  House rolls discarded upstream cannot be reconstructed here. Senate
  procedural rolls are retained when present, with a null bill link.
- `actions` currently contains the official state rows retained in the
  OpenStates ZIPs. The local BILLSTATUS dossier exports did not retain federal
  action or amendment records, so V1 does not reconstruct them from votes or
  prose; `amendments` is empty and reported as such.
- State normalization reads existing OpenStates ZIPs directly and makes exact
  OCD-ID joins. It does not redownload or unpack them to a second raw corpus.
- The three OpenStates U.S.-Congress snapshots are inventoried but not
  normalized because the dedicated federal path supersedes them.
- State bill-version links are not silently treated as downloaded text.
- Public-law and statute links are emitted only from defensible official IDs;
  absence is represented as absence.
- OpenStates source dates are preserved verbatim, including obvious year
  outliers. The integrity report exposes their ranges, and cutoff macros exclude
  future-dated rows; V1 does not guess corrected dates.
