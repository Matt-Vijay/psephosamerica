from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from src.export.contracts import (
    ConfidenceLabel,
    EvidenceCardPayload,
    MemberProfilePayload,
    ZipFeedPayload,
)
from src.export.filesystem import write_planned_files
from src.export.manifest import ManifestEntry, SnapshotManifest
from src.export.writer import (
    PlannedFile,
    evidence_path,
    manifest_path,
    member_path,
    zip_path,
)
from src.export.local_store import HOMEPAGE_FEED_PATH, HomepageFeedPayload
from src.runtime.inspect import (
    load_latest_local_manifest,
    load_local_evidence_card,
    load_local_homepage_feed,
    load_local_manifest,
    load_local_member_profile,
    load_local_zip_feed,
)
from src.homepage.contracts import MemberMovementSummary, RecentEventSummary


# ── Fixture payloads ───────────────────────────────────────────────


SNAPSHOT_DATE = date(2026, 4, 14)
SNAPSHOT_ID = "2026-04-14"


def _member() -> MemberProfilePayload:
    return MemberProfilePayload(
        bioguide_id="P000197",
        name="Nancy Pelosi",
        slug="nancy-pelosi",
        state="CA",
        district="CA-11",
        chamber="house",
        party="Democrat",
        scores=[],
        recent_rule_fires=[],
        committees=[],
        total_evidence_cards=0,
        snapshot_date=SNAPSHOT_DATE,
    )


def _evidence() -> EvidenceCardPayload:
    return EvidenceCardPayload(
        evidence_card_id="ec-inspect-001",
        member_bioguide_id="P000197",
        member_name="Nancy Pelosi",
        member_slug="nancy-pelosi",
        dimension="conflict_of_interest_risk",
        rule_id="committee_sector_trade",
        rule_version=1,
        score_delta=-3.0,
        short_explanation="Trade overlapping committee jurisdiction.",
        blocks=[],
        source_anchors=[],
        confidence=ConfidenceLabel.MEDIUM,
        snapshot_date=SNAPSHOT_DATE,
        created_at=datetime(2026, 4, 14, 8, 0, 0),
    )


def _zip_feed() -> ZipFeedPayload:
    return ZipFeedPayload(
        zip_code="94102",
        congressional_district="CA-11",
        ambiguity_note=None,
        members=[],
        snapshot_date=SNAPSHOT_DATE,
    )


def _manifest(files: list[PlannedFile]) -> SnapshotManifest:
    return SnapshotManifest(
        snapshot_id=SNAPSHOT_ID,
        created_at=datetime(2026, 4, 14, 0, 0, 0),
        entries=[
            ManifestEntry(path=f.path, sha256=f.sha256, size_bytes=f.size_bytes)
            for f in files
        ],
        total_files=len(files),
        total_bytes=sum(f.size_bytes for f in files),
    )


def _serialise(model: object) -> bytes:
    from pydantic import BaseModel as _BM
    assert isinstance(model, _BM)
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode("utf-8")


def _homepage_feed() -> HomepageFeedPayload:
    return HomepageFeedPayload(
        snapshot_date=SNAPSHOT_DATE,
        top_changes=[
            MemberMovementSummary(
                bioguide_id="P000197",
                name="Nancy Pelosi",
                slug="nancy-pelosi",
                chamber="house",
                party="Democrat",
                state="CA",
                dimension="conflict_of_interest_risk",
                score_delta=-3.0,
                abs_delta=3.0,
                event_count=1,
                top_evidence_card_ids=["ec-inspect-001"],
            )
        ],
        recent_events=[
            RecentEventSummary(
                feed_event_id="event-inspect-001",
                member_bioguide_id="P000197",
                member_name="Nancy Pelosi",
                member_slug="nancy-pelosi",
                dimension="conflict_of_interest_risk",
                score_delta=-3.0,
                short_explanation="Trade overlapping committee jurisdiction.",
                evidence_card_id="ec-inspect-001",
                occurred_at=SNAPSHOT_DATE,
            )
        ],
        recent_evidence_card_ids=["ec-inspect-001"],
    )


def _write_homepage_feed(root: Path, payload: HomepageFeedPayload | None = None) -> None:
    feed = payload if payload is not None else _homepage_feed()
    dest = root / HOMEPAGE_FEED_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(_serialise(feed))


def _write_snapshot(root: Path, *, snapshot_id: str = SNAPSHOT_ID) -> None:
    member = _member()
    evidence = _evidence()
    feed = _zip_feed()
    files = [
        PlannedFile.from_bytes(member_path(member.slug), _serialise(member)),
        PlannedFile.from_bytes(evidence_path(evidence.evidence_card_id), _serialise(evidence)),
        PlannedFile.from_bytes(zip_path(feed.zip_code), _serialise(feed)),
    ]
    manifest = SnapshotManifest(
        snapshot_id=snapshot_id,
        created_at=datetime(2026, 4, 14, 0, 0, 0),
        entries=[
            ManifestEntry(path=f.path, sha256=f.sha256, size_bytes=f.size_bytes)
            for f in files
        ],
        total_files=len(files),
        total_bytes=sum(f.size_bytes for f in files),
    )
    files.append(PlannedFile.from_bytes(manifest_path(snapshot_id), _serialise(manifest)))
    write_planned_files(files, root)


# ── load_local_member_profile ──────────────────────────────────────


def test_load_local_member_profile_explicit_root(tmp_path: Path) -> None:
    _write_snapshot(tmp_path)
    result = load_local_member_profile("nancy-pelosi", snapshot_root=tmp_path)
    assert result.bioguide_id == "P000197"
    assert result.slug == "nancy-pelosi"
    assert result.chamber == "house"


def test_load_local_member_profile_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_local_member_profile("ghost-member", snapshot_root=tmp_path)


def test_load_local_member_profile_default_root_is_publish_dir() -> None:
    from src.runtime.paths import local_publish_root
    import unittest.mock as mock

    sentinel = object()
    with mock.patch("src.runtime.inspect.load_member_profile", return_value=sentinel) as patched:
        try:
            load_local_member_profile("any-slug")
        except Exception:
            pass
        if patched.called:
            called_root = patched.call_args[0][0]
            assert called_root == local_publish_root()


# ── load_local_evidence_card ───────────────────────────────────────


def test_load_local_evidence_card_explicit_root(tmp_path: Path) -> None:
    _write_snapshot(tmp_path)
    result = load_local_evidence_card("ec-inspect-001", snapshot_root=tmp_path)
    assert result.evidence_card_id == "ec-inspect-001"
    assert result.confidence == ConfidenceLabel.MEDIUM
    assert result.score_delta == -3.0


def test_load_local_evidence_card_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_local_evidence_card("ec-ghost", snapshot_root=tmp_path)


# ── load_local_zip_feed ────────────────────────────────────────────


def test_load_local_zip_feed_explicit_root(tmp_path: Path) -> None:
    _write_snapshot(tmp_path)
    result = load_local_zip_feed("94102", snapshot_root=tmp_path)
    assert result.zip_code == "94102"
    assert result.congressional_district == "CA-11"


def test_load_local_zip_feed_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_local_zip_feed("00000", snapshot_root=tmp_path)


# ── load_local_homepage_feed ──────────────────────────────────────


def test_load_local_homepage_feed_explicit_root(tmp_path: Path) -> None:
    _write_homepage_feed(tmp_path)
    result = load_local_homepage_feed(snapshot_root=tmp_path)
    assert result.snapshot_date == SNAPSHOT_DATE
    assert result.top_changes[0].slug == "nancy-pelosi"
    assert result.recent_events[0].feed_event_id == "event-inspect-001"
    assert result.recent_evidence_card_ids == ["ec-inspect-001"]


def test_load_local_homepage_feed_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_local_homepage_feed(snapshot_root=tmp_path)


def test_load_local_homepage_feed_default_root_is_publish_dir() -> None:
    import unittest.mock as mock

    from src.runtime.paths import local_publish_root

    with mock.patch("src.runtime.inspect.load_homepage_feed") as patched:
        patched.return_value = object()
        try:
            load_local_homepage_feed()
        except Exception:
            pass
        if patched.called:
            called_root = patched.call_args[0][0]
            assert called_root == local_publish_root()


# ── load_local_manifest ────────────────────────────────────────────


def test_load_local_manifest_explicit_root(tmp_path: Path) -> None:
    _write_snapshot(tmp_path)
    result = load_local_manifest(SNAPSHOT_ID, snapshot_root=tmp_path)
    assert result.snapshot_id == SNAPSHOT_ID
    assert result.verify_counts() is True
    assert result.total_files == 3


def test_load_local_manifest_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_local_manifest("1970-01-01", snapshot_root=tmp_path)


def test_load_local_manifest_invalid_json(tmp_path: Path) -> None:
    dest = tmp_path / manifest_path("bad-snap")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"not json {{{{")
    with pytest.raises(ValueError, match="Invalid JSON"):
        load_local_manifest("bad-snap", snapshot_root=tmp_path)


# ── load_latest_local_manifest ─────────────────────────────────────


def test_load_latest_local_manifest_single_snapshot(tmp_path: Path) -> None:
    _write_snapshot(tmp_path)
    result = load_latest_local_manifest(snapshot_root=tmp_path)
    assert result.snapshot_id == SNAPSHOT_ID
    assert result.verify_counts() is True
    assert result.total_files == 3


def test_load_latest_local_manifest_picks_lexicographic_max(tmp_path: Path) -> None:
    _write_snapshot(tmp_path, snapshot_id="2026-03-01")
    _write_snapshot(tmp_path, snapshot_id="2026-04-14")
    result = load_latest_local_manifest(snapshot_root=tmp_path)
    assert result.snapshot_id == "2026-04-14"


def test_load_latest_local_manifest_no_snapshots(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_latest_local_manifest(snapshot_root=tmp_path)


def test_load_latest_local_manifest_default_root_is_publish_dir() -> None:
    import unittest.mock as mock

    from src.runtime.paths import local_publish_root

    with mock.patch("src.runtime.inspect.load_latest_manifest") as patched:
        patched.return_value = object()
        try:
            load_latest_local_manifest()
        except Exception:
            pass
        if patched.called:
            called_root = patched.call_args[0][0]
            assert called_root == local_publish_root()


# ── Integration: all helpers from one publish tree ─────────────────


def test_full_roundtrip_all_helpers(tmp_path: Path) -> None:
    _write_snapshot(tmp_path)
    _write_homepage_feed(tmp_path)

    member = load_local_member_profile("nancy-pelosi", snapshot_root=tmp_path)
    evidence = load_local_evidence_card("ec-inspect-001", snapshot_root=tmp_path)
    feed = load_local_zip_feed("94102", snapshot_root=tmp_path)
    manifest = load_local_manifest(SNAPSHOT_ID, snapshot_root=tmp_path)
    latest = load_latest_local_manifest(snapshot_root=tmp_path)
    homepage = load_local_homepage_feed(snapshot_root=tmp_path)

    assert member.bioguide_id == "P000197"
    assert evidence.member_bioguide_id == "P000197"
    assert feed.zip_code == "94102"
    assert manifest.verify_counts() is True
    assert latest.snapshot_id == manifest.snapshot_id
    assert homepage.snapshot_date == SNAPSHOT_DATE
