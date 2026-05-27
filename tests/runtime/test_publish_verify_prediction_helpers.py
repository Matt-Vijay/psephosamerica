"""Tests for the pure path/identity helpers in publish_verify_prediction."""

from __future__ import annotations

import pytest

from src.runtime.publish_verify_prediction import (
    _id_from_path,
    _is_prediction_path,
    _prediction_path_identity_mismatch_message,
)


def test_is_prediction_path() -> None:
    assert _is_prediction_path("prediction/readiness.json") is True
    assert _is_prediction_path("prediction/source-context/k.json") is True
    assert _is_prediction_path("members/x.json") is False
    assert _is_prediction_path("") is False


def test_id_from_path_extracts_or_rejects() -> None:
    pfx = ("prediction", "source-context")
    assert _id_from_path("prediction/source-context/abc.json", prefix=pfx) == "abc"
    assert _id_from_path("prediction/source-context/abc.txt", prefix=pfx) is None  # not .json
    assert _id_from_path("prediction/source-context/.json", prefix=pfx) is None  # empty id
    assert _id_from_path("prediction/other/abc.json", prefix=pfx) is None  # wrong prefix
    assert _id_from_path("prediction/source-context/a/b.json", prefix=pfx) is None  # wrong depth


@pytest.mark.parametrize(
    "path,exc_message,expected",
    [
        (
            "prediction/source-context/k.json",
            "source_key does not match requested key",
            "prediction source context path does not match embedded source_key",
        ),
        (
            "prediction/sector-context/energy.json",
            "sector_id does not match requested id",
            "prediction sector context path does not match embedded sector_id",
        ),
        (
            "prediction/committee-context/HSEC.json",
            "committee_id does not match requested id",
            "prediction committee context path does not match embedded committee_id",
        ),
        (
            "prediction/members/A000001.json",
            "bioguide_id does not match requested id",
            "prediction member readiness path does not match embedded bioguide_id",
        ),
        (
            "prediction/member-context/A000001.json",
            "bioguide_id does not match requested id",
            "prediction member context path does not match embedded bioguide_id",
        ),
    ],
)
def test_identity_mismatch_message_maps_each_context(path, exc_message, expected) -> None:
    assert _prediction_path_identity_mismatch_message(path, ValueError(exc_message)) == expected


def test_identity_mismatch_message_returns_none_for_unrelated_error() -> None:
    assert (
        _prediction_path_identity_mismatch_message(
            "prediction/readiness.json", ValueError("some other error")
        )
        is None
    )
