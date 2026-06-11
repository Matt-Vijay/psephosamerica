"""Tests for the Track A dense-bill corpus watcher."""

from __future__ import annotations

import json
from pathlib import Path

from src.runtime.corpus_watch import (
    LinkageStatus,
    count_linked_bills,
    manifest_sha,
    should_trigger,
)


def _write_records(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def test_count_linked_bills_distinguishes_linkable(tmp_path: Path) -> None:
    records = tmp_path / "records.jsonl"
    _write_records(
        records,
        [
            {"entity_type": "person", "canonical_id": "p1"},
            # dense + canonical us_congress -> linkable
            {
                "entity_type": "bill",
                "canonical_id": "us_congress:118:hr1",
                "dossier_embedding": [0.1, 0.2],
                "dossier_json": {"policy_area": "Health"},
            },
            # dense but no join key -> not linkable
            {"entity_type": "bill", "canonical_id": "x:1", "dossier_embedding": [0.1]},
            # join key via external_ids but no embedding -> not linkable
            {"entity_type": "bill", "canonical_id": "x:2", "external_ids": ["us_congress:118:hr2"]},
        ],
    )
    status = count_linked_bills(records)
    assert status.total_bills == 3
    assert status.with_embedding == 2
    assert status.with_policy_area == 1
    assert status.vote_linkable == 1


def test_should_trigger_threshold() -> None:
    below = LinkageStatus(
        total_bills=10, with_embedding=10, with_policy_area=10, vote_linkable=4999
    )
    at = LinkageStatus(total_bills=10, with_embedding=10, with_policy_area=10, vote_linkable=5000)
    assert should_trigger(below) is False
    assert should_trigger(at) is True
    assert should_trigger(below, threshold=4000) is True


def test_manifest_sha_reads_and_tolerates_missing(tmp_path: Path) -> None:
    m = tmp_path / "manifest.json"
    m.write_text(json.dumps({"content_sha256": "deadbeef"}))
    assert manifest_sha(m) == "deadbeef"
    assert manifest_sha(tmp_path / "nope.json") == ""


def test_empty_records_is_zero(tmp_path: Path) -> None:
    assert count_linked_bills(tmp_path / "absent.jsonl") == LinkageStatus(0, 0, 0, 0)
