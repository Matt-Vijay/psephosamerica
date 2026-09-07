# Accepted-source replay

Rebuild three accepted source projections from retained bytes, without their
collector workspaces, source databases, live websites, or today's parser helpers.
The other accepted collector implementations are preserved as inert provenance,
not advertised as a working refresh system.

## Run from a fresh checkout

Use a full Git checkout containing historical commit
`d4f7796724f8718d86dfa3d2beaa3de09f9300d7`. Replay refuses missing historical
objects; it never fetches them. Python **3.12**, `lxml==6.1.3`, and `pypdf==6.17.0`
are the exercised profile. Mississippi also requires **Poppler 26.02.0**
(`pdftotext` on PATH). The core project's other dependencies are unchanged.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e . 'lxml==6.1.3' 'pypdf==6.17.0'

# OBJECTS is a retained content-addressed directory: <sha[:2]>/<sha>.
# It can be the existing data/objects or an independently retained copy.
OBJECTS=/path/to/retained/objects
RUN=$(mktemp -d)
.venv/bin/python replay/replay.py fl --objects "$OBJECTS" --out "$RUN/fl"
.venv/bin/python replay/replay.py nj --objects "$OBJECTS" --out "$RUN/nj"
.venv/bin/python replay/replay.py ms --objects "$OBJECTS" --out "$RUN/ms" \
  --ms-review /path/to/retained/sparse_review.json

# These are ordinary Stores, readable by the existing read-only surface.
.venv/bin/psephos --data "$RUN/fl" read 'fl:stat/1.01'
```

The Mississippi review file must hash to
`a70000966331e4e2d9af15fb6de14f457e958f2393050631f590ed5807f5f249`
(4,265 bytes). It is accepted visual-review evidence, **not executable code**.
In the existing retention tree it is
`data/collectors/mississippi/checks/sparse_review.json`. Retain it with the PDFs;
PDF bytes alone do not reproduce the accepted sparse-page annotations. Its old
render-path strings are metadata labels only; replay never opens those paths.

`--limit 3` bounds document work in stable recipe order. Rerun the same command
without the limit to finish, or rerun a completed output to verify idempotence.
Completed documents are **reparsed and compared**, not merely skipped by ID.
All receipt objects are still required/rehashed even with a document limit.

There are no HTTP requests or fresh source observation times. The Python worker
rejects socket use; Git lazy fetching/transports are disabled. This is reviewed
offline code, not a security sandbox for arbitrary hostile executables. Only
hash-pinned repository core code and the three maintained modules execute—never
the inert archive, source files, caller-supplied modules, or MCP input.

## What is reproduced

| Profile | Accepted scope | Documents | Units | References |
| --- | --- | ---: | ---: | ---: |
| Florida | Constitution and 52 chapters in seven selected 2026 titles | 53 | 2,045 | 268 |
| New Jersey | 71 TXT/RTF statute volumes plus Constitution | 72 | 56,375 | 0 |
| Mississippi | 24 statewide court-rule PDF volumes, 1,324 pages | 24 | 24 | 12 |

These are mixed retrieval units, not counts of operative laws. No source refresh
or broader state completeness is claimed. New Jersey's statute text ends at
P.L.2025 c.405/J.R.22; its newer table of contents is not a newer statute body.

The [execution receipt](verification.json) records exact hashes, counts, versions,
commands and bounded verification. All 149 documents / 58,444 units / 280
references are compared on key, order, parent, kind, citation, heading, text,
markup, URL, metadata and reference evidence. Version/provision IDs, source
hashes, receipt relationships, and every retained clock are checked separately.
Physical SQLite rowids are not identities.

Recipes include 119 accepted receipt artifacts, 67,183,787 bytes, including
inventory/policy evidence. Raw bytes stay out of Git. Six Mississippi receipts
contained `set-cookie`: portable recipes omit that header and record its original
header-map hash and redacted key. Original private receipts remain unchanged.
This is disclosed sanitization, not a claim of byte-identical HTTP header maps.

### Historical projection, not relabeling

The fixed Git revision supplies the exact old `Store`, `Provision`/`Reference`
models and `parse.py` helpers in a temporary process-local package. Accepted
parser labels remain `/text-2`; current Psephos continues to use `/text-3`.
Nothing monkeypatches the running server, replaces canonical versions, or changes
the public API. Newer readable/list/media behavior is not run under old labels.

Two observed historical defects need explicit, narrow restoration:

- New Jersey's `nj-statutes:THE` was accepted as `source_block`, though the saved
  adapter emits `disposition_note`. One overlay is guarded by enclosing archive
  and member hashes, document/key, byte range, text hash and original kind. It
  changes no wording and does not generalize to another export.
- Sixteen Mississippi volumes retained pypdf's process-specific reader address
  in annotation representations. Per-artifact profiles restore only that third
  `IndirectObject` integer, preserving PDF object/generation identity. Twenty-three
  profiles preserve historically absent default metadata fields. Neither mechanism
  can replace legal text, hide nondefault gaps, or alter reference destinations.

Unknown snapshot/publication/effective dates stay NULL. Edition labels, archive
mtime, acquisition/availability clocks and legal effect remain distinct. Exact
same bytes do not make a collection current law. Existing Reader cutoff behavior
still excludes undated snapshots from date-based `as_of` selection.

## Maintained code versus frozen evidence

- `replay.py`: bounded offline CLI, fixed runtime, receipt loading, field hashes,
  safe output ownership, exact comparison, and resumable Store ingestion.
- `fl_nj.py`, `mississippi.py`: reviewed pure projections, no collectors.
- `recipes/*.json`: pinned metadata/receipt inputs and expected hashes; no
  provision bodies or output databases. Canonical receipt IDs come from the
  original publication ledgers, including nested metadata references.
- `seal.py`: maintainer-only recipe derivation from accepted source Stores. It
  checks acceptance/ledger hashes and source logical identity before sealing.
- `catalog.json`, `provenance/`: all **45 actual published batches** from the
  frozen summary. **Three** have maintained offline replay; **42** are provenance
  only here, including separately identified existing native core adapters.

The archive retains 49 deduplicated inert code blobs (635,060 bytes / 10,153
physical lines) and references ten already committed blobs by exact Git revision
(134,030 bytes / 3,131 lines). That is preservation, not 59 new runtime modules.
Tests, finalizers, logs, rejected candidates and unrelated probes are excluded.
Two probe/discovery-named files stay because their acquisition classes are actual
imports. Three helper files lacked original acceptance hashes and are labeled
**currently observed**, never retrospectively acceptance-attested.

Maintained code is three runtime files / 1,013 physical lines plus one 238-line
recipe sealer. It uses the existing Store/Reader contracts, two existing Python
libraries, Git and the pinned PDF extractor; no package dependency was added.

Per-batch source scope, parser identity, terms, dependencies and known gaps are in
the catalog. Public access is not a blanket redistribution license. No publisher
contact or new license interpretation was performed. Raw source retention remains
subject to the original collection's notices and restrictions.

## Maintenance and limits

An output must be empty or owned by this exact pinned recipe. Existing Stores and
their subdirectories are refused; writable symlinks are refused. Artifacts are
rehashed and hard-linked when possible (copied across filesystems). Treat both
input and output objects as immutable: modifying a hard-linked object would also
modify its retained counterpart. Parsing/fingerprint failure publishes no version
for that document; completed documents survive for resumption. Failed attempts
can leave receipt objects and an owned scratch Store, never a canonical update.

To reproduce the metadata recipes themselves, the original accepted source Stores
and publication files—not just raw bytes—must also be retained:

```sh
.venv/bin/python replay/seal.py --out /path/to/new-empty-recipe-directory
.venv/bin/pytest -q tests/test_accepted_replay.py
.venv/bin/ruff check replay/*.py tests/test_accepted_replay.py
.venv/bin/ruff format --check replay/*.py tests/test_accepted_replay.py
.venv/bin/mypy replay/replay.py replay/fl_nj.py replay/mississippi.py replay/seal.py
```

Sealing never overwrites recipes. Replacing a source/parser/profile requires a
new reviewed receipt and deliberate recipe-hash update, not passing an arbitrary
manifest to replay. Future refresh/reprojection is not implemented for these
three adapters. Existing core acquisition commands are unchanged and were not
retested here.

The 42 provenance-only batches still need source-specific pure entrypoints and
real replay before claiming support. Known blockers include WI supporting-page
classifications, NM selection metadata, RI finalized metadata and historical
PDF tool/serialization behavior; their exact receipts/gaps are cataloged. This
checkpoint preserves the implementation evidence but does **not** claim that
every accepted document can yet be regenerated from raw bytes alone.
