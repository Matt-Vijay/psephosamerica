"""Build four-stream examples from real votes + Track A's real embeddings.

Joins the House vote feed to Track A's contract corpus by bioguide id and
constructs ``FourStreamExample`` rows whose politician streams are the *real*
256-d dossier and 64-d structural embeddings (projected into token space) and
whose context stream carries the party-alignment signal. This is the
four-stream architecture running on real data end to end -- the seam that was
gated on Track A's ``dossier_embedding`` and is now live.

The bill streams are left as placeholder zero tokens until bill embeddings are
dense in the corpus (only a handful of bills are enriched today); the ablation
harness then quantifies how little they contribute, honestly. Stays on numpy --
the embeddings feed ``token_projection`` directly.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from src.prediction.nn.ablation import FourStreamExample
from src.prediction.nn.token_projection import ProjectionParams, init_projection, project
from src.prediction.real_data_eval import VoteRow
from src.runtime.contract_corpus import ContractEntity

Array = npt.NDArray[np.float64]


def init_embedding_projections(
    *,
    dossier_dim: int,
    structural_dim: int,
    d_model: int,
    rng: np.random.Generator,
) -> tuple[ProjectionParams, ProjectionParams]:
    """Initialize the dossier->token and structural->token projections."""
    return (
        init_projection(input_dim=dossier_dim, d_model=d_model, rng=rng),
        init_projection(input_dim=structural_dim, d_model=d_model, rng=rng),
    )


def _context_token(party_alignment: float, d_model: int) -> Array:
    token = np.zeros((1, d_model))
    token[0, 0] = party_alignment
    return token


def build_four_stream_real_examples(
    vote_rows: list[VoteRow],
    bioguide_index: dict[str, ContractEntity],
    *,
    dossier_projection: ProjectionParams,
    structural_projection: ProjectionParams,
    d_model: int,
) -> list[FourStreamExample]:
    """Join votes to real embeddings and build four-stream examples.

    Votes whose member is not in the contract corpus (no embedding) are skipped.
    """
    examples: list[FourStreamExample] = []
    zero_token = np.zeros(d_model)
    empty = np.zeros((0, d_model))
    for row in vote_rows:
        if row.vote_option not in {"yea", "nay"}:
            continue
        entity = bioguide_index.get(row.member_bioguide_id)
        if entity is None:
            continue
        dossier_token, _ = project(entity.dossier_embedding, dossier_projection)
        structural_token, _ = project(entity.structural_embedding, structural_projection)
        examples.append(
            FourStreamExample(
                politician_structural=structural_token,
                politician_dossier=dossier_token,
                bill_structural=zero_token,
                bill_dossier=None,
                context_tokens=_context_token(row.signals.get("party_alignment", 0.0), d_model),
                past_vote_tokens=empty,
                is_yea=row.is_yea,
            )
        )
    return examples
