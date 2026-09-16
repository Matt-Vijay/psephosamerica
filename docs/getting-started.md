# Getting started

Psephos runs against a local evidence store. A checkout contains software and
source metadata, not the raw legal corpus. The [v0.1.0 release](https://github.com/Matt-Vijay/psephosamerica/releases/tag/v0.1.0)
provides a U.S. Code starter. There is no hosted MCP endpoint.

## Install

Use Python 3.12 on macOS or Linux. From a fresh checkout:

```sh
git clone https://github.com/Matt-Vijay/psephosamerica.git
cd psephosamerica
python3.12 -m venv .venv
.venv/bin/python -m pip install -c requirements.lock .
.venv/bin/psephos --help
```

The constraints retain an exercised Python 3.12 dependency resolution, not a
blind upgrade. They do not lock build tooling or every platform-specific wheel.
The normal install does not need API keys, a model service, or Poppler. The
separate Mississippi replay profile has additional requirements.

## Add data

Replace the example paths with writable **absolute paths**. Use a new directory
for an import; do not point it at an existing store.

```sh
DATA=/absolute/path/to/psephos-data
.venv/bin/psephos sources
.venv/bin/psephos --data "$DATA" import /absolute/path/to/snapshot.zip
.venv/bin/psephos --data "$DATA" status
```

Download [psephos-uscode-119-102.zip](https://github.com/Matt-Vijay/psephosamerica/releases/download/v0.1.0/psephos-uscode-119-102.zip)
and substitute its absolute path above. The 284 MiB starter contains 58 documents,
63,475 mixed section/context records and their original source bytes, not the
entire local corpus. Its [scope and checksum](starter.md) identify the retained
July 12, 2026 edition. Allow about 4 GiB of free space for download and import.

Import accepts a Psephos bundle, not an arbitrary publisher ZIP. The D.C. source
ZIP and other collections are excluded from this download; see [data notices](data-notices.md).

Alternatively, create a small store directly from the official publisher:

```sh
DATA=/absolute/path/to/psephos-small-data
.venv/bin/psephos --data "$DATA" sync uscode --limit 1
.venv/bin/psephos --data "$DATA" status --collection uscode
.venv/bin/psephos --data "$DATA" search 'definition' --collection uscode --limit 3
.venv/bin/psephos --data "$DATA" status --view documents --collection uscode --limit 3
```

The example requests one non-reserved U.S. Code title archive, plus publisher
inventory/policy requests. It is not a one-request or one-section limit. Other
adapters can download a whole source archive before limiting indexing; do not
assume `--limit` caps bytes. No live collection is needed for installation or CI.
Publisher availability and access policies can stop acquisition. Inspect status
for pending or failed items even when a command completes.

Use a returned document's `first_key` or a search result ID with
`psephos --data "$DATA" read KEY`. Raw bytes and the SQLite catalog stay in the
chosen data directory; `data/` is ignored by Git. A current download does not
reproduce a historical local collection or certify current/effective law.

## Connect an MCP client

For clients using an `mcpServers` JSON configuration, add:

```json
{
  "mcpServers": {
    "psephos": {
      "command": "/absolute/path/to/psephosamerica/.venv/bin/psephos",
      "args": ["--data", "/absolute/path/to/psephos-data", "serve"]
    }
  }
}
```

Use the installed executable and populated store's actual absolute paths; JSON
does not expand `~` or shell variables. This is a local **stdio** process, not an
HTTP URL. Reading tools do not acquire data or mutate the store. Start with
`legal_coverage`, then `legal_search` and `legal_read`; retain returned source URLs,
snapshot dates, and omission warnings when citing results. Your MCP client or
model provider may receive the text returned by tools.

## Portable snapshots

To move selected local collections between machines, using a store containing
both collections in this example:

```sh
.venv/bin/psephos --data /absolute/path/to/existing-store export /absolute/path/to/snapshot.zip \
  --collection uscode --collection dc-code
.venv/bin/psephos --data /absolute/path/to/new-data import /absolute/path/to/snapshot.zip \
  --max-gib 32
```

Repeat `--collection ID` to include several exact catalog collection IDs; these
are not necessarily `sync` aliases. Omit collections absent from your store.
Import refuses even an empty existing data directory; `--max-gib` optionally sets
the expanded archive size ceiling (32 GiB by default). Allow space for the local
database and temporary import files too. Bundles
use a fixed JSONL catalog and SHA-256-addressed objects inside ZIP, not executable
code or a supplied SQL database. Export preserves source identities and dates
while sanitizing HTTP receipt headers; import verifies manifest/object sizes and
hashes. Checksums establish integrity, not publisher endorsement or legal currency.
Review [data notices](data-notices.md) before sharing any bundle.

Exports exclude private campaign budget files. Bundles are portable read
snapshots, not transfers of an operator's campaign runtime, state, or download
allowances. Fresh acquisition is a separate operation with its own source-access
policies and budgets; importing a snapshot does not resume the original campaign.

For an operator backup, stop collectors and close writable store processes before
copying the entire data directory to separate storage. Keep the SQLite catalog,
content-addressed objects, campaign budgets and review evidence together. The
Git repository and public starter are not backups of your larger working corpus.
A second copy on the same disk protects against accidental edits, not disk loss.

## Support and limits

[Automatic refresh](refresh.md) can schedule the current U.S. Code, eCFR, D.C.
and the reviewed Portland guidance pages, with bounded downloads and visible
failure/overdue status. Other retained collections remain explicitly unscheduled.
This feature is in the current checkout, not the original v0.1.0 wheel.

- **Fresh acquisition:** `psephos sources` lists the maintained source commands
  and their scope. Having an adapter does not mean a complete state is available.
- **Imported archives:** readable accepted snapshots can contain collections
  without a maintained refresh command. The [archive catalog](../replay/catalog.json)
  records source-specific scope, dates, terms, and implementation gaps.
- **Offline replay:** [Florida, New Jersey, and Mississippi](../replay/README.md)
  have maintained retained-byte replay profiles. Other preserved collector code
  is provenance, not executable refresh support.

Snapshot, observation, publication, and effective dates are different clocks;
unknown dates remain unknown. Missing results, missing media, and geography
matches do not establish legal applicability or an absence of restrictions.

For local development, install with
`.venv/bin/python -m pip install -c requirements.lock '.[dev]'`, then run
`.venv/bin/ruff check .` and
`.venv/bin/pytest -q -k 'not test_real_197_fidelity_when_payload_present'`.
These checks use fixtures, not a corpus download or exhaustive source audit.
