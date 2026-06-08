"""The per-entity LLM dossier: grounded prompt, anchor guard, output wiring.

Builds on :class:`~src.graph.enrichment.dossier_context.DossierContext` (the
leakage-safe evidence) to produce a :class:`Dossier` where *every claim is
source-anchored* — the blueprint's hard requirement. The actual model call is a
thin injected boundary (a ``DossierModel`` callable: prompt -> summary + claim
drafts), so the pipeline is fully testable with a fake model and provider-
swappable (a Claude Sonnet/Opus-class model in production).

The load-bearing safety property is :func:`assemble_dossier`'s **anchor guard**:
a claim may only cite a ``(source_url, content_sha256)`` that appears in the
grounded context. A model that invents a citation is rejected, so a dossier can
never assert an unsourced — or hallucinated-source — claim.

Filling the contract: :func:`apply_dossier_to_output` writes ``dossier_json``
onto an :class:`~src.graph.contracts.EntityResolutionOutput`. The row stays
``pending`` until the embedding slices also land (the contract requires the
dossier *and* both embeddings for ``ready``).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import datetime

from pydantic import Field, field_validator

from src.export.contracts import ExportContractModel
from src.graph.contracts import EntityResolutionOutput
from src.graph.enrichment.dossier_context import DossierContext

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# A model: a grounded prompt -> (summary, claim drafts). Injected so the
# pipeline is testable and provider-agnostic.
DossierModel = Callable[[str], "tuple[str, list[DossierClaimDraft]]"]


class DossierClaimDraft(ExportContractModel):
    """A claim as a model proposes it, before the anchor guard validates it."""

    text: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    content_sha256: str

    @field_validator("content_sha256")
    @classmethod
    def _valid_sha(cls, value: str) -> str:
        if not _SHA256_RE.match(value):
            raise ValueError("content_sha256 must be 64 lowercase hexadecimal characters")
        return value


class DossierClaim(ExportContractModel):
    """A validated, source-anchored claim in a dossier."""

    text: str
    source_url: str
    content_sha256: str


class Dossier(ExportContractModel):
    """A rolling, source-anchored dossier for one entity, as of a time."""

    canonical_id: str
    as_of: datetime
    summary: str
    claims: list[DossierClaim] = Field(default_factory=list)


def build_dossier_prompt(context: DossierContext) -> str:
    """Render the grounded prompt: identity + the only facts the model may use."""
    lines = [
        "You are writing a factual dossier on a public official.",
        "Use ONLY the provided facts. Every claim must cite a fact's source_url",
        "and content_sha256 verbatim. Do not invent sources or facts.",
        "",
        f"Official: {context.display_name}",
        f"Canonical ID: {context.canonical_id}",
        f"External IDs: {', '.join(context.external_ids) or '(none)'}",
        f"As of: {context.as_of.isoformat()}",
        "",
        "Facts (the complete, exclusive evidence set):",
    ]
    if not context.facts:
        lines.append("  (no facts known as of this date)")
    for fact in context.facts:
        attrs = ", ".join(f"{k}={v}" for k, v in sorted(fact.attributes.items()))
        lines.append(
            f"  - {fact.relation} -> {fact.target_id} [{attrs}] "
            f"(source_url={fact.source_url}, content_sha256={fact.content_sha256})"
        )
    return "\n".join(lines)


def assemble_dossier(
    context: DossierContext,
    *,
    summary: str,
    claim_drafts: Sequence[DossierClaimDraft],
) -> Dossier:
    """Validate that every claim cites a grounded source, then build the dossier."""
    if not summary.strip():
        raise ValueError("dossier summary must be non-blank")
    allowed = {(fact.source_url, fact.content_sha256) for fact in context.facts}
    claims: list[DossierClaim] = []
    for draft in claim_drafts:
        if (draft.source_url, draft.content_sha256) not in allowed:
            raise ValueError(
                f"claim cites a source not in the grounded context: {draft.source_url}"
            )
        claims.append(
            DossierClaim(
                text=draft.text,
                source_url=draft.source_url,
                content_sha256=draft.content_sha256,
            )
        )
    return Dossier(
        canonical_id=context.canonical_id,
        as_of=context.as_of,
        summary=summary.strip(),
        claims=claims,
    )


def generate_dossier(context: DossierContext, model: DossierModel) -> Dossier:
    """Prompt the injected model, then validate its output through the anchor guard."""
    summary, claim_drafts = model(build_dossier_prompt(context))
    return assemble_dossier(context, summary=summary, claim_drafts=claim_drafts)


def apply_dossier_to_output(
    output: EntityResolutionOutput,
    dossier: Dossier,
) -> EntityResolutionOutput:
    """Return a copy of ``output`` with ``dossier_json`` filled (status stays pending).

    ``enrichment_status`` only reaches ``ready`` once the embedding slices land,
    so this fills the dossier and leaves the status untouched.
    """
    return output.model_copy(update={"dossier_json": dossier.model_dump(mode="json")})
