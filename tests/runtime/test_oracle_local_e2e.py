"""Real local-oracle end-to-end integration tests.

Unlike tests/runtime/test_oracle_local.py (which mocks all four runtime
boundaries), these tests exercise run_oracle_local with:

  * A real temp Congress archive directory built from inline JSON
  * A real DisclosuresBundle built from inline JSON via disclosures_bundle_from_dict

Only the innermost DB-touching layers are patched; the archive-reading path
(CongressArchiveClient, fetch_members, …) and the bundle-process path
(_validate_bundle, _BundleIndexProvider construction, transform_parse_sessions)
run against the real objects.

The oracle passes artifact_root=None (default), so the bundle-process step 3
auto-build is skipped (no local files); run_disclosure_parse_runtime is patched
at the DB+IO boundary.  _BundleIndexProvider and transform_parse_sessions run
for real, aligned with the stronger bundle-process path proved in
test_disclosures_bundle_process_e2e.py.

Patch strategy:
  - src.runtime.congress_archive.run_congress_load_runtime   — DB write
  - src.runtime.disclosures_bundle_process.stage_disclosures_bundle  — DB write
  - src.runtime.disclosures_bundle_process.run_disclosure_parse_runtime — DB + IO
  - src.runtime.disclosures_bundle_process.run_disclosures_load_runtime — DB write
  - src.runtime.oracle_local.run_recompute_runtime            — entire stage
  - src.runtime.oracle_local.run_publish_runtime              — entire stage

No network calls.  No binary fixtures.  All temp files generated from inline
JSON/dict data in this module.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from src.db.load_report import TableWriteResult, WarnErrorSummary, build_load_summary
from src.pipeline.recompute_run import RecomputeRunResult
from src.runtime.congress import CongressLoadResult
from src.runtime.disclosures_bundle import (
    DisclosuresBundle,
    HouseBundledIndexRow,
    disclosures_bundle_from_dict,
)
from src.runtime.oracle_contracts import (
    CongressOracleOptions,
    CongressStageSummary,
    LocalOracleOptions,
    LocalOracleRunResult,
)
from src.runtime.oracle_local import run_oracle_local
from src.runtime.publish import PublishRuntimeResult
from src.runtime.recompute import RuntimeRecomputeResult

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SNAPSHOT_DATE = dt.date(2024, 6, 1)
_CONGRESS = 119

_CONGRESS_OPTIONS = CongressOracleOptions(congress=_CONGRESS)

# ---------------------------------------------------------------------------
# Patch-target strings
# ---------------------------------------------------------------------------

_CONGRESS_LOAD_RT = "src.runtime.congress_archive.run_congress_load_runtime"
_STAGE_BUNDLE = "src.runtime.disclosures_bundle_process.stage_disclosures_bundle"
_PARSE_RT = "src.runtime.disclosures_bundle_process.run_disclosure_parse_runtime"
_DISC_LOAD_RT = "src.runtime.disclosures_bundle_process.run_disclosures_load_runtime"
_RECOMPUTE_RT = "src.runtime.oracle_local.run_recompute_runtime"
_PUBLISH_RT = "src.runtime.oracle_local.run_publish_runtime"

# ---------------------------------------------------------------------------
# Real input builders — fixture support 1: temp Congress archive
# ---------------------------------------------------------------------------


def _make_congress_archive(tmp_path: Path, congress: int = _CONGRESS) -> Path:
    """Build a minimal real Congress archive directory from inline JSON.

    Creates all required subdirectories and a one-member members.json so that
    CongressArchiveClient can read real file paths without any network calls.
    committees.json and bills.json are empty-list payloads; cosponsors/,
    member_details/, and bill_details/ directories are created empty so that
    the loader scan returns zero entries without raising.
    """
    root = tmp_path / "congress"
    root.mkdir(parents=True, exist_ok=True)

    members_payload: dict[str, Any] = {
        "members": [
            {
                "bioguideId": "A000001",
                "firstName": "Jane",
                "lastName": "Doe",
                "directOrderName": "Jane Doe",
                # terms.item is empty so _parse_date is not called with an int
                "terms": {"item": [{"chamber": "House of Representatives"}]},
                "currentMember": True,
                "state": "CA",
                "partyName": "Democratic",
            }
        ]
    }
    (root / "members.json").write_text(json.dumps(members_payload), encoding="utf-8")
    (root / "committees.json").write_text(json.dumps({"committees": []}), encoding="utf-8")
    (root / "bills.json").write_text(json.dumps({"bills": []}), encoding="utf-8")
    (root / "member_details").mkdir()
    (root / "bill_details").mkdir()
    (root / "cosponsors").mkdir()

    return root


# ---------------------------------------------------------------------------
# Real input builders — fixture support 2: DisclosuresBundle from inline JSON
# ---------------------------------------------------------------------------

_SHA256 = "a" * 64


def _make_disclosures_bundle(*, include_house: bool = True) -> DisclosuresBundle:
    """Build a real DisclosuresBundle from an inline JSON dict.

    When include_house is True, the bundle carries one House artifact whose
    index_row is deterministic.  When False, returns an empty bundle.
    """
    if not include_house:
        return disclosures_bundle_from_dict({"artifacts": []})

    entry: dict[str, Any] = {
        "source_record_id": "99001",
        "chamber": "house",
        "filing_year": 2024,
        "storage_uri": "house/2024/99001.pdf",
        "source_url": "https://disclosures.house.gov/public_disc/financial-pdfs/2024/99001.pdf",
        "source_slug": "house_disclosures",
        "artifact_kind": "pdf",
        "sha256": _SHA256,
        "index_row": {
            "last_name": "Doe",
            "first_name": "Jane",
            "suffix": "",
            "raw_filing_type": "O",
            "state_dst": "CA08",
            "filing_date": "2024-01-15",
            "doc_id": "99001",
            "filing_kind": "annual",
        },
    }
    return disclosures_bundle_from_dict({"artifacts": [entry]})


# ---------------------------------------------------------------------------
# Fake results for patched DB layers
# ---------------------------------------------------------------------------


def _fake_congress_load_result() -> CongressLoadResult:
    """Return a real CongressLoadResult without a live DB."""
    we = WarnErrorSummary()
    tr = TableWriteResult(table="member", inserted=1)
    summary = build_load_summary([tr], warn_error=we, run_id=1)
    return CongressLoadResult(
        data_source={"id": 1, "slug": "congress-core"},
        run_id=1,
        load_summary=summary,
    )


def _fake_stage_result() -> MagicMock:
    r = MagicMock(name="stage_result")
    r.staged_count = 0
    r.mirrored_count = 0
    return r


def _fake_parse_runtime_result() -> MagicMock:
    """Minimal parse result; parse_sessions is empty so transform is a no-op."""
    r = MagicMock(name="parse_result")
    r.succeeded_count = 0
    r.failed_count = 0
    r.processed_count = 0
    r.parse_sessions = ()
    return r


def _fake_disclosures_load_result() -> MagicMock:
    r = MagicMock(name="disc_load_result")
    r.run_id = 2
    r.data_source = {"id": 3, "slug": "disclosure-load"}
    r.load_summary.total_written = 0
    r.load_summary.ok = True
    return r


def _fake_recompute_result() -> RuntimeRecomputeResult:
    inner = RecomputeRunResult(rule_fires=[], evidence_cards=[], load_summary=None)
    return RuntimeRecomputeResult(
        data_source={"id": 5, "slug": "conflict-recompute"},
        run_id=7,
        recompute_result=inner,
    )


def _fake_publish_result() -> PublishRuntimeResult:
    publish_inner = MagicMock(name="publish_inner")
    publish_inner.written_count = 0
    publish_inner.succeeded = True
    return PublishRuntimeResult(
        data_source={"id": 6, "slug": "snapshot-publish"},
        run_id=9,
        snapshot_id=_SNAPSHOT_DATE.isoformat(),
        publish_result=publish_inner,
    )


# ---------------------------------------------------------------------------
# Helper: build LocalOracleOptions for a given tmp_path
# ---------------------------------------------------------------------------


def _options(tmp_path: Path, *, snapshot_id: str | None = None) -> LocalOracleOptions:
    return LocalOracleOptions(
        congress_options=_CONGRESS_OPTIONS,
        snapshot_date=_SNAPSHOT_DATE,
        target_dir=tmp_path / "publish",
        snapshot_id=snapshot_id,
    )


# ---------------------------------------------------------------------------
# Helper: run oracle with all DB layers patched and real inputs
# ---------------------------------------------------------------------------


def _run_oracle(
    conn: Any,
    archive: Path,
    bundle: DisclosuresBundle,
    options: LocalOracleOptions,
) -> LocalOracleRunResult:
    """Invoke run_oracle_local with real archive + bundle, DB layers patched."""
    with (
        patch(_CONGRESS_LOAD_RT, return_value=_fake_congress_load_result()),
        patch(_STAGE_BUNDLE, return_value=_fake_stage_result()),
        patch(_PARSE_RT, return_value=_fake_parse_runtime_result()),
        patch(_DISC_LOAD_RT, return_value=_fake_disclosures_load_result()),
        patch(_RECOMPUTE_RT, return_value=_fake_recompute_result()),
        patch(_PUBLISH_RT, return_value=_fake_publish_result()),
    ):
        return run_oracle_local(conn, archive, bundle, options)


# ---------------------------------------------------------------------------
# Tests: real input object types are used (not MagicMock stubs)
# ---------------------------------------------------------------------------


class TestRealInputObjectTypes:
    """Confirm the helpers produce real typed objects, not MagicMock stubs."""

    def test_make_congress_archive_returns_real_directory(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        assert archive.is_dir()

    def test_make_congress_archive_has_members_json(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        members_file = archive / "members.json"
        assert members_file.is_file()
        payload = json.loads(members_file.read_text())
        assert payload["members"][0]["bioguideId"] == "A000001"

    def test_make_congress_archive_has_subdirs(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        for subdir in ("member_details", "bill_details", "cosponsors"):
            assert (archive / subdir).is_dir(), f"missing subdir: {subdir}"

    def test_make_disclosures_bundle_returns_real_bundle(self) -> None:
        bundle = _make_disclosures_bundle()
        assert isinstance(bundle, DisclosuresBundle)

    def test_make_disclosures_bundle_has_one_artifact(self) -> None:
        bundle = _make_disclosures_bundle(include_house=True)
        assert len(bundle.artifacts) == 1

    def test_make_disclosures_bundle_artifact_chamber_is_house(self) -> None:
        bundle = _make_disclosures_bundle(include_house=True)
        assert bundle.artifacts[0].chamber == "house"

    def test_make_disclosures_bundle_empty_when_requested(self) -> None:
        bundle = _make_disclosures_bundle(include_house=False)
        assert len(bundle.artifacts) == 0

    def test_bundle_artifact_index_row_is_typed(self) -> None:
        from src.runtime.disclosures_bundle import HouseBundledIndexRow

        bundle = _make_disclosures_bundle()
        assert isinstance(bundle.artifacts[0].index_row, HouseBundledIndexRow)


# ---------------------------------------------------------------------------
# Tests: oracle result shape with real archive + bundle inputs
# ---------------------------------------------------------------------------


class TestOracleLocalE2EResultShape:
    """run_oracle_local returns a fully typed LocalOracleRunResult."""

    def test_returns_local_oracle_run_result(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle()
        opts = _options(tmp_path)
        result = _run_oracle(MagicMock(), archive, bundle, opts)
        assert isinstance(result, LocalOracleRunResult)

    def test_congress_field_is_congress_stage_summary(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle()
        opts = _options(tmp_path)
        result = _run_oracle(MagicMock(), archive, bundle, opts)
        assert isinstance(result.congress, CongressStageSummary)

    def test_disclosures_is_mapping(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle()
        opts = _options(tmp_path)
        result = _run_oracle(MagicMock(), archive, bundle, opts)
        assert isinstance(result.disclosures, dict)

    def test_recompute_is_mapping(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle()
        opts = _options(tmp_path)
        result = _run_oracle(MagicMock(), archive, bundle, opts)
        assert isinstance(result.recompute, dict)

    def test_publish_is_mapping(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle()
        opts = _options(tmp_path)
        result = _run_oracle(MagicMock(), archive, bundle, opts)
        assert isinstance(result.publish, dict)

    def test_snapshot_id_defaults_to_date_isoformat(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle()
        opts = _options(tmp_path)
        result = _run_oracle(MagicMock(), archive, bundle, opts)
        assert result.snapshot_id == _SNAPSHOT_DATE.isoformat()

    def test_explicit_snapshot_id_propagates(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle()
        opts = _options(tmp_path, snapshot_id="e2e-run-001")
        result = _run_oracle(MagicMock(), archive, bundle, opts)
        assert result.snapshot_id == "e2e-run-001"


# ---------------------------------------------------------------------------
# Tests: congress stage summary shape and values
# ---------------------------------------------------------------------------


class TestCongressStageSummaryShape:
    """congress field is a typed, inspectable CongressStageSummary."""

    def test_congress_run_id_is_int(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle()
        result = _run_oracle(MagicMock(), archive, bundle, _options(tmp_path))
        assert isinstance(result.congress.run_id, int)

    def test_congress_source_slug_is_str(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle()
        result = _run_oracle(MagicMock(), archive, bundle, _options(tmp_path))
        assert isinstance(result.congress.source_slug, str)

    def test_congress_total_inserted_is_int(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle()
        result = _run_oracle(MagicMock(), archive, bundle, _options(tmp_path))
        assert isinstance(result.congress.total_inserted, int)

    def test_congress_total_written_is_int(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle()
        result = _run_oracle(MagicMock(), archive, bundle, _options(tmp_path))
        assert isinstance(result.congress.total_written, int)

    def test_congress_load_ok_is_bool(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle()
        result = _run_oracle(MagicMock(), archive, bundle, _options(tmp_path))
        assert isinstance(result.congress.load_ok, bool)

    def test_congress_summary_reflects_load_result(self, tmp_path: Path) -> None:
        """Congress summary values derive from the faked CongressLoadResult."""
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle()
        result = _run_oracle(MagicMock(), archive, bundle, _options(tmp_path))
        # _fake_congress_load_result inserts 1 member row
        assert result.congress.run_id == 1
        assert result.congress.source_slug == "congress-core"
        assert result.congress.total_inserted == 1
        assert result.congress.load_ok is True


# ---------------------------------------------------------------------------
# Tests: disclosures summary keys and values
# ---------------------------------------------------------------------------


class TestDisclosuresSummaryShape:
    """disclosures dict carries the expected keys."""

    def _result(self, tmp_path: Path) -> LocalOracleRunResult:
        return _run_oracle(
            MagicMock(),
            _make_congress_archive(tmp_path),
            _make_disclosures_bundle(),
            _options(tmp_path),
        )

    def test_run_id_present(self, tmp_path: Path) -> None:
        assert "run_id" in self._result(tmp_path).disclosures

    def test_source_slug_present(self, tmp_path: Path) -> None:
        assert "source_slug" in self._result(tmp_path).disclosures

    def test_parse_succeeded_present(self, tmp_path: Path) -> None:
        assert "parse_succeeded" in self._result(tmp_path).disclosures

    def test_parse_failed_present(self, tmp_path: Path) -> None:
        assert "parse_failed" in self._result(tmp_path).disclosures

    def test_transform_count_present(self, tmp_path: Path) -> None:
        assert "transform_count" in self._result(tmp_path).disclosures

    def test_total_written_present(self, tmp_path: Path) -> None:
        assert "total_written" in self._result(tmp_path).disclosures

    def test_load_ok_present(self, tmp_path: Path) -> None:
        assert "load_ok" in self._result(tmp_path).disclosures

    def test_values_from_faked_results(self, tmp_path: Path) -> None:
        d = self._result(tmp_path).disclosures
        assert d["run_id"] == 2
        assert d["parse_succeeded"] == 0
        assert d["parse_failed"] == 0
        assert d["total_written"] == 0
        assert d["load_ok"] is True


# ---------------------------------------------------------------------------
# Tests: stage ordering verified with real inputs
# ---------------------------------------------------------------------------


class TestStageOrderingWithRealInputs:
    """Stages fire in the prescribed order: congress → disclosures → recompute → publish."""

    def test_stages_called_in_order(self, tmp_path: Path) -> None:
        call_log: list[str] = []
        conn = MagicMock()
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle()
        opts = _options(tmp_path)

        def _congress_load(*_a, **_kw):
            call_log.append("congress_load")
            return _fake_congress_load_result()

        def _stage(*_a, **_kw):
            call_log.append("stage")
            return _fake_stage_result()

        def _parse(*_a, **_kw):
            call_log.append("parse")
            return _fake_parse_runtime_result()

        def _disc_load(*_a, **_kw):
            call_log.append("disc_load")
            return _fake_disclosures_load_result()

        def _recompute(*_a, **_kw):
            call_log.append("recompute")
            return _fake_recompute_result()

        def _publish(*_a, **_kw):
            call_log.append("publish")
            return _fake_publish_result()

        with (
            patch(_CONGRESS_LOAD_RT, side_effect=_congress_load),
            patch(_STAGE_BUNDLE, side_effect=_stage),
            patch(_PARSE_RT, side_effect=_parse),
            patch(_DISC_LOAD_RT, side_effect=_disc_load),
            patch(_RECOMPUTE_RT, side_effect=_recompute),
            patch(_PUBLISH_RT, side_effect=_publish),
        ):
            run_oracle_local(conn, archive, bundle, opts)

        # congress_load fires inside run_congress_archive_load (stage 1)
        # stage, parse, disc_load fire inside run_disclosures_bundle_process (stage 2)
        # recompute is stage 3, publish is stage 4
        assert call_log.index("congress_load") < call_log.index("stage")
        assert call_log.index("stage") < call_log.index("parse")
        assert call_log.index("parse") < call_log.index("disc_load")
        assert call_log.index("disc_load") < call_log.index("recompute")
        assert call_log.index("recompute") < call_log.index("publish")

    def test_all_six_db_boundaries_called_exactly_once(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle()
        opts = _options(tmp_path)

        with (
            patch(_CONGRESS_LOAD_RT, return_value=_fake_congress_load_result()) as p1,
            patch(_STAGE_BUNDLE, return_value=_fake_stage_result()) as p2,
            patch(_PARSE_RT, return_value=_fake_parse_runtime_result()) as p3,
            patch(_DISC_LOAD_RT, return_value=_fake_disclosures_load_result()) as p4,
            patch(_RECOMPUTE_RT, return_value=_fake_recompute_result()) as p5,
            patch(_PUBLISH_RT, return_value=_fake_publish_result()) as p6,
        ):
            run_oracle_local(MagicMock(), archive, bundle, opts)

        p1.assert_called_once()
        p2.assert_called_once()
        p3.assert_called_once()
        p4.assert_called_once()
        p5.assert_called_once()
        p6.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: real archive reading (fetch_members runs against temp JSON files)
# ---------------------------------------------------------------------------


class TestCongressArchiveReadsRealFiles:
    """The archive file-reading path runs for real; no archive-level mock."""

    def test_fetch_members_called_with_real_archive(self, tmp_path: Path) -> None:
        """CongressArchiveClient reads the real members.json we wrote."""
        from src.ingest.congress.archive import CongressArchive
        from src.ingest.congress.archive_client import CongressArchiveClient
        from src.ingest.congress.live_api import fetch_members

        archive_path = _make_congress_archive(tmp_path)
        archive = CongressArchive(archive_path, _CONGRESS)
        client = CongressArchiveClient(archive)
        members = fetch_members(client, _CONGRESS)
        assert len(members) == 1
        assert members[0].bioguide_id == "A000001"

    def test_congress_archive_load_uses_path_to_read_members(self, tmp_path: Path) -> None:
        """run_congress_archive_load exercises file reads before the DB write."""
        conn = MagicMock()
        archive_path = _make_congress_archive(tmp_path)

        captured: list[Any] = []

        def _capture_and_return(captured_conn, inputs):
            captured.append(inputs)
            return _fake_congress_load_result()

        with patch(_CONGRESS_LOAD_RT, side_effect=_capture_and_return):
            from src.runtime.congress_archive import run_congress_archive_load
            from src.runtime.congress_options import CongressLoadOptions

            run_congress_archive_load(
                conn,
                archive_path,
                CongressLoadOptions(congress=_CONGRESS, include_votes=False),
            )

        assert len(captured) == 1
        ingest_inputs = captured[0]
        # The one member from members.json was parsed into a MemberRecord
        assert len(ingest_inputs.members) == 1
        assert ingest_inputs.members[0].bioguide_id == "A000001"


# ---------------------------------------------------------------------------
# Tests: real bundle validation (_BundleIndexProvider built from real bundle)
# ---------------------------------------------------------------------------


class TestDisclosuresBundleValidation:
    """_validate_bundle and _BundleIndexProvider construction run against real bundle."""

    def test_bundle_is_real_not_mock(self) -> None:
        bundle = _make_disclosures_bundle()
        # A real DisclosuresBundle is not a MagicMock
        assert not isinstance(bundle, MagicMock)

    def test_real_bundle_passes_validation(self) -> None:
        """_validate_bundle accepts a real DisclosuresBundle without raising."""
        from src.runtime.disclosures_bundle_process import _validate_bundle

        bundle = _make_disclosures_bundle()
        # Should not raise
        _validate_bundle(bundle)

    def test_bundle_provider_built_from_real_artifacts(self) -> None:
        """_BundleIndexProvider indexes real bundle artifacts."""
        from src.runtime.disclosures_bundle_process import _BundleIndexProvider

        bundle = _make_disclosures_bundle(include_house=True)
        provider = _BundleIndexProvider(bundle)
        # The one artifact should be indexed under (chamber, year, source_record_id)
        key = ("house", 2024, "99001")
        assert key in provider._lookup

    def test_empty_bundle_passes_validation(self) -> None:
        from src.runtime.disclosures_bundle_process import _validate_bundle

        bundle = _make_disclosures_bundle(include_house=False)
        _validate_bundle(bundle)  # must not raise

    def test_empty_bundle_provider_has_empty_lookup(self) -> None:
        from src.runtime.disclosures_bundle_process import _BundleIndexProvider

        bundle = _make_disclosures_bundle(include_house=False)
        provider = _BundleIndexProvider(bundle)
        assert len(provider._lookup) == 0


# ---------------------------------------------------------------------------
# Tests: full e2e with empty bundle — zero-artifact path
# ---------------------------------------------------------------------------


class TestOracleLocalE2EEmptyBundle:
    """Empty bundle (no artifacts) flows through without errors."""

    def test_result_type_with_empty_bundle(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle(include_house=False)
        result = _run_oracle(MagicMock(), archive, bundle, _options(tmp_path))
        assert isinstance(result, LocalOracleRunResult)

    def test_congress_summary_present_with_empty_bundle(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle(include_house=False)
        result = _run_oracle(MagicMock(), archive, bundle, _options(tmp_path))
        assert isinstance(result.congress, CongressStageSummary)

    def test_parse_counts_zero_with_empty_bundle(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle(include_house=False)
        result = _run_oracle(MagicMock(), archive, bundle, _options(tmp_path))
        assert result.disclosures["parse_succeeded"] == 0
        assert result.disclosures["parse_failed"] == 0

    def test_transform_count_zero_with_empty_bundle(self, tmp_path: Path) -> None:
        """transform_parse_sessions runs for real (pure function); empty input → 0."""
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle(include_house=False)
        result = _run_oracle(MagicMock(), archive, bundle, _options(tmp_path))
        assert result.disclosures["transform_count"] == 0


# ---------------------------------------------------------------------------
# Tests: bundle-process alignment — _BundleIndexProvider + transform run real
# ---------------------------------------------------------------------------


class TestBundleProcessAlignmentInOracle:
    """Verify bundle-process internals that run for real inside run_oracle_local.

    artifact_root=None (oracle default) means:
      - step 3 auto-build is skipped (no local files required)
      - run_disclosure_parse_runtime is patched (DB+IO boundary)
      - _BundleIndexProvider construction and transform_parse_sessions run real

    This class probes those real-path components directly and confirms they
    remain consistent with the stronger bundle-process path in
    test_disclosures_bundle_process_e2e.py.
    """

    def test_bundle_index_provider_indexes_oracle_bundle_artifact(self) -> None:
        """_BundleIndexProvider built from _make_disclosures_bundle indexes correctly."""
        from src.runtime.disclosures_bundle_process import _BundleIndexProvider

        bundle = _make_disclosures_bundle(include_house=True)
        provider = _BundleIndexProvider(bundle)

        key = ("house", 2024, "99001")
        assert key in provider._lookup
        assert isinstance(provider._lookup[key], HouseBundledIndexRow)

    def test_bundle_index_provider_resolves_artifact_row_for_oracle_bundle(self) -> None:
        from src.runtime.disclosures_bundle_process import _BundleIndexProvider

        bundle = _make_disclosures_bundle(include_house=True)
        provider = _BundleIndexProvider(bundle)

        row = {
            "id": 1,
            "chamber": "house",
            "filing_year": 2024,
            "source_record_id": "99001",
        }
        matches = provider.load_matches([row])
        assert len(matches) == 1
        assert isinstance(matches[0].index_row, HouseBundledIndexRow)
        assert matches[0].index_row.last_name == "Doe"

    def test_transform_count_zero_when_parse_sessions_empty(self, tmp_path: Path) -> None:
        """transform_parse_sessions runs for real; empty parse_sessions → 0."""
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle(include_house=True)
        result = _run_oracle(MagicMock(), archive, bundle, _options(tmp_path))
        # _fake_parse_runtime_result returns parse_sessions=() so transform skips all
        assert result.disclosures["transform_count"] == 0

    def test_bundle_validation_runs_before_stage_in_oracle(self, tmp_path: Path) -> None:
        """_validate_bundle raises TypeError before stage if bundle is malformed."""
        archive = _make_congress_archive(tmp_path)
        opts = _options(tmp_path)
        conn = MagicMock()

        with (
            patch(_CONGRESS_LOAD_RT, return_value=_fake_congress_load_result()),
            patch(_STAGE_BUNDLE) as mock_stage,
            patch(_PARSE_RT, return_value=_fake_parse_runtime_result()),
            patch(_DISC_LOAD_RT, return_value=_fake_disclosures_load_result()),
            patch(_RECOMPUTE_RT, return_value=_fake_recompute_result()),
            patch(_PUBLISH_RT, return_value=_fake_publish_result()),
        ):
            import pytest

            with pytest.raises(TypeError, match="artifacts"):
                run_oracle_local(conn, archive, object(), opts)

        mock_stage.assert_not_called()

    def test_richer_bundle_index_row_fields_accessible(self) -> None:
        """All typed fields on HouseBundledIndexRow are accessible from oracle bundle."""
        from src.runtime.disclosures_bundle_process import _BundleIndexProvider

        bundle = _make_disclosures_bundle(include_house=True)
        provider = _BundleIndexProvider(bundle)
        row = {"id": 1, "chamber": "house", "filing_year": 2024, "source_record_id": "99001"}
        matches = provider.load_matches([row])
        index_row = matches[0].index_row

        assert isinstance(index_row, HouseBundledIndexRow)
        assert index_row.first_name == "Jane"
        assert index_row.last_name == "Doe"
        assert index_row.state_dst == "CA08"
        assert index_row.doc_id == "99001"
        assert index_row.filing_kind == "annual"
