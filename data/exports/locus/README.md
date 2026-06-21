# LOCUS-v1 ordinance corpus (Track A)

U.S. municipal + county law provisions from **LOCUS v1.0**
(`huggingface.co/datasets/LocalLaws/LOCUS-v1`), ingested as pending
`EntityResolutionOutput` bill rows (`entity_type="bill"`) by
`src.graph.ingest.locus_export`.

Each ordinance carries, in `dossier_json`, the LOCUS legal `function`
(Context / Rules / Process / Enforcement), the `topic`, the `is_substantive`
flag, and the four continuous dimension scores — `opacity`, `paternalism`,
`enforcement_discretion`, `problem_salience` — plus its canonical
`jurisdiction_id` in the repo's hierarchical vocabulary
(`us-ca-city-oakland`). Provenance + `known_at` on every row.

## Attribution & license (required)

LOCUS v1.0 — Peskoff, Barrow, Vu & Davenport, *Freeing the Law with LOCUS*,
arXiv:2606.19334. Licensed **CC-BY-NC-4.0 (NON-COMMERCIAL)**. The license is
carried on every row (`external_ids: ["license:cc-by-nc-4.0", ...]`,
`dossier_json.license`) and in `ingest_meta.json`. Downstream use must honor the
non-commercial restriction; this is stricter than the rest of the corpus, so the
LOCUS tier must be filterable on `license:cc-by-nc-4.0`.

## Files

- `records.jsonl` — one ordinance `EntityResolutionOutput` per line, sorted by
  `canonical_id`. **Gitignored** (large, regenerable).
- `deltas.jsonl` — CDC `EntityDelta` feed (every ordinance `created` on first
  ingest). Gitignored.
- `ordinance_content.jsonl` — text sidecar
  (`{canonical_id, text, function, topic, is_substantive, dimension_scores,
  jurisdiction_id}`) for the semantic re-embed. Gitignored.
- `manifest.json` — `as_of`, `record_count`, `content_sha256` of `records.jsonl`.
- `ingest_meta.json` — run provenance: dataset URL, attribution, license, shards
  read vs total, rows scanned/skipped, ordinance + jurisdiction counts, and
  `is_full_corpus` (the honest sample-vs-full flag).
- `sample.records.jsonl` — first 25 rows, committed as the shape reference.

## Regenerate

```
python -m src.graph.ingest.locus_export --out data/exports/locus            # full (8 shards, ~2.2M rows)
python -m src.graph.ingest.locus_export --out data/exports/locus --max-shards 1 --max-rows 60000   # sample
```

The committed `manifest.json` / `ingest_meta.json` reflect a **representative
sample** (cap honestly recorded in `ingest_meta.is_full_corpus=false`); removing
the caps drops the full corpus in unchanged. Ordinance IDs are content-addressed
(`BillRef(jurisdiction, "locus", "ord-<digest>")`), so a re-ingest of the same
provision mints the same `canonical_id` — dedup is by construction.
