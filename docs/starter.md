# U.S. Code starter

[Download v0.1.0](https://github.com/Matt-Vijay/psephosamerica/releases/tag/v0.1.0).
Import instructions and MCP configuration are in [getting started](getting-started.md).

| Property | Value |
| --- | --- |
| File | `psephos-uscode-119-102.zip` |
| Bytes | 297,501,313 |
| SHA-256 | `34232fe0420dc16cc0b4fc191a642f9c34e02deab4491ff5990fcc48b21b3db3` |
| Collection | `uscode` only |
| Publisher edition | OLRC release 119-102, July 12, 2026 |
| Exported | September 16, 2026 |
| Documents / versions | 58 / 58 |
| Retrieval records | 63,475 mixed sections and context units, not distinct laws |
| References | 1,093,160 retained links, not guaranteed resolved targets |
| Inventory records | 59, including reserved-title inventory |
| Source artifacts / receipts | 2 / 2 |
| Geography | No geometry included |

The original source artifacts are:

- [OLRC download index](https://uscode.house.gov/download/download.shtml), 83,561 bytes,
  SHA-256 `190a227afe0fff10944e0e600a15f14f4b16e59fef34d435e73456245e96b98b`.
- [All-title XML archive](https://uscode.house.gov/download/releasepoints/us/pl/119/102/xml_uscAll@119-102.zip),
  108,610,077 bytes, SHA-256 `55c8d19543c4a972a33e33532b592ac3984c83fdcb04de9f5a64ef1f8483d300`.
  It contains 58 XML files, with no other file types.

The catalog is 1,545,301,545 uncompressed bytes, SHA-256
`130c5685b738f9beebd839ae838c26006404ba4c4c972d34fbea3a337492e8ce`.
`manifest.json` inside the bundle records these checksums and table counts.
The importer verifies the catalog, original objects and source relationships
before publishing a new store. No supplied SQL or code is executed.

This is a dated federal snapshot, not current law or the broader local catalog.
Existing source clocks and their disclosed initial-observation migration basis
remain unchanged. Source text can refer to external media and other laws not
included here. See [data notices](data-notices.md); Psephos is not an official
publisher. No model provider, API key or network acquisition is required to read
the imported starter locally. A connected model service has its own data policy.

Verification used a clean Python 3.12 environment outside the checkout: installed
wheel, full starter import, all nine MCP tools discovered, and eight actual stdio
calls covering search, reading, references, versions, source scope and date
exclusion. This is an installation and retrieval check, not a legal accuracy score.
