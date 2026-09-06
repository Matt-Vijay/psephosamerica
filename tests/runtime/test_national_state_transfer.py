"""Unit tests for the national federal->state zero-shot transfer harness.

Exercises the streaming roll-call grouping + per-jurisdiction transfer eval on
small synthetic fixtures (no 22GB file), pinning the data contract: edges keyed by
``external_key`` group into roll-calls, are sharded by the voter's region, party
labels join through ``ce-`` ids, and unlabeled voters are dropped (never invented).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.prediction.defection import build_party_profiles
from src.prediction.defection_head import train_defection_head
from src.runtime.national_state_transfer import (
    build_person_index,
    evaluate_jurisdiction,
    run,
    stream_state_rollcalls,
)


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _people_fixture(tmp_path: Path) -> tuple[Path, tuple[Path, ...]]:
    people = tmp_path / "raw_people.jsonl"
    canonical = tmp_path / "state_legislators.jsonl"
    members = [(f"d{i}", "Democratic") for i in range(4)] + [
        (f"r{i}", "Republican") for i in range(4)
    ]
    _write(
        people,
        [
            {
                "party": party,
                "region": "ZZ",
                "external_ids": [{"system": "openstates", "value": f"ocd-person/{mid}"}],
            }
            for mid, party in members
        ],
    )
    _write(
        canonical,
        [
            {
                "entity_type": "person",
                "canonical_id": f"ce-{mid}",
                "external_ids": [f"openstates:ocd-person/{mid}"],
            }
            for mid, _ in members
        ]
        # No party label -> must be excluded from the index.
        + [
            {
                "entity_type": "person",
                "canonical_id": "ce-x9",
                "external_ids": ["openstates:ocd-person/x9"],
            }
        ],
    )
    return people, (canonical,)


def _edge(vote_id: str, member: str, choice: str, day: str) -> dict[str, Any]:
    return {
        "edge_type": "vote",
        "src_id": member,
        "dst_id": "cb-bill",
        "attributes": {"choice": choice},
        "external_key": f"ocd-vote/{vote_id}",
        "provenance": {"valid_from": day, "known_at": f"{day}T00:00:00Z"},
    }


def test_build_person_index_drops_unlabeled(tmp_path: Path) -> None:
    people, canonical = _people_fixture(tmp_path)
    index = build_person_index(people, canonical)
    assert index["ce-d0"] == ("Democratic", "ZZ")
    assert index["ce-r0"] == ("Republican", "ZZ")
    assert len(index) == 8
    assert "ce-x9" not in index  # no party label -> never invented


def test_stream_groups_by_rollcall_and_region(tmp_path: Path) -> None:
    people, canonical = _people_fixture(tmp_path)
    index = build_person_index(people, canonical)
    members = [f"ce-d{i}" for i in range(4)] + [f"ce-r{i}" for i in range(4)]
    edges = tmp_path / "edges.jsonl"
    # Two roll-calls, edges interleaved (not contiguous) + an unlabeled voter.
    rows = []
    for m in members:
        rows.append(_edge("v1", m, "yea", "2024-01-01"))
    for m in members:
        rows.append(_edge("v2", m, "nay", "2024-02-01"))
    rows.append(_edge("v1", "ce-x9", "yea", "2024-01-01"))  # unlabeled -> dropped
    # Shuffle interleave: alternate the two roll-calls.
    interleaved = [r for pair in zip(rows[:8], rows[8:16], strict=False) for r in pair] + rows[16:]
    _write(edges, interleaved)
    by_state, stats = stream_state_rollcalls(
        edges, index, min_rollcall_votes=5, max_rollcalls_per_state=10
    )
    assert set(by_state) == {"ZZ"}
    rolls = by_state["ZZ"]
    assert len(rolls) == 2  # v1 and v2, regrouped despite interleaving
    assert rolls[0]["date"] <= rolls[1]["date"]  # date-sorted
    for rc in rolls:
        assert len(rc["votes"]) == 8  # unlabeled excluded
    assert stats["__global__"]["total_edges"] == 17
    assert stats["__global__"]["labeled_edges"] == 16
    assert stats["ZZ"]["rollcalls"] == 2


def test_evaluate_jurisdiction_degenerate_split() -> None:
    # A single date cannot form a temporal train/eval split (source args unused here).
    rolls = [
        {
            "date": "2024-01-01",
            "sectors": [],
            "votes": [["ce-d0", "Democratic", "ZZ", "yea"], ["ce-r0", "Republican", "ZZ", "nay"]],
        }
    ]
    profiles = build_party_profiles([])
    head = train_defection_head([], profiles)
    out = evaluate_jurisdiction(
        "ZZ", rolls, [], profiles, head, {"labeled_edges": 2, "rollcalls": 1}
    )
    assert out["status"] == "single_date"


def test_run_smoke_end_to_end(tmp_path: Path) -> None:
    people, canonical = _people_fixture(tmp_path)
    # Federal source corpus in the rich roll-call shape.
    federal = tmp_path / "federal.jsonl"
    fed_rolls = []
    for i in range(40):
        day = f"2022-{(i % 12) + 1:02d}-01"
        votes = [["A", "D", "CA", "yea" if i % 2 else "nay"], ["B", "R", "TX", "nay"]]
        fed_rolls.append({"bill_id": f"f{i}", "date": day, "sectors": [], "votes": votes})
    _write(federal, fed_rolls)
    # State edges spanning enough dates for a split + some defections.
    edges = tmp_path / "edges.jsonl"
    dems = [f"ce-d{i}" for i in range(4)]
    reps = [f"ce-r{i}" for i in range(4)]
    rows = []
    for i in range(60):
        day = f"2023-{(i % 12) + 1:02d}-{(i % 27) + 1:02d}"
        # Mostly party-line; inject occasional defections so eval has positives.
        for j, m in enumerate(dems):
            choice = "nay" if (i + j) % 7 == 0 else "yea"
            rows.append(_edge(f"s{i}", m, choice, day))
        for j, m in enumerate(reps):
            choice = "yea" if (i + j) % 5 == 0 else "nay"
            rows.append(_edge(f"s{i}", m, choice, day))
    _write(edges, rows)
    result = run(
        edges,
        people_path=people,
        canonical_paths=canonical,
        federal_corpus=federal,
        max_rollcalls_per_state=100,
    )
    assert result["jurisdictions_seen"] >= 1
    assert 0.0 <= result["party_label_coverage"] <= 1.0
    assert result["total_edges_streamed"] == 60 * 8
    zz = [s for s in result["per_state"] if s["region"] == "ZZ"]
    assert zz, "ZZ jurisdiction should appear"
