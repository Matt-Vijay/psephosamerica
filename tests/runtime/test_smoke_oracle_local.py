"""Tests for src/runtime/smoke_oracle_local.py.

No live DB, no network, no filesystem writes.
run_oracle_local is mocked at the smoke_oracle_local import path.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.runtime.oracle_contracts import (
    CongressOracleOptions,
    CongressStageSummary,
    LocalOracleOptions,
    LocalOracleRunResult,
)
from src.runtime.publish_roundtrip_types import (
    PublishRoundtripResult,
    PublishRoundtripStageResult,
)
from src.runtime.publish_verify_types import (
    PublishVerifyResult,
    PublishVerifyStageResult,
)
from src.runtime.smoke_oracle_local import smoke_oracle_local

_MODULE = "src.runtime.smoke_oracle_local"

_SNAP_DATE = dt.date(2024, 6, 1)
_TARGET_DIR = Path("/tmp/publish")
_CONGRESS_ARCHIVE = Path("/data/congress")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_options(
    *,
    snapshot_id: str | None = None,
    congress: int = 119,
) -> LocalOracleOptions:
    return LocalOracleOptions(
        congress_options=CongressOracleOptions(congress=congress),
        snapshot_date=_SNAP_DATE,
        target_dir=_TARGET_DIR,
        snapshot_id=snapshot_id,
    )


def _congress_summary(
    *,
    run_id: int = 1,
    source_slug: str = "congress-core",
    total_inserted: int = 10,
    total_written: int = 10,
    load_ok: bool = True,
    configured_congress: int = 119,
    congress_source: str = "explicit",
    include_votes: bool = False,
) -> CongressStageSummary:
    return CongressStageSummary(
        run_id=run_id,
        source_slug=source_slug,
        total_inserted=total_inserted,
        total_written=total_written,
        load_ok=load_ok,
        configured_congress=configured_congress,
        congress_source=congress_source,
        include_votes=include_votes,
    )


def _disclosures_summary(
    *,
    run_id: int = 10,
    source_slug: str = "house-disclosures",
    requested_chamber: str = "both",
    artifact_limit: int | None = None,
    parse_succeeded: int = 3,
    parse_failed: int = 0,
    transform_count: int = 3,
    total_written: int = 6,
    load_ok: bool = True,
) -> dict:
    return {
        "run_id": run_id,
        "source_slug": source_slug,
        "requested_chamber": requested_chamber,
        "artifact_limit": artifact_limit,
        "parse_succeeded": parse_succeeded,
        "parse_failed": parse_failed,
        "transform_count": transform_count,
        "total_written": total_written,
        "load_ok": load_ok,
    }


def _recompute_summary(
    *,
    run_id: int = 20,
    source_slug: str = "conflict-recompute",
    rule_fires: int = 4,
    evidence_cards: int = 2,
) -> dict:
    return {
        "run_id": run_id,
        "source_slug": source_slug,
        "rule_fires": rule_fires,
        "evidence_cards": evidence_cards,
    }


def _publish_summary(
    *,
    run_id: int = 30,
    snapshot_id: str = "2024-06-01",
    source_slug: str = "snapshot-publish",
    written_count: int = 9,
    succeeded: bool = True,
    zip_feeds_generated: bool = False,
) -> dict:
    return {
        "run_id": run_id,
        "snapshot_id": snapshot_id,
        "source_slug": source_slug,
        "written_count": written_count,
        "succeeded": succeeded,
        "zip_feeds_generated": zip_feeds_generated,
    }


def _make_verify_result(
    *,
    ok: bool = True,
    total_checked: int = 5,
    total_errors: int = 0,
    total_warnings: int = 0,
) -> PublishVerifyResult:
    """Build a minimal PublishVerifyResult with one synthetic stage."""
    from src.runtime.publish_verify_types import PublishVerifyIssue

    issues: tuple[PublishVerifyIssue, ...] = ()
    if total_errors:
        issues = tuple(
            PublishVerifyIssue(stage="manifest", message=f"error {i}", severity="error")
            for i in range(total_errors)
        )
    elif total_warnings:
        issues = tuple(
            PublishVerifyIssue(stage="manifest", message=f"warning {i}", severity="warning")
            for i in range(total_warnings)
        )
    stage = PublishVerifyStageResult(
        stage="manifest",
        checked=total_checked,
        issues=issues,
    )
    return PublishVerifyResult(stages=(stage,))


def _make_roundtrip_result(
    *,
    ok: bool = True,
    total_checked: int = 5,
    total_errors: int = 0,
    total_warnings: int = 0,
) -> PublishRoundtripResult:
    """Build a minimal PublishRoundtripResult with one synthetic stage."""
    from src.runtime.publish_roundtrip_types import PublishRoundtripIssue

    issues: tuple[PublishRoundtripIssue, ...] = ()
    if total_errors:
        issues = tuple(
            PublishRoundtripIssue(stage="snapshot", message=f"error {i}", severity="error")
            for i in range(total_errors)
        )
    elif total_warnings:
        issues = tuple(
            PublishRoundtripIssue(stage="snapshot", message=f"warning {i}", severity="warning")
            for i in range(total_warnings)
        )
    stage = PublishRoundtripStageResult(
        stage="snapshot",
        checked=total_checked,
        issues=issues,
    )
    return PublishRoundtripResult(stages=(stage,))


def _make_run_result(
    *,
    snapshot_id: str = "2024-06-01",
    congress: CongressStageSummary | None = None,
    disclosures: dict | None = None,
    recompute: dict | None = None,
    publish: dict | None = None,
    verify: PublishVerifyResult | None = None,
    roundtrip: PublishRoundtripResult | None = None,
) -> LocalOracleRunResult:
    return LocalOracleRunResult(
        snapshot_id=snapshot_id,
        congress=congress or _congress_summary(),
        disclosures=disclosures or _disclosures_summary(),
        recompute=recompute or _recompute_summary(),
        publish=publish or _publish_summary(),
        verify=verify or _make_verify_result(),
        roundtrip=roundtrip or _make_roundtrip_result(),
    )


def _run(
    conn=None,
    *,
    congress_archive: Path = _CONGRESS_ARCHIVE,
    options: LocalOracleOptions | None = None,
    run_result: LocalOracleRunResult | None = None,
):
    conn = conn or MagicMock()
    disclosures_bundle = MagicMock()
    opts = options or _make_options()
    with patch(
        f"{_MODULE}.run_oracle_local",
        return_value=run_result or _make_run_result(),
    ) as mock_oracle:
        summary = smoke_oracle_local(conn, congress_archive, disclosures_bundle, opts)
    return summary, mock_oracle, conn, disclosures_bundle, opts


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


class TestSmokeOracleLocalStructure:
    def test_returns_seven_top_level_keys(self) -> None:
        summary, *_ = _run()
        assert set(summary) == {
            "snapshot_id",
            "congress",
            "disclosures",
            "recompute",
            "publish",
            "verify",
            "roundtrip",
        }

    def test_congress_keys(self) -> None:
        summary, *_ = _run()
        assert set(summary["congress"]) == {
            "run_id",
            "source_slug",
            "total_inserted",
            "total_written",
            "load_ok",
            "configured_congress",
            "congress_source",
            "include_votes",
        }

    def test_disclosures_keys(self) -> None:
        summary, *_ = _run()
        assert set(summary["disclosures"]) == {
            "run_id",
            "source_slug",
            "requested_chamber",
            "artifact_limit",
            "parse_succeeded",
            "parse_failed",
            "transform_count",
            "total_written",
            "load_ok",
        }

    def test_recompute_keys(self) -> None:
        summary, *_ = _run()
        assert set(summary["recompute"]) == {
            "run_id",
            "source_slug",
            "rule_fires",
            "evidence_cards",
        }

    def test_publish_keys(self) -> None:
        summary, *_ = _run()
        assert set(summary["publish"]) == {
            "run_id",
            "snapshot_id",
            "source_slug",
            "written_count",
            "succeeded",
            "zip_feeds_generated",
        }

    def test_verify_keys(self) -> None:
        summary, *_ = _run()
        assert {
            "ok",
            "total_checked",
            "total_errors",
            "total_warnings",
            "stages",
            "issues",
            "issues_truncated",
        }.issubset(summary["verify"])
        assert isinstance(summary["verify"]["issues"], list)
        assert summary["verify"]["issues_truncated"] is False

    def test_roundtrip_keys(self) -> None:
        summary, *_ = _run()
        assert {
            "ok",
            "total_checked",
            "total_errors",
            "total_warnings",
            "stages",
            "issues",
            "issues_truncated",
        }.issubset(summary["roundtrip"])
        assert isinstance(summary["roundtrip"]["issues"], list)
        assert summary["roundtrip"]["issues_truncated"] is False


# ---------------------------------------------------------------------------
# Argument forwarding
# ---------------------------------------------------------------------------


class TestArgumentForwarding:
    def test_conn_forwarded(self) -> None:
        conn = MagicMock()
        _, mock_oracle, *_ = _run(conn=conn)
        assert mock_oracle.call_args[0][0] is conn

    def test_congress_archive_forwarded(self) -> None:
        archive = Path("/custom/archive")
        _, mock_oracle, *_ = _run(congress_archive=archive)
        assert mock_oracle.call_args[0][1] == archive

    def test_disclosures_bundle_forwarded(self) -> None:
        _, mock_oracle, _, bundle, _ = _run()
        assert mock_oracle.call_args[0][2] is bundle

    def test_options_forwarded(self) -> None:
        opts = _make_options(snapshot_id="explicit-snap")
        _, mock_oracle, _, _, _ = _run(options=opts)
        assert mock_oracle.call_args[0][3] is opts


# ---------------------------------------------------------------------------
# Value forwarding
# ---------------------------------------------------------------------------


class TestValueForwarding:
    def test_congress_values(self) -> None:
        cong = _congress_summary(
            run_id=99,
            source_slug="congress-core",
            total_inserted=50,
            total_written=50,
            load_ok=True,
            configured_congress=118,
            congress_source="explicit-arg",
            include_votes=False,
        )
        result = _make_run_result(congress=cong)
        summary, *_ = _run(run_result=result)
        c = summary["congress"]
        assert c["run_id"] == 99
        assert c["source_slug"] == "congress-core"
        assert c["total_inserted"] == 50
        assert c["total_written"] == 50
        assert c["load_ok"] is True
        assert c["configured_congress"] == 118
        assert c["congress_source"] == "explicit-arg"
        assert c["include_votes"] is False

    def test_snapshot_id_from_result(self) -> None:
        result = _make_run_result(snapshot_id="2024-06-01")
        summary, *_ = _run(run_result=result)
        assert summary["snapshot_id"] == "2024-06-01"

    def test_disclosures_values(self) -> None:
        disc = _disclosures_summary(
            run_id=11,
            requested_chamber="senate",
            artifact_limit=25,
            parse_succeeded=5,
            parse_failed=1,
            transform_count=5,
            total_written=8,
            load_ok=True,
        )
        result = _make_run_result(disclosures=disc)
        summary, *_ = _run(run_result=result)
        d = summary["disclosures"]
        assert d["run_id"] == 11
        assert d["requested_chamber"] == "senate"
        assert d["artifact_limit"] == 25
        assert d["parse_succeeded"] == 5
        assert d["parse_failed"] == 1
        assert d["transform_count"] == 5
        assert d["total_written"] == 8
        assert d["load_ok"] is True

    def test_recompute_values(self) -> None:
        rec = _recompute_summary(run_id=21, rule_fires=7, evidence_cards=3)
        result = _make_run_result(recompute=rec)
        summary, *_ = _run(run_result=result)
        r = summary["recompute"]
        assert r["run_id"] == 21
        assert r["rule_fires"] == 7
        assert r["evidence_cards"] == 3

    def test_publish_values(self) -> None:
        pub = _publish_summary(
            run_id=31,
            snapshot_id="snap-id",
            written_count=15,
            succeeded=True,
            zip_feeds_generated=False,
        )
        result = _make_run_result(publish=pub)
        summary, *_ = _run(run_result=result)
        p = summary["publish"]
        assert p["run_id"] == 31
        assert p["snapshot_id"] == "snap-id"
        assert p["written_count"] == 15
        assert p["succeeded"] is True
        assert p["zip_feeds_generated"] is False

    def test_load_ok_false(self) -> None:
        disc = _disclosures_summary(load_ok=False, total_written=0)
        result = _make_run_result(disclosures=disc)
        summary, *_ = _run(run_result=result)
        assert summary["disclosures"]["load_ok"] is False

    def test_succeeded_false(self) -> None:
        pub = _publish_summary(succeeded=False, written_count=0)
        result = _make_run_result(publish=pub)
        summary, *_ = _run(run_result=result)
        assert summary["publish"]["succeeded"] is False
        assert summary["publish"]["written_count"] == 0

    def test_zero_rule_fires(self) -> None:
        rec = _recompute_summary(rule_fires=0, evidence_cards=0)
        result = _make_run_result(recompute=rec)
        summary, *_ = _run(run_result=result)
        assert summary["recompute"]["rule_fires"] == 0
        assert summary["recompute"]["evidence_cards"] == 0


# ---------------------------------------------------------------------------
# Verify value forwarding
# ---------------------------------------------------------------------------


class TestVerifyValueForwarding:
    def test_verify_ok_true(self) -> None:
        vr = _make_verify_result(ok=True, total_checked=8)
        summary, *_ = _run(run_result=_make_run_result(verify=vr))
        assert summary["verify"]["ok"] is True
        assert summary["verify"]["total_checked"] == 8

    def test_verify_ok_false_with_errors(self) -> None:
        vr = _make_verify_result(ok=False, total_checked=3, total_errors=2)
        summary, *_ = _run(run_result=_make_run_result(verify=vr))
        assert summary["verify"]["ok"] is False
        assert summary["verify"]["total_errors"] == 2
        assert summary["verify"]["total_warnings"] == 0

    def test_verify_warnings(self) -> None:
        vr = _make_verify_result(ok=True, total_checked=4, total_warnings=1)
        summary, *_ = _run(run_result=_make_run_result(verify=vr))
        assert summary["verify"]["ok"] is True
        assert summary["verify"]["total_warnings"] == 1
        assert summary["verify"]["total_errors"] == 0

    def test_verify_zero_checked(self) -> None:
        vr = _make_verify_result(total_checked=0)
        summary, *_ = _run(run_result=_make_run_result(verify=vr))
        assert summary["verify"]["total_checked"] == 0
        assert summary["verify"]["ok"] is True


# ---------------------------------------------------------------------------
# Roundtrip value forwarding
# ---------------------------------------------------------------------------


class TestRoundtripValueForwarding:
    def test_roundtrip_ok_true(self) -> None:
        rt = _make_roundtrip_result(ok=True, total_checked=10)
        summary, *_ = _run(run_result=_make_run_result(roundtrip=rt))
        assert summary["roundtrip"]["ok"] is True
        assert summary["roundtrip"]["total_checked"] == 10

    def test_roundtrip_ok_false_with_errors(self) -> None:
        rt = _make_roundtrip_result(ok=False, total_checked=3, total_errors=2)
        summary, *_ = _run(run_result=_make_run_result(roundtrip=rt))
        assert summary["roundtrip"]["ok"] is False
        assert summary["roundtrip"]["total_errors"] == 2
        assert summary["roundtrip"]["total_warnings"] == 0

    def test_roundtrip_warnings(self) -> None:
        rt = _make_roundtrip_result(ok=True, total_checked=4, total_warnings=1)
        summary, *_ = _run(run_result=_make_run_result(roundtrip=rt))
        assert summary["roundtrip"]["ok"] is True
        assert summary["roundtrip"]["total_warnings"] == 1
        assert summary["roundtrip"]["total_errors"] == 0

    def test_roundtrip_zero_checked(self) -> None:
        rt = _make_roundtrip_result(total_checked=0)
        summary, *_ = _run(run_result=_make_run_result(roundtrip=rt))
        assert summary["roundtrip"]["total_checked"] == 0
        assert summary["roundtrip"]["ok"] is True

    def test_roundtrip_values_are_independent_of_verify(self) -> None:
        """verify.ok=True and roundtrip.ok=False surface correctly in the same summary."""
        vr = _make_verify_result(ok=True, total_checked=5)
        rt = _make_roundtrip_result(ok=False, total_checked=3, total_errors=1)
        result = _make_run_result(verify=vr, roundtrip=rt)
        summary, *_ = _run(run_result=result)
        assert summary["verify"]["ok"] is True
        assert summary["roundtrip"]["ok"] is False
        assert summary["roundtrip"]["total_errors"] == 1


# ---------------------------------------------------------------------------
# JSON contract: full summary is operator-safe JSON
# ---------------------------------------------------------------------------


class TestJsonContract:
    def test_summary_is_json_serializable(self) -> None:
        summary, *_ = _run()
        raw = json.dumps(summary, default=str)
        obj = json.loads(raw)
        assert set(obj) == {
            "snapshot_id",
            "congress",
            "disclosures",
            "recompute",
            "publish",
            "verify",
            "roundtrip",
        }

    def test_flat_stage_values_are_primitives(self) -> None:
        """congress, disclosures, recompute, and publish are flat operator dicts."""
        summary, *_ = _run()
        for stage_name in ("congress", "disclosures", "recompute", "publish"):
            for key, value in summary[stage_name].items():
                assert isinstance(value, (str, int, bool, type(None))), (
                    f"{stage_name}.{key} has type {type(value).__name__}, expected primitive"
                )

    def test_verify_stages_is_list(self) -> None:
        summary, *_ = _run()
        assert isinstance(summary["verify"]["stages"], list)

    def test_roundtrip_stages_is_list(self) -> None:
        summary, *_ = _run()
        assert isinstance(summary["roundtrip"]["stages"], list)

    def test_snapshot_id_is_string(self) -> None:
        summary, *_ = _run()
        assert isinstance(summary["snapshot_id"], str)
