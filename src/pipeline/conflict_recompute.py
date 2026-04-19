"""Conflict-of-interest recompute orchestrator over pre-joined inputs.

Pure orchestration only — no DB, no filesystem, no network calls.
Evidence card ID generation is injectable so this module stays fully testable.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Callable

from src.evidence.builder import build_evidence_card_payload
from src.export.contracts import ConfidenceLabel, EvidenceCardPayload
from src.identity.public_ids import build_evidence_card_id
from src.query.conflict import (
    ConflictBundle,
    assemble_committee_sector_trade_bundle,
    assemble_late_or_amended_disclosure_bundle,
    assemble_repeated_committee_linked_trading_bundle,
    assemble_sector_holdings_overlap_bundle,
)
from src.rules.engine import filter_rules, load_canonical_rules
from src.rules.evaluator import evaluate_rule
from src.rules.models import RuleDefinition, RuleFire, Severity
from src.scoring.semantics import severity_score_delta


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

EvidenceCardIdGenerator = Callable[[RuleFire], str]


def _default_id_generator(fire: RuleFire) -> str:
    return build_evidence_card_id(
        bioguide_id=fire.member_bioguide_id,
        rule_id=fire.rule_id,
        dimension=fire.dimension,
        fired_date=fire.fired_at.date(),
    )


@dataclass
class MemberRecomputeResult:
    member_bioguide_id: str
    rule_fires: list[RuleFire] = field(default_factory=list)
    evidence_cards: list[EvidenceCardPayload] = field(default_factory=list)


@dataclass
class RecomputeResult:
    rule_fires: list[RuleFire] = field(default_factory=list)
    evidence_cards: list[EvidenceCardPayload] = field(default_factory=list)
    by_member: dict[str, MemberRecomputeResult] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Maps each launch rule family to its ConflictBundle assembler.
_FAMILY_ASSEMBLERS: dict[str, Callable[[dict[str, Any]], ConflictBundle]] = {
    "committee_sector_trade": assemble_committee_sector_trade_bundle,
    "repeated_committee_linked_trading": assemble_repeated_committee_linked_trading_bundle,
    "late_or_amended_disclosure": assemble_late_or_amended_disclosure_bundle,
    "sector_holdings_overlap": assemble_sector_holdings_overlap_bundle,
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _assemble_bundles(
    family: str,
    rows: list[dict[str, Any]],
    *,
    snapshot_date: dt.date,
) -> list[ConflictBundle]:
    """Unknown families silently return [] so callers can pass extra families."""
    assembler = _FAMILY_ASSEMBLERS.get(family)
    if assembler is None:
        return []
    return [
        assembler(
            {
                **row,
                "snapshot_date": row.get("snapshot_date", snapshot_date),
                "reference_date": row.get("reference_date", snapshot_date),
            }
        )
        for row in rows
    ]


def _build_card_from_fire(
    fire: RuleFire,
    member: dict[str, Any],
    bundle: ConflictBundle,
    snapshot_date: dt.date,
    id_generator: EvidenceCardIdGenerator,
) -> EvidenceCardPayload:
    card_id = id_generator(fire)
    score_delta = severity_score_delta(fire.severity)
    fact_texts = [fire.explanation] if fire.explanation else []

    return build_evidence_card_payload(
        rule_fire=fire,
        member=member,
        source_anchors=list(bundle.source_anchors),
        fact_texts=fact_texts,
        inference_texts=[],
        normative_texts=[],
        evidence_card_id=card_id,
        score_delta=score_delta,
        confidence=ConfidenceLabel.HIGH,
        snapshot_date=snapshot_date,
    )


def _fires_for_bundle(
    bundle: ConflictBundle,
    family_rules: list[RuleDefinition],
    recompute_run_id: str,
) -> list[RuleFire]:
    # Rule parameters are injected as ``parameters.<name>`` so that
    # ``value_ref`` conditions in the YAML rule definitions resolve correctly.
    fires: list[RuleFire] = []
    for rule in family_rules:
        enriched = dict(bundle.context)
        for param_name, param_val in rule.parameters.items():
            enriched[f"parameters.{param_name}"] = param_val

        fire = evaluate_rule(
            rule,
            enriched,
            bundle.member_bioguide_id,
            recompute_run_id,
            superseded_filing_id=bundle.superseded_filing_id,
        )
        if fire is not None:
            fires.append(fire)
    return fires


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def recompute_conflicts(
    rows_by_family: dict[str, list[dict[str, Any]]],
    members_by_bioguide: dict[str, dict[str, Any]],
    recompute_run_id: str,
    snapshot_date: dt.date,
    *,
    rules: list[RuleDefinition] | None = None,
    id_generator: EvidenceCardIdGenerator = _default_id_generator,
) -> RecomputeResult:
    """Orchestrate conflict-of-interest recomputation over pre-joined rows.

    Pass ``rules`` explicitly in tests; ``None`` loads canonical rules from disk.
    ``id_generator`` maps each RuleFire to a stable public evidence-card ID.
    """
    if rules is None:
        rules = load_canonical_rules()

    all_fires: list[RuleFire] = []
    all_cards: list[EvidenceCardPayload] = []
    member_buckets: dict[str, MemberRecomputeResult] = {}

    for family, rows in rows_by_family.items():
        family_rules = filter_rules(rules, family=family)
        if not family_rules:
            continue

        bundles = _assemble_bundles(family, rows, snapshot_date=snapshot_date)

        for bundle in bundles:
            bioguide_id = bundle.member_bioguide_id

            member = members_by_bioguide.get(bioguide_id) or {
                "bioguide_id": bioguide_id,
                "full_name": bioguide_id,
                "slug": bioguide_id,
            }

            if bioguide_id not in member_buckets:
                member_buckets[bioguide_id] = MemberRecomputeResult(
                    member_bioguide_id=bioguide_id
                )
            bucket = member_buckets[bioguide_id]

            fires = _fires_for_bundle(bundle, family_rules, recompute_run_id)

            for fire in fires:
                card = _build_card_from_fire(
                    fire, member, bundle, snapshot_date, id_generator
                )
                bucket.rule_fires.append(fire)
                bucket.evidence_cards.append(card)
                all_fires.append(fire)
                all_cards.append(card)

    return RecomputeResult(
        rule_fires=all_fires,
        evidence_cards=all_cards,
        by_member=member_buckets,
    )


def group_by_member(
    fires: list[RuleFire],
    cards: list[EvidenceCardPayload],
) -> dict[str, MemberRecomputeResult]:
    """Group flat fires and cards into per-member buckets without re-running orchestration."""
    buckets: dict[str, MemberRecomputeResult] = {}

    for fire in fires:
        bid = fire.member_bioguide_id
        if bid not in buckets:
            buckets[bid] = MemberRecomputeResult(member_bioguide_id=bid)
        buckets[bid].rule_fires.append(fire)

    for card in cards:
        bid = card.member_bioguide_id
        if bid not in buckets:
            buckets[bid] = MemberRecomputeResult(member_bioguide_id=bid)
        buckets[bid].evidence_cards.append(card)

    return buckets
