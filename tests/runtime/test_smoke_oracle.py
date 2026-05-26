"""Tests for src/runtime/smoke_oracle.py.

No live DB, no network, no filesystem writes.
All three runtime stage boundaries are mocked at the smoke_oracle import path.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.runtime.smoke_oracle import smoke_oracle_path

_SNAP_DATE = dt.date(2024, 6, 1)
_MODULE = "src.runtime.smoke_oracle"

_LOCAL_ROOT = Path("/data/disclosures")
_TARGET_DIR = Path("/tmp/publish")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _disclosures_summary(
    *,
    run_id: int = 10,
    parse_succeeded: int = 3,
    parse_failed: int = 0,
    total_written: int = 6,
    load_ok: bool = True,
) -> dict:
    return {
        "run_id": run_id,
        "source_slug": "disclosures-senate",
        "parsed": parse_succeeded + parse_failed,
        "parse_succeeded": parse_succeeded,
        "parse_failed": parse_failed,
        "transformed": parse_succeeded,
        "total_written": total_written,
        "load_ok": load_ok,
    }


def _recompute_result(
    *, run_id: int = 20, rule_fires: int = 4, evidence_cards: int = 2
) -> MagicMock:
    r = MagicMock()
    r.run_id = run_id
    r.data_source = {"id": 1, "slug": "conflict-recompute"}
    r.recompute_result.rule_fires = [MagicMock()] * rule_fires
    r.recompute_result.evidence_cards = [MagicMock()] * evidence_cards
    return r


def _publish_result(
    *,
    run_id: int = 30,
    snapshot_id: str = "2024-06-01",
    written_count: int = 9,
    succeeded: bool = True,
) -> MagicMock:
    r = MagicMock()
    r.run_id = run_id
    r.snapshot_id = snapshot_id
    r.data_source = {"id": 2, "slug": "snapshot-publish"}
    r.publish_result.written_count = written_count
    r.publish_result.succeeded = succeeded
    return r


def _run(
    conn=None,
    *,
    disclosures=None,
    recompute=None,
    publish=None,
    snapshot_id=None,
    chamber=None,
    limit=None,
    taxonomy=None,
    issuer_sector_resolver=None,
):
    conn = conn or MagicMock()
    zip_inputs = MagicMock()
    with (
        patch(
            f"{_MODULE}.smoke_process_disclosures",
            return_value=disclosures or _disclosures_summary(),
        ) as mock_disc,
        patch(
            f"{_MODULE}.run_recompute_runtime", return_value=recompute or _recompute_result()
        ) as mock_rec,
        patch(
            f"{_MODULE}.run_publish_runtime", return_value=publish or _publish_result()
        ) as mock_pub,
    ):
        summary = smoke_oracle_path(
            conn,
            _LOCAL_ROOT,
            _SNAP_DATE,
            _TARGET_DIR,
            zip_inputs,
            snapshot_id=snapshot_id,
            chamber=chamber,
            limit=limit,
            taxonomy=taxonomy,
            issuer_sector_resolver=issuer_sector_resolver,
        )
    return summary, mock_disc, mock_rec, mock_pub, conn, zip_inputs


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


class TestSmokeOraclePathStructure:
    def test_returns_three_stage_keys(self) -> None:
        summary, *_ = _run()
        assert set(summary) == {"disclosures", "recompute", "publish"}

    def test_disclosures_summary_keys(self) -> None:
        summary, *_ = _run()
        assert set(summary["disclosures"]) == {
            "run_id",
            "source_slug",
            "parsed",
            "parse_succeeded",
            "parse_failed",
            "transformed",
            "total_written",
            "load_ok",
        }

    def test_recompute_summary_keys(self) -> None:
        summary, *_ = _run()
        assert set(summary["recompute"]) == {
            "run_id",
            "source_slug",
            "rule_fires",
            "evidence_cards",
        }

    def test_publish_summary_keys(self) -> None:
        summary, *_ = _run()
        assert set(summary["publish"]) == {
            "run_id",
            "snapshot_id",
            "source_slug",
            "written_count",
            "succeeded",
        }


# ---------------------------------------------------------------------------
# Disclosures stage
# ---------------------------------------------------------------------------


class TestDisclosuresStage:
    def test_values_forwarded(self) -> None:
        disc = _disclosures_summary(
            run_id=11, parse_succeeded=5, parse_failed=1, total_written=8, load_ok=True
        )
        summary, *_ = _run(disclosures=disc)
        assert summary["disclosures"]["run_id"] == 11
        assert summary["disclosures"]["parse_succeeded"] == 5
        assert summary["disclosures"]["parse_failed"] == 1
        assert summary["disclosures"]["total_written"] == 8
        assert summary["disclosures"]["load_ok"] is True

    def test_conn_forwarded_as_first_arg(self) -> None:
        conn = MagicMock()
        _, mock_disc, *_ = _run(conn=conn)
        assert mock_disc.call_args[0][0] is conn

    def test_local_root_forwarded(self) -> None:
        _, mock_disc, *_ = _run()
        assert mock_disc.call_args[0][1] == _LOCAL_ROOT

    def test_chamber_forwarded(self) -> None:
        _, mock_disc, *_ = _run(chamber="house")
        assert mock_disc.call_args[1]["chamber"] == "house"

    def test_limit_forwarded(self) -> None:
        _, mock_disc, *_ = _run(limit=5)
        assert mock_disc.call_args[1]["limit"] == 5

    def test_load_ok_false_reflected(self) -> None:
        disc = _disclosures_summary(load_ok=False, total_written=0)
        summary, *_ = _run(disclosures=disc)
        assert summary["disclosures"]["load_ok"] is False


# ---------------------------------------------------------------------------
# Recompute stage
# ---------------------------------------------------------------------------


class TestRecomputeStage:
    def test_counts_rule_fires_and_evidence_cards(self) -> None:
        rec = _recompute_result(run_id=21, rule_fires=7, evidence_cards=3)
        summary, *_ = _run(recompute=rec)
        assert summary["recompute"]["run_id"] == 21
        assert summary["recompute"]["rule_fires"] == 7
        assert summary["recompute"]["evidence_cards"] == 3

    def test_source_slug_from_data_source(self) -> None:
        summary, *_ = _run()
        assert summary["recompute"]["source_slug"] == "conflict-recompute"

    def test_snapshot_date_forwarded(self) -> None:
        _, _, mock_rec, *_ = _run()
        assert mock_rec.call_args[0][1] == _SNAP_DATE

    def test_taxonomy_forwarded(self) -> None:
        taxonomy = MagicMock()
        _, _, mock_rec, *_ = _run(taxonomy=taxonomy)
        assert mock_rec.call_args[1]["taxonomy"] is taxonomy

    def test_issuer_sector_resolver_forwarded(self) -> None:
        resolver = lambda n, t: None  # noqa: E731
        _, _, mock_rec, *_ = _run(issuer_sector_resolver=resolver)
        assert mock_rec.call_args[1]["issuer_sector_resolver"] is resolver

    def test_zero_fires_is_valid(self) -> None:
        rec = _recompute_result(rule_fires=0, evidence_cards=0)
        summary, *_ = _run(recompute=rec)
        assert summary["recompute"]["rule_fires"] == 0
        assert summary["recompute"]["evidence_cards"] == 0


# ---------------------------------------------------------------------------
# Publish stage
# ---------------------------------------------------------------------------


class TestPublishStage:
    def test_values_forwarded(self) -> None:
        pub = _publish_result(run_id=31, snapshot_id="2024-06-01", written_count=15, succeeded=True)
        summary, *_ = _run(publish=pub)
        assert summary["publish"]["run_id"] == 31
        assert summary["publish"]["snapshot_id"] == "2024-06-01"
        assert summary["publish"]["written_count"] == 15
        assert summary["publish"]["succeeded"] is True

    def test_source_slug_from_data_source(self) -> None:
        summary, *_ = _run()
        assert summary["publish"]["source_slug"] == "snapshot-publish"

    def test_snapshot_date_forwarded(self) -> None:
        _, _, _, mock_pub, *_ = _run()
        assert mock_pub.call_args[0][1] == _SNAP_DATE

    def test_target_dir_forwarded(self) -> None:
        _, _, _, mock_pub, *_ = _run()
        assert mock_pub.call_args[0][2] == _TARGET_DIR

    def test_zip_inputs_forwarded(self) -> None:
        _, _, _, mock_pub, conn, zip_inputs = _run()
        assert mock_pub.call_args[0][3] is zip_inputs

    def test_explicit_snapshot_id_forwarded(self) -> None:
        _, _, _, mock_pub, *_ = _run(snapshot_id="custom-snap")
        assert mock_pub.call_args[1]["snapshot_id"] == "custom-snap"

    def test_succeeded_false_reflected(self) -> None:
        pub = _publish_result(succeeded=False, written_count=0)
        summary, *_ = _run(publish=pub)
        assert summary["publish"]["succeeded"] is False
        assert summary["publish"]["written_count"] == 0


# ---------------------------------------------------------------------------
# Call order: disclosures before recompute before publish
# ---------------------------------------------------------------------------


class TestCallOrder:
    def test_stages_called_in_order(self) -> None:
        call_log: list[str] = []

        def _disc(*a, **kw):
            call_log.append("disclosures")
            return _disclosures_summary()

        def _rec(*a, **kw):
            call_log.append("recompute")
            return _recompute_result()

        def _pub(*a, **kw):
            call_log.append("publish")
            return _publish_result()

        with (
            patch(f"{_MODULE}.smoke_process_disclosures", side_effect=_disc),
            patch(f"{_MODULE}.run_recompute_runtime", side_effect=_rec),
            patch(f"{_MODULE}.run_publish_runtime", side_effect=_pub),
        ):
            smoke_oracle_path(MagicMock(), _LOCAL_ROOT, _SNAP_DATE, _TARGET_DIR, MagicMock())

        assert call_log == ["disclosures", "recompute", "publish"]


# ---------------------------------------------------------------------------
# JSON contract: full summary is operator-safe JSON
# ---------------------------------------------------------------------------


class TestSmokeOracleJsonContract:
    def test_summary_is_json_serializable(self) -> None:
        summary, *_ = _run()
        raw = json.dumps(summary, default=str)
        obj = json.loads(raw)
        assert set(obj) == {"disclosures", "recompute", "publish"}

    def test_all_stage_values_are_primitives(self) -> None:
        """Operator JSON must contain only str, int, bool, None — no objects."""
        summary, *_ = _run()
        for stage_name, stage in summary.items():
            for key, value in stage.items():
                assert isinstance(value, (str, int, bool, type(None))), (
                    f"{stage_name}.{key} has type {type(value).__name__}, expected primitive"
                )
