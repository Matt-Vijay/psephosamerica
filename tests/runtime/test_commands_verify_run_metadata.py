"""Tests for verify run-metadata builders in commands.py."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

from src.runtime.commands import _prediction_backtest_verify_run_metadata


def test_prediction_backtest_verify_run_metadata_full_flags(tmp_path: Path) -> None:
    artifact = tmp_path / "backtest.json"
    artifact.write_text("{}", encoding="utf-8")
    args = SimpleNamespace(
        require_run_metadata=True,
        require_evaluated_predictions=False,
        require_prediction_source_urls=True,
        require_official_prediction_source_urls=False,
        require_model_name="ontology_signal_model",
        require_bill_semantics_cache=False,
        require_bill_semantics_model_name=["m1", "m2"],
        require_bill_semantics_source_inputs_sha256=False,
    )

    md = _prediction_backtest_verify_run_metadata(args, artifact, source_state={"k": 1})

    assert md["command"] == "verify-prediction-backtest"
    flags = md["verification_flags"]
    assert flags["require_run_metadata"] is True
    assert flags["require_prediction_source_urls"] is True
    assert flags["require_model_name"] == "ontology_signal_model"
    # Cache requirement is implied by a non-empty model-name list.
    assert flags["require_bill_semantics_cache"] is True
    assert flags["require_bill_semantics_model_names"] == ["m1", "m2"]
    assert md["artifact_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert md["source_state"] == {"k": 1}


def test_prediction_backtest_verify_run_metadata_defaults_and_missing_artifact(
    tmp_path: Path,
) -> None:
    md = _prediction_backtest_verify_run_metadata(SimpleNamespace(), tmp_path / "missing.json")

    flags = md["verification_flags"]
    assert flags["require_run_metadata"] is False
    assert flags["require_model_name"] is None
    assert flags["require_bill_semantics_cache"] is False
    assert flags["require_bill_semantics_model_names"] == []
    assert md["artifact_sha256"] is None  # file does not exist
    assert "source_state" not in md  # omitted when not provided
