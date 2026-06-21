from __future__ import annotations

import json
from datetime import UTC, datetime

from src.export.locus_release import (
    RELEASE_SCHEMA,
    ReleaseManifest,
    _dataset_card,
    ordinance_to_release_row,
)
from src.graph.ingest.locus import ordinance_row, parse_locus_row

_KNOWN = datetime(2026, 6, 20, tzinfo=UTC)


def _ordinance_row() -> object:
    record = {
        "header": "### 5.10 Sign permits.",
        "content": "A permit is required for any sign over 12 square feet.",
        "is_substantive": True,
        "function": "Rules",
        "topic": "Zoning",
        "source_jurisdiction_type": "cities",
        "state": "ca",
        "city": "oakland",
        "county": None,
        "opacity": 0.5,
        "paternalism": 0.1,
        "enforcement_discretion": 0.9,
        "problem_salience": 0.3,
    }
    parsed = parse_locus_row(record)
    assert parsed is not None
    return ordinance_row(parsed, known_at=_KNOWN)


def test_release_row_has_full_schema() -> None:
    flat = ordinance_to_release_row(_ordinance_row())  # type: ignore[arg-type]
    assert set(flat) == {name for name, _ in RELEASE_SCHEMA}
    assert flat["jurisdiction_id"] == "us-ca-city-oakland"
    assert flat["jurisdiction_level"] == "city"
    assert flat["function"] == "Rules"
    assert flat["topic"] == "Zoning"
    assert flat["is_substantive"] is True
    assert flat["opacity"] == 0.5
    assert flat["enforcement_discretion"] == 0.9
    assert flat["license"] == "cc-by-nc-4.0"
    assert flat["known_at"] == _KNOWN.isoformat()
    assert flat["canonical_id"].startswith("cb-")
    assert len(flat["content_sha256"]) == 64


def test_release_row_handles_missing_scores() -> None:
    record = {
        "header": "### h",
        "content": "c",
        "is_substantive": False,
        "function": "Context",
        "topic": None,
        "source_jurisdiction_type": "counties",
        "state": "ny",
        "city": None,
        "county": "erie",
        "opacity": None,
        "paternalism": None,
        "enforcement_discretion": None,
        "problem_salience": None,
    }
    parsed = parse_locus_row(record)
    assert parsed is not None
    flat = ordinance_to_release_row(ordinance_row(parsed, known_at=_KNOWN))
    assert flat["opacity"] is None
    assert flat["topic"] is None
    assert flat["jurisdiction_level"] == "county"


def test_dataset_card_is_noncommercial_and_documents_schema() -> None:
    manifest = ReleaseManifest(
        version="v1.0",
        parquet_filename="locus_ordinances.parquet",
        row_count=2207679,
        content_sha256="a" * 64,
        schema=[[name, dtype] for name, dtype in RELEASE_SCHEMA],
        dataset_url="https://huggingface.co/datasets/LocalLaws/LOCUS-v1",
        attribution="LOCUS v1.0 ...",
        license="cc-by-nc-4.0",
    )
    card = _dataset_card(manifest)
    assert card.startswith("---\n")  # HF YAML front-matter
    assert "license: cc-by-nc-4.0" in card
    assert "NON-COMMERCIAL" in card
    assert "a" * 64 in card  # content hash published
    # Every schema column documented in the card body + front-matter.
    for name, dtype in RELEASE_SCHEMA:
        assert name in card
        assert dtype in card
    assert "num_examples: 2207679" in card


def test_release_manifest_serializes() -> None:
    manifest = ReleaseManifest(
        version="v1.0",
        parquet_filename="locus_ordinances.parquet",
        row_count=3,
        content_sha256="b" * 64,
        schema=[["canonical_id", "string"]],
        dataset_url="u",
        attribution="a",
        license="cc-by-nc-4.0",
    )
    from dataclasses import asdict

    payload = json.loads(json.dumps(asdict(manifest)))
    assert payload["version"] == "v1.0"
    assert payload["row_count"] == 3
