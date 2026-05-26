from __future__ import annotations

import datetime as dt

import pytest

from src.api.contracts import (
    ArtifactCounts,
    HistoryBackfillBootstrapPayload,
    MemberCompareScoreRow,
    SearchSessionPayload,
    SnapshotSummaryPayload,
)
from src.identity.current_member_lookup import CurrentMemberLookupPayload


def _snapshot() -> SnapshotSummaryPayload:
    return SnapshotSummaryPayload(
        snapshot_id="2026-04-13",
        snapshot_date=dt.date(2026, 4, 13),
        published_at=dt.datetime(2026, 4, 13, 12, 0, 0),
        root_sha256="a" * 64,
        total_files=1,
        total_bytes=100,
        artifact_counts=ArtifactCounts(
            members=1,
            evidence=1,
            ontology_edges=0,
            ontology_member_graphs=0,
            zip_feeds=1,
            homepage_feeds=1,
            current_member_lookups=1,
        ),
    )


def test_api_contracts_reject_boolean_artifact_counts() -> None:
    with pytest.raises(ValueError, match="members must be an integer"):
        ArtifactCounts(
            members=True,
            evidence=1,
            ontology_edges=0,
            ontology_member_graphs=0,
            zip_feeds=1,
            homepage_feeds=1,
            current_member_lookups=1,
        )


def test_api_contracts_reject_boolean_compare_counts() -> None:
    with pytest.raises(ValueError, match="left_rule_fire_count must be an integer"):
        MemberCompareScoreRow(
            dimension="conflict_of_interest_risk",
            left_rule_fire_count=True,
            right_rule_fire_count=0,
        )


def test_api_contracts_reject_boolean_history_counts() -> None:
    with pytest.raises(ValueError, match="planned_count must be an integer"):
        HistoryBackfillBootstrapPayload(
            congress=119,
            cadence="weekly",
            date_window_start=dt.date(2025, 1, 1),
            date_window_end=dt.date(2025, 12, 31),
            bounded_by_today=True,
            planned_count=True,
            completed_count=0,
            skipped_count=0,
            failed_count=0,
            remaining_count=0,
            aggregate_source_count=0,
            readiness_status="blocked",
            readiness_score=0,
            blockers=["missing_snapshots"],
        )


def test_api_contracts_accept_boolean_flags() -> None:
    payload = HistoryBackfillBootstrapPayload(
        congress=119,
        cadence="weekly",
        date_window_start=dt.date(2025, 1, 1),
        date_window_end=dt.date(2025, 12, 31),
        bounded_by_today=True,
        planned_count=1,
        completed_count=0,
        skipped_count=0,
        failed_count=0,
        remaining_count=1,
        aggregate_source_count=0,
        readiness_status="blocked",
        readiness_score=0,
        blockers=["missing_snapshots"],
    )

    assert payload.bounded_by_today is True


def test_api_contracts_reject_boolean_search_total_matches() -> None:
    with pytest.raises(ValueError, match="total_matches must be an integer"):
        SearchSessionPayload(
            query="pelosi",
            total_matches=True,
            results=[],
            snapshot=_snapshot(),
        )


def test_api_contracts_reject_nested_boolean_snapshot_counts() -> None:
    with pytest.raises(ValueError, match="total_files must be an integer"):
        SnapshotSummaryPayload(
            snapshot_id="2026-04-13",
            snapshot_date=dt.date(2026, 4, 13),
            published_at=dt.datetime(2026, 4, 13, 12, 0, 0),
            root_sha256="a" * 64,
            total_files=True,
            total_bytes=100,
            artifact_counts=ArtifactCounts(
                members=1,
                evidence=1,
                ontology_edges=0,
                ontology_member_graphs=0,
                zip_feeds=1,
                homepage_feeds=1,
                current_member_lookups=1,
            ),
        )


def test_api_contracts_allow_nested_current_member_lookup_payload() -> None:
    payload = CurrentMemberLookupPayload(snapshot_date=dt.date(2026, 4, 13), members=[])

    assert payload.members == []
