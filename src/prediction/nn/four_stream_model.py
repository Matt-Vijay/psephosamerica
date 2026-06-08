"""Four-stream cross-attention model wiring (numpy).

OVERALL_GOAL.md's architecture has the prediction model cross-attend a
politician token stream over three context streams: the bill stream, the
context tokens (``context_encoder``), and the retrieved past-vote tokens
(``past_vote_retrieval``). This module performs that wiring: the politician
tokens are the transformer's query, and the concatenation of the other three
streams is its context. The result runs through the gradient-checked
``vote_transformer`` to a yea probability.

The structural, context, and past-vote token streams are derived from real
federal data; the dossier half of the politician/bill tokens is mockable until
Track A supplies ``dossier_embedding`` (as the blueprint permits). When those
embeddings land they simply extend the politician/bill token rows -- the wiring
here is unchanged.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from src.prediction.nn.vote_transformer import (
    VoteTransformerCache,
    VoteTransformerParams,
    vote_transformer_forward,
)

Array = npt.NDArray[np.float64]


def assemble_context(
    bill_tokens: Array,
    context_tokens: Array,
    past_vote_tokens: Array,
) -> Array:
    """Concatenate the bill, context, and past-vote streams into one context block.

    Empty streams (zero rows) are skipped, so a (politician, bill) pair with no
    retrieved past votes still assembles cleanly. At least one context token
    must be present.
    """
    present = [
        stream for stream in (bill_tokens, context_tokens, past_vote_tokens) if stream.shape[0] > 0
    ]
    if not present:
        raise ValueError("at least one context stream must contribute a token")
    assembled: Array = np.concatenate(present, axis=0)
    return assembled


def four_stream_predict(
    *,
    politician_tokens: Array,
    bill_tokens: Array,
    context_tokens: Array,
    past_vote_tokens: Array,
    params: VoteTransformerParams,
) -> tuple[float, VoteTransformerCache]:
    """Cross-attend politician tokens over the assembled context; return yea probability."""
    context = assemble_context(bill_tokens, context_tokens, past_vote_tokens)
    return vote_transformer_forward(politician_tokens, context, params)
