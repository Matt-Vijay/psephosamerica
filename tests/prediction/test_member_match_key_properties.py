"""Property tests for member feature<->label match keys (prediction correctness).

A feature row and a label row for the *same* member must share a match key, and
rows for different members must not — otherwise predictions silently drop or
mis-join. Seeded stdlib randomness (no hypothesis offline).
"""

from __future__ import annotations

import random

from src.prediction.backtest import _member_index_keys, _member_lookup_keys

_JURISDICTIONS = ["us_congress", "us_state_ca", "us_state_ny"]
_CHAMBERS = ["house", "senate", "assembly"]


def _rand_member_row(rng: random.Random, *, bioguide: str | None = None) -> dict[str, object]:
    jurisdiction = rng.choice(_JURISDICTIONS)
    row: dict[str, object] = {
        "jurisdiction_id": jurisdiction,
        "chamber": rng.choice(_CHAMBERS),
        "bioguide_id": bioguide or rng.choice(["A000001", "B000002", "C000003"]),
    }
    if jurisdiction != "us_congress":
        row["legislative_body_id"] = f"{jurisdiction}_{row['chamber']}"
        row["legislative_session_id"] = f"{jurisdiction}_2025"
    return row


def test_index_and_lookup_keys_are_symmetric() -> None:
    rng = random.Random(1)
    for _ in range(500):
        row = _rand_member_row(rng)
        # Feature indexing and label lookup must agree, or a member's features
        # could never be found for that member's labels.
        assert _member_index_keys(row) == _member_lookup_keys(row)


def test_same_member_feature_and_label_share_a_key() -> None:
    rng = random.Random(2)
    for _ in range(500):
        bioguide = "A000001"
        # Identical identity context for feature and label (same member/place).
        base = _rand_member_row(rng, bioguide=bioguide)
        feature = dict(base)
        label = dict(base)
        assert set(_member_index_keys(feature)) & set(_member_lookup_keys(label))


def test_different_members_do_not_share_a_key() -> None:
    feature = {"jurisdiction_id": "us_congress", "chamber": "house", "bioguide_id": "A000001"}
    other = {"jurisdiction_id": "us_congress", "chamber": "house", "bioguide_id": "B000002"}
    assert not (set(_member_index_keys(feature)) & set(_member_lookup_keys(other)))


def test_same_bioguide_different_jurisdiction_does_not_collide() -> None:
    # The same bioguide id under different jurisdictions must not cross-match.
    congress = {"jurisdiction_id": "us_congress", "chamber": "house", "bioguide_id": "A000001"}
    state = {
        "jurisdiction_id": "us_state_ca",
        "chamber": "assembly",
        "bioguide_id": "A000001",
        "legislative_body_id": "us_state_ca_assembly",
        "legislative_session_id": "us_state_ca_2025",
    }
    assert not (set(_member_index_keys(congress)) & set(_member_lookup_keys(state)))
