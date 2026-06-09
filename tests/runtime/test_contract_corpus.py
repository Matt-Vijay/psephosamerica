"""Tests for the Track A contract-corpus loader.

Uses Track A's committed sample corpus (``data/exports/contract_records/
sample.records.jsonl``) when present, plus a synthetic fixture, to verify
embeddings are loaded and bioguide indexing works.
"""

from __future__ import annotations

from pathlib import Path

from src.runtime.contract_corpus import (
    bioguide_embedding_index,
    load_contract_entities,
)

_SAMPLE = Path("data/exports/contract_records/sample.records.jsonl")


def _write_fixture(path: Path) -> None:
    anchor = {
        "source_system": "house_clerk",
        "record_id": "r1",
        "source_url": "https://clerk.house.gov/Votes/1",
        "content_sha256": "a" * 64,
        "content_address": "sha256/aa/aa/" + "a" * 64,
        "known_at": "2025-01-01T00:00:00+00:00",
        "valid_from": "2025-01-01T00:00:00+00:00",
        "valid_to": None,
    }
    record = {
        "canonical_id": "ce-1",
        "entity_type": "person",
        "display_name": "Rep Example",
        "external_ids": ["bioguide:m001199"],
        "known_at": "2025-01-01T00:00:00+00:00",
        "source_anchors": [anchor],
        "dossier_json": {"summary": "x"},
        "dossier_embedding": [0.1] * 256,
        "structural_embedding": [0.2] * 64,
        "enrichment_status": "ready",
    }
    import json

    path.write_text(json.dumps(record) + "\n", encoding="utf-8")


def test_load_fixture_entity_with_embeddings(tmp_path: Path) -> None:
    path = tmp_path / "records.jsonl"
    _write_fixture(path)
    entities = load_contract_entities(path)
    assert len(entities) == 1
    entity = entities[0]
    assert entity.dossier_embedding.shape == (256,)
    assert entity.structural_embedding.shape == (64,)


def test_bioguide_index_uppercases_key(tmp_path: Path) -> None:
    path = tmp_path / "records.jsonl"
    _write_fixture(path)
    index = bioguide_embedding_index(load_contract_entities(path))
    assert "M001199" in index  # contract stores lowercase; index normalizes
    assert index["M001199"].dossier_embedding.shape == (256,)


def test_loads_track_a_sample_corpus_if_present() -> None:
    if not _SAMPLE.exists():
        return  # Track A sample not checked out; the fixture tests cover behavior.
    entities = load_contract_entities(_SAMPLE)
    # Every loaded entity is enriched with both embeddings.
    assert all(e.dossier_embedding.size > 0 and e.structural_embedding.size > 0 for e in entities)
    index = bioguide_embedding_index(entities)
    # The sample carries at least one federal (bioguide) member.
    assert len(index) >= 1
