"""Connectivity / quality scorecard — Track A v8 definition-of-done (deliverable #5).

The platform-first pivot defines "done" not as "all data exists" but as the
unified graph clearing quality gates. This module computes those gates over a
contract corpus (a directory of :class:`~src.graph.contracts.EntityResolutionOutput`
rows) plus the LOCUS jurisdiction-linkage P/R, and renders a pinned scorecard:

* **per-tier coverage** — entity counts split by jurisdiction tier
  (federal / state / county / city) and entity type (person / bill / org);
* **dup rate** — fraction of rows whose ``canonical_id`` is not unique (must be
  0.0: IDs are content-addressed, so a dup means a real double-write);
* **orphan-node rate** — fraction of entities carrying no source anchor (must be
  0.0: the contract rejects empty ``source_anchors``, so this is a structural
  guarantee re-verified here);
* **cross-tier link counts** — how many entities the LOCUS join connects to the
  canonical jurisdiction layer (the headline cross-tier connectivity);
* **P/R** — the LOCUS->canonical jurisdiction linkage precision/recall.

A gate fails loudly (``passed=False``) so a regression is visible. The scorecard
is serialized to JSON and pinned by a test, and folded into the coverage
snapshot, so the numbers cannot silently drift.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field

from src.graph.contracts import EntityResolutionOutput


def _tier_of_jurisdiction(code: str | None) -> str:
    """Map a jurisdiction code (or bill/person hint) to a coverage tier."""
    if not code:
        return "unknown"
    if code in ("us", "us-congress"):
        return "federal"
    parts = code.split("-")
    if len(parts) == 1 and parts[0] == "us":
        return "federal"
    if len(parts) == 2:
        return "state"
    if len(parts) >= 3:
        token = parts[2]
        return {"city": "city", "county": "county", "sd": "special_district"}.get(token, "state")
    return "unknown"


def _row_tier(row: EntityResolutionOutput) -> str:
    """Best-effort tier for a contract row from its dossier jurisdiction hint."""
    dossier = row.dossier_json or {}
    juris = dossier.get("jurisdiction_id")
    if isinstance(juris, str):
        return _tier_of_jurisdiction(juris)
    # Federal congress bills carry us-congress in their external IDs / id space;
    # without a jurisdiction hint we fall back to "unknown" rather than guess.
    return "unknown"


@dataclass(frozen=True)
class Gate:
    """One quality gate: a measured value, its threshold, and pass/fail."""

    name: str
    value: float
    threshold: float
    comparison: str  # "<=" or ">="
    passed: bool


@dataclass(frozen=True)
class ConnectivityScorecard:
    """The Track A v8 connectivity/quality scorecard."""

    total_entities: int
    by_entity_type: dict[str, int]
    by_tier: dict[str, int]
    unique_canonical_ids: int
    dup_rate: float
    orphan_rate: float
    cross_tier_links: int
    jurisdiction_precision: float
    jurisdiction_recall: float
    jurisdictions_linked: int
    jurisdictions_minted: int
    jurisdictions_total: int
    # Cross-tier connectivity through the canonical jurisdiction join (deliverable
    # #3): ordinances + officials reachable across the LOCUS<->municipal bridge.
    connected_ordinances: int = 0
    connected_officials: int = 0
    gates: list[Gate] = field(default_factory=list)

    @property
    def all_gates_passed(self) -> bool:
        return all(gate.passed for gate in self.gates)


def _build_gates(
    *,
    dup_rate: float,
    orphan_rate: float,
    precision: float,
    recall: float,
) -> list[Gate]:
    return [
        Gate("dup_rate", dup_rate, 0.0, "<=", dup_rate <= 0.0),
        Gate("orphan_rate", orphan_rate, 0.0, "<=", orphan_rate <= 0.0),
        # Precision must be perfect (zero false merges is the core promise).
        Gate("jurisdiction_precision", precision, 1.0, ">=", precision >= 1.0),
        # Recall floor per the v8 directive (>= 0.75 with precision held at 1.00).
        Gate("jurisdiction_recall", recall, 0.75, ">=", recall >= 0.75),
    ]


def compute_scorecard(
    rows: Iterable[EntityResolutionOutput],
    *,
    jurisdiction_precision: float,
    jurisdiction_recall: float,
    jurisdictions_linked: int,
    jurisdictions_minted: int,
    jurisdictions_total: int,
    connected_ordinances: int = 0,
    connected_officials: int = 0,
) -> ConnectivityScorecard:
    """Compute the connectivity/quality scorecard over a contract corpus."""
    by_type: Counter[str] = Counter()
    by_tier: Counter[str] = Counter()
    seen_ids: set[str] = set()
    total = 0
    dups = 0
    orphans = 0

    for row in rows:
        total += 1
        by_type[row.entity_type] += 1
        by_tier[_row_tier(row)] += 1
        if row.canonical_id in seen_ids:
            dups += 1
        else:
            seen_ids.add(row.canonical_id)
        if not row.source_anchors:
            orphans += 1

    dup_rate = dups / total if total else 0.0
    orphan_rate = orphans / total if total else 0.0
    gates = _build_gates(
        dup_rate=dup_rate,
        orphan_rate=orphan_rate,
        precision=jurisdiction_precision,
        recall=jurisdiction_recall,
    )
    return ConnectivityScorecard(
        total_entities=total,
        by_entity_type=dict(sorted(by_type.items())),
        by_tier=dict(sorted(by_tier.items())),
        unique_canonical_ids=len(seen_ids),
        dup_rate=dup_rate,
        orphan_rate=orphan_rate,
        cross_tier_links=jurisdictions_linked,
        jurisdiction_precision=jurisdiction_precision,
        jurisdiction_recall=jurisdiction_recall,
        jurisdictions_linked=jurisdictions_linked,
        jurisdictions_minted=jurisdictions_minted,
        jurisdictions_total=jurisdictions_total,
        connected_ordinances=connected_ordinances,
        connected_officials=connected_officials,
        gates=gates,
    )


def scorecard_to_json(scorecard: ConnectivityScorecard) -> str:
    """Serialize the scorecard to deterministic, sorted JSON."""
    payload = asdict(scorecard)
    payload["all_gates_passed"] = scorecard.all_gates_passed
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def render_scorecard_markdown(scorecard: ConnectivityScorecard) -> str:
    """Render the scorecard as a human-readable markdown section."""
    lines = [
        "# Track A connectivity / quality scorecard",
        "",
        "Auto-generated from `src.graph.scorecard.compute_scorecard` over the LOCUS",
        "contract corpus + the jurisdiction-linkage P/R. Pinned by",
        "`tests/graph/test_scorecard.py`. This is the v8 definition-of-done: the",
        "graph clears its gates, not merely 'all data exists'.",
        "",
        f"- **total entities**: {scorecard.total_entities:,}",
        f"- **unique canonical IDs**: {scorecard.unique_canonical_ids:,}",
        f"- **dup rate**: {scorecard.dup_rate:.6f}",
        f"- **orphan-node rate**: {scorecard.orphan_rate:.6f}",
        f"- **cross-tier links (LOCUS->canonical jurisdiction)**: {scorecard.cross_tier_links:,}",
        f"- **cross-tier reach (ordinances <-> officials via the join)**: "
        f"{scorecard.connected_ordinances:,} ordinances <-> "
        f"{scorecard.connected_officials:,} officials",
        f"- **jurisdiction precision / recall**: "
        f"{scorecard.jurisdiction_precision:.4f} / {scorecard.jurisdiction_recall:.4f}",
        f"- **jurisdictions linked / minted / total**: "
        f"{scorecard.jurisdictions_linked:,} / {scorecard.jurisdictions_minted:,} / "
        f"{scorecard.jurisdictions_total:,}",
        "",
        "## By entity type",
        "",
        "| entity_type | count |",
        "|---|---|",
    ]
    for entity_type, count in scorecard.by_entity_type.items():
        lines.append(f"| {entity_type} | {count:,} |")
    lines += ["", "## By tier", "", "| tier | count |", "|---|---|"]
    for tier, count in scorecard.by_tier.items():
        lines.append(f"| {tier} | {count:,} |")
    lines += ["", "## Gates", "", "| gate | value | threshold | passed |", "|---|---|---|---|"]
    for gate in scorecard.gates:
        mark = "PASS" if gate.passed else "FAIL"
        lines.append(
            f"| {gate.name} | {gate.value:.6f} | {gate.comparison} {gate.threshold} | {mark} |"
        )
    lines += [
        "",
        f"**All gates passed: {scorecard.all_gates_passed}**",
        "",
    ]
    return "\n".join(lines)
