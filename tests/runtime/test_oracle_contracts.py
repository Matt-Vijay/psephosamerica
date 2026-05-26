"""Tests for src/runtime/oracle_contracts.py."""

from __future__ import annotations

import datetime as dt
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from src.runtime.disclosures_bundle import DisclosuresBundle
from src.runtime.oracle_contracts import (
    CongressOracleOptions,
    CongressStageSummary,
    LocalOracleInputs,
    LocalOracleOptions,
    LocalOracleRunResult,
)
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

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_DATE = dt.date(2025, 6, 1)
_CONGRESS_OPTS = CongressOracleOptions(congress=119)
_EMPTY_BUNDLE = DisclosuresBundle(artifacts=())
_CONGRESS_SUMMARY = CongressStageSummary(
    run_id=1,
    source_slug="congress_core",
    total_inserted=500,
    total_written=500,
    load_ok=True,
)
_PUBLISH_SUMMARY = {
    "run_id": 9,
    "snapshot_id": "2025-06-01",
    "source_slug": "snapshot-publish",
    "written_count": 12,
    "succeeded": True,
}
_PUBLISH_VERIFY_OK = PublishVerifyResult(
    stages=(
        PublishVerifyStageResult(stage="manifest", checked=3, issues=()),
        PublishVerifyStageResult(stage="profiles", checked=10, issues=()),
        PublishVerifyStageResult(stage="evidence", checked=5, issues=()),
        PublishVerifyStageResult(stage="zip", checked=1, issues=()),
    )
)
_PUBLISH_VERIFY_WITH_ERROR = PublishVerifyResult(
    stages=(
        PublishVerifyStageResult(
            stage="manifest",
            checked=3,
            issues=(
                PublishVerifyIssue(
                    stage="manifest",
                    message="missing manifest.json",
                    severity="error",
                    path="manifest.json",
                ),
            ),
        ),
        PublishVerifyStageResult(stage="profiles", checked=0, issues=()),
        PublishVerifyStageResult(stage="evidence", checked=0, issues=()),
        PublishVerifyStageResult(stage="zip", checked=0, issues=()),
    )
)
_ROUNDTRIP_OK = PublishRoundtripResult(
    stages=(
        PublishRoundtripStageResult(stage="snapshot", checked=1, issues=()),
        PublishRoundtripStageResult(stage="profiles", checked=10, issues=()),
        PublishRoundtripStageResult(stage="evidence", checked=5, issues=()),
        PublishRoundtripStageResult(stage="ontology", checked=2, issues=()),
        PublishRoundtripStageResult(stage="prediction", checked=4, issues=()),
        PublishRoundtripStageResult(stage="zip", checked=3, issues=()),
        PublishRoundtripStageResult(stage="homepage", checked=1, issues=()),
        PublishRoundtripStageResult(stage="lookup", checked=1, issues=()),
    )
)
_ROUNDTRIP_WITH_ERROR = PublishRoundtripResult(
    stages=(
        PublishRoundtripStageResult(stage="snapshot", checked=1, issues=()),
        PublishRoundtripStageResult(
            stage="profiles",
            checked=10,
            issues=(
                PublishRoundtripIssue(
                    stage="profiles",
                    message="published profile missing for A000001",
                    severity="error",
                    path="profiles/a000001.json",
                ),
            ),
        ),
        PublishRoundtripStageResult(stage="evidence", checked=5, issues=()),
        PublishRoundtripStageResult(stage="zip", checked=3, issues=()),
        PublishRoundtripStageResult(stage="homepage", checked=1, issues=()),
        PublishRoundtripStageResult(stage="lookup", checked=1, issues=()),
    )
)


# ---------------------------------------------------------------------------
# CongressOracleOptions — defaults
# ---------------------------------------------------------------------------


class TestCongressOracleOptionsDefaults:
    def test_chamber_defaults_none(self) -> None:
        opts = CongressOracleOptions(congress=119)
        assert opts.chamber is None

    def test_limit_defaults_none(self) -> None:
        opts = CongressOracleOptions(congress=119)
        assert opts.limit is None

    def test_parser_name_defaults(self) -> None:
        opts = CongressOracleOptions(congress=119)
        assert opts.parser_name == "text_extract_v1"

    def test_parser_version_defaults(self) -> None:
        opts = CongressOracleOptions(congress=119)
        assert opts.parser_version == "1"


class TestCongressOracleOptionsExplicit:
    def test_congress_stored(self) -> None:
        opts = CongressOracleOptions(congress=118)
        assert opts.congress == 118

    def test_chamber_stored(self) -> None:
        opts = CongressOracleOptions(congress=119, chamber="house")
        assert opts.chamber == "house"

    def test_limit_stored(self) -> None:
        opts = CongressOracleOptions(congress=119, limit=10)
        assert opts.limit == 10

    def test_parser_name_stored(self) -> None:
        opts = CongressOracleOptions(congress=119, parser_name="my_parser")
        assert opts.parser_name == "my_parser"

    def test_parser_version_stored(self) -> None:
        opts = CongressOracleOptions(congress=119, parser_version="2")
        assert opts.parser_version == "2"

    def test_all_fields_together(self) -> None:
        opts = CongressOracleOptions(
            congress=119,
            chamber="senate",
            limit=5,
            parser_name="text_extract_v1",
            parser_version="1",
        )
        assert opts.congress == 119
        assert opts.chamber == "senate"
        assert opts.limit == 5


class TestCongressOracleOptionsImmutability:
    def test_frozen_congress(self) -> None:
        opts = CongressOracleOptions(congress=119)
        with pytest.raises(FrozenInstanceError):
            opts.congress = 120  # type: ignore[misc]

    def test_frozen_chamber(self) -> None:
        opts = CongressOracleOptions(congress=119, chamber="house")
        with pytest.raises(FrozenInstanceError):
            opts.chamber = "senate"  # type: ignore[misc]

    def test_frozen_limit(self) -> None:
        opts = CongressOracleOptions(congress=119, limit=5)
        with pytest.raises(FrozenInstanceError):
            opts.limit = 10  # type: ignore[misc]


class TestCongressOracleOptionsEquality:
    def test_equal_when_fields_match(self) -> None:
        a = CongressOracleOptions(congress=119, chamber="house", limit=3)
        b = CongressOracleOptions(congress=119, chamber="house", limit=3)
        assert a == b

    def test_not_equal_on_congress_diff(self) -> None:
        a = CongressOracleOptions(congress=118)
        b = CongressOracleOptions(congress=119)
        assert a != b

    def test_not_equal_on_chamber_diff(self) -> None:
        a = CongressOracleOptions(congress=119, chamber="house")
        b = CongressOracleOptions(congress=119, chamber="senate")
        assert a != b


# ---------------------------------------------------------------------------
# LocalOracleInputs
# ---------------------------------------------------------------------------


class TestLocalOracleInputsStored:
    def test_congress_archive_stored(self, tmp_path: Path) -> None:
        archive = tmp_path / "congress"
        inputs = LocalOracleInputs(
            congress_archive=archive,
            disclosures_bundle=_EMPTY_BUNDLE,
        )
        assert inputs.congress_archive == archive

    def test_disclosures_bundle_stored(self, tmp_path: Path) -> None:
        archive = tmp_path / "congress"
        inputs = LocalOracleInputs(
            congress_archive=archive,
            disclosures_bundle=_EMPTY_BUNDLE,
        )
        assert inputs.disclosures_bundle is _EMPTY_BUNDLE

    def test_bundle_with_artifacts(self, tmp_path: Path) -> None:
        # A non-empty bundle is accepted without modification.
        from src.runtime.disclosures_bundle import (
            DisclosureArtifactEntry,
            HouseBundledIndexRow,
        )

        entry = DisclosureArtifactEntry(
            source_record_id="99999",
            chamber="house",
            filing_year=2024,
            storage_uri="house/2024/99999.pdf",
            source_url="https://disclosures.house.gov/public_disc/financial-pdfs/2024/99999.pdf",
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
                doc_id="99999",
                filing_kind="annual",
            ),
        )
        bundle = DisclosuresBundle(artifacts=(entry,))
        inputs = LocalOracleInputs(
            congress_archive=tmp_path / "congress",
            disclosures_bundle=bundle,
        )
        assert len(inputs.disclosures_bundle.artifacts) == 1


class TestLocalOracleInputsImmutability:
    def test_frozen_congress_archive(self, tmp_path: Path) -> None:
        inputs = LocalOracleInputs(
            congress_archive=tmp_path / "congress",
            disclosures_bundle=_EMPTY_BUNDLE,
        )
        with pytest.raises(FrozenInstanceError):
            inputs.congress_archive = tmp_path / "other"  # type: ignore[misc]

    def test_frozen_disclosures_bundle(self, tmp_path: Path) -> None:
        inputs = LocalOracleInputs(
            congress_archive=tmp_path / "congress",
            disclosures_bundle=_EMPTY_BUNDLE,
        )
        with pytest.raises(FrozenInstanceError):
            inputs.disclosures_bundle = DisclosuresBundle(artifacts=())  # type: ignore[misc]


class TestLocalOracleInputsEquality:
    def test_equal_when_fields_match(self, tmp_path: Path) -> None:
        archive = tmp_path / "congress"
        a = LocalOracleInputs(congress_archive=archive, disclosures_bundle=_EMPTY_BUNDLE)
        b = LocalOracleInputs(congress_archive=archive, disclosures_bundle=_EMPTY_BUNDLE)
        assert a == b

    def test_not_equal_on_archive_diff(self, tmp_path: Path) -> None:
        a = LocalOracleInputs(
            congress_archive=tmp_path / "congress_a",
            disclosures_bundle=_EMPTY_BUNDLE,
        )
        b = LocalOracleInputs(
            congress_archive=tmp_path / "congress_b",
            disclosures_bundle=_EMPTY_BUNDLE,
        )
        assert a != b


# ---------------------------------------------------------------------------
# LocalOracleOptions — defaults
# ---------------------------------------------------------------------------


class TestLocalOracleOptionsDefaults:
    def test_snapshot_id_defaults_none(self, tmp_path: Path) -> None:
        opts = LocalOracleOptions(
            congress_options=_CONGRESS_OPTS,
            snapshot_date=_DATE,
            target_dir=tmp_path,
        )
        assert opts.snapshot_id is None

    def test_artifact_root_defaults_none(self, tmp_path: Path) -> None:
        opts = LocalOracleOptions(
            congress_options=_CONGRESS_OPTS,
            snapshot_date=_DATE,
            target_dir=tmp_path,
        )
        assert opts.artifact_root is None


class TestLocalOracleOptionsExplicit:
    def test_congress_options_stored(self, tmp_path: Path) -> None:
        opts = LocalOracleOptions(
            congress_options=_CONGRESS_OPTS,
            snapshot_date=_DATE,
            target_dir=tmp_path,
        )
        assert opts.congress_options is _CONGRESS_OPTS

    def test_snapshot_date_stored(self, tmp_path: Path) -> None:
        opts = LocalOracleOptions(
            congress_options=_CONGRESS_OPTS,
            snapshot_date=_DATE,
            target_dir=tmp_path,
        )
        assert opts.snapshot_date == _DATE

    def test_target_dir_stored(self, tmp_path: Path) -> None:
        opts = LocalOracleOptions(
            congress_options=_CONGRESS_OPTS,
            snapshot_date=_DATE,
            target_dir=tmp_path,
        )
        assert opts.target_dir == tmp_path

    def test_snapshot_id_stored(self, tmp_path: Path) -> None:
        opts = LocalOracleOptions(
            congress_options=_CONGRESS_OPTS,
            snapshot_date=_DATE,
            target_dir=tmp_path,
            snapshot_id="2025-06-01-manual",
        )
        assert opts.snapshot_id == "2025-06-01-manual"

    def test_artifact_root_stored(self, tmp_path: Path) -> None:
        root = tmp_path / "artifacts"
        opts = LocalOracleOptions(
            congress_options=_CONGRESS_OPTS,
            snapshot_date=_DATE,
            target_dir=tmp_path,
            artifact_root=root,
        )
        assert opts.artifact_root == root


class TestLocalOracleOptionsResolvedSnapshotId:
    def test_derives_from_date_when_none(self, tmp_path: Path) -> None:
        opts = LocalOracleOptions(
            congress_options=_CONGRESS_OPTS,
            snapshot_date=dt.date(2025, 6, 1),
            target_dir=tmp_path,
        )
        assert opts.resolved_snapshot_id() == "2025-06-01"

    def test_uses_explicit_snapshot_id(self, tmp_path: Path) -> None:
        opts = LocalOracleOptions(
            congress_options=_CONGRESS_OPTS,
            snapshot_date=dt.date(2025, 6, 1),
            target_dir=tmp_path,
            snapshot_id="custom-id",
        )
        assert opts.resolved_snapshot_id() == "custom-id"

    def test_date_iso_format_shape(self, tmp_path: Path) -> None:
        opts = LocalOracleOptions(
            congress_options=_CONGRESS_OPTS,
            snapshot_date=dt.date(2024, 12, 31),
            target_dir=tmp_path,
        )
        assert opts.resolved_snapshot_id() == "2024-12-31"


class TestLocalOracleOptionsImmutability:
    def test_frozen_snapshot_date(self, tmp_path: Path) -> None:
        opts = LocalOracleOptions(
            congress_options=_CONGRESS_OPTS,
            snapshot_date=_DATE,
            target_dir=tmp_path,
        )
        with pytest.raises(FrozenInstanceError):
            opts.snapshot_date = dt.date(2026, 1, 1)  # type: ignore[misc]

    def test_frozen_target_dir(self, tmp_path: Path) -> None:
        opts = LocalOracleOptions(
            congress_options=_CONGRESS_OPTS,
            snapshot_date=_DATE,
            target_dir=tmp_path,
        )
        with pytest.raises(FrozenInstanceError):
            opts.target_dir = Path("/other")  # type: ignore[misc]

    def test_frozen_snapshot_id(self, tmp_path: Path) -> None:
        opts = LocalOracleOptions(
            congress_options=_CONGRESS_OPTS,
            snapshot_date=_DATE,
            target_dir=tmp_path,
            snapshot_id="abc",
        )
        with pytest.raises(FrozenInstanceError):
            opts.snapshot_id = "xyz"  # type: ignore[misc]


class TestLocalOracleOptionsEquality:
    def test_equal_when_fields_match(self, tmp_path: Path) -> None:
        a = LocalOracleOptions(
            congress_options=_CONGRESS_OPTS,
            snapshot_date=_DATE,
            target_dir=tmp_path,
        )
        b = LocalOracleOptions(
            congress_options=_CONGRESS_OPTS,
            snapshot_date=_DATE,
            target_dir=tmp_path,
        )
        assert a == b

    def test_not_equal_on_date_diff(self, tmp_path: Path) -> None:
        a = LocalOracleOptions(
            congress_options=_CONGRESS_OPTS,
            snapshot_date=dt.date(2025, 1, 1),
            target_dir=tmp_path,
        )
        b = LocalOracleOptions(
            congress_options=_CONGRESS_OPTS,
            snapshot_date=dt.date(2025, 6, 1),
            target_dir=tmp_path,
        )
        assert a != b

    def test_not_equal_on_snapshot_id_diff(self, tmp_path: Path) -> None:
        a = LocalOracleOptions(
            congress_options=_CONGRESS_OPTS,
            snapshot_date=_DATE,
            target_dir=tmp_path,
            snapshot_id="a",
        )
        b = LocalOracleOptions(
            congress_options=_CONGRESS_OPTS,
            snapshot_date=_DATE,
            target_dir=tmp_path,
            snapshot_id="b",
        )
        assert a != b


# ---------------------------------------------------------------------------
# CongressStageSummary
# ---------------------------------------------------------------------------


class TestCongressStageSummaryStored:
    def test_run_id_stored(self) -> None:
        s = CongressStageSummary(
            run_id=42,
            source_slug="congress_core",
            total_inserted=1000,
            total_written=1000,
            load_ok=True,
        )
        assert s.run_id == 42

    def test_source_slug_stored(self) -> None:
        s = CongressStageSummary(
            run_id=1,
            source_slug="congress_core",
            total_inserted=0,
            total_written=0,
            load_ok=True,
        )
        assert s.source_slug == "congress_core"

    def test_total_inserted_stored(self) -> None:
        s = CongressStageSummary(
            run_id=1,
            source_slug="congress_core",
            total_inserted=750,
            total_written=800,
            load_ok=True,
        )
        assert s.total_inserted == 750

    def test_total_written_stored(self) -> None:
        s = CongressStageSummary(
            run_id=1,
            source_slug="congress_core",
            total_inserted=750,
            total_written=800,
            load_ok=True,
        )
        assert s.total_written == 800

    def test_load_ok_true(self) -> None:
        s = CongressStageSummary(
            run_id=1,
            source_slug="congress_core",
            total_inserted=0,
            total_written=0,
            load_ok=True,
        )
        assert s.load_ok is True

    def test_load_ok_false(self) -> None:
        s = CongressStageSummary(
            run_id=1,
            source_slug="congress_core",
            total_inserted=0,
            total_written=0,
            load_ok=False,
        )
        assert s.load_ok is False


class TestCongressStageSummaryImmutability:
    def test_frozen_run_id(self) -> None:
        s = CongressStageSummary(
            run_id=1,
            source_slug="congress_core",
            total_inserted=0,
            total_written=0,
            load_ok=True,
        )
        with pytest.raises(FrozenInstanceError):
            s.run_id = 99  # type: ignore[misc]

    def test_frozen_source_slug(self) -> None:
        s = CongressStageSummary(
            run_id=1,
            source_slug="congress_core",
            total_inserted=0,
            total_written=0,
            load_ok=True,
        )
        with pytest.raises(FrozenInstanceError):
            s.source_slug = "other"  # type: ignore[misc]

    def test_frozen_load_ok(self) -> None:
        s = CongressStageSummary(
            run_id=1,
            source_slug="congress_core",
            total_inserted=0,
            total_written=0,
            load_ok=True,
        )
        with pytest.raises(FrozenInstanceError):
            s.load_ok = False  # type: ignore[misc]


class TestCongressStageSummaryEquality:
    def test_equal_when_fields_match(self) -> None:
        a = CongressStageSummary(
            run_id=1, source_slug="congress_core", total_inserted=10, total_written=10, load_ok=True
        )
        b = CongressStageSummary(
            run_id=1, source_slug="congress_core", total_inserted=10, total_written=10, load_ok=True
        )
        assert a == b

    def test_not_equal_on_run_id_diff(self) -> None:
        a = CongressStageSummary(
            run_id=1, source_slug="congress_core", total_inserted=0, total_written=0, load_ok=True
        )
        b = CongressStageSummary(
            run_id=2, source_slug="congress_core", total_inserted=0, total_written=0, load_ok=True
        )
        assert a != b

    def test_not_equal_on_load_ok_diff(self) -> None:
        a = CongressStageSummary(
            run_id=1, source_slug="congress_core", total_inserted=0, total_written=0, load_ok=True
        )
        b = CongressStageSummary(
            run_id=1, source_slug="congress_core", total_inserted=0, total_written=0, load_ok=False
        )
        assert a != b


# ---------------------------------------------------------------------------
# LocalOracleRunResult
# ---------------------------------------------------------------------------


class TestLocalOracleRunResultStored:
    def _make(self) -> LocalOracleRunResult:
        return LocalOracleRunResult(
            snapshot_id="2025-06-01",
            congress=_CONGRESS_SUMMARY,
            disclosures={"filings_found": 42, "loaded": 40},
            recompute={"run_id": 7, "rule_fires": 5, "evidence_cards": 3},
            publish=_PUBLISH_SUMMARY,
            verify=_PUBLISH_VERIFY_OK,
            roundtrip=_ROUNDTRIP_OK,
        )

    def test_snapshot_id_stored(self) -> None:
        result = self._make()
        assert result.snapshot_id == "2025-06-01"

    def test_congress_stored(self) -> None:
        result = self._make()
        assert result.congress is _CONGRESS_SUMMARY

    def test_congress_fields_accessible(self) -> None:
        result = self._make()
        assert result.congress.run_id == 1
        assert result.congress.source_slug == "congress_core"
        assert result.congress.total_inserted == 500
        assert result.congress.load_ok is True

    def test_disclosures_stored(self) -> None:
        result = self._make()
        assert result.disclosures["filings_found"] == 42
        assert result.disclosures["loaded"] == 40

    def test_recompute_stored(self) -> None:
        result = self._make()
        assert result.recompute["run_id"] == 7
        assert result.recompute["rule_fires"] == 5
        assert result.recompute["evidence_cards"] == 3

    def test_publish_summary_stored(self) -> None:
        result = self._make()
        assert result.publish["run_id"] == 9
        assert result.publish["succeeded"] is True

    def test_verify_is_publish_verify_result(self) -> None:
        result = self._make()
        assert isinstance(result.verify, PublishVerifyResult)

    def test_verify_ok_when_no_errors(self) -> None:
        result = self._make()
        assert result.verify.ok is True

    def test_verify_stage_count(self) -> None:
        result = self._make()
        assert len(result.verify.stages) == 4

    def test_verify_total_checked(self) -> None:
        result = self._make()
        # manifest(3) + profiles(10) + evidence(5) + zip(1)
        assert result.verify.total_checked == 19

    def test_verify_stage_result_lookup(self) -> None:
        result = self._make()
        manifest = result.verify.stage_result("manifest")
        assert manifest is not None
        assert manifest.checked == 3

    def test_verify_with_error_not_ok(self) -> None:
        result = LocalOracleRunResult(
            snapshot_id="2025-06-01",
            congress=_CONGRESS_SUMMARY,
            disclosures={},
            recompute={},
            publish=_PUBLISH_SUMMARY,
            verify=_PUBLISH_VERIFY_WITH_ERROR,
            roundtrip=_ROUNDTRIP_OK,
        )
        assert result.verify.ok is False
        assert result.verify.total_errors == 1

    def test_verify_all_issues_empty_when_ok(self) -> None:
        result = self._make()
        assert result.verify.all_issues() == []

    def test_verify_all_issues_populated_on_error(self) -> None:
        result = LocalOracleRunResult(
            snapshot_id="2025-06-01",
            congress=_CONGRESS_SUMMARY,
            disclosures={},
            recompute={},
            publish=_PUBLISH_SUMMARY,
            verify=_PUBLISH_VERIFY_WITH_ERROR,
            roundtrip=_ROUNDTRIP_OK,
        )
        issues = result.verify.all_issues()
        assert len(issues) == 1
        assert issues[0].stage == "manifest"
        assert issues[0].severity == "error"

    def test_roundtrip_is_publish_roundtrip_result(self) -> None:
        result = self._make()
        assert isinstance(result.roundtrip, PublishRoundtripResult)

    def test_roundtrip_ok_when_no_errors(self) -> None:
        result = self._make()
        assert result.roundtrip.ok is True

    def test_roundtrip_stage_count(self) -> None:
        result = self._make()
        assert len(result.roundtrip.stages) == 8

    def test_roundtrip_total_checked(self) -> None:
        result = self._make()
        # snapshot(1) + profiles(10) + evidence(5) + ontology(2)
        # + prediction(4) + zip(3) + homepage(1) + lookup(1)
        assert result.roundtrip.total_checked == 27

    def test_roundtrip_stage_result_lookup(self) -> None:
        result = self._make()
        profiles = result.roundtrip.stage_result("profiles")
        assert profiles is not None
        assert profiles.checked == 10

    def test_roundtrip_with_error_not_ok(self) -> None:
        result = LocalOracleRunResult(
            snapshot_id="2025-06-01",
            congress=_CONGRESS_SUMMARY,
            disclosures={},
            recompute={},
            publish=_PUBLISH_SUMMARY,
            verify=_PUBLISH_VERIFY_OK,
            roundtrip=_ROUNDTRIP_WITH_ERROR,
        )
        assert result.roundtrip.ok is False
        assert result.roundtrip.total_errors == 1

    def test_roundtrip_all_issues_empty_when_ok(self) -> None:
        result = self._make()
        assert result.roundtrip.all_issues() == []

    def test_roundtrip_all_issues_populated_on_error(self) -> None:
        result = LocalOracleRunResult(
            snapshot_id="2025-06-01",
            congress=_CONGRESS_SUMMARY,
            disclosures={},
            recompute={},
            publish=_PUBLISH_SUMMARY,
            verify=_PUBLISH_VERIFY_OK,
            roundtrip=_ROUNDTRIP_WITH_ERROR,
        )
        issues = result.roundtrip.all_issues()
        assert len(issues) == 1
        assert issues[0].stage == "profiles"
        assert issues[0].severity == "error"

    def test_roundtrip_snapshot_stage_checked(self) -> None:
        result = self._make()
        snapshot_stage = result.roundtrip.stage_result("snapshot")
        assert snapshot_stage is not None
        assert snapshot_stage.checked == 1

    def test_roundtrip_homepage_stage_checked(self) -> None:
        result = self._make()
        homepage_stage = result.roundtrip.stage_result("homepage")
        assert homepage_stage is not None
        assert homepage_stage.checked == 1

    def test_verify_and_roundtrip_independent(self) -> None:
        # verify ok, roundtrip failing — both fields are independent
        result = LocalOracleRunResult(
            snapshot_id="2025-06-01",
            congress=_CONGRESS_SUMMARY,
            disclosures={},
            recompute={},
            publish=_PUBLISH_SUMMARY,
            verify=_PUBLISH_VERIFY_OK,
            roundtrip=_ROUNDTRIP_WITH_ERROR,
        )
        assert result.verify.ok is True
        assert result.roundtrip.ok is False


class TestLocalOracleRunResultImmutability:
    def test_frozen_snapshot_id(self) -> None:
        result = LocalOracleRunResult(
            snapshot_id="2025-06-01",
            congress=_CONGRESS_SUMMARY,
            disclosures={},
            recompute={},
            publish=_PUBLISH_SUMMARY,
            verify=_PUBLISH_VERIFY_OK,
            roundtrip=_ROUNDTRIP_OK,
        )
        with pytest.raises(FrozenInstanceError):
            result.snapshot_id = "other"  # type: ignore[misc]

    def test_frozen_congress(self) -> None:
        result = LocalOracleRunResult(
            snapshot_id="2025-06-01",
            congress=_CONGRESS_SUMMARY,
            disclosures={},
            recompute={},
            publish=_PUBLISH_SUMMARY,
            verify=_PUBLISH_VERIFY_OK,
            roundtrip=_ROUNDTRIP_OK,
        )
        with pytest.raises(FrozenInstanceError):
            result.congress = _CONGRESS_SUMMARY  # type: ignore[misc]

    def test_frozen_disclosures(self) -> None:
        result = LocalOracleRunResult(
            snapshot_id="2025-06-01",
            congress=_CONGRESS_SUMMARY,
            disclosures={},
            recompute={},
            publish=_PUBLISH_SUMMARY,
            verify=_PUBLISH_VERIFY_OK,
            roundtrip=_ROUNDTRIP_OK,
        )
        with pytest.raises(FrozenInstanceError):
            result.disclosures = {"x": 1}  # type: ignore[misc]

    def test_frozen_publish(self) -> None:
        result = LocalOracleRunResult(
            snapshot_id="2025-06-01",
            congress=_CONGRESS_SUMMARY,
            disclosures={},
            recompute={},
            publish=_PUBLISH_SUMMARY,
            verify=_PUBLISH_VERIFY_OK,
            roundtrip=_ROUNDTRIP_OK,
        )
        with pytest.raises(FrozenInstanceError):
            result.publish = {"run_id": 0}  # type: ignore[misc]

    def test_frozen_verify(self) -> None:
        result = LocalOracleRunResult(
            snapshot_id="2025-06-01",
            congress=_CONGRESS_SUMMARY,
            disclosures={},
            recompute={},
            publish=_PUBLISH_SUMMARY,
            verify=_PUBLISH_VERIFY_OK,
            roundtrip=_ROUNDTRIP_OK,
        )
        with pytest.raises(FrozenInstanceError):
            result.verify = _PUBLISH_VERIFY_WITH_ERROR  # type: ignore[misc]

    def test_frozen_roundtrip(self) -> None:
        result = LocalOracleRunResult(
            snapshot_id="2025-06-01",
            congress=_CONGRESS_SUMMARY,
            disclosures={},
            recompute={},
            publish=_PUBLISH_SUMMARY,
            verify=_PUBLISH_VERIFY_OK,
            roundtrip=_ROUNDTRIP_OK,
        )
        with pytest.raises(FrozenInstanceError):
            result.roundtrip = _ROUNDTRIP_WITH_ERROR  # type: ignore[misc]


class TestLocalOracleRunResultEquality:
    def test_equal_when_fields_match(self) -> None:
        a = LocalOracleRunResult(
            snapshot_id="2025-06-01",
            congress=_CONGRESS_SUMMARY,
            disclosures={"n": 1},
            recompute={"run_id": 2},
            publish=_PUBLISH_SUMMARY,
            verify=_PUBLISH_VERIFY_OK,
            roundtrip=_ROUNDTRIP_OK,
        )
        b = LocalOracleRunResult(
            snapshot_id="2025-06-01",
            congress=_CONGRESS_SUMMARY,
            disclosures={"n": 1},
            recompute={"run_id": 2},
            publish=_PUBLISH_SUMMARY,
            verify=_PUBLISH_VERIFY_OK,
            roundtrip=_ROUNDTRIP_OK,
        )
        assert a == b

    def test_not_equal_on_snapshot_id_diff(self) -> None:
        a = LocalOracleRunResult(
            snapshot_id="2025-06-01",
            congress=_CONGRESS_SUMMARY,
            disclosures={},
            recompute={},
            publish=_PUBLISH_SUMMARY,
            verify=_PUBLISH_VERIFY_OK,
            roundtrip=_ROUNDTRIP_OK,
        )
        b = LocalOracleRunResult(
            snapshot_id="2025-07-01",
            congress=_CONGRESS_SUMMARY,
            disclosures={},
            recompute={},
            publish=_PUBLISH_SUMMARY,
            verify=_PUBLISH_VERIFY_OK,
            roundtrip=_ROUNDTRIP_OK,
        )
        assert a != b

    def test_not_equal_on_congress_diff(self) -> None:
        other_summary = CongressStageSummary(
            run_id=99,
            source_slug="congress_core",
            total_inserted=0,
            total_written=0,
            load_ok=False,
        )
        a = LocalOracleRunResult(
            snapshot_id="2025-06-01",
            congress=_CONGRESS_SUMMARY,
            disclosures={},
            recompute={},
            publish=_PUBLISH_SUMMARY,
            verify=_PUBLISH_VERIFY_OK,
            roundtrip=_ROUNDTRIP_OK,
        )
        b = LocalOracleRunResult(
            snapshot_id="2025-06-01",
            congress=other_summary,
            disclosures={},
            recompute={},
            publish=_PUBLISH_SUMMARY,
            verify=_PUBLISH_VERIFY_OK,
            roundtrip=_ROUNDTRIP_OK,
        )
        assert a != b

    def test_not_equal_on_publish_diff(self) -> None:
        a = LocalOracleRunResult(
            snapshot_id="2025-06-01",
            congress=_CONGRESS_SUMMARY,
            disclosures={},
            recompute={},
            publish=_PUBLISH_SUMMARY,
            verify=_PUBLISH_VERIFY_OK,
            roundtrip=_ROUNDTRIP_OK,
        )
        b = LocalOracleRunResult(
            snapshot_id="2025-06-01",
            congress=_CONGRESS_SUMMARY,
            disclosures={},
            recompute={},
            publish={"run_id": 99},
            verify=_PUBLISH_VERIFY_OK,
            roundtrip=_ROUNDTRIP_OK,
        )
        assert a != b

    def test_not_equal_on_verify_diff(self) -> None:
        a = LocalOracleRunResult(
            snapshot_id="2025-06-01",
            congress=_CONGRESS_SUMMARY,
            disclosures={},
            recompute={},
            publish=_PUBLISH_SUMMARY,
            verify=_PUBLISH_VERIFY_OK,
            roundtrip=_ROUNDTRIP_OK,
        )
        b = LocalOracleRunResult(
            snapshot_id="2025-06-01",
            congress=_CONGRESS_SUMMARY,
            disclosures={},
            recompute={},
            publish=_PUBLISH_SUMMARY,
            verify=_PUBLISH_VERIFY_WITH_ERROR,
            roundtrip=_ROUNDTRIP_OK,
        )
        assert a != b

    def test_not_equal_on_roundtrip_diff(self) -> None:
        a = LocalOracleRunResult(
            snapshot_id="2025-06-01",
            congress=_CONGRESS_SUMMARY,
            disclosures={},
            recompute={},
            publish=_PUBLISH_SUMMARY,
            verify=_PUBLISH_VERIFY_OK,
            roundtrip=_ROUNDTRIP_OK,
        )
        b = LocalOracleRunResult(
            snapshot_id="2025-06-01",
            congress=_CONGRESS_SUMMARY,
            disclosures={},
            recompute={},
            publish=_PUBLISH_SUMMARY,
            verify=_PUBLISH_VERIFY_OK,
            roundtrip=_ROUNDTRIP_WITH_ERROR,
        )
        assert a != b
