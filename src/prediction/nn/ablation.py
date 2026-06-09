"""Six-stream ablation harness for the four-stream model.

OVERALL_GOAL.md wants an ablation table proving which token stream contributes.
The model attends a politician token block (structural + dossier rows) over a
context built from the bill block (structural + dossier rows), the context
encoder tokens, and the retrieved past-vote tokens -- six streams in all. This
harness runs the full model and each "minus one stream" variant over an
evaluation set and reports accuracy + Brier per ablation, so the per-stream
contribution (full minus ablated) falls straight out.

Dossier streams are absent until Track A emits ``dossier_embedding``; when a
stream is absent its ablation simply equals the full run (it removes nothing),
which the table makes explicit.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from src.prediction.nn.four_stream_model import assemble_context
from src.prediction.nn.vote_transformer import VoteTransformerParams, vote_transformer_forward

Array = npt.NDArray[np.float64]

STREAMS = (
    "politician_structural",
    "politician_dossier",
    "bill_structural",
    "bill_dossier",
    "context",
    "retrieved_past_votes",
)


@dataclass(frozen=True)
class FourStreamExample:
    """One evaluation example's component token streams and the realized vote."""

    politician_structural: Array
    politician_dossier: Array | None
    bill_structural: Array
    bill_dossier: Array | None
    context_tokens: Array
    past_vote_tokens: Array
    is_yea: bool


@dataclass(frozen=True)
class AblationMetrics:
    """Accuracy and Brier for one ablation over the evaluation set."""

    accuracy: float
    brier_score: float
    sample_count: int


def _stack(rows: list[Array], *, width: int) -> Array:
    if not rows:
        return np.zeros((0, width))
    stacked: Array = np.stack(rows, axis=0)
    return stacked


def _predict(
    example: FourStreamExample, params: VoteTransformerParams, *, drop: str | None
) -> float:
    width = example.politician_structural.shape[0]
    politician_rows: list[Array] = []
    if drop != "politician_structural":
        politician_rows.append(example.politician_structural)
    if example.politician_dossier is not None and drop != "politician_dossier":
        politician_rows.append(example.politician_dossier)

    bill_rows: list[Array] = []
    if drop != "bill_structural":
        bill_rows.append(example.bill_structural)
    if example.bill_dossier is not None and drop != "bill_dossier":
        bill_rows.append(example.bill_dossier)

    context = example.context_tokens if drop != "context" else np.zeros((0, width))
    past_votes = (
        example.past_vote_tokens if drop != "retrieved_past_votes" else np.zeros((0, width))
    )

    if not politician_rows:
        # The ablation removed the entire politician (query) stream; with no
        # politician representation the model has nothing to attend with, so the
        # honest output is a neutral 0.5 (this only arises when the dossier row
        # is absent and the structural row is the one being dropped).
        return 0.5
    politician_tokens = _stack(politician_rows, width=width)
    bill_tokens = _stack(bill_rows, width=width)
    assembled_context = assemble_context(bill_tokens, context, past_votes)
    probability, _cache = vote_transformer_forward(politician_tokens, assembled_context, params)
    return probability


def _metrics(scored: list[tuple[float, bool]]) -> AblationMetrics:
    accuracy = sum(1 for p, y in scored if (p >= 0.5) == y) / len(scored)
    brier = sum((p - (1.0 if y else 0.0)) ** 2 for p, y in scored) / len(scored)
    return AblationMetrics(accuracy=accuracy, brier_score=brier, sample_count=len(scored))


def run_ablation(
    examples: list[FourStreamExample],
    params: VoteTransformerParams,
) -> dict[str, AblationMetrics]:
    """Run the full model and each single-stream ablation; return name -> metrics."""
    if not examples:
        raise ValueError("examples must not be empty")
    table: dict[str, AblationMetrics] = {}
    for drop in (None, *STREAMS):
        scored = [(_predict(example, params, drop=drop), example.is_yea) for example in examples]
        name = "full" if drop is None else f"minus:{drop}"
        table[name] = _metrics(scored)
    return table


__all__ = ["STREAMS", "FourStreamExample", "AblationMetrics", "run_ablation"]
