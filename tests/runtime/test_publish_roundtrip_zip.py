"""Tests for src/runtime/publish_roundtrip_zip.py.

Uses real temp publish trees — no mocks for filesystem operations.
DB boundaries (fetch_zip_member_summary_rows, fetch_recent_evidence_ids_by_bioguide)
are patched because they require a live DB connection.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from src.export.contracts import ScoreSummary, ZipFeedPayload, ZipMemberSummary
from src.export.filesystem import write_planned_files
from src.export.manifest import ManifestEntry, SnapshotManifest, manifest_root_sha256
from src.export.writer import PlannedFile, serialize_payload, zip_path
from src.runtime.publish_roundtrip_types import PublishRoundtripStageResult
from src.runtime.publish_roundtrip_zip import verify_published_zip_roundtrip

# ---------------------------------------------------------------------------
# Patch targets — irreducible DB boundaries in the module under test
# ---------------------------------------------------------------------------

_PATCH_SCORE_ROWS = "src.runtime.publish_roundtrip_zip.fetch_zip_member_summary_rows"
_PATCH_EVIDENCE_IDS = "src.runtime.publish_roundtrip_zip.fetch_recent_evidence_ids_by_bioguide"

_SNAPSHOT_DATE = date(2026, 4, 14)
_SNAPSHOT_ID = "2026-04-14"

# ---------------------------------------------------------------------------
# Payload and tree builders
# ---------------------------------------------------------------------------


def _member(
    bioguide_id: str = "B000575",
    name: str = "Roy Blunt",
    slug: str = "roy-blunt",
    chamber: str = "senate",
    party: str = "Republican",
    scores: list[ScoreSummary] | None = None,
    top_evidence_card_ids: list[str] | None = None,
) -> ZipMemberSummary:
    return ZipMemberSummary(
        bioguide_id=bioguide_id,
        name=name,
        slug=slug,
        chamber=chamber,  # type: ignore[arg-type]
        party=party,
        scores=scores or [],
        top_evidence_card_ids=top_evidence_card_ids or [],
    )


def _feed(
    zip_code: str = "63101",
    congressional_district: str | None = "MO-02",
    members: list[ZipMemberSummary] | None = None,
    ambiguity_note: str | None = None,
) -> ZipFeedPayload:
    return ZipFeedPayload(
        zip_code=zip_code,
        congressional_district=congressional_district,
        ambiguity_note=ambiguity_note,
        members=members if members is not None else [],
        snapshot_date=_SNAPSHOT_DATE,
    )


def _planned(feed: ZipFeedPayload) -> PlannedFile:
    return PlannedFile.from_bytes(zip_path(feed.zip_code), serialize_payload(feed))


def _manifest(files: list[PlannedFile]) -> SnapshotManifest:
    entries = [ManifestEntry(path=f.path, sha256=f.sha256, size_bytes=f.size_bytes) for f in files]
    return SnapshotManifest(
        snapshot_id=_SNAPSHOT_ID,
        created_at=datetime(2026, 4, 14, 0, 0, 0),
        entries=entries,
        total_files=len(files),
        total_bytes=sum(f.size_bytes for f in files),
        root_sha256=manifest_root_sha256(entries),
    )


def _empty_manifest() -> SnapshotManifest:
    entries: list[ManifestEntry] = []
    return SnapshotManifest(
        snapshot_id=_SNAPSHOT_ID,
        created_at=datetime(2026, 4, 14, 0, 0, 0),
        entries=entries,
        total_files=0,
        total_bytes=0,
        root_sha256=manifest_root_sha256(entries),
    )


def _manifest_from_entries(entries: list[ManifestEntry]) -> SnapshotManifest:
    return SnapshotManifest(
        snapshot_id=_SNAPSHOT_ID,
        created_at=datetime(2026, 4, 14, 0, 0, 0),
        entries=entries,
        total_files=len(entries),
        total_bytes=sum(entry.size_bytes for entry in entries),
        root_sha256=manifest_root_sha256(entries),
    )


def _write_feed(root: Path, feed: ZipFeedPayload) -> tuple[PlannedFile, SnapshotManifest]:
    """Write a single feed to *root* and return the planned file and manifest."""
    pf = _planned(feed)
    write_planned_files([pf], root)
    return pf, _manifest([pf])


def _score_rows(bioguide_id: str, scores: list[ScoreSummary]) -> list[dict]:
    """Convert ScoreSummary objects to the row shape expected by assemble_zip_feed."""
    return [
        {
            "bioguide_id": bioguide_id,
            "dimension": s.dimension,
            "current_score": s.current_score,
            "rule_fire_count": s.rule_fire_count,
        }
        for s in scores
    ]


# ---------------------------------------------------------------------------
# No zip entries — optional, zero is valid
# ---------------------------------------------------------------------------


class TestNoZipEntries:
    def test_empty_manifest_ok(self, tmp_path: Path) -> None:
        with patch(_PATCH_SCORE_ROWS) as mock_s, patch(_PATCH_EVIDENCE_IDS) as mock_e:
            result = verify_published_zip_roundtrip(None, tmp_path, _empty_manifest(), _SNAPSHOT_DATE)
        assert result.ok is True
        mock_s.assert_not_called()
        mock_e.assert_not_called()

    def test_empty_manifest_checked_zero(self, tmp_path: Path) -> None:
        with patch(_PATCH_SCORE_ROWS), patch(_PATCH_EVIDENCE_IDS):
            result = verify_published_zip_roundtrip(None, tmp_path, _empty_manifest(), _SNAPSHOT_DATE)
        assert result.checked == 0

    def test_empty_manifest_no_issues(self, tmp_path: Path) -> None:
        with patch(_PATCH_SCORE_ROWS), patch(_PATCH_EVIDENCE_IDS):
            result = verify_published_zip_roundtrip(None, tmp_path, _empty_manifest(), _SNAPSHOT_DATE)
        assert result.issues == ()

    def test_stage_name_is_zip(self, tmp_path: Path) -> None:
        with patch(_PATCH_SCORE_ROWS), patch(_PATCH_EVIDENCE_IDS):
            result = verify_published_zip_roundtrip(None, tmp_path, _empty_manifest(), _SNAPSHOT_DATE)
        assert result.stage == "zip"

    def test_non_zip_entries_ignored(self, tmp_path: Path) -> None:
        entry = ManifestEntry(path="members/alice-smith.json", sha256="a" * 64, size_bytes=100)
        manifest = _manifest_from_entries([entry])
        with patch(_PATCH_SCORE_ROWS), patch(_PATCH_EVIDENCE_IDS):
            result = verify_published_zip_roundtrip(None, tmp_path, manifest, _SNAPSHOT_DATE)
        assert result.ok is True
        assert result.checked == 0

    def test_returns_stage_result_type(self, tmp_path: Path) -> None:
        with patch(_PATCH_SCORE_ROWS), patch(_PATCH_EVIDENCE_IDS):
            result = verify_published_zip_roundtrip(None, tmp_path, _empty_manifest(), _SNAPSHOT_DATE)
        assert isinstance(result, PublishRoundtripStageResult)


# ---------------------------------------------------------------------------
# Matching payloads — DB data agrees with published
# ---------------------------------------------------------------------------


class TestMatchingPayload:
    def test_feed_with_no_members_ok(self, tmp_path: Path) -> None:
        """ZIP with no members (data gap) is valid; empty vs empty matches."""
        feed = _feed(members=[])
        _, manifest = _write_feed(tmp_path, feed)
        with patch(_PATCH_SCORE_ROWS, return_value=[]), patch(_PATCH_EVIDENCE_IDS, return_value={}):
            result = verify_published_zip_roundtrip(None, tmp_path, manifest, _SNAPSHOT_DATE)
        assert result.ok is True

    def test_feed_with_scores_ok(self, tmp_path: Path) -> None:
        scores = [ScoreSummary(dimension="conflict_of_interest_risk", current_score=3.5, rule_fire_count=2)]
        mem = _member(scores=scores)
        feed = _feed(members=[mem])
        _, manifest = _write_feed(tmp_path, feed)

        rows = _score_rows(mem.bioguide_id, scores)
        with patch(_PATCH_SCORE_ROWS, return_value=rows), patch(_PATCH_EVIDENCE_IDS, return_value={}):
            result = verify_published_zip_roundtrip(None, tmp_path, manifest, _SNAPSHOT_DATE)
        assert result.ok is True

    def test_feed_with_evidence_ok(self, tmp_path: Path) -> None:
        mem = _member(top_evidence_card_ids=["ec-001", "ec-002"])
        feed = _feed(members=[mem])
        _, manifest = _write_feed(tmp_path, feed)

        evidence_ids = {mem.bioguide_id: ["ec-001", "ec-002"]}
        with patch(_PATCH_SCORE_ROWS, return_value=[]), \
             patch(_PATCH_EVIDENCE_IDS, return_value=evidence_ids):
            result = verify_published_zip_roundtrip(None, tmp_path, manifest, _SNAPSHOT_DATE)
        assert result.ok is True

    def test_evidence_sliced_to_three(self, tmp_path: Path) -> None:
        """assemble_zip_feed slices evidence to [:3]; published must agree."""
        mem = _member(top_evidence_card_ids=["ec-001", "ec-002", "ec-003"])
        feed = _feed(members=[mem])
        _, manifest = _write_feed(tmp_path, feed)

        # DB returns 5 cards; only first 3 should appear in reassembled payload
        evidence_ids = {mem.bioguide_id: ["ec-001", "ec-002", "ec-003", "ec-004", "ec-005"]}
        with patch(_PATCH_SCORE_ROWS, return_value=[]), \
             patch(_PATCH_EVIDENCE_IDS, return_value=evidence_ids):
            result = verify_published_zip_roundtrip(None, tmp_path, manifest, _SNAPSHOT_DATE)
        assert result.ok is True

    def test_feed_with_house_member_ok(self, tmp_path: Path) -> None:
        house = _member(bioguide_id="A000370", name="Alma Adams", slug="alma-adams",
                        chamber="house", party="Democrat")
        feed = _feed(members=[house])
        _, manifest = _write_feed(tmp_path, feed)

        with patch(_PATCH_SCORE_ROWS, return_value=[]), patch(_PATCH_EVIDENCE_IDS, return_value={}):
            result = verify_published_zip_roundtrip(None, tmp_path, manifest, _SNAPSHOT_DATE)
        assert result.ok is True

    def test_checked_count_equals_zip_entries(self, tmp_path: Path) -> None:
        feed = _feed()
        _, manifest = _write_feed(tmp_path, feed)
        with patch(_PATCH_SCORE_ROWS, return_value=[]), patch(_PATCH_EVIDENCE_IDS, return_value={}):
            result = verify_published_zip_roundtrip(None, tmp_path, manifest, _SNAPSHOT_DATE)
        assert result.checked == 1

    def test_no_issues_when_payloads_match(self, tmp_path: Path) -> None:
        feed = _feed()
        _, manifest = _write_feed(tmp_path, feed)
        with patch(_PATCH_SCORE_ROWS, return_value=[]), patch(_PATCH_EVIDENCE_IDS, return_value={}):
            result = verify_published_zip_roundtrip(None, tmp_path, manifest, _SNAPSHOT_DATE)
        assert result.issues == ()


# ---------------------------------------------------------------------------
# Score mismatch
# ---------------------------------------------------------------------------


class TestScoreMismatch:
    def _setup(self, tmp_path: Path) -> tuple[ZipMemberSummary, SnapshotManifest]:
        scores = [ScoreSummary(dimension="conflict_of_interest_risk", current_score=5.0, rule_fire_count=2)]
        mem = _member(bioguide_id="B000575", scores=scores)
        feed = _feed(members=[mem])
        _, manifest = _write_feed(tmp_path, feed)
        return mem, manifest

    def _db_rows_with_different_score(self, bioguide_id: str) -> list[dict]:
        return _score_rows(bioguide_id, [
            ScoreSummary(dimension="conflict_of_interest_risk", current_score=9.0, rule_fire_count=7),
        ])

    def test_not_ok(self, tmp_path: Path) -> None:
        mem, manifest = self._setup(tmp_path)
        rows = self._db_rows_with_different_score(mem.bioguide_id)
        with patch(_PATCH_SCORE_ROWS, return_value=rows), patch(_PATCH_EVIDENCE_IDS, return_value={}):
            result = verify_published_zip_roundtrip(None, tmp_path, manifest, _SNAPSHOT_DATE)
        assert result.ok is False

    def test_error_count(self, tmp_path: Path) -> None:
        mem, manifest = self._setup(tmp_path)
        rows = self._db_rows_with_different_score(mem.bioguide_id)
        with patch(_PATCH_SCORE_ROWS, return_value=rows), patch(_PATCH_EVIDENCE_IDS, return_value={}):
            result = verify_published_zip_roundtrip(None, tmp_path, manifest, _SNAPSHOT_DATE)
        assert result.error_count >= 1

    def test_error_mentions_member(self, tmp_path: Path) -> None:
        mem, manifest = self._setup(tmp_path)
        rows = self._db_rows_with_different_score(mem.bioguide_id)
        with patch(_PATCH_SCORE_ROWS, return_value=rows), patch(_PATCH_EVIDENCE_IDS, return_value={}):
            result = verify_published_zip_roundtrip(None, tmp_path, manifest, _SNAPSHOT_DATE)
        assert any("B000575" in i.message for i in result.issues if i.severity == "error")

    def test_checked_still_counts_entry(self, tmp_path: Path) -> None:
        mem, manifest = self._setup(tmp_path)
        rows = self._db_rows_with_different_score(mem.bioguide_id)
        with patch(_PATCH_SCORE_ROWS, return_value=rows), patch(_PATCH_EVIDENCE_IDS, return_value={}):
            result = verify_published_zip_roundtrip(None, tmp_path, manifest, _SNAPSHOT_DATE)
        assert result.checked == 1


# ---------------------------------------------------------------------------
# Evidence card mismatch
# ---------------------------------------------------------------------------


class TestEvidenceMismatch:
    def test_not_ok(self, tmp_path: Path) -> None:
        mem = _member(bioguide_id="P000197", top_evidence_card_ids=["ec-001", "ec-002"])
        feed = _feed(members=[mem])
        _, manifest = _write_feed(tmp_path, feed)

        with patch(_PATCH_SCORE_ROWS, return_value=[]), \
             patch(_PATCH_EVIDENCE_IDS, return_value={mem.bioguide_id: ["ec-999"]}):
            result = verify_published_zip_roundtrip(None, tmp_path, manifest, _SNAPSHOT_DATE)
        assert result.ok is False

    def test_error_mentions_member(self, tmp_path: Path) -> None:
        mem = _member(bioguide_id="P000197", top_evidence_card_ids=["ec-001"])
        feed = _feed(members=[mem])
        _, manifest = _write_feed(tmp_path, feed)

        with patch(_PATCH_SCORE_ROWS, return_value=[]), \
             patch(_PATCH_EVIDENCE_IDS, return_value={mem.bioguide_id: ["ec-999"]}):
            result = verify_published_zip_roundtrip(None, tmp_path, manifest, _SNAPSHOT_DATE)
        assert any("P000197" in i.message for i in result.issues)

    def test_empty_published_vs_db_evidence(self, tmp_path: Path) -> None:
        """Published has no evidence cards; DB also returns none — ok."""
        mem = _member(top_evidence_card_ids=[])
        feed = _feed(members=[mem])
        _, manifest = _write_feed(tmp_path, feed)

        with patch(_PATCH_SCORE_ROWS, return_value=[]), \
             patch(_PATCH_EVIDENCE_IDS, return_value={}):
            result = verify_published_zip_roundtrip(None, tmp_path, manifest, _SNAPSHOT_DATE)
        assert result.ok is True


# ---------------------------------------------------------------------------
# Missing file
# ---------------------------------------------------------------------------


class TestMissingFile:
    def _manifest_missing(self) -> SnapshotManifest:
        entry = ManifestEntry(path="zip/99999.json", sha256="a" * 64, size_bytes=50)
        return _manifest_from_entries([entry])

    def test_not_ok(self, tmp_path: Path) -> None:
        with patch(_PATCH_SCORE_ROWS), patch(_PATCH_EVIDENCE_IDS):
            result = verify_published_zip_roundtrip(None, tmp_path, self._manifest_missing(), _SNAPSHOT_DATE)
        assert result.ok is False

    def test_error_severity(self, tmp_path: Path) -> None:
        with patch(_PATCH_SCORE_ROWS), patch(_PATCH_EVIDENCE_IDS):
            result = verify_published_zip_roundtrip(None, tmp_path, self._manifest_missing(), _SNAPSHOT_DATE)
        assert result.error_count == 1
        assert result.issues[0].severity == "error"

    def test_error_references_zip_code(self, tmp_path: Path) -> None:
        with patch(_PATCH_SCORE_ROWS), patch(_PATCH_EVIDENCE_IDS):
            result = verify_published_zip_roundtrip(None, tmp_path, self._manifest_missing(), _SNAPSHOT_DATE)
        issue = result.issues[0]
        assert "99999" in issue.message or "99999" in (issue.path or "")

    def test_checked_counts_attempted(self, tmp_path: Path) -> None:
        with patch(_PATCH_SCORE_ROWS), patch(_PATCH_EVIDENCE_IDS):
            result = verify_published_zip_roundtrip(None, tmp_path, self._manifest_missing(), _SNAPSHOT_DATE)
        assert result.checked == 1


# ---------------------------------------------------------------------------
# Multiple feeds
# ---------------------------------------------------------------------------


class TestMultipleFeeds:
    def test_all_ok(self, tmp_path: Path) -> None:
        feeds = [_feed("10001", "NY-12"), _feed("90210", "CA-30"), _feed("73301", "TX-21")]
        files = [_planned(f) for f in feeds]
        write_planned_files(files, tmp_path)
        manifest = _manifest(files)

        with patch(_PATCH_SCORE_ROWS, return_value=[]), patch(_PATCH_EVIDENCE_IDS, return_value={}):
            result = verify_published_zip_roundtrip(None, tmp_path, manifest, _SNAPSHOT_DATE)
        assert result.ok is True
        assert result.checked == 3

    def test_all_ok_no_issues(self, tmp_path: Path) -> None:
        feeds = [_feed("10001", "NY-12"), _feed("90210", "CA-30")]
        files = [_planned(f) for f in feeds]
        write_planned_files(files, tmp_path)
        manifest = _manifest(files)

        with patch(_PATCH_SCORE_ROWS, return_value=[]), patch(_PATCH_EVIDENCE_IDS, return_value={}):
            result = verify_published_zip_roundtrip(None, tmp_path, manifest, _SNAPSHOT_DATE)
        assert result.issues == ()

    def test_one_missing_one_ok(self, tmp_path: Path) -> None:
        good_pf = _planned(_feed("10001", "NY-12"))
        write_planned_files([good_pf], tmp_path)

        missing_entry = ManifestEntry(path="zip/99998.json", sha256="b" * 64, size_bytes=50)
        manifest = _manifest_from_entries([
            ManifestEntry(path=good_pf.path, sha256=good_pf.sha256, size_bytes=good_pf.size_bytes),
            missing_entry,
        ])

        with patch(_PATCH_SCORE_ROWS, return_value=[]), patch(_PATCH_EVIDENCE_IDS, return_value={}):
            result = verify_published_zip_roundtrip(None, tmp_path, manifest, _SNAPSHOT_DATE)
        assert result.ok is False
        assert result.checked == 2
        assert result.error_count == 1

    def test_missing_error_path_identifies_file(self, tmp_path: Path) -> None:
        good_pf = _planned(_feed("10001", "NY-12"))
        write_planned_files([good_pf], tmp_path)

        missing_entry = ManifestEntry(path="zip/99998.json", sha256="b" * 64, size_bytes=50)
        manifest = _manifest_from_entries([
            ManifestEntry(path=good_pf.path, sha256=good_pf.sha256, size_bytes=good_pf.size_bytes),
            missing_entry,
        ])

        with patch(_PATCH_SCORE_ROWS, return_value=[]), patch(_PATCH_EVIDENCE_IDS, return_value={}):
            result = verify_published_zip_roundtrip(None, tmp_path, manifest, _SNAPSHOT_DATE)
        error_paths = [i.path for i in result.issues if i.severity == "error"]
        assert "zip/99998.json" in error_paths


# ---------------------------------------------------------------------------
# Unparseable congressional_district
# ---------------------------------------------------------------------------


class TestUnparseableDistrict:
    def test_null_district_error(self, tmp_path: Path) -> None:
        """Feed with congressional_district=None cannot be reassembled."""
        mem = _member()
        feed = ZipFeedPayload(
            zip_code="63101",
            congressional_district=None,
            ambiguity_note=None,
            members=[mem],
            snapshot_date=_SNAPSHOT_DATE,
        )
        _, manifest = _write_feed(tmp_path, feed)

        with patch(_PATCH_SCORE_ROWS), patch(_PATCH_EVIDENCE_IDS):
            result = verify_published_zip_roundtrip(None, tmp_path, manifest, _SNAPSHOT_DATE)
        assert result.ok is False
        assert result.error_count >= 1

    def test_null_district_error_message(self, tmp_path: Path) -> None:
        mem = _member()
        feed = ZipFeedPayload(
            zip_code="63101",
            congressional_district=None,
            ambiguity_note=None,
            members=[mem],
            snapshot_date=_SNAPSHOT_DATE,
        )
        _, manifest = _write_feed(tmp_path, feed)

        with patch(_PATCH_SCORE_ROWS), patch(_PATCH_EVIDENCE_IDS):
            result = verify_published_zip_roundtrip(None, tmp_path, manifest, _SNAPSHOT_DATE)
        assert any("63101" in (i.path or "") or "bundle" in i.message.lower() for i in result.issues)


# ---------------------------------------------------------------------------
# Ambiguous ZIP (ambiguity_note set)
# ---------------------------------------------------------------------------


class TestAmbiguousZip:
    def test_ambiguity_note_propagated_ok(self, tmp_path: Path) -> None:
        """ZIP spanning multiple districts: ambiguity_note is preserved through roundtrip."""
        note = "ZIP 10001 spans NY-12 (80%) and NY-13 (20%)."
        feed = _feed(ambiguity_note=note)
        _, manifest = _write_feed(tmp_path, feed)

        with patch(_PATCH_SCORE_ROWS, return_value=[]), patch(_PATCH_EVIDENCE_IDS, return_value={}):
            result = verify_published_zip_roundtrip(None, tmp_path, manifest, _SNAPSHOT_DATE)
        assert result.ok is True


# ---------------------------------------------------------------------------
# Stage result properties
# ---------------------------------------------------------------------------


class TestStageResultProperties:
    def test_ok_true_when_no_errors(self, tmp_path: Path) -> None:
        feed = _feed()
        _, manifest = _write_feed(tmp_path, feed)
        with patch(_PATCH_SCORE_ROWS, return_value=[]), patch(_PATCH_EVIDENCE_IDS, return_value={}):
            result = verify_published_zip_roundtrip(None, tmp_path, manifest, _SNAPSHOT_DATE)
        assert result.ok is True
        assert result.error_count == 0
        assert result.warning_count == 0

    def test_ok_false_when_error(self, tmp_path: Path) -> None:
        entry = ManifestEntry(path="zip/55555.json", sha256="d" * 64, size_bytes=10)
        manifest = _manifest_from_entries([entry])
        with patch(_PATCH_SCORE_ROWS), patch(_PATCH_EVIDENCE_IDS):
            result = verify_published_zip_roundtrip(None, tmp_path, manifest, _SNAPSHOT_DATE)
        assert result.ok is False
