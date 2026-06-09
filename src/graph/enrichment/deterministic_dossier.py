"""Build a deterministic, source-anchored dossier without an LLM.

The production LLM dossier (Claude-class) is credential-gated
(``ANTHROPIC_API_KEY``). Until that key is provisioned, the regeneration driver
still needs a real, source-anchored ``dossier_json`` for every entity — so this
module assembles one *deterministically* from the entity's graph facts: a
templated factual summary plus one claim per fact, each anchored to the fact's
``(source_url, content_sha256)``. It reuses
:func:`~src.graph.enrichment.dossier.assemble_dossier`, so the same
hallucination guard applies (a claim can only cite a source present in the
grounded context).

The LLM upgrade swaps :func:`~src.graph.enrichment.dossier.generate_dossier`
(with a real ``DossierModel``) in for this builder without changing the contract
— the dossier shape and anchoring are identical.
"""

from __future__ import annotations

from src.graph.enrichment.dossier import Dossier, DossierClaimDraft, assemble_dossier
from src.graph.enrichment.dossier_context import DossierContext, DossierFact


def _fact_claim_text(fact: DossierFact) -> str:
    attrs = ", ".join(f"{key}={value}" for key, value in sorted(fact.attributes.items()))
    suffix = f" [{attrs}]" if attrs else ""
    return f"{fact.relation} -> {fact.target_id}{suffix} (as of {fact.valid_from.isoformat()})"


def build_deterministic_dossier(context: DossierContext) -> Dossier:
    """Assemble a deterministic source-anchored dossier from the entity's facts."""
    ids = ", ".join(context.external_ids) if context.external_ids else "no external IDs"
    summary = (
        f"{context.display_name} ({ids}): {len(context.facts)} source-anchored "
        f"fact(s) on record as of {context.as_of.date().isoformat()}."
    )
    drafts = [
        DossierClaimDraft(
            text=_fact_claim_text(fact),
            source_url=fact.source_url,
            content_sha256=fact.content_sha256,
        )
        for fact in context.facts
    ]
    return assemble_dossier(context, summary=summary, claim_drafts=drafts)
