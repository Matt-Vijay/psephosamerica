# New Mexico roll-reassociation measurement fixture

This is a bounded, row-faithful **prototype**, not a task schema, expected answer,
or repaired output. It retains both conflicting source claims for
`ocd-vote/a5a38c48-675e-4dc9-bb67-c06337a2bce8`; it does not choose which SB 5
association is historically authoritative.

The files below `input/` are clearly labeled derived fixture bytes in
`input/SOURCE.json`. Each CSV consists of its parent member's exact header record
and exact selected data records in their original order. The two `README` files
are copied verbatim from their respective ZIP members. No parent ZIP is committed.
The 18 extracted files total 19,259 bytes and have canonical extract SHA-256
`c54606d1adc5ce8bef87a302641b14ed0766e71eb679fe6d093d1df152917136`.

Build and audit from the repository root:

```sh
.venv/bin/python -m src.incident_lab.prototype.nm_reassociation_fixture audit \
  --source-root /Users/matthew/Documents/Coding/psephosamerica/data/raw/openstates_bulk

NM_REBUILD_DIR="$(mktemp -d)/input"
.venv/bin/python -m src.incident_lab.prototype.nm_reassociation_fixture build \
  --output "$NM_REBUILD_DIR"
```

The builder verifies both parent ZIP size/hash pairs and the pinned URL manifest,
refuses to overwrite an existing destination, filters original record byte spans,
and writes through a temporary sibling directory. The auditor enforces a closed
file allow-list, the public canonical extract digest, source-member receipts,
relationship counts, and the measured cross-session voter topology.
