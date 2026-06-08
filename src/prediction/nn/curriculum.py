"""Curriculum training schedule for the vote transformer.

OVERALL_GOAL.md's recipe: stabilize on easy partisan votes first, then
mid-difficulty, then close votes at a higher learning rate, and finally
fine-tune on the cross-pressured slice only (the votes anyone cares about
predicting). Difficulty is derived from the roll-call margin -- a near-tied
vote is hard, a lopsided one easy -- so the curriculum needs no Track A data;
it only orders and stages the existing trainable transformer.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.prediction.nn.vote_transformer import (
    VoteTransformerExample,
    VoteTransformerParams,
    train_vote_transformer,
)

_FINE_TUNE_STAGE = "cross_pressured_fine_tune"


@dataclass(frozen=True)
class CurriculumExample:
    """A training example tagged with its difficulty and cross-pressure status."""

    politician_tokens: object
    context_tokens: object
    is_yea: bool
    difficulty: float
    is_cross_pressured: bool = False

    def to_training_example(self) -> VoteTransformerExample:
        return VoteTransformerExample(
            politician_tokens=self.politician_tokens,  # type: ignore[arg-type]
            context_tokens=self.context_tokens,  # type: ignore[arg-type]
            is_yea=self.is_yea,
        )


@dataclass(frozen=True)
class CurriculumStage:
    """One stage of the schedule: its examples, epochs, and learning rate."""

    name: str
    examples: list[CurriculumExample]
    epochs: int
    learning_rate: float


def difficulty_from_margin(*, yea_count: int, total: int) -> float:
    """Map a roll-call margin to difficulty in ``[0, 1]`` (1 = tied, 0 = unanimous)."""
    if total <= 0:
        raise ValueError("total must be positive")
    yea_fraction = yea_count / total
    return 1.0 - 2.0 * abs(yea_fraction - 0.5)


def build_curriculum(
    examples: list[CurriculumExample],
    *,
    stage_count: int = 3,
    epochs_per_stage: int,
    base_learning_rate: float,
    learning_rate_growth: float = 1.5,
    fine_tune_epochs: int = 0,
    fine_tune_learning_rate: float | None = None,
) -> list[CurriculumStage]:
    """Order examples easy->hard into difficulty-quantile stages with rising LR.

    Appends a final ``cross_pressured_fine_tune`` stage over the cross-pressured
    examples when ``fine_tune_epochs`` is set and any such examples exist.
    """
    if stage_count <= 0:
        raise ValueError("stage_count must be positive")
    if epochs_per_stage <= 0:
        raise ValueError("epochs_per_stage must be positive")

    ordered = sorted(examples, key=lambda item: item.difficulty)
    stages: list[CurriculumStage] = []
    count = len(ordered)
    for index in range(stage_count):
        start = (index * count) // stage_count
        end = ((index + 1) * count) // stage_count
        chunk = ordered[start:end]
        if not chunk:
            continue
        stages.append(
            CurriculumStage(
                name=f"stage_{index}",
                examples=chunk,
                epochs=epochs_per_stage,
                learning_rate=base_learning_rate * (learning_rate_growth**index),
            )
        )

    if fine_tune_epochs > 0:
        cross_pressured = [item for item in ordered if item.is_cross_pressured]
        if cross_pressured:
            stages.append(
                CurriculumStage(
                    name=_FINE_TUNE_STAGE,
                    examples=cross_pressured,
                    epochs=fine_tune_epochs,
                    learning_rate=(
                        fine_tune_learning_rate
                        if fine_tune_learning_rate is not None
                        else base_learning_rate
                    ),
                )
            )
    return stages


def train_with_curriculum(
    params: VoteTransformerParams,
    stages: list[CurriculumStage],
) -> tuple[VoteTransformerParams, list[list[float]]]:
    """Train sequentially through the stages, threading parameters between them.

    Returns the final parameters and the per-stage loss histories.
    """
    if not stages:
        raise ValueError("stages must not be empty")
    current = params
    stage_histories: list[list[float]] = []
    for stage in stages:
        current, history = train_vote_transformer(
            current,
            [item.to_training_example() for item in stage.examples],
            epochs=stage.epochs,
            learning_rate=stage.learning_rate,
        )
        stage_histories.append(history)
    return current, stage_histories
