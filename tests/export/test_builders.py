from __future__ import annotations

import copy
from datetime import date, datetime

import pytest
from pydantic import ValidationError

from src.export.builders import (
    _normalized_card_ids,
    build_evidence_card,
    build_manifest,
    build_member_profile,
    build_zip_feed,
    sha256_hex,
)
from src.export.contracts import ConfidenceLabel, EvidenceSection
from src.export.manifest import SnapshotManifest, manifest_root_sha256

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
        {
            "section": "normative_judgment",
            "text": "This pattern suggests conflict-of-interest risk.",
        },
    ],
    "created_at": datetime(2026, 4, 13, 12, 0, 0),
}

SOURCE_ROWS = [
    {
        "source_type": "financial_disclosure",
        "source_id": "fd-99",
        "url": "https://efdsearch.senate.gov/search/view/paper/99/",
        "label": "2025 Annual Disclosure",
    },
]


# ── Evidence Card — happy path ─────────────────────────────────────


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
    assert data["source_count"] == 1
    assert data["official_source_count"] == 1
    assert data["primary_source_url"] == "https://efdsearch.senate.gov/search/view/paper/99/"
    assert data["primary_source_label"] == "2025 Annual Disclosure"


# ── Evidence Card — missing required fields ────────────────────────


@pytest.mark.parametrize(
    "missing_key",
    [
        "evidence_card_id",
        "dimension",
        "rule_id",
        "rule_version",
        "score_delta",
        "short_explanation",
        "confidence",
        "blocks",
    ],
)
def test_evidence_card_missing_rule_fire_field(missing_key):
    rf = copy.deepcopy(RULE_FIRE)
    del rf[missing_key]
    with pytest.raises(ValueError, match=missing_key):
        build_evidence_card(rf, MEMBER, SOURCE_ROWS, SNAPSHOT_DATE)


@pytest.mark.parametrize("missing_key", ["bioguide_id", "name", "slug"])
def test_evidence_card_missing_member_field(missing_key):
    m = copy.deepcopy(MEMBER)
    del m[missing_key]
    with pytest.raises(ValueError, match=missing_key):
        build_evidence_card(RULE_FIRE, m, SOURCE_ROWS, SNAPSHOT_DATE)


@pytest.mark.parametrize("missing_key", ["source_type", "source_id", "label"])
def test_evidence_card_missing_source_field(missing_key):
    rows = [copy.deepcopy(SOURCE_ROWS[0])]
    del rows[0][missing_key]
    with pytest.raises(ValueError, match=missing_key):
        build_evidence_card(RULE_FIRE, MEMBER, rows, SNAPSHOT_DATE)


@pytest.mark.parametrize("missing_key", ["section", "text"])
def test_evidence_card_missing_block_field(missing_key):
    rf = copy.deepcopy(RULE_FIRE)
    del rf["blocks"][0][missing_key]
    with pytest.raises(ValueError, match=missing_key):
        build_evidence_card(rf, MEMBER, SOURCE_ROWS, SNAPSHOT_DATE)


def test_evidence_card_source_url_optional_for_zero_delta_card():
    """url is optional for zero-delta cards that do not make a score claim."""
    rows = [copy.deepcopy(SOURCE_ROWS[0])]
    del rows[0]["url"]
    rf = copy.deepcopy(RULE_FIRE)
    rf["score_delta"] = 0.0
    card = build_evidence_card(rf, MEMBER, rows, SNAPSHOT_DATE)
    assert card.source_anchors[0].url is None


@pytest.mark.parametrize(
    "source_type",
    ["financial_disclosure", "fec_contribution", "vote_event"],
)
def test_build_evidence_card_rejects_claim_bearing_source_without_https(source_type):
    rows = [copy.deepcopy(SOURCE_ROWS[0])]
    rows[0]["source_type"] = source_type
    del rows[0]["url"]

    with pytest.raises(ValueError, match=rf"{source_type}.*fd-99"):
        build_evidence_card(RULE_FIRE, MEMBER, rows, SNAPSHOT_DATE)


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


@pytest.mark.parametrize(
    "missing_key",
    [
        "bioguide_id",
        "name",
        "slug",
        "state",
        "chamber",
        "party",
    ],
)
def test_member_profile_missing_member_field(missing_key):
    m = copy.deepcopy(MEMBER)
    del m[missing_key]
    with pytest.raises(ValueError, match=missing_key):
        build_member_profile(m, SCORE_ROWS, RECENT_FIRES, COMMITTEE_ROWS, 0, SNAPSHOT_DATE)


@pytest.mark.parametrize("missing_key", ["dimension", "current_score", "rule_fire_count"])
def test_member_profile_missing_score_field(missing_key):
    rows = [copy.deepcopy(SCORE_ROWS[0])]
    del rows[0][missing_key]
    with pytest.raises(ValueError, match=missing_key):
        build_member_profile(MEMBER, rows, RECENT_FIRES, COMMITTEE_ROWS, 0, SNAPSHOT_DATE)


@pytest.mark.parametrize(
    "missing_key",
    [
        "rule_id",
        "evidence_card_id",
        "short_explanation",
        "score_delta",
        "snapshot_date",
    ],
)
def test_member_profile_missing_fire_field(missing_key):
    fires = [copy.deepcopy(RECENT_FIRES[0])]
    del fires[0][missing_key]
    with pytest.raises(ValueError, match=missing_key):
        build_member_profile(MEMBER, SCORE_ROWS, fires, COMMITTEE_ROWS, 0, SNAPSHOT_DATE)


def test_member_profile_missing_committee_name():
    rows = [{"role": "Member"}]  # no committee_name
    with pytest.raises(ValueError, match="committee_name"):
        build_member_profile(MEMBER, SCORE_ROWS, RECENT_FIRES, rows, 0, SNAPSHOT_DATE)


def test_member_profile_committee_role_optional():
    rows = [{"committee_name": "Judiciary"}]  # no role key
    profile = build_member_profile(MEMBER, SCORE_ROWS, RECENT_FIRES, rows, 0, SNAPSHOT_DATE)
    assert profile.committees[0].role is None


def test_member_profile_district_optional():
    m = copy.deepcopy(MEMBER)
    del m["district"]
    profile = build_member_profile(m, SCORE_ROWS, RECENT_FIRES, COMMITTEE_ROWS, 0, SNAPSHOT_DATE)
    assert profile.district is None


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
                {
                    "dimension": "conflict_of_interest_risk",
                    "current_score": 72.0,
                    "rule_fire_count": 3,
                },
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


@pytest.mark.parametrize("missing_key", ["bioguide_id", "name", "slug", "chamber", "party"])
def test_zip_feed_missing_member_field(missing_key):
    row = {
        "bioguide_id": "S000148",
        "name": "Charles Schumer",
        "slug": "charles-schumer",
        "chamber": "senate",
        "party": "Democrat",
    }
    del row[missing_key]
    with pytest.raises(ValueError, match=missing_key):
        build_zip_feed("10001", None, None, [row], SNAPSHOT_DATE)


@pytest.mark.parametrize("missing_key", ["dimension", "current_score", "rule_fire_count"])
def test_zip_feed_missing_score_field(missing_key):
    score = {"dimension": "conflict_of_interest_risk", "current_score": 50.0, "rule_fire_count": 1}
    del score[missing_key]
    row = {
        "bioguide_id": "S000148",
        "name": "Charles Schumer",
        "slug": "charles-schumer",
        "chamber": "senate",
        "party": "Democrat",
        "scores": [score],
    }
    with pytest.raises(ValueError, match=missing_key):
        build_zip_feed("10001", None, None, [row], SNAPSHOT_DATE)


def test_zip_feed_scores_and_cards_default_empty():
    """scores and top_evidence_card_ids are optional in member rows."""
    row = {
        "bioguide_id": "S000148",
        "name": "Charles Schumer",
        "slug": "charles-schumer",
        "chamber": "senate",
        "party": "Democrat",
    }
    feed = build_zip_feed("10001", None, None, [row], SNAPSHOT_DATE)
    assert feed.members[0].scores == []
    assert feed.members[0].top_evidence_card_ids == []


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


def test_build_manifest_sets_root_sha256():
    files = [
        {"path": "members/charles-schumer.json", "sha256": "a" * 64, "size_bytes": 1024},
        {"path": "zip/10001.json", "sha256": "b" * 64, "size_bytes": 512},
    ]
    manifest = build_manifest("2026-04-13", files)
    assert manifest.root_sha256 == manifest_root_sha256(manifest.entries)


def test_build_manifest_root_sha256_is_order_independent():
    files = [
        {"path": "zip/10001.json", "sha256": "b" * 64, "size_bytes": 512},
        {"path": "members/charles-schumer.json", "sha256": "a" * 64, "size_bytes": 1024},
    ]
    manifest_a = build_manifest("2026-04-13", files)
    manifest_b = build_manifest("2026-04-13", list(reversed(files)))
    assert manifest_a.root_sha256 == manifest_b.root_sha256


def test_snapshot_manifest_requires_explicit_root_sha256():
    with pytest.raises(ValidationError, match="root_sha256"):
        SnapshotManifest.model_validate(
            {
                "snapshot_id": "2026-04-13",
                "created_at": "2026-04-13T00:00:00",
                "entries": [],
                "total_files": 0,
                "total_bytes": 0,
            }
        )


def test_manifest_entry_rejects_boolean_size_bytes():
    with pytest.raises(ValueError, match="size_bytes must be an integer"):
        SnapshotManifest.model_validate(
            {
                "snapshot_id": "2026-04-13",
                "created_at": "2026-04-13T00:00:00",
                "entries": [
                    {"path": "x.json", "sha256": "a" * 64, "size_bytes": True},
                ],
                "total_files": 1,
                "total_bytes": 1,
                "root_sha256": "b" * 64,
            }
        )


def test_snapshot_manifest_rejects_boolean_totals():
    with pytest.raises(ValueError, match="total_files must be an integer"):
        SnapshotManifest.model_validate(
            {
                "snapshot_id": "2026-04-13",
                "created_at": "2026-04-13T00:00:00",
                "entries": [],
                "total_files": True,
                "total_bytes": 0,
                "root_sha256": "b" * 64,
            }
        )


def test_manifest_verify_counts_mismatch():
    m = build_manifest("2026-04-13", [])
    # Manually break invariant
    m.total_files = 5
    assert m.verify_counts() is False


@pytest.mark.parametrize("missing_key", ["path", "sha256", "size_bytes"])
def test_manifest_missing_file_entry_field(missing_key):
    entry = {"path": "x.json", "sha256": "a" * 64, "size_bytes": 100}
    del entry[missing_key]
    with pytest.raises(ValueError, match=missing_key):
        build_manifest("2026-04-13", [entry])


# ── helper / validation branch coverage ───────────────────────────


def test_normalized_card_ids_dedups_skips_blanks_and_truncates():
    assert _normalized_card_ids(None) == []
    assert _normalized_card_ids([]) == []
    assert _normalized_card_ids(["a", "", "a", "b"]) == ["a", "b"]
    assert _normalized_card_ids(["a", "b", "c", "d"], limit=2) == ["a", "b"]


def test_build_evidence_card_dedups_source_anchors_by_identity():
    rows = [
        {
            "source_type": "financial_disclosure",
            "source_id": "fd-99",
            "url": "https://efdsearch.senate.gov/search/view/paper/99/",
            "label": "FD 99",
        },
        {
            "source_type": "financial_disclosure",
            "source_id": "fd-99",
            "url": "https://efdsearch.senate.gov/search/view/paper/99/",
            "label": "2025 Annual Disclosure (longer label)",
        },
        {
            # A worse candidate (shorter label) must NOT displace the winner.
            "source_type": "financial_disclosure",
            "source_id": "fd-99",
            "url": "https://efdsearch.senate.gov/search/view/paper/99/",
            "label": "FD",
        },
    ]
    card = build_evidence_card(RULE_FIRE, MEMBER, rows, SNAPSHOT_DATE)
    assert len(card.source_anchors) == 1
    # The longer label wins the dedup tie-break for the same source identity.
    assert card.source_anchors[0].label == "2025 Annual Disclosure (longer label)"


def test_build_manifest_rejects_unconfined_entry_path():
    files = [{"path": "../escape.json", "sha256": "a" * 64, "size_bytes": 10}]
    with pytest.raises(ValueError, match="confined to publish root"):
        build_manifest("2026-04-13", files)


def test_build_manifest_rejects_duplicate_entry_paths():
    files = [
        {"path": "members/x.json", "sha256": "a" * 64, "size_bytes": 10},
        {"path": "members/x.json", "sha256": "b" * 64, "size_bytes": 20},
    ]
    with pytest.raises(ValueError, match="duplicate manifest entry paths"):
        build_manifest("2026-04-13", files)
