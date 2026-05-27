"""Tests for the pure evidence-card-id helpers in api/read_service.py."""

from __future__ import annotations

from types import SimpleNamespace

from src.api.read_service import (
    _missing_evidence_card_ids,
    _movement_feed_evidence_card_ids,
    _recent_event_evidence_card_ids,
    _slice_evidence_cards_by_id,
    _timeline_page_evidence_card_ids,
)


def _ev(card_id: str | None) -> SimpleNamespace:
    return SimpleNamespace(evidence_card_id=card_id)


def test_recent_event_evidence_card_ids_dedups_and_skips_blank() -> None:
    events = [_ev("a"), _ev(""), _ev("a"), _ev("b"), _ev(None)]
    assert _recent_event_evidence_card_ids(events) == ["a", "b"]


def test_movement_feed_merges_top_changes_then_recent_events() -> None:
    changes = [SimpleNamespace(top_evidence_card_ids=["x", "y", "x", ""])]
    recent = [_ev("y"), _ev("z")]
    # top-change ids first (deduped), then recent-event ids not already seen.
    assert _movement_feed_evidence_card_ids(changes, recent) == ["x", "y", "z"]


def test_slice_evidence_cards_by_id_orders_and_skips_missing() -> None:
    cards = [_ev("a"), _ev("b")]
    sliced = _slice_evidence_cards_by_id(cards, ["b", "missing", "a"])
    assert [c.evidence_card_id for c in sliced] == ["b", "a"]


def test_missing_evidence_card_ids_reports_unresolved() -> None:
    assert _missing_evidence_card_ids(["a", "b", "c"], [_ev("a")]) == ["b", "c"]


def test_timeline_page_evidence_card_ids_dedups_and_skips_blank() -> None:
    events = [_ev("a"), _ev("a"), _ev(""), _ev("b"), _ev(None)]
    assert _timeline_page_evidence_card_ids(events) == ["a", "b"]
