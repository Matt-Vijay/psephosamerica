"""Tests for content-addressed checkpoint pinning."""

from __future__ import annotations

from pathlib import Path

from src.prediction.per_member_model import MemberVoteExample, train_per_member_model
from src.runtime.checkpoint import (
    checkpoint_path,
    content_address,
    pin_checkpoint,
    verify_checkpoint,
)


def _model() -> object:
    examples = [
        MemberVoteExample(member_id="M0", signals={"party_alignment": 1.0}, is_yea=True),
        MemberVoteExample(member_id="M0", signals={"party_alignment": -1.0}, is_yea=False),
    ]
    return train_per_member_model(examples, pooling_penalty=1.0)


def test_pin_is_content_addressed_and_reproducible(tmp_path: Path) -> None:
    model = _model()
    first = pin_checkpoint(model, root=tmp_path)  # type: ignore[arg-type]
    second = pin_checkpoint(model, root=tmp_path)  # type: ignore[arg-type]
    assert first == second  # same params -> same path
    sha = content_address(model)  # type: ignore[arg-type]
    assert first == checkpoint_path(tmp_path, sha)
    assert sha in first.name


def test_verify_checkpoint(tmp_path: Path) -> None:
    path = pin_checkpoint(_model(), root=tmp_path)  # type: ignore[arg-type]
    assert verify_checkpoint(path)
    path.write_bytes(b'{"tampered":true}')
    assert not verify_checkpoint(path)


def test_distinct_models_get_distinct_paths(tmp_path: Path) -> None:
    a = pin_checkpoint(_model(), root=tmp_path)  # type: ignore[arg-type]
    other = train_per_member_model(
        [MemberVoteExample(member_id="Z9", signals={"x": 1.0}, is_yea=True)], pooling_penalty=2.0
    )
    b = pin_checkpoint(other, root=tmp_path)
    assert a != b
