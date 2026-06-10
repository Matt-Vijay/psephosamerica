"""Dual-emit semantic embeddings into the contract corpus (resumable).

Reads the exported corpus + the bill **content sidecar** (``bill_content.jsonl``,
title + policy area + subjects + CRS summary per bill), computes a 384-d
sentence-transformers embedding for every row that lacks one, and writes it to
the row's ``semantic_embedding`` *alongside* the existing 256-d hash
``dossier_embedding`` -- so Track B can A/B the two. Bills draw their text from
the sidecar (rich CRS text); persons draw theirs from their own ``dossier_json``
(summary + source-anchored claims).

Resumable: a row that already has ``semantic_embedding`` is skipped, so a re-run
continues where the last left off. Each pass rewrites the corpus and appends a
delta-CDC feed (the change fires ``enrichment_changed``), which Track B's
hot-swap watcher reads. torch lives behind :class:`SemanticEmbedder` only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from src.graph.cdc import diff_outputs
from src.graph.contracts import EntityResolutionOutput
from src.graph.enrichment.semantic_embedder import SemanticEmbedder
from src.graph.export import read_contract_corpus, write_contract_corpus, write_delta_feed

_RECORDS = "records.jsonl"
_DELTAS = "deltas.jsonl"


def load_content_text(path: Path | str) -> dict[str, str]:
    """Load ``{canonical_id: text}`` from a bill content sidecar (empty if absent)."""
    sidecar = Path(path)
    if not sidecar.exists():
        return {}
    out: dict[str, str] = {}
    for line in sidecar.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            text = record.get("text")
            if text:
                out[str(record["canonical_id"])] = str(text)
    return out


def _dossier_text(dossier: dict[str, Any] | None) -> str | None:
    """A person/org's embed text: dossier summary + every claim text."""
    if not dossier:
        return None
    parts = [str(dossier.get("summary") or "")]
    parts += [str(claim.get("text") or "") for claim in dossier.get("claims", [])]
    text = "\n".join(part for part in parts if part)
    return text or None


def _text_for(row: EntityResolutionOutput, sidecar: dict[str, str]) -> str | None:
    """The text to embed for a row: sidecar text for bills, dossier text otherwise."""
    if row.entity_type == "bill":
        return sidecar.get(row.canonical_id)
    return _dossier_text(row.dossier_json)


@dataclass(frozen=True)
class ReembedProgress:
    """The outcome of one semantic re-embed pass."""

    corpus_rows: int
    eligible: int
    embedded_now: int
    already_embedded: int
    remaining: int
    deltas_written: int


def semantic_reembed_corpus(
    *,
    corpus_directory: Path | str,
    content_sidecar: Path | str,
    as_of: datetime,
    embedder: SemanticEmbedder | None = None,
    batch_size: int = 256,
    max_rows: int | None = None,
) -> ReembedProgress:
    """Fill ``semantic_embedding`` for rows that lack it; rewrite corpus + CDC."""
    embed = embedder if embedder is not None else SemanticEmbedder()
    directory = Path(corpus_directory)
    rows = read_contract_corpus(directory)
    sidecar = load_content_text(content_sidecar)
    prior = {row.canonical_id: row for row in rows}

    already = sum(1 for row in rows if row.semantic_embedding is not None)
    todo = [
        index
        for index, row in enumerate(rows)
        if row.semantic_embedding is None and _text_for(row, sidecar)
    ]
    batch = todo if max_rows is None else todo[:max_rows]

    embedded = 0
    for start in range(0, len(batch), batch_size):
        chunk = batch[start : start + batch_size]
        texts = [_text_for(rows[i], sidecar) or "" for i in chunk]
        vectors = embed.embed_batch(texts, batch_size=batch_size)
        for index, vector in zip(chunk, vectors, strict=True):
            rows[index] = rows[index].model_copy(update={"semantic_embedding": vector})
            embedded += 1

    write_contract_corpus(rows, directory=directory, as_of=as_of)
    deltas = diff_outputs(prior, {row.canonical_id: row for row in rows})
    deltas_written = write_delta_feed(deltas, path=directory / _DELTAS, append=True)
    return ReembedProgress(
        corpus_rows=len(rows),
        eligible=len(todo) + already,
        embedded_now=embedded,
        already_embedded=already,
        remaining=len(todo) - embedded,
        deltas_written=deltas_written,
    )


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI glue
    import argparse
    from datetime import UTC

    parser = argparse.ArgumentParser(description="Dual-emit semantic embeddings into the corpus")
    parser.add_argument("--corpus", default="data/exports/contract_records")
    parser.add_argument("--content", default="data/exports/govinfo_bills/bill_content.jsonl")
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--max-rows", type=int, default=20000, help="rows per pass")
    parser.add_argument("--max-passes", type=int, default=100000)
    args = parser.parse_args(argv)

    for pass_index in range(args.max_passes):
        progress = semantic_reembed_corpus(
            corpus_directory=args.corpus,
            content_sidecar=args.content,
            as_of=datetime.now(UTC),
            batch_size=args.batch_size,
            max_rows=args.max_rows,
        )
        print(
            f"pass {pass_index}: corpus={progress.corpus_rows} "
            f"+{progress.embedded_now} semantic (have {progress.already_embedded}) "
            f"remaining={progress.remaining} deltas={progress.deltas_written}",
            flush=True,
        )
        if progress.embedded_now == 0:
            print(f"DONE: {progress.already_embedded} rows carry semantic_embedding", flush=True)
            return 0
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
