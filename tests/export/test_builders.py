"""Tests for src/export/builders.py — pure-function payload assembly."""

from __future__ import annotations

from datetime import date, datetime

from src.export.builders import (
    build_evidence_card,
    build_manifest,
    build_member_profile,
    build_zip_feed,
    sha256_hex,
)
from src.export.contracts import ConfidenceLabel, EvidenceSection


# ── Fixtures ───────────────────────────────────────────────────────

SNAPSHOT_DATE = date(2026, 4, 13)

MEMBER = {
    "bioguide_id": "S000148",
    "name": "Charles Schumer",
    "slug": "charles-schumer",
    "state": "NY",
    "district": None,
    "chamber": "senate",
    "party": "Democrat",
}

RULE_FIRE = {
    "evidence_card_id": "ec-001",
    "dimension": "conflict_of_interest_risk",
    "rule_id": "committee_sector_trade",
    "rule_version": 1,
    "score_delta": -5.0,
    "short_explanation": "Trade in sector overlapping with committee jurisdiction.",
    "confidence": "high",
    "blocks": [
        {"section": "fact", "text": "Purchased AAPL shares on 2026-01-15."},
        {"section": "inference", "text": "Technology sector overlaps with committee."},
        {"section": "normative_judgment", "text": "This pattern suggests conflict-of-interest risk."},
    ],
    "created_at": datetime(2026, 4, 13, 12, 0, 0),
}

SOURCE_ROWS = [
    {
        "source_type": "financial_disclosure",
        "source_id": "fd-99",
        "url": "https://efdsearch.senate.gov/filing/99",
        "label": "2025 Annual Disclosure",
    },
]


# ── Evidence Card ──────────────────────────────────────────────────


def test_build_evidence_card_basic():
    card = build_evidence_card(RULE_FIRE, MEMBER, SOURCE_ROWS, SNAPSHOT_DATE)
    assert card.evidence_card_id == "ec-001"
    assert card.member_bioguide_id == "S000148"
    assert card.score_delta == -5.0
    assert card.confidence == ConfidenceLabel.HIGH
    assert len(card.blocks) == 3
    assert card.blocks[0].section == EvidenceSection.FACT
    assert len(card.source_anchors) == 1
    assert card.snapshot_date == SNAPSHOT_DATE


def test_evidence_card_roundtrip_json():
    card = build_evidence_card(RULE_FIRE, MEMBER, SOURCE_ROWS, SNAPSHOT_DATE)
    data = card.model_dump(mode="json")
    assert isinstance(data["evidence_card_id"], str)
    assert data["dimension"] == "conflict_of_interest_risk"


# ── Member Profile ─────────────────────────────────────────────────


SCORE_ROWS = [
    {"dimension": "conflict_of_interest_risk", "current_score": 72.0, "rule_fire_count": 3},
]

RECENT_FIRES = [
    {
        "rule_id": "committee_sector_trade",
        "evidence_card_id": "ec-001",
        "short_explanation": "Trade overlapping committee.",
        "score_delta": -5.0,
        "snapshot_date": SNAPSHOT_DATE,
    },
]

COMMITTEE_ROWS = [
    {"committee_name": "Banking, Housing, and Urban Affairs", "role": "Chair"},
]


def test_build_member_profile():
    profile = build_member_profile(
        MEMBER, SCORE_ROWS, RECENT_FIRES, COMMITTEE_ROWS, 12, SNAPSHOT_DATE
    )
    assert profile.bioguide_id == "S000148"
    assert profile.chamber == "senate"
    assert profile.district is None
    assert len(profile.scores) == 1
    assert profile.scores[0].current_score == 72.0
    assert profile.total_evidence_cards == 12


# ── ZIP Feed ───────────────────────────────────────────────────────


def test_build_zip_feed():
    member_rows = [
        {
            "bioguide_id": "S000148",
            "name": "Charles Schumer",
            "slug": "charles-schumer",
            "chamber": "senate",
            "party": "Democrat",
            "scores": [
                {"dimension": "conflict_of_interest_risk", "current_score": 72.0, "rule_fire_count": 3},
            ],
            "top_evidence_card_ids": ["ec-001"],
        },
    ]
    feed = build_zip_feed("10001", "NY-12", None, member_rows, SNAPSHOT_DATE)
    assert feed.zip_code == "10001"
    assert feed.congressional_district == "NY-12"
    assert feed.ambiguity_note is None
    assert len(feed.members) == 1
    assert feed.members[0].top_evidence_card_ids == ["ec-001"]


def test_zip_feed_with_ambiguity():
    feed = build_zip_feed(
        "10001",
        "NY-12",
        "This ZIP spans multiple districts; showing the plurality district.",
        [],
        SNAPSHOT_DATE,
    )
    assert feed.ambiguity_note is not None


# ── Manifest ───────────────────────────────────────────────────────


def test_sha256_hex():
    digest = sha256_hex(b"hello")
    assert len(digest) == 64
    assert digest == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


def test_build_manifest():
    files = [
        {"path": "members/charles-schumer.json", "sha256": "a" * 64, "size_bytes": 1024},
        {"path": "zip/10001.json", "sha256": "b" * 64, "size_bytes": 512},
    ]
    m = build_manifest("2026-04-13", files)
    assert m.snapshot_id == "2026-04-13"
    assert m.total_files == 2
    assert m.total_bytes == 1536
    assert m.verify_counts() is True


def test_manifest_verify_counts_mismatch():
    m = build_manifest("2026-04-13", [])
    # Manually break invariant
    m.total_files = 5
    assert m.verify_counts() is False
