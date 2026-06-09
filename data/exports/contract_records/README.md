# Track A → Track B contract corpus

`EntityResolutionOutput` rows (the spec contract:
`canonical_person_id`/`canonical_bill_id`, `dossier_json`,
`dossier_embedding`, `structural_embedding`, `known_at`, `source_anchors[]`),
fully enriched to `enrichment_status="ready"` by the regenerate driver.

## Files

- `records.jsonl` — one JSON `EntityResolutionOutput` per line, sorted by
  `canonical_id` (deterministic bytes). **Gitignored** (large, regenerable);
  present on disk for offline consumption.
- `deltas.jsonl` — the CDC `EntityDelta` feed (created/updated/removed),
  tailable. Gitignored (regenerable).
- `manifest.json` — `as_of`, `record_count`, and the `content_sha256` of
  `records.jsonl`. Time-resolvable + content-addressed; two runs over the same
  rows + `as_of` are byte-identical.
- `sample.records.jsonl` — first 25 rows, committed as the shape reference.

## Regenerate

```
python -m src.graph.regenerate   # or call src.graph.regenerate.regenerate_corpus
```

Embeddings are populated by the in-repo deterministic `LocalTextEmbedder`
(`dossier_embedding`, 256-d) and `rgcn_lite_embeddings` (`structural_embedding`,
relational message-passing). No paid API; swap `ANTHROPIC_API_KEY` in for richer
LLM dossiers and a learned RGCN/HGT for the structural side without changing the
contract.
