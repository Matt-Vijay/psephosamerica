"""Tests for the bill-content (dense bill embedding) defection experiment."""

from __future__ import annotations

from datetime import date

import numpy as np

from src.runtime.bill_content_experiment import (
    build_linked_votes,
    load_bill_embedding_map,
    normalize_bill_key,
    run_bill_content_experiment,
    synthetic_bill_embedding,
)


def test_normalize_bill_key_joins_all_formats() -> None:
    # roll-call slug, contract external id, and govinfo id all name the same bill
    assert (
        normalize_bill_key("us_congress:113:h-r-529")
        == normalize_bill_key("congress:113-hr-529")
        == normalize_bill_key("govinfo:billstatus-113hr529")
        == "113:hr:529"
    )
    assert normalize_bill_key("us_congress:118:h-res-5") == "118:hres:5"
    # procedural rows have no real bill
    assert normalize_bill_key("us_congress:118:quorum") is None
    assert normalize_bill_key("us_congress:118:adjourn") is None
    assert normalize_bill_key("") is None


def _rollcalls() -> list[dict]:
    rolls = []
    # defector D breaks on hr-energy bills, loyal on hr-health; two loyalists never break
    for i in range(40):
        kind = "energy" if i % 2 == 0 else "health"
        rolls.append(
            {
                "bill_id": f"us_congress:118:hr{kind}{i}",
                "date": f"2023-{1 + i % 12:02d}-15",
                "congress": 118,
                "sectors": [kind],
                "votes": [
                    ["D", "R", "TX", "yea" if kind == "energy" else "nay"],
                    ["L1", "R", "TX", "nay"],
                    ["L2", "R", "TX", "nay"],
                ],
            }
        )
    return rolls


def test_synthetic_embedding_is_deterministic_and_per_bill() -> None:
    a = synthetic_bill_embedding("us_congress:118:hr1")
    b = synthetic_bill_embedding("us_congress:118:hr1")
    c = synthetic_bill_embedding("us_congress:118:hr2")
    assert np.allclose(a, b)
    assert not np.allclose(a, c)


def test_build_linked_votes_carries_embedding() -> None:
    linked = build_linked_votes(_rollcalls(), None, synthetic=True)
    assert linked
    assert all(lv.bill_embedding is not None for lv in linked)
    assert all(lv.bill_id.startswith("us_congress:") for lv in linked)


def test_experiment_runs_and_reports_ablation() -> None:
    report = run_bill_content_experiment(
        _rollcalls(),
        None,
        cutoff=date(2023, 8, 1),
        eval_end=date(2023, 12, 31),
        k_values=(4, 8),
        synthetic=True,
    )
    assert report["embedding_source"] == "synthetic"
    assert report["vote_linked_bills"] > 0
    assert set(report["k_ablation"]) == {"k=4", "k=8"}
    assert "beats_pin" in report and isinstance(report["beats_pin"], bool)


def test_experiment_with_projection_reports_both_variants() -> None:
    report = run_bill_content_experiment(
        _rollcalls(),
        None,
        cutoff=date(2023, 8, 1),
        eval_end=date(2023, 12, 31),
        k_values=(4, 8),
        synthetic=True,
        projection_dim=4,
    )
    assert report["projection_dim"] == 4
    assert report["best_variant"] in {"base", "rag", "rag_proj"}
    # projection variant is reported alongside rag for each k
    assert "rag_proj_auc" in report["k_ablation"]["k=4"]


def test_load_bill_embedding_map_empty_when_no_dense(tmp_path) -> None:
    records = tmp_path / "records.jsonl"
    records.write_text('{"entity_type":"bill","canonical_id":"us_congress:118:hr1"}\n')
    assert load_bill_embedding_map(records) == {}
