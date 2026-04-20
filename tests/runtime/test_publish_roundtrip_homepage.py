"""Tests for src/runtime/publish_roundtrip_homepage.py.

Uses real temp publish trees: ``homepage/feed.json`` is written to disk
using the production ``serialize_payload`` helper.  The DB boundary
(``fetch_homepage_feed_rows``) is patched as an irreducible external
dependency — the only mock in this module.

No network calls.  No subprocess invocations.  No sys.modules injection.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from src.api.contracts import ArtifactCounts, HomepageBootstrapPayload, MovementFeedPayload, SnapshotSummaryPayload
from src.export.filesystem import write_planned_files
from src.export.manifest import ManifestEntry, SnapshotManifest, manifest_root_sha256
from src.export.writer import PlannedFile, current_member_lookup_path, homepage_bootstrap_path, serialize_payload
from src.homepage.builders import build_featured_lookup_entries
from src.identity.current_member_lookup import CurrentMemberLookupEntry, CurrentMemberLookupPayload, normalize_lookup_name
from src.homepage.contracts import HomepageFeedPayload
from src.query.homepage_feed import assemble_homepage_payload
from src.runtime.publish_roundtrip_homepage import verify_published_homepage_roundtrip
from src.runtime.publish_roundtrip_types import PublishRoundtripStageResult

_SNAP = dt.date(2026, 1, 1)
_HOMEPAGE_PATH = "homepage/feed.json"
_BOOTSTRAP_PATH = "homepage/bootstrap.json"
_PATCH_TARGET = "src.runtime.publish_roundtrip_homepage.fetch_homepage_feed_rows"


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _row(
    public_id: str,
    slug: str,
    *,
    bioguide_id: str = "A000001",
    score_delta: float = -10.0,
    dimension: str = "conflict_of_interest_risk",
    short_explanation: str = "Test explanation.",
    rendered_at: dt.date | None = None,
    member_full_name: str = "Alice Smith",
    chamber: str = "house",
    party: str = "D",
    state: str = "CA",
) -> dict[str, Any]:
    return {
        "public_id": public_id,
        "member_bioguide_id": bioguide_id,
        "member_slug": slug,
        "dimension": dimension,
        "score_delta": score_delta,
        "short_explanation": short_explanation,
        "rendered_at": rendered_at or _SNAP,
        "member_full_name": member_full_name,
        "confidence_label": "HIGH",
        "chamber": chamber,
        "party": party,
        "state": state,
    }


def _payload(rows: list[dict[str, Any]], snapshot_date: dt.date = _SNAP) -> HomepageFeedPayload:
    """Build a HomepageFeedPayload from rows using the production assembler."""
    return assemble_homepage_payload(rows, snapshot_date=snapshot_date)


def _lookup_payload(rows: list[dict[str, Any]], snapshot_date: dt.date = _SNAP) -> CurrentMemberLookupPayload:
    seen: dict[str, CurrentMemberLookupEntry] = {}
    for row in rows:
        bioguide_id = str(row["member_bioguide_id"])
        if bioguide_id in seen:
            continue
        name = str(row["member_full_name"])
        seen[bioguide_id] = CurrentMemberLookupEntry(
            bioguide_id=bioguide_id,
            slug=str(row["member_slug"]),
            name=name,
            search_name=normalize_lookup_name(name),
            state=str(row["state"]).upper(),
            district=None,
            chamber=str(row["chamber"]),  # type: ignore[arg-type]
        )
    return CurrentMemberLookupPayload(snapshot_date=snapshot_date, members=list(seen.values()))


def _bootstrap_payload(
    homepage_feed: HomepageFeedPayload,
    lookup_payload: CurrentMemberLookupPayload,
    manifest: SnapshotManifest,
) -> HomepageBootstrapPayload:
    return HomepageBootstrapPayload(
        snapshot=SnapshotSummaryPayload(
            snapshot_id=manifest.snapshot_id,
            snapshot_date=homepage_feed.snapshot_date,
            published_at=manifest.created_at,
            root_sha256=manifest.root_sha256,
            total_files=manifest.total_files,
            total_bytes=manifest.total_bytes,
            artifact_counts=ArtifactCounts(
                members=0,
                evidence=0,
                zip_feeds=0,
                homepage_feeds=1,
                current_member_lookups=1,
            ),
        ),
        movement=MovementFeedPayload(
            snapshot_date=homepage_feed.snapshot_date,
            top_changes=homepage_feed.top_changes,
            recent_events=homepage_feed.recent_events,
            recent_evidence_card_ids=homepage_feed.recent_evidence_card_ids,
        ),
        featured_lookup_entries=build_featured_lookup_entries(
            homepage_feed.top_changes,
            homepage_feed.recent_events,
            lookup_payload.members,
        ),
    )


def _write_homepage(root: Path, payload: HomepageFeedPayload) -> None:
    """Write homepage feed plus required bootstrap prerequisites under *root*."""
    lookup_payload = _lookup_payload(
        [
            {
                "member_bioguide_id": change.bioguide_id,
                "member_slug": change.slug,
                "member_full_name": change.name,
                "state": change.state,
                "chamber": change.chamber,
            }
            for change in payload.top_changes
        ]
        + [
            {
                "member_bioguide_id": event.member_bioguide_id,
                "member_slug": event.member_slug,
                "member_full_name": event.member_name,
                "state": "CA",
                "chamber": "house",
            }
            for event in payload.recent_events
        ],
        payload.snapshot_date,
    )
    lookup_file = PlannedFile.from_bytes(
        current_member_lookup_path(),
        serialize_payload(lookup_payload),
    )
    manifest_entries = [
        ManifestEntry(path=lookup_file.path, sha256=lookup_file.sha256, size_bytes=lookup_file.size_bytes)
    ]
    manifest = SnapshotManifest(
        snapshot_id=payload.snapshot_date.isoformat(),
        created_at=dt.datetime(2026, 1, 1, 0, 0, 0),
        entries=manifest_entries,
        total_files=len(manifest_entries),
        total_bytes=sum(entry.size_bytes for entry in manifest_entries),
        root_sha256=manifest_root_sha256(manifest_entries),
    )
    bootstrap_payload = _bootstrap_payload(payload, lookup_payload, manifest)
    write_planned_files(
        [
            PlannedFile.from_bytes(_HOMEPAGE_PATH, serialize_payload(payload)),
            lookup_file,
            PlannedFile.from_bytes(
                f"snapshots/{manifest.snapshot_id}/manifest.json",
                serialize_payload(manifest),
            ),
            PlannedFile.from_bytes(_BOOTSTRAP_PATH, serialize_payload(bootstrap_payload)),
        ],
        root,
    )


def _run(
    root: Path,
    db_rows: list[dict[str, Any]],
    snapshot_date: dt.date = _SNAP,
) -> PublishRoundtripStageResult:
    """Call verify_published_homepage_roundtrip with patched DB fetch."""
    with patch(_PATCH_TARGET, return_value=db_rows):
        return verify_published_homepage_roundtrip(None, root, snapshot_date)


# ---------------------------------------------------------------------------
# Return type and stage name
# ---------------------------------------------------------------------------


class TestReturnType:
    def test_returns_stage_result_instance(self, tmp_path: Path) -> None:
        rows = [_row("card-1", "alice-smith")]
        _write_homepage(tmp_path, _payload(rows))
        result = _run(tmp_path, rows)
        assert isinstance(result, PublishRoundtripStageResult)

    def test_stage_name_is_homepage(self, tmp_path: Path) -> None:
        rows = [_row("card-1", "alice-smith")]
        _write_homepage(tmp_path, _payload(rows))
        result = _run(tmp_path, rows)
        assert result.stage == "homepage"


# ---------------------------------------------------------------------------
# Happy path — payloads agree
# ---------------------------------------------------------------------------


class TestMatchingPayloads:
    def test_single_event_ok(self, tmp_path: Path) -> None:
        rows = [_row("card-1", "alice-smith")]
        _write_homepage(tmp_path, _payload(rows))
        result = _run(tmp_path, rows)
        assert result.ok is True
        assert result.checked == 1
        assert result.issues == ()

    def test_empty_feed_ok(self, tmp_path: Path) -> None:
        rows: list[dict[str, Any]] = []
        _write_homepage(tmp_path, _payload(rows))
        result = _run(tmp_path, rows)
        assert result.ok is True
        assert result.checked == 1

    def test_multiple_events_ok(self, tmp_path: Path) -> None:
        rows = [
            _row("card-a", "alice-smith", score_delta=-30.0),
            _row("card-b", "bob-jones", score_delta=-15.0, member_full_name="Bob Jones"),
        ]
        _write_homepage(tmp_path, _payload(rows))
        result = _run(tmp_path, rows)
        assert result.ok is True

    def test_no_errors_for_matching_payloads(self, tmp_path: Path) -> None:
        rows = [
            _row("card-a", "alice-smith", score_delta=-50.0),
            _row("card-b", "carol-lee", score_delta=-20.0,
                 member_full_name="Carol Lee", chamber="senate", state="TX"),
        ]
        _write_homepage(tmp_path, _payload(rows))
        result = _run(tmp_path, rows)
        assert result.error_count == 0


# ---------------------------------------------------------------------------
# Missing or corrupt published file
# ---------------------------------------------------------------------------


class TestMissingOrCorruptFile:
    def test_missing_file_is_error(self, tmp_path: Path) -> None:
        result = _run(tmp_path, [])
        assert result.ok is False
        assert result.checked == 0
        assert any("missing" in i.message for i in result.issues)

    def test_missing_file_issue_names_path(self, tmp_path: Path) -> None:
        result = _run(tmp_path, [])
        assert any(i.path == _HOMEPAGE_PATH for i in result.issues)

    def test_corrupt_json_is_error(self, tmp_path: Path) -> None:
        dest = tmp_path / _HOMEPAGE_PATH
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"not json {{{")
        result = _run(tmp_path, [])
        assert result.ok is False
        assert result.checked == 0

    def test_invalid_schema_is_error(self, tmp_path: Path) -> None:
        dest = tmp_path / _HOMEPAGE_PATH
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(json.dumps({"snapshot_date": "not-a-date"}).encode())
        result = _run(tmp_path, [])
        assert result.ok is False
        assert result.checked == 0

    def test_missing_bootstrap_is_error(self, tmp_path: Path) -> None:
        rows = [_row("card-1", "alice-smith")]
        _write_homepage(tmp_path, _payload(rows))
        (tmp_path / _BOOTSTRAP_PATH).unlink()

        result = _run(tmp_path, rows)

        assert result.ok is False
        assert result.checked == 0
        assert any(issue.path == _BOOTSTRAP_PATH for issue in result.issues)


# ---------------------------------------------------------------------------
# Stage ordering: file load happens before DB query
# ---------------------------------------------------------------------------


class TestStageOrdering:
    def test_missing_file_does_not_query_db(self, tmp_path: Path) -> None:
        """When the published file is absent the DB must not be queried."""
        call_log: list[int] = []

        def tracking_fetch(conn: object | None, *, limit: int) -> list[dict[str, Any]]:
            call_log.append(1)
            return []

        with patch(_PATCH_TARGET, side_effect=tracking_fetch):
            verify_published_homepage_roundtrip(None, tmp_path, _SNAP)

        assert call_log == []


# ---------------------------------------------------------------------------
# Payload field mismatches
# ---------------------------------------------------------------------------


class TestPayloadMismatches:
    def test_snapshot_date_mismatch_is_error(self, tmp_path: Path) -> None:
        rows = [_row("card-1", "alice-smith")]
        # Write file with an earlier snapshot date.
        _write_homepage(tmp_path, _payload(rows, snapshot_date=dt.date(2025, 6, 1)))
        # DB assembled with _SNAP.
        result = _run(tmp_path, rows, snapshot_date=_SNAP)
        assert result.ok is False
        assert any("snapshot_date" in i.message for i in result.issues)

    def test_top_changes_slug_mismatch_is_error(self, tmp_path: Path) -> None:
        pub_rows = [_row("card-1", "alice-smith", score_delta=-50.0)]
        db_rows = [_row("card-1", "bob-jones", score_delta=-50.0,
                        member_full_name="Bob Jones")]
        _write_homepage(tmp_path, _payload(pub_rows))
        result = _run(tmp_path, db_rows)
        assert result.ok is False
        assert any("top_changes" in i.message for i in result.issues)

    def test_recent_events_mismatch_is_error(self, tmp_path: Path) -> None:
        pub_rows = [_row("card-1", "alice-smith")]
        # Different card ID → different feed_event_id → mismatch.
        db_rows = [_row("card-2", "alice-smith")]
        _write_homepage(tmp_path, _payload(pub_rows))
        result = _run(tmp_path, db_rows)
        assert result.ok is False
        assert any(
            "recent_events" in i.message or "recent_evidence_card_ids" in i.message
            for i in result.issues
        )

    def test_recent_evidence_card_ids_mismatch_is_error(self, tmp_path: Path) -> None:
        pub_rows = [_row("card-1", "alice-smith")]
        db_rows = [_row("card-99", "alice-smith")]
        _write_homepage(tmp_path, _payload(pub_rows))
        result = _run(tmp_path, db_rows)
        assert result.ok is False
        assert any("recent_evidence_card_ids" in i.message for i in result.issues)

    def test_top_changes_member_metadata_drift_is_error(self, tmp_path: Path) -> None:
        pub_rows = [_row("card-1", "alice-smith", party="D", state="CA")]
        db_rows = [_row("card-1", "alice-smith", party="R", state="NV")]
        _write_homepage(tmp_path, _payload(pub_rows))
        result = _run(tmp_path, db_rows)
        assert result.ok is False
        assert any("top_changes" in i.message for i in result.issues)

    def test_recent_events_text_drift_with_same_event_id_is_error(self, tmp_path: Path) -> None:
        pub_rows = [_row("card-1", "alice-smith", short_explanation="Published explanation.")]
        db_rows = [_row("card-1", "alice-smith", short_explanation="DB explanation.")]
        _write_homepage(tmp_path, _payload(pub_rows))
        result = _run(tmp_path, db_rows)
        assert result.ok is False
        assert any("recent_events" in i.message for i in result.issues)

    def test_checked_is_1_when_payloads_differ(self, tmp_path: Path) -> None:
        pub_rows = [_row("card-1", "alice-smith")]
        db_rows = [_row("card-2", "alice-smith")]
        _write_homepage(tmp_path, _payload(pub_rows))
        result = _run(tmp_path, db_rows)
        # File was loaded successfully; mismatch is in the comparison.
        assert result.checked == 1

    def test_all_mismatches_reported_in_one_pass(self, tmp_path: Path) -> None:
        """Multiple field mismatches all surface in a single call."""
        pub_rows = [_row("card-1", "alice-smith", score_delta=-50.0)]
        pub_payload = _payload(pub_rows, snapshot_date=dt.date(2025, 6, 1))
        _write_homepage(tmp_path, pub_payload)

        db_rows = [_row("card-2", "bob-jones", score_delta=-50.0,
                        member_full_name="Bob Jones")]
        result = _run(tmp_path, db_rows, snapshot_date=_SNAP)
        # Expect at least: snapshot_date + top_changes + recent_events
        assert result.error_count >= 2


# ---------------------------------------------------------------------------
# Payload ordering is preserved through roundtrip
# ---------------------------------------------------------------------------


class TestOrderingPreserved:
    def test_top_changes_order_preserved_through_file_roundtrip(
        self, tmp_path: Path
    ) -> None:
        """Serialize then deserialize; the top_changes order must survive."""
        rows = [
            _row("card-a", "alice-smith", score_delta=-50.0),
            _row("card-b", "bob-jones", score_delta=-30.0, member_full_name="Bob Jones"),
        ]
        p = _payload(rows)
        _write_homepage(tmp_path, p)
        result = _run(tmp_path, rows)
        assert result.ok is True

    def test_recent_events_order_preserved_through_file_roundtrip(
        self, tmp_path: Path
    ) -> None:
        """Serialize then deserialize; the recent_events order must survive."""
        rows = [
            _row("card-a", "alice-smith",
                 rendered_at=dt.date(2025, 12, 10), score_delta=-20.0),
            _row("card-b", "bob-jones",
                 rendered_at=dt.date(2025, 11, 5), score_delta=-10.0,
                 member_full_name="Bob Jones"),
        ]
        p = _payload(rows)
        _write_homepage(tmp_path, p)
        result = _run(tmp_path, rows)
        assert result.ok is True
