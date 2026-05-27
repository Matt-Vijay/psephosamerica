"""Integration: roundtrip verify — seed DB, recompute, publish, read back and compare.

Exercises the full cycle:
  DB rows → recompute → publish tree → read back JSON → compare typed payloads

Published files are verified both by SHA-256 (publish pipeline's own verify
stage) and by deserializing back into Pydantic models and comparing field
values against the recompute output.

Requires OPENPACT_TEST_POSTGRES_DSN in the environment.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import pytest

from src.db.bootstrap import apply_sql, read_schema_sql
from src.db.repositories import execute_one, fetch_all
from src.export.builders import build_member_profile, build_zip_feed, sha256_hex
from src.export.contracts import (
    EvidenceCardPayload,
    MemberProfilePayload,
    ZipFeedPayload,
)
from src.export.filesystem import read_manifest, verify_written_files
from src.export.writer import evidence_path, plan_snapshot
from src.pipeline.conflict_recompute import recompute_conflicts
from src.pipeline.publish_pipeline import PublishConfig, run_publish
from src.rules.models import (
    Condition,
    ConditionGroup,
    Operator,
    RuleDefinition,
    RuleFire,
    Severity,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENPACT_TEST_POSTGRES_DSN"),
    reason="OPENPACT_TEST_POSTGRES_DSN not set",
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SNAPSHOT_DATE = dt.date(2026, 2, 1)
_SNAPSHOT_ID = "2026-02-01"
_RUN_ID = "roundtrip-run-001"


# ---------------------------------------------------------------------------
# DB seed helpers
# ---------------------------------------------------------------------------


def _seed_schema(conn) -> None:
    apply_sql(conn, read_schema_sql())


def _seed_member(
    conn,
    bioguide_id: str,
    full_name: str,
    slug: str,
    state: str = "CA",
    chamber: str = "house",
    party: str = "Democrat",
) -> int:
    execute_one(
        conn,
        """
        INSERT INTO member (bioguide_id, slug, last_name, full_name, party, state, chamber, is_current)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (bioguide_id, slug, full_name.split()[-1], full_name, party, state, chamber, True),
    )
    rows = fetch_all(conn, "SELECT id FROM member WHERE bioguide_id = %s", (bioguide_id,))
    return rows[0]["id"]


def _seed_committee(conn, code: str, name: str, congress: int = 119) -> int:
    execute_one(
        conn,
        """
        INSERT INTO committee (committee_code, congress, chamber, committee_type, name, review_tier)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (code, congress, "house", "standing", name, "deterministic"),
    )
    rows = fetch_all(
        conn,
        "SELECT id FROM committee WHERE committee_code = %s AND congress = %s",
        (code, congress),
    )
    return rows[0]["id"]


def _seed_committee_membership(
    conn,
    committee_id: int,
    member_id: int,
    start: dt.date = dt.date(2025, 1, 3),
) -> int:
    execute_one(
        conn,
        """
        INSERT INTO committee_membership (committee_id, member_id, role, start_date, is_current)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (committee_id, member_id, "Member", start, True),
    )
    rows = fetch_all(
        conn,
        "SELECT id FROM committee_membership WHERE committee_id = %s AND member_id = %s AND start_date = %s",
        (committee_id, member_id, start),
    )
    return rows[0]["id"]


def _seed_disclosure(
    conn,
    member_id: int,
    filing_year: int = 2025,
    period_start: dt.date = dt.date(2025, 1, 1),
    period_end: dt.date = dt.date(2025, 12, 31),
) -> int:
    execute_one(
        conn,
        """
        INSERT INTO financial_disclosure
            (member_id, chamber, filing_year, filing_type,
             filing_period_start, filing_period_end, filed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (member_id, "house", filing_year, "annual", period_start, period_end, dt.date(2025, 5, 15)),
    )
    rows = fetch_all(
        conn,
        "SELECT id FROM financial_disclosure WHERE member_id = %s AND filing_year = %s",
        (member_id, filing_year),
    )
    return rows[0]["id"]


def _seed_holding(conn, disclosure_id: int, line: int = 1) -> int:
    execute_one(
        conn,
        """
        INSERT INTO holding
            (financial_disclosure_id, line_number, owner_type, issuer_name,
             issuer_ticker, value_min, value_max)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (disclosure_id, line, "self", "Exxon Mobil", "XOM", 15001.0, 50000.0),
    )
    rows = fetch_all(
        conn,
        "SELECT id FROM holding WHERE financial_disclosure_id = %s AND line_number = %s",
        (disclosure_id, line),
    )
    return rows[0]["id"]


# ---------------------------------------------------------------------------
# Recompute helpers
# ---------------------------------------------------------------------------


def _make_cst_rule() -> RuleDefinition:
    return RuleDefinition(
        rule_id="conflict_of_interest_risk.committee_sector_trade.v1",
        dimension="conflict_of_interest_risk",
        version=1,
        inputs=[],
        conditions=ConditionGroup(
            all_of=[
                Condition(fact="committee_sector", operator=Operator.is_not_null),
                Condition(fact="holding_sector", operator=Operator.is_not_null),
                Condition(
                    fact="committee_service_overlap_days",
                    operator=Operator.gte,
                    value_ref="parameters.minimum_overlap_days",
                ),
                Condition(
                    fact="holding_overlap_days",
                    operator=Operator.gte,
                    value_ref="parameters.minimum_overlap_days",
                ),
            ]
        ),
        parameters={"minimum_overlap_days": 1},
        severity=Severity.medium,
        source_types_required=["committee_membership", "financial_disclosure"],
        explanation_template=(
            "Member served on {committee_name} while disclosing a holding "
            "in {sector_name}; overlap was {committee_service_overlap_days} day(s)."
        ),
    )


def _build_cst_row(
    bioguide: str,
    fd_id: int,
    cm_id: int,
    committee_name: str = "Energy Committee",
    sector: str = "energy",
) -> dict[str, Any]:
    return {
        "member_bioguide_id": bioguide,
        "committee_name": committee_name,
        "committee_sector": sector,
        "holding_sector": sector,
        "sector_name": sector.capitalize(),
        "committee_start_date": dt.date(2025, 1, 3),
        "committee_end_date": None,
        "disclosure_period_start": dt.date(2025, 1, 1),
        "disclosure_period_end": dt.date(2025, 12, 31),
        "financial_disclosure_id": str(fd_id),
        "committee_membership_id": str(cm_id),
        # Public evidence cards require an HTTPS source anchor for every
        # claim-bearing anchor on nonzero deltas (production threads these in).
        "source_url": "https://disclosures.house.gov/public_disc/financial-pdfs/2025/"
        + bioguide
        + ".pdf",
        "committee_membership_source_url": "https://www.congress.gov/committee/energy-committee",
    }


def _seq_id_gen(prefix: str = "ec-rt") -> Any:
    counter = {"n": 0}

    def gen(fire: RuleFire) -> str:
        counter["n"] += 1
        return f"{prefix}-{counter['n']}"

    return gen


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRoundtripVerify:
    """Publish a snapshot and verify every file round-trips correctly."""

    def _seed_and_recompute(
        self, conn, bioguide: str = "R000001", slug: str = "rep-roundtrip"
    ) -> tuple[dict[str, Any], list[EvidenceCardPayload]]:
        """Seed one member's full row tree, recompute, return (member_dict, cards)."""
        _seed_schema(conn)
        member_id = _seed_member(conn, bioguide, f"Rep {bioguide}", slug)
        committee_id = _seed_committee(conn, "HENG", "Energy Committee")
        cm_id = _seed_committee_membership(conn, committee_id, member_id)
        fd_id = _seed_disclosure(conn, member_id)
        _seed_holding(conn, fd_id)

        member_dict = {
            "bioguide_id": bioguide,
            "full_name": f"Rep {bioguide}",
            "slug": slug,
        }

        cst_row = _build_cst_row(bioguide, fd_id, cm_id)
        result = recompute_conflicts(
            rows_by_family={"committee_sector_trade": [cst_row]},
            members_by_bioguide={bioguide: member_dict},
            recompute_run_id=_RUN_ID,
            snapshot_date=_SNAPSHOT_DATE,
            rules=[_make_cst_rule()],
            id_generator=_seq_id_gen(),
        )
        return member_dict, result.evidence_cards

    def test_evidence_card_roundtrip(self, pg_conn_clean, tmp_path: Path):
        """Write evidence card via publish pipeline, read back, compare typed payload."""
        conn = pg_conn_clean
        member_dict, cards = self._seed_and_recompute(conn)
        assert len(cards) == 1
        original_card = cards[0]

        def planner():
            return plan_snapshot(
                snapshot_id=_SNAPSHOT_ID,
                member_profiles=[],
                zip_feeds=[],
                evidence_cards=cards,
            )

        config = PublishConfig(snapshot_id=_SNAPSHOT_ID, target_dir=tmp_path)
        pub = run_publish(config, planner)
        assert pub.succeeded

        # Read back and deserialize
        card_path = tmp_path / f"evidence/{original_card.evidence_card_id}.json"
        assert card_path.exists()
        raw = json.loads(card_path.read_bytes())
        restored = EvidenceCardPayload.model_validate(raw)

        # Compare typed fields
        assert restored.evidence_card_id == original_card.evidence_card_id
        assert restored.member_bioguide_id == original_card.member_bioguide_id
        assert restored.member_name == original_card.member_name
        assert restored.member_slug == original_card.member_slug
        assert restored.dimension == original_card.dimension
        assert restored.rule_id == original_card.rule_id
        assert restored.rule_version == original_card.rule_version
        assert restored.score_delta == original_card.score_delta
        assert restored.short_explanation == original_card.short_explanation
        assert restored.confidence == original_card.confidence
        assert restored.snapshot_date == original_card.snapshot_date

    def test_manifest_roundtrip(self, pg_conn_clean, tmp_path: Path):
        """Verify manifest file contains correct entry count and hashes."""
        conn = pg_conn_clean
        _, cards = self._seed_and_recompute(conn)

        def planner():
            return plan_snapshot(
                snapshot_id=_SNAPSHOT_ID,
                member_profiles=[],
                zip_feeds=[],
                evidence_cards=cards,
            )

        config = PublishConfig(snapshot_id=_SNAPSHOT_ID, target_dir=tmp_path)
        pub = run_publish(config, planner)
        assert pub.succeeded

        manifest_path = tmp_path / f"snapshots/{_SNAPSHOT_ID}/manifest.json"
        assert manifest_path.exists()
        manifest = read_manifest(manifest_path)

        # Manifest should cover evidence card files and any additional
        # publish-layer sidecars, but not the manifest itself.
        manifest_paths = {entry.path for entry in manifest.entries}
        expected_evidence_paths = {evidence_path(card.evidence_card_id) for card in cards}
        assert expected_evidence_paths.issubset(manifest_paths)
        assert "manifest.json" not in manifest_paths
        assert manifest.total_files == len(manifest.entries)
        assert manifest.verify_counts()

        # Verify each entry's hash matches the actual file
        for entry in manifest.entries:
            file_path = tmp_path / entry.path
            assert file_path.exists(), f"Manifest entry {entry.path} not found on disk"
            actual_hash = sha256_hex(file_path.read_bytes())
            assert actual_hash == entry.sha256, f"Hash mismatch for {entry.path}"

    def test_full_snapshot_with_profile_and_card(self, pg_conn_clean, tmp_path: Path):
        """Publish a snapshot with both a member profile and evidence card, verify both."""
        conn = pg_conn_clean
        member_dict, cards = self._seed_and_recompute(conn, bioguide="F000001", slug="rep-full")

        # Build a member profile payload from the recompute results
        profile = build_member_profile(
            member={
                "bioguide_id": "F000001",
                "name": "Rep F000001",
                "slug": "rep-full",
                "state": "CA",
                "district": None,
                "chamber": "house",
                "party": "Democrat",
            },
            score_rows=[
                {
                    "dimension": "conflict_of_interest_risk",
                    "current_score": 98.0,
                    "rule_fire_count": 1,
                }
            ],
            recent_fires=[
                {
                    "rule_id": cards[0].rule_id,
                    "evidence_card_id": cards[0].evidence_card_id,
                    "short_explanation": cards[0].short_explanation,
                    "score_delta": cards[0].score_delta,
                    "snapshot_date": _SNAPSHOT_DATE,
                }
            ],
            committee_rows=[{"committee_name": "Energy Committee", "role": "Member"}],
            total_evidence_cards=1,
            snapshot_date=_SNAPSHOT_DATE,
        )

        def planner():
            return plan_snapshot(
                snapshot_id=_SNAPSHOT_ID,
                member_profiles=[profile],
                zip_feeds=[],
                evidence_cards=cards,
            )

        config = PublishConfig(snapshot_id=_SNAPSHOT_ID, target_dir=tmp_path)
        pub = run_publish(config, planner)
        assert pub.succeeded

        # Verify profile roundtrips
        profile_path = tmp_path / "members/rep-full.json"
        assert profile_path.exists()
        raw = json.loads(profile_path.read_bytes())
        restored_profile = MemberProfilePayload.model_validate(raw)
        assert restored_profile.bioguide_id == "F000001"
        assert restored_profile.slug == "rep-full"
        assert len(restored_profile.scores) == 1
        assert restored_profile.scores[0].dimension == "conflict_of_interest_risk"
        assert restored_profile.total_evidence_cards == 1

        # Verify card roundtrips
        card_path = tmp_path / f"evidence/{cards[0].evidence_card_id}.json"
        assert card_path.exists()
        restored_card = EvidenceCardPayload.model_validate(json.loads(card_path.read_bytes()))
        assert restored_card.evidence_card_id == cards[0].evidence_card_id

        # Manifest should cover the profile/card files and any additional
        # publish-layer sidecars.
        manifest = read_manifest(tmp_path / f"snapshots/{_SNAPSHOT_ID}/manifest.json")
        entry_paths = {e.path for e in manifest.entries}
        assert "members/rep-full.json" in entry_paths
        assert evidence_path(cards[0].evidence_card_id) in entry_paths
        assert "manifest.json" not in entry_paths
        assert manifest.total_files == len(manifest.entries)

    def test_zip_feed_roundtrip(self, pg_conn_clean, tmp_path: Path):
        """Publish a ZIP feed, read back, compare typed payload."""
        conn = pg_conn_clean
        _, cards = self._seed_and_recompute(conn, bioguide="Z000001", slug="rep-zip")

        zip_feed = build_zip_feed(
            zip_code="94102",
            district="CA-11",
            ambiguity_note=None,
            member_rows=[
                {
                    "bioguide_id": "Z000001",
                    "name": "Rep Z000001",
                    "slug": "rep-zip",
                    "chamber": "house",
                    "party": "Democrat",
                    "scores": [
                        {
                            "dimension": "conflict_of_interest_risk",
                            "current_score": 98.0,
                            "rule_fire_count": 1,
                        }
                    ],
                    "top_evidence_card_ids": [cards[0].evidence_card_id],
                }
            ],
            snapshot_date=_SNAPSHOT_DATE,
        )

        def planner():
            return plan_snapshot(
                snapshot_id=_SNAPSHOT_ID,
                member_profiles=[],
                zip_feeds=[zip_feed],
                evidence_cards=cards,
            )

        config = PublishConfig(snapshot_id=_SNAPSHOT_ID, target_dir=tmp_path)
        pub = run_publish(config, planner)
        assert pub.succeeded

        zip_path = tmp_path / "zip/94102.json"
        assert zip_path.exists()
        restored = ZipFeedPayload.model_validate(json.loads(zip_path.read_bytes()))
        assert restored.zip_code == "94102"
        assert restored.congressional_district == "CA-11"
        assert len(restored.members) == 1
        assert restored.members[0].bioguide_id == "Z000001"


class TestPublishVerifyIntegrity:
    """Verify SHA-256 integrity of every published file."""

    def test_tampered_file_detected(self, pg_conn_clean, tmp_path: Path):
        """If a file is tampered after writing, verify_written_files catches it."""
        conn = pg_conn_clean
        _seed_schema(conn)
        member_id = _seed_member(conn, "V000001", "Rep Verify", "rep-verify")
        committee_id = _seed_committee(conn, "HVER", "Verify Committee")
        cm_id = _seed_committee_membership(conn, committee_id, member_id)
        fd_id = _seed_disclosure(conn, member_id)
        _seed_holding(conn, fd_id)

        cst_row = _build_cst_row("V000001", fd_id, cm_id, committee_name="Verify Committee")
        member_dict = {
            "bioguide_id": "V000001",
            "full_name": "Rep Verify",
            "slug": "rep-verify",
        }

        result = recompute_conflicts(
            rows_by_family={"committee_sector_trade": [cst_row]},
            members_by_bioguide={"V000001": member_dict},
            recompute_run_id=_RUN_ID,
            snapshot_date=_SNAPSHOT_DATE,
            rules=[_make_cst_rule()],
            id_generator=_seq_id_gen("ec-ver"),
        )

        planned = plan_snapshot(
            snapshot_id=_SNAPSHOT_ID,
            member_profiles=[],
            zip_feeds=[],
            evidence_cards=result.evidence_cards,
        )

        # Write files manually
        from src.export.filesystem import write_planned_files

        write_planned_files(planned, tmp_path)

        # Verify passes before tampering
        failures = verify_written_files(planned, tmp_path)
        assert failures == []

        # Tamper with one file
        card = result.evidence_cards[0]
        card_path = tmp_path / f"evidence/{card.evidence_card_id}.json"
        card_path.write_bytes(b'{"tampered": true}')

        # Verify now catches the mismatch
        failures = verify_written_files(planned, tmp_path)
        assert len(failures) == 1
        assert f"evidence/{card.evidence_card_id}.json" in failures[0]
