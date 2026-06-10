from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from src.graph.contracts import ContractSourceAnchor, EntityResolutionOutput
from src.graph.enrichment.semantic_embedder import SEMANTIC_DIM, SemanticEmbedder
from src.graph.export import read_contract_corpus, write_contract_corpus
from src.runtime.semantic_reembed import load_content_text, semantic_reembed_corpus

_AS_OF = datetime(2025, 1, 1, tzinfo=UTC)


class _FakeEncoder:
    def encode(self, sentences: list[str], **kwargs: Any) -> Any:
        rows = []
        for text in sentences:
            rng = np.random.default_rng(abs(hash(text)) % (2**32))
            v = rng.standard_normal(SEMANTIC_DIM)
            rows.append(v / (np.linalg.norm(v) or 1.0))
        return np.array(rows)


def _embedder() -> SemanticEmbedder:
    return SemanticEmbedder(encoder=_FakeEncoder())


def _anchor() -> ContractSourceAnchor:
    return ContractSourceAnchor(
        source_system="govinfo",
        record_id="r",
        source_url="https://x",
        content_sha256="a" * 64,
        content_address="sha256/aa/aa/" + "a" * 64,
        known_at=datetime(2023, 1, 1, tzinfo=UTC),
        valid_from=datetime(2023, 1, 1, tzinfo=UTC).date(),
    )


def _row(cid: str, etype: str, *, dossier: dict[str, Any] | None = None) -> EntityResolutionOutput:
    return EntityResolutionOutput(
        canonical_id=cid,
        entity_type=etype,  # type: ignore[arg-type]
        display_name="X",
        external_ids=[],
        known_at=datetime(2023, 1, 1, tzinfo=UTC),
        source_anchors=[_anchor()],
        dossier_json=dossier,
    )


def _write_corpus(directory: Path) -> None:
    rows = [
        _row("cb-1", "bill"),  # bill -> text from sidecar
        _row("cb-2", "bill"),  # bill NOT in sidecar -> skipped
        _row(
            "cp-1", "person", dossier={"summary": "Rep X record", "claims": [{"text": "voted yea"}]}
        ),
        _row("cp-2", "person", dossier=None),  # no text -> skipped
    ]
    write_contract_corpus(rows, directory=directory, as_of=_AS_OF)


def _write_sidecar(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"canonical_id": "cb-1", "text": "Lower Energy Costs Act. Policy area: Energy."})
        + "\n",
        encoding="utf-8",
    )


def test_load_content_text(tmp_path: Path) -> None:
    sidecar = tmp_path / "bill_content.jsonl"
    sidecar.write_text(
        '{"canonical_id": "cb-1", "text": "hello"}\n\n{"canonical_id": "cb-9"}\n',
        encoding="utf-8",
    )
    assert load_content_text(sidecar) == {"cb-1": "hello"}
    assert load_content_text(tmp_path / "nope.jsonl") == {}


def test_reembed_fills_bill_from_sidecar_and_person_from_dossier(tmp_path: Path) -> None:
    corpus = tmp_path / "contract_records"
    sidecar = tmp_path / "bill_content.jsonl"
    _write_corpus(corpus)
    _write_sidecar(sidecar)

    progress = semantic_reembed_corpus(
        corpus_directory=corpus, content_sidecar=sidecar, as_of=_AS_OF, embedder=_embedder()
    )
    # cb-1 (sidecar) + cp-1 (dossier) are eligible; cb-2 (no sidecar) + cp-2 (no dossier) not.
    assert progress.eligible == 2
    assert progress.embedded_now == 2
    assert progress.remaining == 0
    assert progress.deltas_written == 2  # the two updated rows

    by_id = {r.canonical_id: r for r in read_contract_corpus(corpus)}
    assert by_id["cb-1"].semantic_embedding is not None
    assert len(by_id["cb-1"].semantic_embedding) == SEMANTIC_DIM  # type: ignore[arg-type]
    assert by_id["cp-1"].semantic_embedding is not None
    assert by_id["cb-2"].semantic_embedding is None  # no text -> untouched
    assert by_id["cp-2"].semantic_embedding is None
    # dual-emit: the hash dossier_embedding field is untouched (still present/None)
    assert by_id["cb-1"].entity_type == "bill"


def test_reembed_is_resumable(tmp_path: Path) -> None:
    corpus = tmp_path / "contract_records"
    sidecar = tmp_path / "bill_content.jsonl"
    _write_corpus(corpus)
    _write_sidecar(sidecar)

    first = semantic_reembed_corpus(
        corpus_directory=corpus,
        content_sidecar=sidecar,
        as_of=_AS_OF,
        embedder=_embedder(),
        max_rows=1,
    )
    assert first.embedded_now == 1 and first.remaining == 1
    second = semantic_reembed_corpus(
        corpus_directory=corpus, content_sidecar=sidecar, as_of=_AS_OF, embedder=_embedder()
    )
    assert second.already_embedded == 1
    assert second.embedded_now == 1 and second.remaining == 0
    # third pass: nothing left
    third = semantic_reembed_corpus(
        corpus_directory=corpus, content_sidecar=sidecar, as_of=_AS_OF, embedder=_embedder()
    )
    assert third.embedded_now == 0 and third.deltas_written == 0


def test_distinct_texts_distinct_vectors(tmp_path: Path) -> None:
    corpus = tmp_path / "contract_records"
    sidecar = tmp_path / "bill_content.jsonl"
    _write_corpus(corpus)
    _write_sidecar(sidecar)
    semantic_reembed_corpus(
        corpus_directory=corpus, content_sidecar=sidecar, as_of=_AS_OF, embedder=_embedder()
    )
    by_id = {r.canonical_id: r for r in read_contract_corpus(corpus)}
    assert by_id["cb-1"].semantic_embedding != by_id["cp-1"].semantic_embedding
