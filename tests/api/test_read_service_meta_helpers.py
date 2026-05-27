"""Tests for read-service featured-change selection and snapshot-meta helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from src.api.read_service import (
    _featured_member_changes_from_top_changes,
    _meta_or_not_found,
)

_MOD = "src.api.read_service"


def test_featured_member_changes_dedups_slugs_and_skips_missing() -> None:
    changes = [SimpleNamespace(slug="a"), SimpleNamespace(slug="a"), SimpleNamespace(slug="b")]

    def fake_load(slug: str, *, snapshot_root: object) -> object:
        if slug == "b":
            raise FileNotFoundError
        return SimpleNamespace(slug=slug)

    with patch(f"{_MOD}._load_member_change_summary_payload", side_effect=fake_load):
        result = _featured_member_changes_from_top_changes(changes, snapshot_root=None, limit=10)

    # Duplicate "a" deduped; "b" skipped because its payload is missing.
    assert [r.slug for r in result] == ["a"]


def test_featured_member_changes_respects_limit() -> None:
    changes = [SimpleNamespace(slug=s) for s in ("a", "b", "c")]
    with patch(
        f"{_MOD}._load_member_change_summary_payload",
        side_effect=lambda slug, *, snapshot_root: SimpleNamespace(slug=slug),
    ):
        result = _featured_member_changes_from_top_changes(changes, snapshot_root=None, limit=2)
    assert [r.slug for r in result] == ["a", "b"]


def test_meta_or_not_found_returns_not_found_when_snapshot_missing() -> None:
    with patch(f"{_MOD}.load_latest_local_snapshot_metadata", side_effect=FileNotFoundError):
        result = _meta_or_not_found(snapshot_root=None)
    assert result.resource_type == "snapshot"
    assert result.identifier == "latest"
