"""Recompute load-plan layer: rule_fire, evidence_card, score_snapshot.

Produces ordered operation dicts compatible with src/db/load_executor.py.
No DB writes; FK resolution deferred to the write layer via underscore hint keys.

Insertion order (FK-safe):
  1. rule_fire        — FK → member (_bioguide_id), ingestion_run (recompute_run_id integer)
  2. evidence_card    — FK → member (_bioguide_id), rule_fire (_rule_fire_public_id)
  3. score_snapshot   — FK → member (_bioguide_id), ingestion_run (recompute_run_id integer)
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from src.rules.models import RuleFire
from src.export.contracts import EvidenceCardPayload
from src.scoring.snapshots import build_snapshot_rows


# ---------------------------------------------------------------------------
# rule_fire
# ---------------------------------------------------------------------------


def _rule_fire_row(rf: RuleFire) -> dict[str, Any]:
    return {
        "_bioguide_id": rf.member_bioguide_id,
        "rule_id": rf.rule_id,
        "dimension": rf.dimension,
        "rule_version": str(rf.rule_version),
        "severity": rf.severity.value,
        "source_facts": rf.sourced_facts,
        "derived_values": rf.derived_values,
        "parameters": rf.parameters_used,
        # conditions are already captured in source_facts / parameters; we
        # store a stable empty sentinel so the column constraint is satisfied.
        "conditions": {},
        "source_types_required": [],
        "explanation": rf.explanation,
        "recompute_run_id": int(rf.recompute_run_id),
        "fired_at": rf.fired_at,
        "source_record_id": rf.fire_id,
        # Natural-key reference to superseded filing when this fire was triggered
        # by an amendment.  The write layer resolves this to the PK FK column.
        "_superseded_filing_source_id": rf.superseded_filing_id,
    }


def plan_rule_fires(fires: list[RuleFire]) -> dict[str, Any]:
    """One rule_fire row per RuleFire.

    Conflict identity: source_record_id (fire_id is globally unique per run).
    member_id resolved via _bioguide_id.
    """
    return {
        "table": "rule_fire",
        "rows": [_rule_fire_row(rf) for rf in fires],
        "conflict_columns": ["source_record_id"],
        "mode": "upsert",
    }


# ---------------------------------------------------------------------------
# evidence_card
# ---------------------------------------------------------------------------


def _evidence_card_row(card: EvidenceCardPayload) -> dict[str, Any]:
    return {
        "_bioguide_id": card.member_bioguide_id,
        # rule_fire FK resolved via natural key: source_record_id = fire_id
        "_rule_fire_source_record_id": f"{card.member_bioguide_id}:{card.rule_id}:{card.rule_version}",
        "public_id": card.evidence_card_id,
        "dimension": card.dimension,
        "score_delta": card.score_delta,
        "short_explanation": card.short_explanation,
        "source_anchors": [a.model_dump() for a in card.source_anchors],
        "facts": {
            b.section.value: b.text
            for b in card.blocks
            if b.section.value == "fact"
        },
        "inferences": {
            b.section.value: b.text
            for b in card.blocks
            if b.section.value == "inference"
        },
        "normative_judgments": {
            b.section.value: b.text
            for b in card.blocks
            if b.section.value == "normative_judgment"
        },
        "confidence_label": card.confidence.value.upper(),
        "member_page_slug": card.member_slug,
        "source_record_id": card.evidence_card_id,
    }


def plan_evidence_cards(cards: list[EvidenceCardPayload]) -> dict[str, Any]:
    """One evidence_card row per EvidenceCardPayload.

    Conflict identity: public_id (globally unique).
    member_id resolved via _bioguide_id.
    rule_fire_id resolved via _rule_fire_source_record_id.
    """
    return {
        "table": "evidence_card",
        "rows": [_evidence_card_row(c) for c in cards],
        "conflict_columns": ["public_id"],
        "mode": "upsert",
    }


# ---------------------------------------------------------------------------
# score_snapshot
# ---------------------------------------------------------------------------


def _snapshot_op_row(row: dict[str, Any], bioguide_id: str) -> dict[str, Any]:
    """Attach hint key to a snapshot row produced by build_snapshot_row(s)."""
    return {
        "_bioguide_id": bioguide_id,
        "snapshot_at": row["snapshot_at"],
        "recompute_run_id": row["recompute_run_id"],
        "score_total": row["score_total"],
        "dimension_scores": row["dimension_scores"],
    }


def plan_score_snapshots(
    members: list[dict[str, Any]],
    delta_rows_by_member_id: dict[int, list[dict[str, Any]]],
    snapshot_at: dt.date,
    recompute_run_id: int,
) -> dict[str, Any]:
    """One score_snapshot row per member.

    Uses build_snapshot_rows from src/scoring/snapshots.py.
    Members must include at minimum ``id`` and ``bioguide_id``.
    Conflict identity: (member_id, snapshot_at).
    member_id resolved via _bioguide_id.
    """
    raw_rows = build_snapshot_rows(
        members=members,
        delta_rows_by_member_id=delta_rows_by_member_id,
        snapshot_at=snapshot_at,
        recompute_run_id=recompute_run_id,
    )
    bioguide_by_id = {m["id"]: m["bioguide_id"] for m in members}
    rows = [
        _snapshot_op_row(row, bioguide_by_id[row["member_id"]])
        for row in raw_rows
    ]
    return {
        "table": "score_snapshot",
        "rows": rows,
        "conflict_columns": ["member_id", "snapshot_at"],
        "mode": "upsert",
    }


# ---------------------------------------------------------------------------
# Top-level plan
# ---------------------------------------------------------------------------


def recompute_load_plan(
    fires: list[RuleFire],
    cards: list[EvidenceCardPayload],
    members: list[dict[str, Any]],
    delta_rows_by_member_id: dict[int, list[dict[str, Any]]],
    snapshot_at: dt.date,
    recompute_run_id: int,
) -> list[dict[str, Any]]:
    """Ordered operation plan for a full recompute.

    Execute in list order to respect FK dependencies:
      1. rule_fire       — FK → member, ingestion_run
      2. evidence_card   — FK → member, rule_fire
      3. score_snapshot  — FK → member, ingestion_run
    """
    return [
        plan_rule_fires(fires),
        plan_evidence_cards(cards),
        plan_score_snapshots(members, delta_rows_by_member_id, snapshot_at, recompute_run_id),
    ]
