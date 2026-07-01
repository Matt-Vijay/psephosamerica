# ADR 0002: Artifact-first immutable publishing

Status: accepted

## Context

Psephos America must be auditable and stable: a member or evidence card a reader sees
today must be reproducible and unchanged tomorrow, even as the canonical database
is recomputed weekly. Serving the public directly from canonical Postgres tables
would couple page output to in-flight recomputes and make "what did the site say
on date X" unanswerable.

## Decision

Separate the canonical store from the read path.

1. **Canonical Postgres is the source of truth** for members, votes, disclosures,
   rule fires, evidence cards, and score snapshots — but it is never read directly
   by the public product.
2. **Publish frozen snapshots.** `recompute` writes canonical rows; `publish`
   exports a dated, immutable snapshot tree (per-member profiles, ZIP feeds,
   evidence cards) plus a `SnapshotManifest` carrying a SHA-256 for every file and
   a root hash over the manifest.
3. **The read API serves only precomputed artifacts** (`src/api/http.py`,
   `read_service.py`) over the published tree, with manifest-derived ETags.
4. **Immutability is enforced and tested.** `verify-publish` recomputes every
   file hash and the root hash to detect tampering; `verify-publish-roundtrip`
   re-derives artifacts from the database and confirms they match what was
   published. Raw source files are likewise immutable.

## Consequences

- Public output is decomposable, inspectable, deterministic, and frozen — the v1
  product principles — and any post-publication change to a released snapshot is
  detectable.
- Corrections are made at the data/mapping layer and surfaced as a *new* dated
  snapshot + changelog entry, never by editing a released one.
- The read path scales independently of recompute and can be served from object
  storage / CDN rather than the database.
- This contract is exercised end-to-end by the integration suite
  (`tests/integration/test_recompute_publish_integration.py`,
  `test_roundtrip_verify_integration.py`) against real Postgres, including a
  tampered-file detection test.
