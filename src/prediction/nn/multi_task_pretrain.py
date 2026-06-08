"""Multi-task pretraining over the shared cross-attention transformer trunk.

OVERALL_GOAL.md: predicting votes jointly with cosponsorship, donor-receipt,
statement-stance valence, committee reassignment, bill passage, and
endorsements off one shared representation forces the embedding to encode the
whole political object. This is the integration that makes that real -- it
wires the gradient-checked :mod:`transformer_stack` trunk (query tokens cross-
attending over context, then mean-pooled) to the :mod:`multi_task_heads`, and
trains them jointly so each auxiliary task's loss flows back into the *shared*
trunk, not just its own readout.

Online SGD over examples; auxiliary targets may be mocked from Track A's
contract until the real feeds arrive (as the blueprint permits). The forward
contract is unchanged when the trunk is later swapped for torch.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from src.prediction.nn.multi_task_heads import (
    MultiTaskHeadsParams,
    init_multi_task_heads,
    multi_task_forward,
    multi_task_loss_and_grad,
)
from src.prediction.nn.transformer import CrossAttentionBlockCache
from src.prediction.nn.transformer_stack import (
    TransformerStackParams,
    apply_stack_gradients,
    init_transformer_stack,
    transformer_stack_backward,
    transformer_stack_forward,
)

Array = npt.NDArray[np.float64]


@dataclass(frozen=True)
class MultiTaskModelParams:
    """The shared transformer trunk plus its per-task heads."""

    stack: TransformerStackParams
    heads: MultiTaskHeadsParams


@dataclass(frozen=True)
class MultiTaskExample:
    """Token streams plus whichever task targets are observed for this example."""

    politician_tokens: Array
    context_tokens: Array
    targets: Mapping[str, float]


def init_multi_task_model(
    *,
    num_layers: int,
    d_model: int,
    num_heads: int,
    d_hidden: int,
    task_names: list[str],
    rng: np.random.Generator,
) -> MultiTaskModelParams:
    """Initialize the trunk stack and one head per task."""
    return MultiTaskModelParams(
        stack=init_transformer_stack(
            num_layers=num_layers, d_model=d_model, num_heads=num_heads, d_hidden=d_hidden, rng=rng
        ),
        heads=init_multi_task_heads(d_model=d_model, task_names=task_names, rng=rng),
    )


def multi_task_model_forward(
    politician_tokens: Array,
    context_tokens: Array,
    params: MultiTaskModelParams,
) -> tuple[dict[str, float], Array, list[CrossAttentionBlockCache]]:
    """Run the trunk, mean-pool, and read every task head."""
    output, caches = transformer_stack_forward(politician_tokens, context_tokens, params.stack)
    pooled: Array = output.mean(axis=0)
    probabilities = multi_task_forward(pooled, params.heads)
    return probabilities, pooled, caches


def _updated_heads(
    heads: MultiTaskHeadsParams,
    d_weights: Mapping[str, Array],
    d_biases: Mapping[str, float],
    learning_rate: float,
) -> MultiTaskHeadsParams:
    return MultiTaskHeadsParams(
        weights={
            name: weight - learning_rate * d_weights[name] for name, weight in heads.weights.items()
        },
        biases={name: bias - learning_rate * d_biases[name] for name, bias in heads.biases.items()},
    )


def train_multi_task_model(
    params: MultiTaskModelParams,
    examples: list[MultiTaskExample],
    *,
    epochs: int,
    learning_rate: float,
    task_weights: Mapping[str, float] | None = None,
) -> tuple[MultiTaskModelParams, list[float]]:
    """Joint online-SGD training; auxiliary losses flow into the shared trunk.

    Returns the trained parameters and the per-epoch mean joint loss.
    """
    if epochs <= 0:
        raise ValueError("epochs must be positive")
    if not examples:
        raise ValueError("examples must not be empty")

    current = params
    history: list[float] = []
    for _ in range(epochs):
        epoch_loss = 0.0
        for example in examples:
            output, caches = transformer_stack_forward(
                example.politician_tokens, example.context_tokens, current.stack
            )
            pooled: Array = output.mean(axis=0)
            loss, d_pooled, head_grads = multi_task_loss_and_grad(
                pooled, current.heads, targets=example.targets, task_weights=task_weights
            )
            epoch_loss += loss

            sequence_length = output.shape[0]
            d_output: Array = np.broadcast_to(d_pooled / sequence_length, output.shape).copy()
            _d_query, _d_context, layer_grads = transformer_stack_backward(
                d_output, caches=caches, params=current.stack
            )

            current = MultiTaskModelParams(
                stack=apply_stack_gradients(current.stack, layer_grads, learning_rate),
                heads=_updated_heads(
                    current.heads, head_grads.d_weights, head_grads.d_biases, learning_rate
                ),
            )
        history.append(epoch_loss / len(examples))
    return current, history
