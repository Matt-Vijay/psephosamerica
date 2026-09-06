"""Tests for prediction window/manifest metadata helpers in commands.py."""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from types import SimpleNamespace

from src.runtime.commands._shared import _congress_archive_manifest_metadata
from src.runtime.commands.history import _validate_congress_archive_manifest_metadata
from src.runtime.commands.prediction_eval import _prediction_window_run_metadata
from src.runtime.commands.prediction_misc import _prediction_backtest_run_metadata


def test_prediction_window_run_metadata() -> None:
    args = SimpleNamespace(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
    )
    md = _prediction_window_run_metadata(args)
    assert md["training_feature_cutoff"] == "2022-12-31"
    assert md["label_end"] == "2025-12-31"


def test_prediction_backtest_run_metadata() -> None:
    args = SimpleNamespace(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
    )
    assert _prediction_backtest_run_metadata(args) == {
        "feature_cutoff": "2024-12-31",
        "label_start": "2025-01-01",
        "label_end": "2025-12-31",
    }


def test_congress_archive_manifest_metadata(tmp_path: Path) -> None:
    assert _congress_archive_manifest_metadata(None) is None
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    md = _congress_archive_manifest_metadata(manifest)
    assert md == {
        "path": str(manifest),
        "sha256": hashlib.sha256(b"{}").hexdigest(),
    }


def _validate(manifest: object, *, require: bool) -> list[str]:
    issues: list[str] = []
    _validate_congress_archive_manifest_metadata(
        label="archive manifest",
        manifest=manifest,
        issues=issues,
        require_manifest=require,
    )
    return issues


def test_validate_congress_archive_manifest_metadata(tmp_path: Path) -> None:
    real = tmp_path / "m.json"
    real.write_text("{}", encoding="utf-8")
    real_sha = hashlib.sha256(b"{}").hexdigest()

    assert _validate(None, require=True) == ["archive manifest missing"]
    assert _validate(None, require=False) == []
    assert "must be an object" in _validate("not-a-dict", require=False)[0]
    assert "path missing" in _validate({"sha256": "a" * 64}, require=False)[0]
    assert "sha256 invalid" in _validate({"path": "/x", "sha256": "bad"}, require=False)[0]
    missing = _validate({"path": str(tmp_path / "nope"), "sha256": "a" * 64}, require=False)
    assert "file not found" in missing[0]
    mismatch = _validate({"path": str(real), "sha256": "a" * 64}, require=False)
    assert "sha256 mismatch" in mismatch[0]
    assert _validate({"path": str(real), "sha256": real_sha}, require=False) == []
