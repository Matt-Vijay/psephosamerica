"""Tests for src/runtime/oracle_local.py.

No live DB — all six runtime boundaries are mocked.
Covers: result shape, call ordering, argument forwarding, failure paths,
        verify stage wiring, and roundtrip stage wiring.

Integration test (TestRunOracleLocalVerifyIntegration) writes a real publish
tree to a temp directory and lets _run_verify execute against it.  It is
skipped automatically when the stage-verifier modules are not yet available.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.pipeline.recompute_run import RecomputeRunResult
from src.export.manifest import manifest_root_sha256
from src.runtime.disclosures_bundle import (
    DisclosureArtifactEntry,
    DisclosuresBundle,
    HouseBundledIndexRow,
    SenateBundledIndexRow,
)
from src.runtime.oracle_contracts import (
    CongressOracleOptions,
    CongressStageSummary,
    LocalOracleOptions,
    LocalOracleRunResult,
)
from src.runtime.oracle_local import run_oracle_local
from src.runtime.publish import PublishRuntimeResult
from src.runtime.publish_roundtrip_types import (
    PublishRoundtripIssue,
    PublishRoundtripResult,
    PublishRoundtripStageResult,
)
from src.runtime.publish_verify_types import (
    PublishVerifyIssue,
    PublishVerifyResult,
    PublishVerifyStageResult,
)
from src.runtime.recompute import RuntimeRecomputeResult

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MODULE = "src.runtime.oracle_local"
_SNAPSHOT_DATE = dt.date(2024, 6, 1)
_CONGRESS = 119
_ARCHIVE = Path("/tmp/congress_archive")
_TARGET_DIR = Path("/tmp/publish_out")

_CONGRESS_OPTIONS = CongressOracleOptions(congress=_CONGRESS)

_OPTIONS = LocalOracleOptions(
    congress_options=_CONGRESS_OPTIONS,
    snapshot_date=_SNAPSHOT_DATE,
    target_dir=_TARGET_DIR,
)

_OPTIONS_WITH_ARTIFACT_ROOT = LocalOracleOptions(
    congress_options=_CONGRESS_OPTIONS,
    snapshot_date=_SNAPSHOT_DATE,
    target_dir=_TARGET_DIR,
    artifact_root=Path("/tmp/artifacts"),
)

_OPTIONS_WITH_SNAPSHOT_ID = LocalOracleOptions(
    congress_options=_CONGRESS_OPTIONS,
    snapshot_date=_SNAPSHOT_DATE,
    target_dir=_TARGET_DIR,
    snapshot_id="custom-snapshot-001",
)


# ---------------------------------------------------------------------------
# Detect whether the verify stage modules are fully wired up.
# Used to gate the real-filesystem integration test.
# ---------------------------------------------------------------------------

try:
    from src.runtime.publish_verify import verify_local_publish as _check_import  # noqa: F401

    _VERIFY_AVAILABLE = True
except (ImportError, AttributeError):
    _VERIFY_AVAILABLE = False


# ---------------------------------------------------------------------------
# Fake results
# ---------------------------------------------------------------------------


def _fake_congress_result() -> MagicMock:
    r = MagicMock()
    r.run_id = 1
    r.data_source = {"id": 3, "slug": "congress-core"}
    r.load_summary.total_inserted = 10
    r.load_summary.total_written = 10
    r.load_summary.ok = True
    return r


def _fake_disclosures_result() -> MagicMock:
    r = MagicMock()
    r.load_result.run_id = 2
    r.load_result.data_source = {"slug": "financial-disclosures"}
    r.load_result.load_summary.total_written = 5
    r.load_result.load_summary.ok = True
    r.parse_result.succeeded_count = 3
    r.parse_result.failed_count = 0
    r.transform_count = 3
    return r


def _fake_scoped_disclosures_result(
    *,
    run_id: int,
    source_slug: str,
    parse_succeeded: int,
    parse_failed: int = 0,
    transform_count: int | None = None,
    total_written: int | None = None,
    load_ok: bool = True,
) -> MagicMock:
    r = MagicMock()
    r.load_result.run_id = run_id
    r.load_result.data_source = {"slug": source_slug}
    r.load_result.load_summary.total_written = (
        total_written if total_written is not None else parse_succeeded
    )
    r.load_result.load_summary.ok = load_ok
    r.parse_result.succeeded_count = parse_succeeded
    r.parse_result.failed_count = parse_failed
    r.transform_count = transform_count if transform_count is not None else parse_succeeded
    return r


def _house_bundle_entry(source_record_id: str) -> DisclosureArtifactEntry:
    return DisclosureArtifactEntry(
        source_record_id=source_record_id,
        chamber="house",
        filing_year=2024,
        storage_uri=f"house/2024/{source_record_id}.pdf",
        source_url=(
            f"https://disclosures.house.gov/public_disc/financial-pdfs/2024/{source_record_id}.pdf"
        ),
        source_slug="house_disclosures",
        artifact_kind="pdf",
        sha256="a" * 64,
        index_row=HouseBundledIndexRow(
            last_name="Smith",
            first_name="Jane",
            suffix="",
            raw_filing_type="O",
            state_dst="CA08",
            filing_date="2024-01-15",
            doc_id=source_record_id,
            filing_kind="annual",
        ),
    )


def _senate_bundle_entry(source_record_id: str) -> DisclosureArtifactEntry:
    return DisclosureArtifactEntry(
        source_record_id=source_record_id,
        chamber="senate",
        filing_year=2024,
        storage_uri=f"senate/2024/{source_record_id}.pdf",
        source_url=f"https://efdsearch.senate.gov/search/view/paper/{source_record_id}/",
        source_slug="senate_disclosures",
        artifact_kind="pdf",
        sha256="b" * 64,
        index_row=SenateBundledIndexRow(
            first_name="John",
            last_name="Doe",
            office="Senator, CA",
            report_type="Annual Report for CY2023",
            date_filed="01/15/2024",
            doc_id=source_record_id,
        ),
    )


def _house_only_bundle() -> DisclosuresBundle:
    return DisclosuresBundle(artifacts=(_house_bundle_entry("HOUSE-ONLY-001"),))


def _fake_recompute_result() -> RuntimeRecomputeResult:
    inner = RecomputeRunResult(
        rule_fires=["fire1", "fire2"], evidence_cards=["card1"], load_summary=None
    )
    return RuntimeRecomputeResult(
        data_source={"id": 5, "slug": "conflict-recompute"},
        run_id=7,
        recompute_result=inner,
    )


def _fake_publish_result() -> PublishRuntimeResult:
    publish_inner = MagicMock()
    publish_inner.planned_count = 43
    publish_inner.written_count = 42
    publish_inner.succeeded = True
    publish_inner.verification_failures = []
    return PublishRuntimeResult(
        data_source={"id": 6, "slug": "snapshot-publish"},
        run_id=9,
        snapshot_id=_SNAPSHOT_DATE.isoformat(),
        publish_result=publish_inner,
    )


def _fake_verify_result() -> PublishVerifyResult:
    """A passing verify result with zero items checked (empty publish tree)."""
    return PublishVerifyResult(
        stages=(
            PublishVerifyStageResult(stage="manifest", checked=1, issues=()),
            PublishVerifyStageResult(stage="profiles", checked=0, issues=()),
            PublishVerifyStageResult(stage="evidence", checked=0, issues=()),
            PublishVerifyStageResult(stage="zip", checked=0, issues=()),
        )
    )


def _fake_verify_failure() -> PublishVerifyResult:
    """A failing verify result — missing manifest."""
    return PublishVerifyResult(
        stages=(
            PublishVerifyStageResult(
                stage="manifest",
                checked=1,
                issues=(
                    PublishVerifyIssue(
                        stage="manifest",
                        message="manifest file missing: snapshots/2024-06-01/manifest.json",
                        severity="error",
                        path="snapshots/2024-06-01/manifest.json",
                    ),
                ),
            ),
            PublishVerifyStageResult(stage="profiles", checked=0, issues=()),
            PublishVerifyStageResult(stage="evidence", checked=0, issues=()),
            PublishVerifyStageResult(stage="zip", checked=0, issues=()),
        )
    )


def _fake_roundtrip_result() -> PublishRoundtripResult:
    """A passing roundtrip result with a representative stage breakdown."""
    return PublishRoundtripResult(
        stages=(
            PublishRoundtripStageResult(stage="snapshot", checked=5, issues=()),
            PublishRoundtripStageResult(stage="profiles", checked=2, issues=()),
            PublishRoundtripStageResult(stage="evidence", checked=3, issues=()),
            PublishRoundtripStageResult(stage="zip", checked=0, issues=()),
            PublishRoundtripStageResult(stage="homepage", checked=0, issues=()),
        )
    )


def _fake_roundtrip_failure() -> PublishRoundtripResult:
    """A failing roundtrip result — snapshot stage error."""
    return PublishRoundtripResult(
        stages=(
            PublishRoundtripStageResult(
                stage="snapshot",
                checked=0,
                issues=(
                    PublishRoundtripIssue(
                        stage="snapshot",
                        message="no manifest.json found under snapshots/",
                        severity="error",
                    ),
                ),
            ),
            PublishRoundtripStageResult(stage="profiles", checked=0, issues=()),
            PublishRoundtripStageResult(stage="evidence", checked=0, issues=()),
            PublishRoundtripStageResult(stage="zip", checked=0, issues=()),
            PublishRoundtripStageResult(stage="homepage", checked=0, issues=()),
        )
    )


# ---------------------------------------------------------------------------
# Patch helper
# ---------------------------------------------------------------------------


def _patched_oracle(
    *,
    congress_result: Any = None,
    disclosures_result: Any = None,
    recompute_result: Any = None,
    publish_result: Any = None,
    verify_result: Any = None,
    roundtrip_result: Any = None,
):
    """Context manager tuple that patches all six runtime boundaries."""
    if congress_result is None:
        congress_result = _fake_congress_result()
    if disclosures_result is None:
        disclosures_result = _fake_disclosures_result()
    if recompute_result is None:
        recompute_result = _fake_recompute_result()
    if publish_result is None:
        publish_result = _fake_publish_result()
    if verify_result is None:
        verify_result = _fake_verify_result()
    if roundtrip_result is None:
        roundtrip_result = _fake_roundtrip_result()

    return (
        patch(f"{_MODULE}.run_congress_archive_load", return_value=congress_result),
        patch(f"{_MODULE}.run_disclosures_bundle_process", return_value=disclosures_result),
        patch(f"{_MODULE}.run_recompute_runtime", return_value=recompute_result),
        patch(f"{_MODULE}.run_publish_runtime", return_value=publish_result),
        patch(f"{_MODULE}._run_verify", return_value=verify_result),
        patch(f"{_MODULE}._run_roundtrip", return_value=roundtrip_result),
    )


# ---------------------------------------------------------------------------
# Happy path: result shape
# ---------------------------------------------------------------------------


class TestRunOracleLocalResultShape:
    def test_returns_local_oracle_run_result(self):
        conn = MagicMock()
        bundle = MagicMock()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            result = run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        assert isinstance(result, LocalOracleRunResult)

    def test_snapshot_id_defaults_to_snapshot_date_isoformat(self):
        conn = MagicMock()
        bundle = MagicMock()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            result = run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        assert result.snapshot_id == _SNAPSHOT_DATE.isoformat()

    def test_explicit_snapshot_id_is_forwarded(self):
        conn = MagicMock()
        bundle = MagicMock()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            result = run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS_WITH_SNAPSHOT_ID)

        assert result.snapshot_id == "custom-snapshot-001"

    def test_congress_stage_is_congress_stage_summary(self):
        conn = MagicMock()
        bundle = MagicMock()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            result = run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        assert isinstance(result.congress, CongressStageSummary)

    def test_congress_summary_contains_expected_keys(self):
        conn = MagicMock()
        bundle = MagicMock()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            result = run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        assert result.congress.run_id == 1
        assert result.congress.source_slug == "congress-core"
        assert result.congress.total_inserted == 10
        assert result.congress.total_written == 10
        assert result.congress.load_ok is True

    def test_disclosures_summary_contains_expected_keys(self):
        conn = MagicMock()
        bundle = MagicMock()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            result = run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        assert "run_id" in result.disclosures
        assert "parse_succeeded" in result.disclosures
        assert "parse_failed" in result.disclosures
        assert "transform_count" in result.disclosures
        assert "total_written" in result.disclosures
        assert "load_ok" in result.disclosures

    def test_recompute_summary_contains_expected_keys(self):
        conn = MagicMock()
        bundle = MagicMock()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            result = run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        assert "run_id" in result.recompute
        assert "rule_fires" in result.recompute
        assert "evidence_cards" in result.recompute
        assert "source_slug" in result.recompute

    def test_publish_summary_contains_expected_keys(self):
        conn = MagicMock()
        bundle = MagicMock()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            result = run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        assert "run_id" in result.publish
        assert "snapshot_id" in result.publish
        assert "planned_count" in result.publish
        assert "written_count" in result.publish
        assert "succeeded" in result.publish
        assert "verification_failures" in result.publish

    def test_verify_field_is_publish_verify_result(self):
        conn = MagicMock()
        bundle = MagicMock()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            result = run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        assert isinstance(result.verify, PublishVerifyResult)

    def test_verify_ok_is_true_when_no_errors(self):
        conn = MagicMock()
        bundle = MagicMock()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            result = run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        assert result.verify.ok is True

    def test_roundtrip_field_is_publish_roundtrip_result(self):
        conn = MagicMock()
        bundle = MagicMock()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            result = run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        assert isinstance(result.roundtrip, PublishRoundtripResult)

    def test_roundtrip_ok_is_true_when_no_errors(self):
        conn = MagicMock()
        bundle = MagicMock()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            result = run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        assert result.roundtrip.ok is True


# ---------------------------------------------------------------------------
# Argument forwarding
# ---------------------------------------------------------------------------


class TestRunOracleLocalArgForwarding:
    def test_congress_archive_load_receives_archive_path_and_congress(self):
        conn = MagicMock()
        bundle = MagicMock()

        with (
            patch(
                f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()
            ) as m_congress,
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        args, kwargs = m_congress.call_args
        assert args[0] is conn
        assert args[1] == _ARCHIVE
        from src.runtime.congress_options import CongressLoadOptions

        assert isinstance(args[2], CongressLoadOptions)
        assert args[2].congress == _CONGRESS

    def test_congress_archive_load_does_not_request_votes(self):
        conn = MagicMock()
        bundle = MagicMock()

        with (
            patch(
                f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()
            ) as m_congress,
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        congress_load_options = m_congress.call_args[0][2]
        assert congress_load_options.include_votes is False

    def test_result_surfaces_local_oracle_scope_honestly(self):
        conn = MagicMock()
        bundle = MagicMock()
        options = LocalOracleOptions(
            congress_options=CongressOracleOptions(
                congress=118,
                chamber="house",
                limit=25,
                congress_source="current-date-default",
            ),
            snapshot_date=_SNAPSHOT_DATE,
            target_dir=_TARGET_DIR,
        )

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            result = run_oracle_local(conn, _ARCHIVE, bundle, options)

        assert result.congress.configured_congress == 118
        assert result.congress.congress_source == "current-date-default"
        assert result.congress.include_votes is False
        assert result.disclosures["requested_chamber"] == "house"
        assert result.disclosures["artifact_limit"] == 25
        assert result.publish["zip_feeds_generated"] is False

    def test_disclosures_bundle_process_receives_conn_and_bundle(self):
        conn = MagicMock()
        bundle = _house_only_bundle()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ) as m_disc,
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        args, kwargs = m_disc.call_args
        assert args[0] is conn
        assert args[1] == bundle

    def test_disclosures_bundle_process_passes_artifact_root(self):
        conn = MagicMock()
        bundle = _house_only_bundle()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ) as m_disc,
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS_WITH_ARTIFACT_ROOT)

        kwargs = m_disc.call_args.kwargs
        assert kwargs.get("local_root") == _OPTIONS_WITH_ARTIFACT_ROOT.artifact_root

    def test_disclosures_bundle_process_local_root_none_when_absent(self):
        conn = MagicMock()
        bundle = _house_only_bundle()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ) as m_disc,
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        kwargs = m_disc.call_args.kwargs
        assert kwargs.get("local_root") is None

    def test_disclosures_bundle_process_scopes_bundle_by_requested_chamber_and_limit(self):
        conn = MagicMock()
        bundle = DisclosuresBundle(
            artifacts=(
                _house_bundle_entry("HOUSE-001"),
                _senate_bundle_entry("SEN-001"),
                _house_bundle_entry("HOUSE-002"),
            )
        )
        options = LocalOracleOptions(
            congress_options=CongressOracleOptions(
                congress=118,
                chamber="house",
                limit=1,
                congress_source="current-date-default",
            ),
            snapshot_date=_SNAPSHOT_DATE,
            target_dir=_TARGET_DIR,
        )

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process",
                return_value=_fake_scoped_disclosures_result(
                    run_id=11,
                    source_slug="house_disclosures",
                    parse_succeeded=1,
                ),
            ) as m_disc,
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            result = run_oracle_local(conn, _ARCHIVE, bundle, options)

        scoped_bundle = m_disc.call_args.args[1]
        assert [entry.source_record_id for entry in scoped_bundle.artifacts] == ["HOUSE-001"]
        assert result.disclosures["processed_artifact_count"] == 1
        assert result.disclosures["source_slugs"] == ["house_disclosures"]

    def test_disclosures_bundle_process_splits_mixed_bundle_by_source_slug(self):
        conn = MagicMock()
        bundle = DisclosuresBundle(
            artifacts=(
                _house_bundle_entry("HOUSE-001"),
                _senate_bundle_entry("SEN-001"),
                _house_bundle_entry("HOUSE-002"),
            )
        )

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process",
                side_effect=[
                    _fake_scoped_disclosures_result(
                        run_id=21,
                        source_slug="house_disclosures",
                        parse_succeeded=2,
                        total_written=2,
                    ),
                    _fake_scoped_disclosures_result(
                        run_id=22,
                        source_slug="senate_disclosures",
                        parse_succeeded=1,
                        total_written=1,
                    ),
                ],
            ) as m_disc,
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            result = run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        assert m_disc.call_count == 2
        house_bundle = m_disc.call_args_list[0].args[1]
        senate_bundle = m_disc.call_args_list[1].args[1]
        assert [entry.source_record_id for entry in house_bundle.artifacts] == [
            "HOUSE-001",
            "HOUSE-002",
        ]
        assert [entry.source_record_id for entry in senate_bundle.artifacts] == ["SEN-001"]
        assert result.disclosures["run_id"] is None
        assert result.disclosures["run_ids"] == [21, 22]
        assert result.disclosures["source_slug"] is None
        assert result.disclosures["source_slugs"] == [
            "house_disclosures",
            "senate_disclosures",
        ]
        assert result.disclosures["parse_succeeded"] == 3
        assert result.disclosures["total_written"] == 3
        assert result.disclosures["processed_artifact_count"] == 3

    def test_recompute_receives_conn_and_snapshot_date(self):
        conn = MagicMock()
        bundle = MagicMock()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(
                f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()
            ) as m_recompute,
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        args, _ = m_recompute.call_args
        assert args[0] is conn
        assert args[1] == _SNAPSHOT_DATE

    def test_publish_receives_conn_snapshot_date_and_target_dir(self):
        conn = MagicMock()
        bundle = MagicMock()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(
                f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()
            ) as m_publish,
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        args, kwargs = m_publish.call_args
        assert args[0] is conn
        assert args[1] == _SNAPSHOT_DATE
        assert args[2] == _TARGET_DIR

    def test_publish_receives_explicit_snapshot_id(self):
        conn = MagicMock()
        bundle = MagicMock()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(
                f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()
            ) as m_publish,
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS_WITH_SNAPSHOT_ID)

        _, kwargs = m_publish.call_args
        assert kwargs.get("snapshot_id") == "custom-snapshot-001"

    def test_publish_receives_empty_zip_bundle(self):
        """Local oracle runs pass an empty ZipBundleInputs — no ZIP feed generation."""
        conn = MagicMock()
        bundle = MagicMock()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(
                f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()
            ) as m_publish,
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        from src.pipeline.publish_snapshot_run import ZipBundleInputs

        args, _ = m_publish.call_args
        zip_inputs = args[3]
        assert isinstance(zip_inputs, ZipBundleInputs)
        assert zip_inputs.zip5_codes == []
        assert zip_inputs.zip_district_rows == []
        assert zip_inputs.district_member_rows == []
        assert zip_inputs.senator_rows == []

    def test_verify_receives_target_dir(self):
        conn = MagicMock()
        bundle = MagicMock()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()) as m_verify,
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        args, _ = m_verify.call_args
        assert args[0] == _TARGET_DIR

    def test_roundtrip_receives_conn_and_target_dir(self):
        conn = MagicMock()
        bundle = MagicMock()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(
                f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()
            ) as m_roundtrip,
        ):
            run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        args, _ = m_roundtrip.call_args
        assert args[0] is conn
        assert args[1] == _TARGET_DIR


# ---------------------------------------------------------------------------
# Summary value accuracy
# ---------------------------------------------------------------------------


class TestRunOracleLocalSummaryValues:
    def test_congress_summary_reflects_congress_load_result(self):
        conn = MagicMock()
        bundle = MagicMock()
        congress = _fake_congress_result()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=congress),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            result = run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        assert result.congress.run_id == 1
        assert result.congress.source_slug == "congress-core"
        assert result.congress.total_inserted == 10
        assert result.congress.total_written == 10
        assert result.congress.load_ok is True

    def test_recompute_summary_counts_rule_fires_and_evidence_cards(self):
        conn = MagicMock()
        bundle = MagicMock()
        recompute = _fake_recompute_result()  # 2 fires, 1 card

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=recompute),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            result = run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        assert result.recompute["rule_fires"] == 2
        assert result.recompute["evidence_cards"] == 1

    def test_publish_summary_reflects_publish_result(self):
        conn = MagicMock()
        bundle = MagicMock()
        publish = _fake_publish_result()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=publish),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            result = run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        assert result.publish["run_id"] == 9
        assert result.publish["planned_count"] == 43
        assert result.publish["written_count"] == 42
        assert result.publish["succeeded"] is True

    def test_publish_summary_reflects_publish_failures(self):
        conn = MagicMock()
        bundle = MagicMock()
        publish = _fake_publish_result()
        publish.publish_result.succeeded = False
        publish.publish_result.verification_failures = ["members/alice.json: sha256 mismatch"]

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=publish),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            result = run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        assert result.publish["succeeded"] is False
        assert result.publish["verification_failures"] == ["members/alice.json: sha256 mismatch"]

    def test_disclosures_summary_reflects_bundle_process_result(self):
        conn = MagicMock()
        bundle = _house_only_bundle()
        disc = _fake_disclosures_result()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(f"{_MODULE}.run_disclosures_bundle_process", return_value=disc),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            result = run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        assert result.disclosures["run_id"] == 2
        assert result.disclosures["parse_succeeded"] == 3
        assert result.disclosures["parse_failed"] == 0
        assert result.disclosures["transform_count"] == 3
        assert result.disclosures["total_written"] == 5
        assert result.disclosures["load_ok"] is True

    def test_verify_result_is_propagated_into_result(self):
        conn = MagicMock()
        bundle = MagicMock()
        verify = _fake_verify_result()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=verify),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            result = run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        assert result.verify is verify

    def test_roundtrip_result_is_propagated_into_result(self):
        conn = MagicMock()
        bundle = MagicMock()
        roundtrip = _fake_roundtrip_result()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=roundtrip),
        ):
            result = run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        assert result.roundtrip is roundtrip


# ---------------------------------------------------------------------------
# Stage ordering
# ---------------------------------------------------------------------------


class TestRunOracleLocalStageOrdering:
    def test_stages_called_in_order(self):
        """congress → disclosures → recompute → publish → verify → roundtrip."""
        call_log: list[str] = []
        conn = MagicMock()
        bundle = _house_only_bundle()

        def _congress(*_a, **_kw):
            call_log.append("congress")
            return _fake_congress_result()

        def _disclosures(*_a, **_kw):
            call_log.append("disclosures")
            return _fake_disclosures_result()

        def _recompute(*_a, **_kw):
            call_log.append("recompute")
            return _fake_recompute_result()

        def _publish(*_a, **_kw):
            call_log.append("publish")
            return _fake_publish_result()

        def _verify(*_a, **_kw):
            call_log.append("verify")
            return _fake_verify_result()

        def _roundtrip(*_a, **_kw):
            call_log.append("roundtrip")
            return _fake_roundtrip_result()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", side_effect=_congress),
            patch(f"{_MODULE}.run_disclosures_bundle_process", side_effect=_disclosures),
            patch(f"{_MODULE}.run_recompute_runtime", side_effect=_recompute),
            patch(f"{_MODULE}.run_publish_runtime", side_effect=_publish),
            patch(f"{_MODULE}._run_verify", side_effect=_verify),
            patch(f"{_MODULE}._run_roundtrip", side_effect=_roundtrip),
        ):
            run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        assert call_log == [
            "congress",
            "disclosures",
            "recompute",
            "publish",
            "verify",
            "roundtrip",
        ]

    def test_verify_is_called_after_publish(self):
        """verify must run after publish, not before."""
        publish_done = False
        conn = MagicMock()
        bundle = MagicMock()

        def _publish(*_a, **_kw):
            nonlocal publish_done
            publish_done = True
            return _fake_publish_result()

        def _verify(*_a, **_kw):
            assert publish_done, "verify was called before publish completed"
            return _fake_verify_result()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", side_effect=_publish),
            patch(f"{_MODULE}._run_verify", side_effect=_verify),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

    def test_roundtrip_is_called_after_verify(self):
        """roundtrip must run after verify, not before."""
        verify_done = False
        conn = MagicMock()
        bundle = MagicMock()

        def _verify(*_a, **_kw):
            nonlocal verify_done
            verify_done = True
            return _fake_verify_result()

        def _roundtrip(*_a, **_kw):
            assert verify_done, "roundtrip was called before verify completed"
            return _fake_roundtrip_result()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", side_effect=_verify),
            patch(f"{_MODULE}._run_roundtrip", side_effect=_roundtrip),
        ):
            run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)


# ---------------------------------------------------------------------------
# Verify failure surfacing
# ---------------------------------------------------------------------------


class TestRunOracleLocalVerifyFailureSurfacing:
    def test_verify_failure_is_present_in_result_not_raised(self):
        """A failed verify result must be stored in result.verify, not raised."""
        conn = MagicMock()
        bundle = MagicMock()
        failure = _fake_verify_failure()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=failure),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            # Must not raise even though verify failed.
            result = run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        assert isinstance(result.verify, PublishVerifyResult)
        assert result.verify.ok is False
        assert result.verify.total_errors == 1

    def test_verify_failure_preserves_error_details(self):
        conn = MagicMock()
        bundle = MagicMock()
        failure = _fake_verify_failure()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=failure),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            result = run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        issues = result.verify.all_issues()
        assert len(issues) == 1
        assert issues[0].stage == "manifest"
        assert issues[0].severity == "error"

    def test_verify_failure_does_not_prevent_result_from_being_returned(self):
        """All other stage summaries must still be present when verify fails."""
        conn = MagicMock()
        bundle = MagicMock()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_failure()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            result = run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        assert isinstance(result, LocalOracleRunResult)
        assert isinstance(result.congress, CongressStageSummary)
        assert "run_id" in result.disclosures
        assert "rule_fires" in result.recompute
        assert "written_count" in result.publish


# ---------------------------------------------------------------------------
# Roundtrip failure surfacing
# ---------------------------------------------------------------------------


class TestRunOracleLocalRoundtripFailureSurfacing:
    def test_roundtrip_failure_is_present_in_result_not_raised(self):
        """A failed roundtrip result must be stored in result.roundtrip, not raised."""
        conn = MagicMock()
        bundle = MagicMock()
        failure = _fake_roundtrip_failure()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=failure),
        ):
            # Must not raise even though roundtrip failed.
            result = run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        assert isinstance(result.roundtrip, PublishRoundtripResult)
        assert result.roundtrip.ok is False
        assert result.roundtrip.total_errors == 1

    def test_roundtrip_failure_preserves_error_details(self):
        conn = MagicMock()
        bundle = MagicMock()
        failure = _fake_roundtrip_failure()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=failure),
        ):
            result = run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        issues = result.roundtrip.all_issues()
        assert len(issues) == 1
        assert issues[0].stage == "snapshot"
        assert issues[0].severity == "error"

    def test_roundtrip_failure_does_not_prevent_result_from_being_returned(self):
        """All other stage summaries must still be present when roundtrip fails."""
        conn = MagicMock()
        bundle = MagicMock()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_failure()),
        ):
            result = run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        assert isinstance(result, LocalOracleRunResult)
        assert isinstance(result.congress, CongressStageSummary)
        assert "run_id" in result.disclosures
        assert "rule_fires" in result.recompute
        assert "written_count" in result.publish
        assert result.verify.ok is True

    def test_verify_ok_and_roundtrip_failure_are_independent(self):
        """verify.ok=True and roundtrip.ok=False can coexist in one result."""
        conn = MagicMock()
        bundle = MagicMock()

        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=_fake_publish_result()),
            patch(f"{_MODULE}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_failure()),
        ):
            result = run_oracle_local(conn, _ARCHIVE, bundle, _OPTIONS)

        assert result.verify.ok is True
        assert result.roundtrip.ok is False


# ---------------------------------------------------------------------------
# Integration: real temp publish tree
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _VERIFY_AVAILABLE,
    reason="publish_verify stage modules not yet available",
)
class TestRunOracleLocalVerifyIntegration:
    def test_verify_passes_against_real_minimal_publish_tree(self, tmp_path: Path):
        """Write a real (zero-entry) publish tree; let _run_verify execute for real.
        _run_roundtrip is mocked because roundtrip stages have DB dependencies."""
        from datetime import UTC, datetime

        snapshot_id = _SNAPSHOT_DATE.isoformat()
        target_dir = tmp_path / "publish"

        # Write a minimal valid manifest with zero entries to the real tree.
        manifest_rel = f"snapshots/{snapshot_id}/manifest.json"
        manifest_file = target_dir / manifest_rel
        manifest_file.parent.mkdir(parents=True, exist_ok=True)
        manifest_content = json.dumps(
            {
                "snapshot_id": snapshot_id,
                "created_at": datetime.now(UTC).isoformat(),
                "entries": [],
                "total_files": 0,
                "total_bytes": 0,
                "root_sha256": manifest_root_sha256([]),
            },
            sort_keys=True,
        ).encode()
        manifest_file.write_bytes(manifest_content)

        options = LocalOracleOptions(
            congress_options=_CONGRESS_OPTIONS,
            snapshot_date=_SNAPSHOT_DATE,
            target_dir=target_dir,
        )
        publish_result = PublishRuntimeResult(
            data_source={"id": 6, "slug": "snapshot-publish"},
            run_id=9,
            snapshot_id=snapshot_id,
            publish_result=MagicMock(written_count=0, succeeded=True),
        )

        conn = MagicMock()
        bundle = MagicMock()

        # verify runs against the real tree; roundtrip is mocked (DB-backed).
        with (
            patch(f"{_MODULE}.run_congress_archive_load", return_value=_fake_congress_result()),
            patch(
                f"{_MODULE}.run_disclosures_bundle_process", return_value=_fake_disclosures_result()
            ),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=_fake_recompute_result()),
            patch(f"{_MODULE}.run_publish_runtime", return_value=publish_result),
            patch(f"{_MODULE}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            result = run_oracle_local(conn, _ARCHIVE, bundle, options)

        assert isinstance(result.verify, PublishVerifyResult)
        assert result.verify.ok is True
        assert result.verify.total_errors == 0
        assert isinstance(result.roundtrip, PublishRoundtripResult)
