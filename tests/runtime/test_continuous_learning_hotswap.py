"""Tests for the live hot-swap continuous-learning runner."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.prediction.continuous_learning import DatedExample
from src.runtime.continuous_learning_hotswap import (
    CorpusFingerprint,
    plan_hotswap_tick,
)


def _corpus() -> list[DatedExample]:
    return [
        DatedExample(example=(f"M{i}", i % 2 == 0), vote_date=date(2025, 1, 1 + i))
        for i in range(20)
    ]


def test_fingerprint_reads_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"content_sha256": "abc", "record_count": 7918}))
    fp = CorpusFingerprint.read(manifest)
    assert fp.content_sha256 == "abc" and fp.record_count == 7918
    # missing manifest -> empty fingerprint, never raises
    assert CorpusFingerprint.read(tmp_path / "nope.json") == CorpusFingerprint("", 0)


def test_hot_swap_forces_full_retrain(tmp_path: Path) -> None:
    cdc = tmp_path / "deltas.jsonl"
    cdc.write_text("{}\n{}\n")
    fp = CorpusFingerprint("xyz", 9000)
    # Without a hot-swap, an in-cadence tick after a recent full retrain is incremental.
    no_swap = plan_hotswap_tick(
        _corpus(),
        cdc,
        fp,
        tick=1,
        as_of=date(2025, 1, 15),
        last_full_retrain=date(2025, 1, 10),
        last_incremental_at=date(2025, 1, 10),
        full_cadence_days=90,
        hot_swapped=False,
        corpus_source="house_118_rich.jsonl",
    )
    assert no_swap.kind == "incremental"
    # A hot-swap on the same inputs forces a full retrain and records the event.
    swapped = plan_hotswap_tick(
        _corpus(),
        cdc,
        fp,
        tick=1,
        as_of=date(2025, 1, 15),
        last_full_retrain=date(2025, 1, 10),
        last_incremental_at=date(2025, 1, 10),
        full_cadence_days=90,
        hot_swapped=True,
        corpus_source="house_5congress_sample.jsonl",
    )
    assert swapped.kind == "full"
    assert swapped.hot_swapped is True
    assert swapped.corpus_sha == "xyz"
    assert swapped.cdc_delta_count == 2


def test_tick_is_idempotent(tmp_path: Path) -> None:
    cdc = tmp_path / "deltas.jsonl"
    cdc.write_text("{}\n")
    fp = CorpusFingerprint("s", 1)
    kwargs = dict(
        tick=3,
        as_of=date(2025, 3, 1),
        last_full_retrain=date(2025, 1, 1),
        last_incremental_at=date(2025, 2, 1),
        full_cadence_days=90,
        hot_swapped=False,
        corpus_source="c.jsonl",
    )
    first = plan_hotswap_tick(_corpus(), cdc, fp, **kwargs)
    second = plan_hotswap_tick(_corpus(), cdc, fp, **kwargs)
    assert first == second
