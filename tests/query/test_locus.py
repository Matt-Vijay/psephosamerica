"""Tests for the LOCUS ordinance analyses (cross-jurisdiction flagship)."""

from __future__ import annotations

import json
from pathlib import Path

from src.query.locus import (
    LOCUS_LICENSE,
    iter_ordinances,
    near_duplicate_ordinances,
    opacity_paternalism_map,
    topic_diffusion,
)


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _row(
    cid: str, juris: str, text: str, *, topic: str | None = "Zoning", dims: dict | None = None
) -> dict:
    return {
        "canonical_id": cid,
        "jurisdiction_id": juris,
        "topic": topic,
        "function": "Rules",
        "text": text,
        "dimension_scores": dims
        or {
            "opacity": 1.0,
            "paternalism": 2.0,
            "enforcement_discretion": 0.5,
            "problem_salience": 1.5,
        },
    }


def test_iter_ordinances_parses_dimensions_from_dict_and_str(tmp_path: Path) -> None:
    p = tmp_path / "c.jsonl"
    _write(
        p,
        [
            _row(
                "cb-1", "us-ca-city-oakland", "a b c d e", dims={"opacity": 3.0, "paternalism": 1.0}
            ),
            {
                "canonical_id": "cb-2",
                "jurisdiction_id": "us-ca-city-berkeley",
                "topic": "Nuisance",
                "function": "Rules",
                "text": "x y z",
                "dimension_scores": "{'opacity': 2.5, 'paternalism': 0.5}",  # stringified dict
            },
        ],
    )
    ords = list(iter_ordinances(p))
    assert len(ords) == 2
    assert ords[0].dimensions["opacity"] == 3.0
    assert ords[1].dimensions["opacity"] == 2.5  # parsed from the stringified form
    assert ords[0].citation["license"] == LOCUS_LICENSE


def test_iter_ordinances_missing_file_yields_nothing(tmp_path: Path) -> None:
    assert list(iter_ordinances(tmp_path / "nope.jsonl")) == []


def test_opacity_paternalism_map_ranks_by_dimension(tmp_path: Path) -> None:
    p = tmp_path / "c.jsonl"
    rows = []
    # oakland: high opacity; berkeley: low opacity
    for i in range(3):
        rows.append(
            _row(
                f"o{i}",
                "us-ca-city-oakland",
                f"text {i}",
                dims={
                    "opacity": 5.0,
                    "paternalism": 1.0,
                    "enforcement_discretion": 0,
                    "problem_salience": 0,
                },
            )
        )
        rows.append(
            _row(
                f"b{i}",
                "us-ca-city-berkeley",
                f"text {i}",
                dims={
                    "opacity": 1.0,
                    "paternalism": 1.0,
                    "enforcement_discretion": 0,
                    "problem_salience": 0,
                },
            )
        )
    _write(p, rows)
    ranking = opacity_paternalism_map(p, min_ordinances=3, rank_by="opacity")
    assert ranking[0].jurisdiction_id == "us-ca-city-oakland"
    assert ranking[0].means["opacity"] == 5.0
    assert ranking[0].ordinance_count == 3


def test_opacity_map_respects_min_ordinances(tmp_path: Path) -> None:
    p = tmp_path / "c.jsonl"
    _write(p, [_row("o0", "us-ca-city-tiny", "text")])
    assert opacity_paternalism_map(p, min_ordinances=5) == []


def test_near_duplicate_cross_jurisdiction(tmp_path: Path) -> None:
    p = tmp_path / "c.jsonl"
    shared = (
        "no person shall keep maintain or harbor within the city any animal "
        "that by loud or unusual noise disturbs the peace and quiet of any neighborhood"
    )
    rows = [
        _row("cb-oak", "us-ca-city-oakland", shared),
        _row("cb-sj", "us-ca-city-san_jose", shared),  # same model text, different city
        _row(
            "cb-other",
            "us-tx-city-austin",
            "a completely different ordinance about parking meters downtown only",
        ),
    ]
    _write(p, rows)
    dups = near_duplicate_ordinances(p, threshold=0.6, min_tokens=5)
    assert len(dups) == 1
    pair = dups[0]
    assert pair.cross_jurisdiction is True
    assert {pair.a["jurisdiction_id"], pair.b["jurisdiction_id"]} == {
        "us-ca-city-oakland",
        "us-ca-city-san_jose",
    }
    assert pair.jaccard >= 0.6
    assert pair.a["citation"]["license"] == LOCUS_LICENSE


def test_near_duplicate_can_include_same_jurisdiction(tmp_path: Path) -> None:
    p = tmp_path / "c.jsonl"
    shared = "the speed limit on all residential streets within the city shall be twenty five miles per hour"
    _write(
        p,
        [
            _row("cb-1", "us-ca-city-oakland", shared),
            _row("cb-2", "us-ca-city-oakland", shared),
        ],
    )
    assert (
        near_duplicate_ordinances(p, threshold=0.6, cross_jurisdiction_only=True, min_tokens=5)
        == []
    )
    same = near_duplicate_ordinances(p, threshold=0.6, cross_jurisdiction_only=False, min_tokens=5)
    assert len(same) == 1
    assert same[0].cross_jurisdiction is False


def test_topic_diffusion(tmp_path: Path) -> None:
    p = tmp_path / "c.jsonl"
    rows = [
        _row("z1", "us-ca-city-oakland", "t", topic="Zoning"),
        _row("z2", "us-tx-city-austin", "t", topic="Zoning"),
        _row("z3", "us-ny-city-buffalo", "t", topic="Zoning"),
        _row("n1", "us-ca-city-oakland", "t", topic="Nuisance"),
    ]
    _write(p, rows)
    diffusion = topic_diffusion(p)
    assert diffusion[0].topic == "Zoning"
    assert diffusion[0].jurisdiction_count == 3
    assert diffusion[0].ordinance_count == 3
