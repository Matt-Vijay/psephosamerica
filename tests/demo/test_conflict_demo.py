"""End-to-end tests for the conflict-of-interest demo path.

Validates that the current architecture can produce real, inspectable
output from synthetic inputs — no DB, no network, no filesystem writes.

All assertions are deterministic; the module-scoped fixture runs once.
"""

from __future__ import annotations

import datetime as dt

import pytest

from src.demo.conflict_demo import DemoResult, main, run_conflict_demo
from src.export.contracts import (
    ConfidenceLabel,
    EvidenceCardPayload,
    MemberProfilePayload,
    ZipFeedPayload,
)
from src.export.manifest import SnapshotManifest
from src.rules.models import RuleFire


@pytest.fixture(scope="module")
def result() -> DemoResult:
    """Run the demo once and share it across all tests in this module."""
    return run_conflict_demo()


# ---------------------------------------------------------------------------
# Demo top-level
# ---------------------------------------------------------------------------


class TestDemoRuns:
    def test_returns_demo_result(self, result: DemoResult) -> None:
        assert isinstance(result, DemoResult)

    def test_member_bioguide_id(self, result: DemoResult) -> None:
        assert result.member["bioguide_id"] == "S000999"

    def test_member_slug(self, result: DemoResult) -> None:
        assert result.member["slug"] == "jane-smith"

    def test_contexts_nonempty(self, result: DemoResult) -> None:
        assert len(result.contexts) >= 1

    def test_at_least_one_rule_fires(self, result: DemoResult) -> None:
        assert len(result.fires) >= 1

    def test_all_fires_are_rule_fire_instances(self, result: DemoResult) -> None:
        assert all(isinstance(f, RuleFire) for f in result.fires)

    def test_fires_linked_to_synthetic_member(self, result: DemoResult) -> None:
        for f in result.fires:
            assert f.member_bioguide_id == "S000999"

    def test_fires_linked_to_demo_run_id(self, result: DemoResult) -> None:
        for f in result.fires:
            assert f.recompute_run_id == "demo-run-2026-04-13"

    def test_all_four_rule_families_fire(self, result: DemoResult) -> None:
        fired_ids = {f.rule_id for f in result.fires}
        assert "conflict_of_interest_risk.committee_sector_trade.v1" in fired_ids
        assert "conflict_of_interest_risk.sector_holdings_overlap.v1" in fired_ids
        assert "conflict_of_interest_risk.repeated_committee_linked_trading.v1" in fired_ids
        assert "conflict_of_interest_risk.late_or_amended_disclosure.v1" in fired_ids

    def test_fire_ids_unique(self, result: DemoResult) -> None:
        ids = [f.fire_id for f in result.fires]
        assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# Evidence card
# ---------------------------------------------------------------------------


class TestEvidenceCard:
    def test_returns_evidence_card_payload(self, result: DemoResult) -> None:
        assert isinstance(result.evidence_card, EvidenceCardPayload)

    def test_linked_to_member_bioguide(self, result: DemoResult) -> None:
        assert result.evidence_card.member_bioguide_id == "S000999"

    def test_linked_to_member_slug(self, result: DemoResult) -> None:
        assert result.evidence_card.member_slug == "jane-smith"

    def test_has_blocks(self, result: DemoResult) -> None:
        assert len(result.evidence_card.blocks) >= 1

    def test_has_source_anchors(self, result: DemoResult) -> None:
        assert len(result.evidence_card.source_anchors) >= 1

    def test_snapshot_date(self, result: DemoResult) -> None:
        assert result.evidence_card.snapshot_date == dt.date(2026, 4, 13)

    def test_score_delta_is_negative(self, result: DemoResult) -> None:
        assert result.evidence_card.score_delta < 0

    def test_confidence_is_high(self, result: DemoResult) -> None:
        assert result.evidence_card.confidence == ConfidenceLabel.HIGH

    def test_dimension_is_conflict_of_interest_risk(self, result: DemoResult) -> None:
        assert result.evidence_card.dimension == "conflict_of_interest_risk"

    def test_evidence_card_id_nonempty(self, result: DemoResult) -> None:
        assert result.evidence_card.evidence_card_id

    def test_rule_id_nonempty(self, result: DemoResult) -> None:
        assert result.evidence_card.rule_id

    def test_short_explanation_nonempty(self, result: DemoResult) -> None:
        assert result.evidence_card.short_explanation


# ---------------------------------------------------------------------------
# Member profile
# ---------------------------------------------------------------------------


class TestMemberProfile:
    def test_returns_member_profile_payload(self, result: DemoResult) -> None:
        assert isinstance(result.member_profile, MemberProfilePayload)

    def test_bioguide_id(self, result: DemoResult) -> None:
        assert result.member_profile.bioguide_id == "S000999"

    def test_name(self, result: DemoResult) -> None:
        assert result.member_profile.name == "Jane Smith"

    def test_slug(self, result: DemoResult) -> None:
        assert result.member_profile.slug == "jane-smith"

    def test_chamber(self, result: DemoResult) -> None:
        assert result.member_profile.chamber == "house"

    def test_party(self, result: DemoResult) -> None:
        assert result.member_profile.party == "D"

    def test_has_scores(self, result: DemoResult) -> None:
        assert len(result.member_profile.scores) >= 1

    def test_score_dimension(self, result: DemoResult) -> None:
        assert result.member_profile.scores[0].dimension == "conflict_of_interest_risk"

    def test_rule_fire_count_positive(self, result: DemoResult) -> None:
        assert result.member_profile.scores[0].rule_fire_count >= 1

    def test_has_recent_rule_fires(self, result: DemoResult) -> None:
        assert len(result.member_profile.recent_rule_fires) >= 1

    def test_has_committees(self, result: DemoResult) -> None:
        assert len(result.member_profile.committees) >= 1

    def test_total_evidence_cards_positive(self, result: DemoResult) -> None:
        assert result.member_profile.total_evidence_cards >= 1

    def test_snapshot_date(self, result: DemoResult) -> None:
        assert result.member_profile.snapshot_date == dt.date(2026, 4, 13)


# ---------------------------------------------------------------------------
# ZIP feed
# ---------------------------------------------------------------------------


class TestZipFeed:
    def test_returns_zip_feed_payload(self, result: DemoResult) -> None:
        assert isinstance(result.zip_feed, ZipFeedPayload)

    def test_zip_code(self, result: DemoResult) -> None:
        assert result.zip_feed.zip_code == "94107"

    def test_has_members(self, result: DemoResult) -> None:
        assert len(result.zip_feed.members) >= 1

    def test_synthetic_member_in_feed(self, result: DemoResult) -> None:
        ids = {m.bioguide_id for m in result.zip_feed.members}
        assert "S000999" in ids

    def test_member_has_scores(self, result: DemoResult) -> None:
        member = result.zip_feed.members[0]
        assert len(member.scores) >= 1

    def test_member_has_evidence_card_ids(self, result: DemoResult) -> None:
        member = result.zip_feed.members[0]
        assert len(member.top_evidence_card_ids) >= 1

    def test_snapshot_date(self, result: DemoResult) -> None:
        assert result.zip_feed.snapshot_date == dt.date(2026, 4, 13)


# ---------------------------------------------------------------------------
# Snapshot manifest
# ---------------------------------------------------------------------------


class TestSnapshotManifest:
    def test_returns_snapshot_manifest(self, result: DemoResult) -> None:
        assert isinstance(result.snapshot_manifest, SnapshotManifest)

    def test_snapshot_id(self, result: DemoResult) -> None:
        assert result.snapshot_manifest.snapshot_id == "2026-04-13"

    def test_has_entries(self, result: DemoResult) -> None:
        assert len(result.snapshot_manifest.entries) >= 1

    def test_counts_consistent(self, result: DemoResult) -> None:
        assert result.snapshot_manifest.verify_counts()

    def test_sha256_lengths(self, result: DemoResult) -> None:
        for entry in result.snapshot_manifest.entries:
            assert len(entry.sha256) == 64, f"bad sha256 for {entry.path}"

    def test_all_sizes_positive(self, result: DemoResult) -> None:
        for entry in result.snapshot_manifest.entries:
            assert entry.size_bytes > 0, f"zero size for {entry.path}"

    def test_paths_nonempty(self, result: DemoResult) -> None:
        for entry in result.snapshot_manifest.entries:
            assert entry.path, "empty path in manifest entry"

    def test_total_bytes_matches_sum(self, result: DemoResult) -> None:
        expected = sum(e.size_bytes for e in result.snapshot_manifest.entries)
        assert result.snapshot_manifest.total_bytes == expected


# ---------------------------------------------------------------------------
# main() entry point
# ---------------------------------------------------------------------------


class TestMain:
    def test_main_runs_without_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        main()
        captured = capsys.readouterr()
        assert captured.out.strip()

    def test_main_outputs_valid_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        import json

        main()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, dict)

    def test_main_status_ok(self, capsys: pytest.CaptureFixture[str]) -> None:
        import json

        main()
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "ok"

    def test_main_includes_bioguide_id(self, capsys: pytest.CaptureFixture[str]) -> None:
        import json

        main()
        data = json.loads(capsys.readouterr().out)
        assert data["bioguide_id"] == "S000999"

    def test_main_rule_fires_positive(self, capsys: pytest.CaptureFixture[str]) -> None:
        import json

        main()
        data = json.loads(capsys.readouterr().out)
        assert data["rule_fires"] >= 1

    def test_main_fired_rule_ids_present(self, capsys: pytest.CaptureFixture[str]) -> None:
        import json

        main()
        data = json.loads(capsys.readouterr().out)
        assert len(data["fired_rule_ids"]) >= 1
