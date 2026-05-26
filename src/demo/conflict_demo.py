"""Deterministic end-to-end conflict-of-interest demo — no DB, no network, no filesystem.

Usage::

    from src.demo.conflict_demo import run_conflict_demo
    result = run_conflict_demo()
    print(result.fires)
    print(result.evidence_card.model_dump_json(indent=2))
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from src.evidence.builder import build_evidence_card_payload, build_source_anchor
from src.export.builders import (
    build_manifest,
    build_member_profile,
    build_zip_feed,
    sha256_hex,
)
from src.export.contracts import (
    ConfidenceLabel,
    EvidenceCardPayload,
    MemberProfilePayload,
    ZipFeedPayload,
)
from src.export.manifest import SnapshotManifest
from src.identity.public_ids import build_evidence_card_id
from src.rules.contexts import (
    build_committee_sector_trade_context,
    build_late_or_amended_disclosure_context,
    build_repeated_committee_linked_trading_context,
    build_sector_holdings_overlap_context,
)
from src.rules.engine import load_canonical_rules, run_member_batch
from src.rules.models import RuleFire


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SNAPSHOT_DATE = dt.date(2026, 4, 13)
_SNAPSHOT_ID = "2026-04-13"
_RECOMPUTE_RUN_ID = "demo-run-2026-04-13"

_MEMBER: dict[str, Any] = {
    "bioguide_id": "S000999",
    "full_name": "Jane Smith",
    "name": "Jane Smith",
    "slug": "jane-smith",
    "state": "CA",
    "district": "12",
    "chamber": "house",
    "party": "D",
}

_COMMITTEES: list[dict[str, Any]] = [
    {"committee_name": "House Financial Services Committee", "role": "member"},
]


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DemoResult:
    member: dict[str, Any]
    contexts: list[dict[str, Any]]
    fires: list[RuleFire]
    evidence_card: EvidenceCardPayload
    member_profile: MemberProfilePayload
    zip_feed: ZipFeedPayload
    snapshot_manifest: SnapshotManifest


# ---------------------------------------------------------------------------
# Context builders
# ---------------------------------------------------------------------------


def _build_contexts() -> list[dict[str, Any]]:
    # Each context is augmented with ``parameters.*`` keys so that
    # ``value_ref`` conditions in the rule YAML resolve correctly.
    # Returns one dict per launch rule family.

    # Shared holding row used by the two holdings-based rule families.
    _holdings_row: dict[str, Any] = {
        "committee_name": "House Financial Services Committee",
        "committee_sector": "finance",
        "committee_start_date": dt.date(2023, 1, 3),
        "committee_end_date": None,  # still serving
        "holding_sector": "finance",
        "sector_name": "Finance",
        "disclosure_period_start": dt.date(2023, 1, 1),
        "disclosure_period_end": dt.date(2023, 12, 31),
    }

    cst_ctx = build_committee_sector_trade_context(_holdings_row)
    cst_ctx["parameters.minimum_overlap_days"] = 1

    sho_row: dict[str, Any] = {
        **_holdings_row,
        "holding_value_min": 15_000.0,
        "holding_value_max": 50_000.0,
    }
    sho_ctx = build_sector_holdings_overlap_context(sho_row)
    sho_ctx["parameters.minimum_overlap_days"] = 1
    sho_ctx["parameters.minimum_holding_value_usd"] = 1_000.0

    rct_row: dict[str, Any] = {
        "committee_name": "House Financial Services Committee",
        "committee_sector": "finance",
        "committee_start_date": dt.date(2023, 1, 3),
        "committee_end_date": dt.date(2023, 12, 31),
        "transactions": [
            {"transaction_date": dt.date(2023, 3, 15), "sector": "finance"},
            {"transaction_date": dt.date(2023, 6, 1), "sector": "finance"},
            {"transaction_date": dt.date(2023, 9, 10), "sector": "finance"},
        ],
    }
    rct_ctx = build_repeated_committee_linked_trading_context(rct_row)
    rct_ctx["parameters.minimum_matching_transactions"] = 2
    rct_ctx["parameters.minimum_distinct_trade_days"] = 2
    rct_ctx["parameters.minimum_overlap_days"] = 1

    # 30 days late original filing
    late_row: dict[str, Any] = {
        "filing_id": "fd-S000999-2023",
        "filed_at": dt.date(2023, 6, 14),
        "deadline": dt.date(2023, 5, 15),
        "filing_is_amendment": False,
        "superseded_filing_id": None,
        "filing_kind": "original",
    }
    late_ctx = build_late_or_amended_disclosure_context(late_row)
    late_ctx["parameters.late_threshold_days"] = 0

    return [cst_ctx, sho_ctx, rct_ctx, late_ctx]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_conflict_demo() -> DemoResult:
    """Run a deterministic end-to-end conflict-of-interest demo.

    Raises:
        AssertionError: if no rules fire (fixture problem, not a product bug).
    """
    member = _MEMBER
    contexts = _build_contexts()

    rules = load_canonical_rules()
    fires = run_member_batch(rules, member["bioguide_id"], contexts, _RECOMPUTE_RUN_ID)

    if not fires:
        raise AssertionError(
            "Demo produced zero rule fires — check synthetic context construction."
        )

    first_fire = fires[0]
    evidence_card_id = build_evidence_card_id(
        bioguide_id=first_fire.member_bioguide_id,
        rule_id=first_fire.rule_id,
        dimension=first_fire.dimension,
        fired_date=first_fire.fired_at.date(),
    )

    source_anchors = [
        build_source_anchor(
            source_type="financial_disclosure",
            source_id="fd-S000999-2023",
            url="https://disclosures.house.gov/public_disc/financial-pdfs/2023/fd-S000999-2023.pdf",
            label="Annual Financial Disclosure 2023",
        ),
        build_source_anchor(
            source_type="committee_membership",
            source_id="cm-S000999-hfsc",
            url="https://api.congress.gov/v3/committee/house/HSBA?format=json",
            label="House Financial Services Committee membership",
        ),
    ]

    evidence_card = build_evidence_card_payload(
        rule_fire=first_fire,
        member=member,
        source_anchors=source_anchors,
        fact_texts=[
            f"Member served on {_COMMITTEES[0]['committee_name']} from 2023-01-03.",
            "Member disclosed finance-sector holdings in the 2023 Annual Financial Disclosure.",
        ],
        inference_texts=[
            "The committee service period and the disclosed holding period overlap.",
        ],
        normative_texts=[],
        evidence_card_id=evidence_card_id,
        score_delta=-5.0,
        confidence=ConfidenceLabel.HIGH,
        snapshot_date=_SNAPSHOT_DATE,
    )

    member_profile = build_member_profile(
        member=member,
        score_rows=[
            {
                "dimension": "conflict_of_interest_risk",
                "current_score": float(-5 * len(fires)),
                "rule_fire_count": len(fires),
            }
        ],
        recent_fires=[
            {
                "rule_id": f.rule_id,
                "evidence_card_id": evidence_card_id,
                "short_explanation": f.explanation,
                "score_delta": -5.0,
                "snapshot_date": _SNAPSHOT_DATE,
            }
            for f in fires
        ],
        committee_rows=_COMMITTEES,
        total_evidence_cards=len(fires),
        snapshot_date=_SNAPSHOT_DATE,
    )

    zip_feed = build_zip_feed(
        zip_code="94107",
        district="CA-12",
        ambiguity_note=None,
        member_rows=[
            {
                "bioguide_id": member["bioguide_id"],
                "name": member["name"],
                "slug": member["slug"],
                "chamber": member["chamber"],
                "party": member["party"],
                "scores": [
                    {
                        "dimension": "conflict_of_interest_risk",
                        "current_score": float(-5 * len(fires)),
                        "rule_fire_count": len(fires),
                    }
                ],
                "top_evidence_card_ids": [evidence_card_id],
            }
        ],
        snapshot_date=_SNAPSHOT_DATE,
    )

    ec_bytes = evidence_card.model_dump_json().encode()
    mp_bytes = member_profile.model_dump_json().encode()
    zf_bytes = zip_feed.model_dump_json().encode()

    snapshot_manifest = build_manifest(
        snapshot_id=_SNAPSHOT_ID,
        file_entries=[
            {
                "path": f"evidence/{evidence_card_id}.json",
                "sha256": sha256_hex(ec_bytes),
                "size_bytes": len(ec_bytes),
            },
            {
                "path": f"member/{member['slug']}.json",
                "sha256": sha256_hex(mp_bytes),
                "size_bytes": len(mp_bytes),
            },
            {
                "path": "zip/94107.json",
                "sha256": sha256_hex(zf_bytes),
                "size_bytes": len(zf_bytes),
            },
        ],
    )

    return DemoResult(
        member=member,
        contexts=contexts,
        fires=fires,
        evidence_card=evidence_card,
        member_profile=member_profile,
        zip_feed=zip_feed,
        snapshot_manifest=snapshot_manifest,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Print a compact human/JSON summary of the conflict demo to stdout."""
    import json
    import sys

    result = run_conflict_demo()

    summary = {
        "member": result.member["full_name"],
        "bioguide_id": result.member["bioguide_id"],
        "rule_fires": len(result.fires),
        "fired_rule_ids": sorted({f.rule_id for f in result.fires}),
        "score_delta_total": float(result.member_profile.scores[0].current_score),
        "evidence_card_id": result.evidence_card.evidence_card_id,
        "snapshot_id": result.snapshot_manifest.snapshot_id,
        "manifest_entries": len(result.snapshot_manifest.entries),
        "status": "ok",
    }
    json.dump(summary, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
