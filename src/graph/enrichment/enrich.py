"""Close the enrichment loop: dossier + embeddings -> a ``ready`` output row.

:func:`enrich_output` is the unit of "per-entity dossier+embedding
regeneration": given a :class:`~src.graph.enrichment.dossier.Dossier`, a
structural feature vector (from
:mod:`src.graph.enrichment.structural_features`), and an injected text embedder,
it fills all three of the output contract's enrichment fields — ``dossier_json``,
``dossier_embedding``, ``structural_embedding`` — and flips
``enrichment_status`` to ``ready`` (which the contract validates requires all
three).

The embedder is a thin injected boundary (text -> vector), so the loop is
testable with a toy embedder and provider-swappable (a hosted embedding model in
production).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from src.graph.contracts import EntityResolutionOutput
from src.graph.enrichment.dossier import Dossier

# text -> embedding vector. Injected for testability / provider-independence.
DossierEmbedder = Callable[[str], list[float]]


def dossier_text(dossier: Dossier) -> str:
    """The text fed to the embedder: the summary followed by every claim."""
    return "\n".join([dossier.summary, *(claim.text for claim in dossier.claims)])


def enrich_output(
    output: EntityResolutionOutput,
    *,
    dossier: Dossier,
    structural_embedding: Sequence[float],
    embedder: DossierEmbedder,
) -> EntityResolutionOutput:
    """Return a ``ready`` copy of ``output`` with dossier + both embeddings filled."""
    if not structural_embedding:
        raise ValueError("structural_embedding must be non-empty")
    dossier_embedding = embedder(dossier_text(dossier))
    if not dossier_embedding:
        raise ValueError("dossier embedding must be non-empty")
    return output.model_copy(
        update={
            "dossier_json": dossier.model_dump(mode="json"),
            "dossier_embedding": list(dossier_embedding),
            "structural_embedding": list(structural_embedding),
            "enrichment_status": "ready",
        }
    )
