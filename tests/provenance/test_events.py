"""Tests for the pure StageEvent factory helpers in src/provenance/events.py."""

from __future__ import annotations

import hashlib
import json
from datetime import timezone

from src.provenance.events import (
    export_event,
    fetch_event,
    fetch_failed_event,
    generic_event,
    normalize_event,
    parse_event,
    parse_failed_event,
)

_SHA = hashlib.sha256(b"artifact").hexdigest()
_SNAP = hashlib.sha256(b"snapshot").hexdigest()


def _expected_payload_hash(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def test_fetch_event_records_success_with_source_url_metadata() -> None:
    event = fetch_event(_SHA, source_url="https://clerk.house.gov/x", notes="ok")
    assert event.stage == "fetch"
    assert event.status == "succeeded"
    assert event.artifact_sha256 == _SHA
    assert event.notes == "ok"
    assert event.metadata == {"source_url": "https://clerk.house.gov/x"}
    # _utcnow() yields a tz-aware UTC timestamp.
    assert event.occurred_at.tzinfo is timezone.utc


def test_fetch_event_omits_source_url_when_absent() -> None:
    event = fetch_event(_SHA)
    assert event.metadata == {}
    assert event.notes is None


def test_fetch_failed_event_records_failure_without_artifact() -> None:
    event = fetch_failed_event("timeout", source_url="https://senate.gov/y")
    assert event.stage == "fetch"
    assert event.status == "failed"
    assert event.artifact_sha256 is None
    assert event.error_message == "timeout"
    assert event.metadata == {"source_url": "https://senate.gov/y"}


def test_fetch_failed_event_omits_source_url_when_absent() -> None:
    event = fetch_failed_event("network down")
    assert event.metadata == {}


def test_parse_event_hashes_payload_and_records_parser_metadata() -> None:
    payload = {"rows": [1, 2, 3]}
    event = parse_event(_SHA, "house_ptr", "v2", payload, notes="parsed")
    assert event.stage == "parse"
    assert event.status == "succeeded"
    assert event.artifact_sha256 == _SHA
    assert event.payload_hash == _expected_payload_hash(payload)
    assert event.notes == "parsed"
    assert event.metadata == {"parser_name": "house_ptr", "parser_version": "v2"}


def test_parse_failed_event_records_error_and_metadata() -> None:
    event = parse_failed_event(_SHA, "senate_efd", "v1", "bad section header")
    assert event.stage == "parse"
    assert event.status == "failed"
    assert event.error_message == "bad section header"
    assert event.payload_hash is None
    assert event.metadata == {"parser_name": "senate_efd", "parser_version": "v1"}


def test_normalize_event_hashes_payload() -> None:
    payload = {"sector": "energy"}
    event = normalize_event(_SHA, payload, notes="normalized")
    assert event.stage == "normalize"
    assert event.status == "succeeded"
    assert event.payload_hash == _expected_payload_hash(payload)
    assert event.notes == "normalized"


def test_export_event_records_export_key() -> None:
    event = export_event(_SNAP, "members/A000001.json", notes="exported")
    assert event.stage == "export"
    assert event.status == "succeeded"
    assert event.artifact_sha256 == _SNAP
    assert event.metadata == {"export_key": "members/A000001.json"}


def test_generic_event_hashes_payload_only_when_present() -> None:
    with_payload = generic_event("custom", "running", payload={"a": 1})
    assert with_payload.payload_hash == _expected_payload_hash({"a": 1})

    without_payload = generic_event("custom", "queued")
    assert without_payload.payload_hash is None
    assert without_payload.metadata == {}  # None metadata normalises to {}


def test_generic_event_passes_through_metadata_and_status() -> None:
    event = generic_event(
        "recompute",
        "skipped",
        artifact_sha256=_SHA,
        error_message="no change",
        metadata={"run_id": 7},
    )
    assert event.stage == "recompute"
    assert event.status == "skipped"
    assert event.artifact_sha256 == _SHA
    assert event.error_message == "no change"
    assert event.metadata == {"run_id": 7}


def test_payload_hash_is_deterministic_and_key_order_independent() -> None:
    # sort_keys=True means dict key order must not change the hash.
    a = parse_event(_SHA, "p", "v", {"x": 1, "y": 2})
    b = parse_event(_SHA, "p", "v", {"y": 2, "x": 1})
    assert a.payload_hash == b.payload_hash


def test_payload_hash_uses_str_fallback_for_non_serializable_payload() -> None:
    # A set is not JSON-serialisable; default=str must keep hashing from raising.
    event = normalize_event(_SHA, {"ids": {1, 2, 3}})
    assert event.payload_hash == _expected_payload_hash({"ids": {1, 2, 3}})
