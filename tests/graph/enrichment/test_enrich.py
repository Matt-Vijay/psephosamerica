from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.contracts import ContractSourceAnchor, EntityResolutionOutput
from src.graph.enrichment.dossier import Dossier, DossierClaim
from src.graph.enrichment.enrich import dossier_text, enrich_output


def _output() -> EntityResolutionOutput:
    return EntityResolutionOutput(
        canonical_id="ce-jane",
        entity_type="person",
        display_name="Jane Doe",
        external_ids=["bioguide:p000001"],
        known_at=datetime(2024, 1, 5, tzinfo=UTC),
        source_anchors=[
            ContractSourceAnchor(
                source_system="congress",
                record_id="sr-1",
                source_url="https://example.gov/1",
                content_sha256="a" * 64,
                content_address="sha256/aa/aa/" + "a" * 64,
                known_at=datetime(2024, 1, 5, tzinfo=UTC),
                valid_from=date(2024, 1, 1),
            )
        ],
    )


_DOSSIER = Dossier(
    canonical_id="ce-jane",
    as_of=datetime(2024, 4, 1, tzinfo=UTC),
    summary="A representative from California.",
    claims=[
        DossierClaim(
            text="Voted yea on H.R. 1.",
            source_url="https://clerk.house.gov/Votes/1",
            content_sha256="b" * 64,
        )
    ],
)

_STRUCTURAL = [1.0, 0.0, 2.0]


def _fake_embedder(text: str) -> list[float]:
    # Deterministic toy embedding: length + vowel count.
    return [float(len(text)), float(sum(text.lower().count(v) for v in "aeiou"))]


# ── dossier_text ───────────────────────────────────────────────────


def test_dossier_text_includes_summary_and_claims() -> None:
    text = dossier_text(_DOSSIER)
    assert "A representative from California." in text
    assert "Voted yea on H.R. 1." in text


# ── enrich_output ──────────────────────────────────────────────────


def test_enrich_fills_all_three_fields_and_marks_ready() -> None:
    enriched = enrich_output(
        _output(),
        dossier=_DOSSIER,
        structural_embedding=_STRUCTURAL,
        embedder=_fake_embedder,
    )
    assert enriched.enrichment_status == "ready"
    assert enriched.dossier_json is not None
    assert enriched.dossier_json["summary"] == "A representative from California."
    assert enriched.dossier_embedding == _fake_embedder(dossier_text(_DOSSIER))
    assert enriched.structural_embedding == _STRUCTURAL


def test_enriched_output_satisfies_ready_invariant() -> None:
    enriched = enrich_output(
        _output(), dossier=_DOSSIER, structural_embedding=_STRUCTURAL, embedder=_fake_embedder
    )
    # Round-trips through validation (ready requires all three fields).
    assert EntityResolutionOutput.model_validate(enriched.model_dump()) == enriched


def test_enrich_does_not_mutate_original() -> None:
    output = _output()
    enrich_output(
        output, dossier=_DOSSIER, structural_embedding=_STRUCTURAL, embedder=_fake_embedder
    )
    assert output.enrichment_status == "pending"
    assert output.dossier_json is None


def test_empty_structural_embedding_rejected() -> None:
    with pytest.raises(ValueError, match="structural_embedding"):
        enrich_output(_output(), dossier=_DOSSIER, structural_embedding=[], embedder=_fake_embedder)


def test_embedder_returning_empty_rejected() -> None:
    with pytest.raises(ValueError, match="embedding"):
        enrich_output(
            _output(),
            dossier=_DOSSIER,
            structural_embedding=_STRUCTURAL,
            embedder=lambda _text: [],
        )
