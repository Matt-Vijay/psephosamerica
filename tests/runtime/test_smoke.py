"""Tests for src/runtime/smoke.py.

No live DB, no network, no filesystem writes.
All runtime boundaries are mocked at the smoke module's import path.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.runtime.smoke import smoke_publish, smoke_recompute

_SNAP_DATE = dt.date(2024, 6, 1)
_MODULE = "src.runtime.smoke"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _recompute_result(*, run_id: int = 1, rule_fires: int = 2, evidence_cards: int = 1) -> MagicMock:
    r = MagicMock()
    r.run_id = run_id
    r.data_source = {"id": 99, "slug": "conflict-recompute"}
    r.recompute_result.rule_fires = [MagicMock()] * rule_fires
    r.recompute_result.evidence_cards = [MagicMock()] * evidence_cards
    return r


def _publish_result(
    *,
    run_id: int = 2,
    snapshot_id: str = "2024-06-01",
    written_count: int = 5,
    succeeded: bool = True,
) -> MagicMock:
    r = MagicMock()
    r.run_id = run_id
    r.snapshot_id = snapshot_id
    r.data_source = {"id": 7, "slug": "snapshot-publish"}
    r.publish_result.written_count = written_count
    r.publish_result.succeeded = succeeded
    return r


# ---------------------------------------------------------------------------
# smoke_recompute
# ---------------------------------------------------------------------------


class TestSmokeRecompute:
    def test_returns_dict_with_expected_keys(self) -> None:
        with patch(f"{_MODULE}.run_recompute_runtime", return_value=_recompute_result()):
            summary = smoke_recompute(MagicMock(), _SNAP_DATE)

        assert set(summary) == {"run_id", "source_slug", "rule_fires", "evidence_cards"}

    def test_counts_rule_fires_and_evidence_cards(self) -> None:
        with patch(f"{_MODULE}.run_recompute_runtime", return_value=_recompute_result(rule_fires=3, evidence_cards=2)):
            summary = smoke_recompute(MagicMock(), _SNAP_DATE)

        assert summary["rule_fires"] == 3
        assert summary["evidence_cards"] == 2

    def test_run_id_and_source_slug_forwarded(self) -> None:
        with patch(f"{_MODULE}.run_recompute_runtime", return_value=_recompute_result(run_id=42)):
            summary = smoke_recompute(MagicMock(), _SNAP_DATE)

        assert summary["run_id"] == 42
        assert summary["source_slug"] == "conflict-recompute"

    def test_passes_optional_taxonomy_and_resolver(self) -> None:
        taxonomy = MagicMock()
        resolver = lambda n, t: None  # noqa: E731

        with patch(f"{_MODULE}.run_recompute_runtime", return_value=_recompute_result()) as mock_run:
            smoke_recompute(MagicMock(), _SNAP_DATE, taxonomy=taxonomy, issuer_sector_resolver=resolver)

        _, kwargs = mock_run.call_args
        assert kwargs["taxonomy"] is taxonomy
        assert kwargs["issuer_sector_resolver"] is resolver

    def test_zero_fires_is_valid(self) -> None:
        with patch(f"{_MODULE}.run_recompute_runtime", return_value=_recompute_result(rule_fires=0, evidence_cards=0)):
            summary = smoke_recompute(MagicMock(), _SNAP_DATE)

        assert summary["rule_fires"] == 0
        assert summary["evidence_cards"] == 0

    def test_conn_forwarded_as_first_positional_arg(self) -> None:
        conn = MagicMock()

        with patch(f"{_MODULE}.run_recompute_runtime", return_value=_recompute_result()) as mock_run:
            smoke_recompute(conn, _SNAP_DATE)

        assert mock_run.call_args[0][0] is conn
        assert mock_run.call_args[0][1] == _SNAP_DATE


# ---------------------------------------------------------------------------
# smoke_publish
# ---------------------------------------------------------------------------


class TestSmokePublish:
    def test_returns_dict_with_expected_keys(self) -> None:
        with patch(f"{_MODULE}.run_publish_runtime", return_value=_publish_result()):
            summary = smoke_publish(MagicMock(), _SNAP_DATE, Path("/tmp"), MagicMock())

        assert set(summary) == {"run_id", "snapshot_id", "source_slug", "written_count", "succeeded"}

    def test_summarizes_publish_result(self) -> None:
        pub = _publish_result(run_id=7, snapshot_id="2024-06-01", written_count=12, succeeded=True)

        with patch(f"{_MODULE}.run_publish_runtime", return_value=pub):
            summary = smoke_publish(MagicMock(), _SNAP_DATE, Path("/tmp"), MagicMock())

        assert summary["run_id"] == 7
        assert summary["snapshot_id"] == "2024-06-01"
        assert summary["written_count"] == 12
        assert summary["succeeded"] is True
        assert summary["source_slug"] == "snapshot-publish"

    def test_explicit_snapshot_id_forwarded(self) -> None:
        with patch(f"{_MODULE}.run_publish_runtime", return_value=_publish_result()) as mock_run:
            smoke_publish(MagicMock(), _SNAP_DATE, Path("/tmp"), MagicMock(), snapshot_id="custom-snap")

        _, kwargs = mock_run.call_args
        assert kwargs["snapshot_id"] == "custom-snap"

    def test_positional_args_forwarded_in_order(self) -> None:
        conn = MagicMock()
        zip_inputs = MagicMock()
        target_dir = Path("/tmp/target")

        with patch(f"{_MODULE}.run_publish_runtime", return_value=_publish_result()) as mock_run:
            smoke_publish(conn, _SNAP_DATE, target_dir, zip_inputs)

        args, _ = mock_run.call_args
        assert args[0] is conn
        assert args[1] == _SNAP_DATE
        assert args[2] is target_dir
        assert args[3] is zip_inputs

    def test_succeeded_false_reflected_in_summary(self) -> None:
        pub = _publish_result(succeeded=False, written_count=0)

        with patch(f"{_MODULE}.run_publish_runtime", return_value=pub):
            summary = smoke_publish(MagicMock(), _SNAP_DATE, Path("/tmp"), MagicMock())

        assert summary["succeeded"] is False
        assert summary["written_count"] == 0
