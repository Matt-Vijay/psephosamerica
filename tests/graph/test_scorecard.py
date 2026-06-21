from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from src.graph.ingest.locus import ordinance_row, parse_locus_row
from src.graph.scorecard import (
    ConnectivityScorecard,
    compute_scorecard,
    render_scorecard_markdown,
    scorecard_to_json,
)

_KNOWN = datetime(2026, 6, 20, tzinfo=UTC)


def _ordinance(state: str, place: str, level: str, content: str) -> object:
    record = {
        "header": "### x",
        "content": content,
        "is_substantive": True,
        "function": "Rules",
        "topic": "Zoning",
        "source_jurisdiction_type": "cities" if level == "city" else "counties",
        "state": state,
        "city": place if level == "city" else None,
        "county": place if level == "county" else None,
        "opacity": 0.1,
        "paternalism": 0.1,
        "enforcement_discretion": 0.1,
        "problem_salience": 0.1,
    }
    parsed = parse_locus_row(record)
    assert parsed is not None
    return ordinance_row(parsed, known_at=_KNOWN)


def test_scorecard_gates_pass_on_clean_corpus() -> None:
    rows = [
        _ordinance("ca", "oakland", "city", "a"),
        _ordinance("ca", "oakland", "city", "b"),
        _ordinance("wa", "king_county", "county", "c"),
    ]
    sc = compute_scorecard(
        rows,
        jurisdiction_precision=1.0,
        jurisdiction_recall=1.0,
        jurisdictions_linked=2,
        jurisdictions_minted=0,
        jurisdictions_total=2,
    )
    assert sc.total_entities == 3
    assert sc.unique_canonical_ids == 3
    assert sc.dup_rate == 0.0
    assert sc.orphan_rate == 0.0
    assert sc.by_entity_type == {"bill": 3}
    assert sc.by_tier == {"city": 2, "county": 1}
    assert sc.cross_tier_links == 2
    assert sc.all_gates_passed


def test_scorecard_detects_duplicate_ids() -> None:
    one = _ordinance("ca", "oakland", "city", "same")
    # Re-use the exact same row object twice -> a real double-write.
    sc = compute_scorecard(
        [one, one],
        jurisdiction_precision=1.0,
        jurisdiction_recall=1.0,
        jurisdictions_linked=0,
        jurisdictions_minted=1,
        jurisdictions_total=1,
    )
    assert sc.dup_rate == 0.5
    assert not sc.all_gates_passed
    dup_gate = next(g for g in sc.gates if g.name == "dup_rate")
    assert not dup_gate.passed


def test_scorecard_recall_floor_gate() -> None:
    sc = compute_scorecard(
        [_ordinance("ca", "oakland", "city", "a")],
        jurisdiction_precision=1.0,
        jurisdiction_recall=0.50,  # below the 0.75 floor
        jurisdictions_linked=1,
        jurisdictions_minted=0,
        jurisdictions_total=2,
    )
    assert not sc.all_gates_passed
    recall_gate = next(g for g in sc.gates if g.name == "jurisdiction_recall")
    assert not recall_gate.passed


def test_scorecard_precision_must_be_perfect() -> None:
    sc = compute_scorecard(
        [_ordinance("ca", "oakland", "city", "a")],
        jurisdiction_precision=0.99,  # any false merge fails the gate
        jurisdiction_recall=1.0,
        jurisdictions_linked=1,
        jurisdictions_minted=0,
        jurisdictions_total=1,
    )
    assert not sc.all_gates_passed


def test_scorecard_json_roundtrips() -> None:
    sc = compute_scorecard(
        [_ordinance("ca", "oakland", "city", "a")],
        jurisdiction_precision=1.0,
        jurisdiction_recall=1.0,
        jurisdictions_linked=1,
        jurisdictions_minted=0,
        jurisdictions_total=1,
    )
    payload = json.loads(scorecard_to_json(sc))
    assert payload["all_gates_passed"] is True
    assert payload["total_entities"] == 1
    assert "### x" not in scorecard_to_json(sc)  # no raw text leaks into the scorecard


def test_committed_scorecard_artifact_is_consistent() -> None:
    """The committed real-corpus scorecard JSON must self-validate (gates pass)."""
    artifact = Path(__file__).resolve().parents[2] / "data" / "exports" / "locus" / "scorecard.json"
    if not artifact.exists():  # pragma: no cover - artifact optional in minimal checkouts
        return
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["all_gates_passed"] is True
    assert payload["dup_rate"] == 0.0
    assert payload["orphan_rate"] == 0.0
    assert payload["jurisdiction_precision"] >= 1.0
    assert payload["jurisdiction_recall"] >= 0.75
    # The rendered markdown of the same numbers must mention the gate table.
    sc = ConnectivityScorecard(
        total_entities=payload["total_entities"],
        by_entity_type=payload["by_entity_type"],
        by_tier=payload["by_tier"],
        unique_canonical_ids=payload["unique_canonical_ids"],
        dup_rate=payload["dup_rate"],
        orphan_rate=payload["orphan_rate"],
        cross_tier_links=payload["cross_tier_links"],
        jurisdiction_precision=payload["jurisdiction_precision"],
        jurisdiction_recall=payload["jurisdiction_recall"],
        jurisdictions_linked=payload["jurisdictions_linked"],
        jurisdictions_minted=payload["jurisdictions_minted"],
        jurisdictions_total=payload["jurisdictions_total"],
    )
    assert "All gates passed: True" in render_scorecard_markdown(sc)
