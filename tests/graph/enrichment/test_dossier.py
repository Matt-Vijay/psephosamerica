from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.enrichment.dossier import (
    Dossier,
    DossierClaimDraft,
    apply_dossier_to_output,
    assemble_dossier,
    build_dossier_prompt,
    generate_dossier,
)
from src.graph.enrichment.dossier_context import DossierContext, DossierFact

_FACT = DossierFact(
    relation="vote",
    target_id="cb-bill1",
    attributes={"choice": "yea"},
    source_url="https://clerk.house.gov/Votes/1",
    content_sha256="b" * 64,
    known_at=datetime(2024, 3, 1, tzinfo=UTC),
    valid_from=date(2024, 3, 1),
)

_CONTEXT = DossierContext(
    canonical_id="ce-jane",
    display_name="Jane Doe",
    external_ids=["bioguide:p000001"],
    as_of=datetime(2024, 4, 1, tzinfo=UTC),
    facts=[_FACT],
)


def _draft(**overrides: object) -> DossierClaimDraft:
    base: dict[str, object] = {
        "text": "Voted yea on cb-bill1.",
        "source_url": "https://clerk.house.gov/Votes/1",
        "content_sha256": "b" * 64,
    }
    base.update(overrides)
    return DossierClaimDraft(**base)  # type: ignore[arg-type]


# ── prompt ─────────────────────────────────────────────────────────


def test_prompt_includes_identity_and_facts() -> None:
    prompt = build_dossier_prompt(_CONTEXT)
    assert "Jane Doe" in prompt
    assert "bioguide:p000001" in prompt
    assert "vote" in prompt
    assert "cb-bill1" in prompt
    assert "https://clerk.house.gov/Votes/1" in prompt


def test_prompt_instructs_source_anchoring() -> None:
    prompt = build_dossier_prompt(_CONTEXT).lower()
    assert "source" in prompt
    assert "only" in prompt  # only the provided facts


def test_prompt_handles_no_facts() -> None:
    empty = DossierContext(
        canonical_id="ce-new",
        display_name="Newcomer",
        external_ids=[],
        as_of=datetime(2024, 1, 1, tzinfo=UTC),
        facts=[],
    )
    prompt = build_dossier_prompt(empty)
    assert "no facts known" in prompt
    assert "(none)" in prompt  # no external ids


def test_claim_draft_rejects_bad_sha() -> None:
    with pytest.raises(ValueError, match="content_sha256"):
        _draft(content_sha256="not-a-sha")


# ── assemble + anchor guard ────────────────────────────────────────


def test_assemble_accepts_anchored_claims() -> None:
    dossier = assemble_dossier(_CONTEXT, summary="A representative.", claim_drafts=[_draft()])
    assert isinstance(dossier, Dossier)
    assert dossier.canonical_id == "ce-jane"
    assert dossier.as_of == _CONTEXT.as_of
    assert dossier.summary == "A representative."
    assert len(dossier.claims) == 1
    assert dossier.claims[0].source_url == "https://clerk.house.gov/Votes/1"


def test_assemble_rejects_hallucinated_anchor() -> None:
    # A claim citing a source NOT in the grounded context is rejected.
    with pytest.raises(ValueError, match="not in the grounded context"):
        assemble_dossier(
            _CONTEXT,
            summary="...",
            claim_drafts=[_draft(source_url="https://evil.example/made-up")],
        )


def test_assemble_rejects_mismatched_hash() -> None:
    with pytest.raises(ValueError, match="not in the grounded context"):
        assemble_dossier(_CONTEXT, summary="...", claim_drafts=[_draft(content_sha256="c" * 64)])


def test_assemble_allows_empty_claims() -> None:
    dossier = assemble_dossier(_CONTEXT, summary="No record yet.", claim_drafts=[])
    assert dossier.claims == []


def test_assemble_rejects_blank_summary() -> None:
    with pytest.raises(ValueError):
        assemble_dossier(_CONTEXT, summary="   ", claim_drafts=[])


# ── generate via injected model ────────────────────────────────────


def test_generate_dossier_with_fake_model() -> None:
    def fake_model(prompt: str) -> tuple[str, list[DossierClaimDraft]]:
        assert "Jane Doe" in prompt
        return "Summary from model.", [_draft()]

    dossier = generate_dossier(_CONTEXT, fake_model)
    assert dossier.summary == "Summary from model."
    assert len(dossier.claims) == 1


def test_generate_dossier_propagates_anchor_guard() -> None:
    def hallucinating_model(prompt: str) -> tuple[str, list[DossierClaimDraft]]:
        return "...", [_draft(source_url="https://evil.example/x")]

    with pytest.raises(ValueError, match="not in the grounded context"):
        generate_dossier(_CONTEXT, hallucinating_model)


# ── apply to output row ────────────────────────────────────────────


def test_apply_dossier_fills_dossier_json() -> None:
    from src.graph.contracts import ContractSourceAnchor, EntityResolutionOutput

    output = EntityResolutionOutput(
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
    dossier = assemble_dossier(_CONTEXT, summary="A rep.", claim_drafts=[_draft()])
    enriched = apply_dossier_to_output(output, dossier)
    assert enriched.dossier_json is not None
    assert enriched.dossier_json["summary"] == "A rep."
    # Embeddings still missing, so it stays pending (contract invariant).
    assert enriched.enrichment_status == "pending"
    assert output.dossier_json is None  # original untouched
