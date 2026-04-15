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

from src.export.writer import serialize_payload
from src.homepage.contracts import HomepageFeedPayload
from src.query.homepage_feed import assemble_homepage_payload
from src.runtime.publish_roundtrip_homepage import verify_published_homepage_roundtrip
from src.runtime.publish_roundtrip_types import PublishRoundtripStageResult

_SNAP = dt.date(2026, 1, 1)
_HOMEPAGE_PATH = "homepage/feed.json"
_PATCH_TARGET = "src.runtime.publish_roundtrip_homepage.fetch_homepage_feed_rows"


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _row(
    public_id: str,
    slug: str,
    *,
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


def _payload(rows: list[dict], snapshot_date: dt.date = _SNAP) -> HomepageFeedPayload:
    """Build a HomepageFeedPayload from rows using the production assembler."""
    return assemble_homepage_payload(rows, snapshot_date=snapshot_date)


def _write_homepage(root: Path, payload: HomepageFeedPayload) -> None:
    """Write *payload* to ``homepage/feed.json`` under *root* using production serialization."""
    dest = root / _HOMEPAGE_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(serialize_payload(payload))


def _run(
    root: Path,
    db_rows: list[dict],
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
        rows: list[dict] = []
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


# ---------------------------------------------------------------------------
# Stage ordering: file load happens before DB query
# ---------------------------------------------------------------------------


class TestStageOrdering:
    def test_missing_file_does_not_query_db(self, tmp_path: Path) -> None:
        """When the published file is absent the DB must not be queried."""
        call_log: list[int] = []

        def tracking_fetch(conn, *, limit: int) -> list:
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
