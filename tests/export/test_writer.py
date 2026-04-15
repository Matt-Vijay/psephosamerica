from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from src.export.builders import sha256_hex
from src.export.contracts import (
    ConfidenceLabel,
    EvidenceBlock,
    EvidenceCardPayload,
    EvidenceSection,
    MemberProfilePayload,
    ScoreSummary,
    SourceAnchor,
    ZipFeedPayload,
    ZipMemberSummary,
)
from src.export.writer import (
    PlannedFile,
    evidence_path,
    manifest_path,
    member_path,
    plan_snapshot,
    serialize_payload,
    zip_path,
)

SNAPSHOT_DATE = date(2026, 4, 13)
SNAPSHOT_ID = "2026-04-13"


# ── Fixtures ────────────────────────────────────────────────────────────────────


def _make_member_profile() -> MemberProfilePayload:
    return MemberProfilePayload(
        bioguide_id="S000148",
        name="Charles Schumer",
        slug="charles-schumer",
        state="NY",
        chamber="senate",
        party="Democrat",
        scores=[
            ScoreSummary(
                dimension="conflict_of_interest_risk",
                current_score=72.0,
                rule_fire_count=3,
            )
        ],
        recent_rule_fires=[],
        committees=[],
        total_evidence_cards=1,
        snapshot_date=SNAPSHOT_DATE,
    )


def _make_evidence_card() -> EvidenceCardPayload:
    return EvidenceCardPayload(
        evidence_card_id="ec-001",
        member_bioguide_id="S000148",
        member_name="Charles Schumer",
        member_slug="charles-schumer",
        dimension="conflict_of_interest_risk",
        rule_id="committee_sector_trade",
        rule_version=1,
        score_delta=-5.0,
        short_explanation="Trade in sector overlapping with committee jurisdiction.",
        blocks=[
            EvidenceBlock(section=EvidenceSection.FACT, text="Purchased AAPL on 2026-01-15."),
        ],
        source_anchors=[
            SourceAnchor(
                source_type="financial_disclosure",
                source_id="fd-99",
                url="https://efdsearch.senate.gov/filing/99",
                label="2025 Annual Disclosure",
            )
        ],
        confidence=ConfidenceLabel.HIGH,
        snapshot_date=SNAPSHOT_DATE,
        created_at=datetime(2026, 4, 13, 12, 0, 0),
    )


def _make_zip_feed() -> ZipFeedPayload:
    return ZipFeedPayload(
        zip_code="10001",
        members=[
            ZipMemberSummary(
                bioguide_id="S000148",
                name="Charles Schumer",
                slug="charles-schumer",
                chamber="senate",
                party="Democrat",
                scores=[],
            )
        ],
        snapshot_date=SNAPSHOT_DATE,
    )


# ── serialize_payload ────────────────────────────────────────────────────────────


def test_serialize_payload_returns_bytes():
    result = serialize_payload(_make_member_profile())
    assert isinstance(result, bytes)


def test_serialize_payload_valid_json():
    data = json.loads(serialize_payload(_make_member_profile()))
    assert data["bioguide_id"] == "S000148"
    assert data["slug"] == "charles-schumer"


def test_serialize_payload_sorted_keys():
    raw = serialize_payload(_make_member_profile()).decode("utf-8")
    top_keys = list(json.loads(raw).keys())
    assert top_keys == sorted(top_keys)


def test_serialize_payload_deterministic():
    profile = _make_member_profile()
    assert serialize_payload(profile) == serialize_payload(profile)


def test_serialize_payload_dates_as_strings():
    data = json.loads(serialize_payload(_make_member_profile()))
    assert isinstance(data["snapshot_date"], str)
    assert data["snapshot_date"] == "2026-04-13"


def test_serialize_payload_evidence_card_roundtrip():
    card = _make_evidence_card()
    data = json.loads(serialize_payload(card))
    assert data["evidence_card_id"] == "ec-001"
    assert data["score_delta"] == -5.0
    assert data["confidence"] == "high"


# ── path helpers ─────────────────────────────────────────────────────────────────


def test_member_path():
    assert member_path("charles-schumer") == "members/charles-schumer.json"


def test_member_path_ends_with_json():
    assert member_path("any-slug").endswith(".json")


def test_zip_path():
    assert zip_path("10001") == "zip/10001.json"


def test_evidence_path():
    assert evidence_path("ec-001") == "evidence/ec-001.json"


def test_manifest_path():
    assert manifest_path("2026-04-13") == "snapshots/2026-04-13/manifest.json"


def test_manifest_path_contains_snapshot_id():
    sid = "2025-12-31"
    assert sid in manifest_path(sid)


# ── PlannedFile ────────────────────────────────────────────────────────────────────


def test_planned_file_from_bytes_fields():
    content = b"hello world"
    pf = PlannedFile.from_bytes("test/path.json", content)
    assert pf.path == "test/path.json"
    assert pf.content == content
    assert pf.size_bytes == len(content)
    assert len(pf.sha256) == 64


def test_planned_file_sha256_matches():
    content = b"test data for hashing"
    pf = PlannedFile.from_bytes("x.json", content)
    assert pf.sha256 == sha256_hex(content)


def test_planned_file_size_matches():
    content = b"abc"
    pf = PlannedFile.from_bytes("x.json", content)
    assert pf.size_bytes == 3


def test_planned_file_frozen():
    pf = PlannedFile.from_bytes("x.json", b"data")
    with pytest.raises((AttributeError, TypeError)):
        pf.path = "changed"  # type: ignore[misc]


def test_planned_file_empty_content():
    pf = PlannedFile.from_bytes("empty.json", b"")
    assert pf.size_bytes == 0
    assert len(pf.sha256) == 64


# ── plan_snapshot ──────────────────────────────────────────────────────────────────


def test_plan_snapshot_file_count():
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [_make_member_profile()],
        [_make_zip_feed()],
        [_make_evidence_card()],
    )
    # 1 member + 1 zip + 1 evidence + 1 manifest
    assert len(plan) == 4


def test_plan_snapshot_empty_inputs():
    plan = plan_snapshot(SNAPSHOT_ID, [], [], [])
    # Only the manifest
    assert len(plan) == 1
    assert plan[0].path == manifest_path(SNAPSHOT_ID)


def test_plan_snapshot_manifest_is_last():
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [_make_member_profile()],
        [_make_zip_feed()],
        [_make_evidence_card()],
    )
    assert plan[-1].path == manifest_path(SNAPSHOT_ID)


def test_plan_snapshot_member_path_present():
    plan = plan_snapshot(SNAPSHOT_ID, [_make_member_profile()], [], [])
    paths = [f.path for f in plan]
    assert "members/charles-schumer.json" in paths


def test_plan_snapshot_zip_path_present():
    plan = plan_snapshot(SNAPSHOT_ID, [], [_make_zip_feed()], [])
    paths = [f.path for f in plan]
    assert "zip/10001.json" in paths


def test_plan_snapshot_evidence_path_present():
    plan = plan_snapshot(SNAPSHOT_ID, [], [], [_make_evidence_card()])
    paths = [f.path for f in plan]
    assert "evidence/ec-001.json" in paths


def test_plan_snapshot_manifest_covers_data_files():
    """Manifest entries must exactly cover all non-manifest planned files."""
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [_make_member_profile()],
        [_make_zip_feed()],
        [_make_evidence_card()],
    )
    manifest_content = json.loads(plan[-1].content)
    manifest_paths = {e["path"] for e in manifest_content["entries"]}
    data_paths = {f.path for f in plan[:-1]}
    assert data_paths == manifest_paths


def test_plan_snapshot_manifest_sha256_correct():
    """SHA-256 recorded in the manifest matches actual file content."""
    plan = plan_snapshot(SNAPSHOT_ID, [_make_member_profile()], [], [])
    manifest_data = json.loads(plan[-1].content)
    for entry in manifest_data["entries"]:
        matching = next(f for f in plan if f.path == entry["path"])
        assert entry["sha256"] == matching.sha256


def test_plan_snapshot_manifest_size_correct():
    """size_bytes recorded in the manifest matches actual file content."""
    plan = plan_snapshot(SNAPSHOT_ID, [_make_member_profile()], [], [])
    manifest_data = json.loads(plan[-1].content)
    for entry in manifest_data["entries"]:
        matching = next(f for f in plan if f.path == entry["path"])
        assert entry["size_bytes"] == matching.size_bytes


def test_plan_snapshot_deterministic():
    """Data files are content-deterministic; all paths are stable across calls.

    The manifest's ``created_at`` is wall-clock-stamped by ``build_manifest``,
    so manifest *content* may differ between calls — but all paths and every
    data-file content must be identical.
    """
    plan1 = plan_snapshot(SNAPSHOT_ID, [_make_member_profile()], [_make_zip_feed()], [])
    plan2 = plan_snapshot(SNAPSHOT_ID, [_make_member_profile()], [_make_zip_feed()], [])
    assert len(plan1) == len(plan2)
    mpath = manifest_path(SNAPSHOT_ID)
    for f1, f2 in zip(plan1, plan2):
        assert f1.path == f2.path  # path-stable always
        if f1.path != mpath:
            assert f1.content == f2.content  # data files are content-deterministic
            assert f1.sha256 == f2.sha256


def test_plan_snapshot_all_files_have_valid_sha256():
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [_make_member_profile()],
        [_make_zip_feed()],
        [_make_evidence_card()],
    )
    for f in plan:
        assert f.sha256 == sha256_hex(f.content)
        assert len(f.sha256) == 64


def test_plan_snapshot_no_duplicate_paths():
    plan = plan_snapshot(
        SNAPSHOT_ID,
        [_make_member_profile()],
        [_make_zip_feed()],
        [_make_evidence_card()],
    )
    paths = [f.path for f in plan]
    assert len(paths) == len(set(paths))
