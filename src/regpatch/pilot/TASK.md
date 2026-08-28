# Psephos RegPatch task contract

Produce the successor eCFR XML state from a historical eCFR XML base and the
ordered Federal Register rule XML documents supplied with the episode.

Your submission is either one `solution.ts`/`solution.js` file or a directory
with exactly one of those names at its root. Local modules are allowed. The
runner invokes it twice in fresh workspaces, sends one JSON request on standard
input, and permits reads only from the frozen submission, `manifest.json`, and
the listed inputs. It permits writes only below `output/`; network, environment,
process, system, FFI, package, and remote-import access are denied.

The request contains:

- `episode_id`;
- `manifest_path`;
- `base.path`, SHA-256, and byte count;
- `rules`, ordered by `order`, each with a path, document number, SHA-256, and
  byte count; and
- `output.result_path` plus `output.provenance_path`.

Write the complete resulting eCFR XML document to `result_path`. Write JSON to
`provenance_path` with exactly these required values:

```json
{
  "schema_version": 1,
  "episode_id": "the request episode_id",
  "base_sha256": "the request base SHA-256",
  "rule_sha256s": ["the ordered request rule SHA-256 values"],
  "result_sha256": "SHA-256 of the exact result.xml bytes"
}
```

The evaluator safely parses the XML and scores changed-region agreement,
preservation of unaffected content, node and citation identity, hierarchy,
numbering, cross-references, tables, authority citations, provenance, and
two-run determinism. Fabrication, deletion of unaffected provisions, duplicate
nodes, malformed output, missing provenance, and nondeterminism are penalized.

Federal Register publication, eCFR editorial incorporation, and legal effective
dates are distinct clocks. The observed successor is an eCFR editorial state;
the task does not assert that its incorporation date is the rule's legal
effective date. FederalRegister.gov XML is a machine-readable rendition of the
published document; its official PDF is linked in the metadata. eCFR is
authoritative but unofficial.

When an episode contains multiple documents, apply them in the manifest's
order. That order follows observed eCFR amendment/incorporation dates; Federal
Register publication date and page are deterministic tie-breakers only.

Scoring projects out eCFR's non-derivable editorial `CITA` and pending-amendment
or correction `XREF` annotations. It also treats table presentation wrappers
and layout-only attributes as neutral while preserving ordered cell text,
row/column spans, captions, and hierarchy. A substantively exact result can
therefore earn 100 without inventing opaque eCFR bookkeeping identifiers.
