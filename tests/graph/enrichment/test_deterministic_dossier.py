from __future__ import annotations

from datetime import UTC, date, datetime

from src.graph.enrichment.deterministic_dossier import build_deterministic_dossier
from src.graph.enrichment.dossier import Dossier
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


def _context(facts: list[DossierFact]) -> DossierContext:
    return DossierContext(
        canonical_id="ce-jane",
        display_name="Jane Doe",
        external_ids=["bioguide:p000001"],
        as_of=datetime(2024, 4, 1, tzinfo=UTC),
        facts=facts,
    )


def test_builds_source_anchored_dossier() -> None:
    dossier = build_deterministic_dossier(_context([_FACT]))
    assert isinstance(dossier, Dossier)
    assert dossier.canonical_id == "ce-jane"
    assert dossier.as_of == datetime(2024, 4, 1, tzinfo=UTC)
    assert "Jane Doe" in dossier.summary
    assert len(dossier.claims) == 1
    claim = dossier.claims[0]
    assert claim.source_url == "https://clerk.house.gov/Votes/1"
    assert claim.content_sha256 == "b" * 64
    assert "vote" in claim.text and "cb-bill1" in claim.text and "choice=yea" in claim.text


def test_every_claim_is_anchored_to_a_context_fact() -> None:
    facts = [
        _FACT,
        DossierFact(
            relation="donation",
            target_id="ce-pac",
            attributes={"amount_cents": "5000"},
            source_url="https://www.fec.gov/x",
            content_sha256="c" * 64,
            known_at=datetime(2024, 2, 1, tzinfo=UTC),
            valid_from=date(2024, 2, 1),
        ),
    ]
    dossier = build_deterministic_dossier(_context(facts))
    allowed = {(f.source_url, f.content_sha256) for f in facts}
    assert all((c.source_url, c.content_sha256) in allowed for c in dossier.claims)
    assert len(dossier.claims) == 2


def test_empty_facts_yield_summary_only() -> None:
    dossier = build_deterministic_dossier(_context([]))
    assert dossier.claims == []
    assert "0" in dossier.summary or "no" in dossier.summary.lower()
    assert "Jane Doe" in dossier.summary


def test_is_deterministic() -> None:
    ctx = _context([_FACT])
    assert build_deterministic_dossier(ctx) == build_deterministic_dossier(ctx)


def test_summary_mentions_external_ids_and_count() -> None:
    dossier = build_deterministic_dossier(_context([_FACT]))
    assert "bioguide:p000001" in dossier.summary
    assert "1" in dossier.summary  # one fact


def test_claims_sorted_by_known_at() -> None:
    later = DossierFact(
        relation="endorsement",
        target_id="ce-org",
        attributes={},
        source_url="https://example.org/e",
        content_sha256="d" * 64,
        known_at=datetime(2024, 5, 1, tzinfo=UTC),
        valid_from=date(2024, 5, 1),
    )
    # context facts arrive sorted by known_at already (DossierContext invariant)
    dossier = build_deterministic_dossier(_context([_FACT, later]))
    assert dossier.claims[0].text.startswith("vote")
    assert dossier.claims[1].text.startswith("endorsement")
