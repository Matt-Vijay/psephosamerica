from __future__ import annotations

from datetime import date
import json
from pathlib import Path

import pytest

import src.api as api
from src.export.writer import current_member_lookup_path
from src.export.writer import PlannedFile
from src.export.writer import serialize_payload
from src.export.writer import zip_entry_path
from src.api.contracts import NotFoundBody
from src.api.contracts import ZipEntryPayload
from src.api.read_service import get_zip_entry
from src.pipeline.history_aggregate_run import write_history_aggregate
from tests.support.published_snapshot_fixtures import make_snapshot, make_zip_feed
from src.export.filesystem import write_planned_files


def test_get_zip_entry_returns_zip_lookup_and_snapshot(tmp_path: Path) -> None:
    make_snapshot(tmp_path, zip_feeds=[make_zip_feed(zip_code="94102")])

    result = get_zip_entry("94102", snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.zip_feed.zip_code == "94102"
    assert [entry.slug for entry in result.data.member_lookup_entries] == ["nancy-pelosi"]
    assert result.data.snapshot.snapshot_id == "2026-01-01"
    assert result.data.snapshot.artifact_counts.zip_feeds == 1


def test_get_zip_entry_filters_lookup_to_zip_members(tmp_path: Path) -> None:
    make_snapshot(tmp_path, zip_feeds=[make_zip_feed(zip_code="94102")])

    result = get_zip_entry("94102", snapshot_root=tmp_path)

    assert result.ok is True
    assert {entry.bioguide_id for entry in result.data.member_lookup_entries} == {"P000197"}


def test_get_zip_entry_returns_not_found_for_missing_zip(tmp_path: Path) -> None:
    make_snapshot(tmp_path, zip_feeds=[make_zip_feed(zip_code="94102")])

    result = get_zip_entry("00000", snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "zip"
    assert result.identifier == "00000"


def test_get_zip_entry_degrades_when_lookup_sidecar_is_missing(tmp_path: Path) -> None:
    make_snapshot(tmp_path, zip_feeds=[make_zip_feed(zip_code="94102")])
    (tmp_path / current_member_lookup_path()).unlink()

    result = get_zip_entry("94102", snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.zip_feed.zip_code == "94102"
    assert result.data.member_lookup_entries == []


def test_get_zip_entry_prefers_precomputed_artifact_and_falls_back_cleanly(tmp_path: Path) -> None:
    make_snapshot(tmp_path, zip_feeds=[make_zip_feed(zip_code="94102")])

    fallback = get_zip_entry("94102", snapshot_root=tmp_path)
    assert fallback.ok is True

    artifact = ZipEntryPayload.model_validate(fallback.data.model_dump(mode="json"))
    artifact_path = tmp_path / zip_entry_path("94102")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(artifact.model_dump(mode="json")), encoding="utf-8")

    precomputed = get_zip_entry("94102", snapshot_root=tmp_path)

    assert precomputed.ok is True
    assert precomputed.data.model_dump(mode="json") == fallback.data.model_dump(mode="json")


def test_get_zip_entry_ignores_stale_precomputed_artifact(tmp_path: Path) -> None:
    make_snapshot(tmp_path, zip_feeds=[make_zip_feed(zip_code="94102")])

    fallback = get_zip_entry("94102", snapshot_root=tmp_path)
    assert fallback.ok is True

    stale = fallback.data.model_copy(
        update={
            "snapshot": fallback.data.snapshot.model_copy(
                update={"snapshot_id": "stale-snapshot", "root_sha256": "f" * 64}
            ),
            "member_lookup_entries": [],
        }
    )
    artifact_path = tmp_path / zip_entry_path("94102")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(stale.model_dump(mode="json")), encoding="utf-8")

    result = get_zip_entry("94102", snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.model_dump(mode="json") == fallback.data.model_dump(mode="json")


def test_get_zip_entry_serves_matching_precomputed_artifact_without_sidecars(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path, zip_feeds=[make_zip_feed(zip_code="94102")])

    fallback = get_zip_entry("94102", snapshot_root=tmp_path)
    assert fallback.ok is True

    artifact = ZipEntryPayload.model_validate(fallback.data.model_dump(mode="json"))
    artifact_path = tmp_path / zip_entry_path("94102")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(artifact.model_dump(mode="json")), encoding="utf-8")

    (tmp_path / "zip" / "94102.json").unlink()
    (tmp_path / current_member_lookup_path()).unlink()

    result = get_zip_entry("94102", snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.model_dump(mode="json") == fallback.data.model_dump(mode="json")


def test_src_api_exports_zip_entry_helper() -> None:
    assert api.get_zip_entry is get_zip_entry


def test_get_zip_entry_history_aggregate_root_serves_copied_artifact_without_sidecars(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "2026-01-06"
    second_root = tmp_path / "2026-01-13"
    aggregate_root = tmp_path / "aggregate"
    first_date = date(2026, 1, 6)
    second_date = date(2026, 1, 13)
    make_snapshot(
        first_root,
        snapshot_id="2026-01-06",
        zip_feeds=[make_zip_feed(zip_code="94102", snapshot_date=first_date)],
    )
    make_snapshot(
        second_root,
        snapshot_id="2026-01-13",
        zip_feeds=[make_zip_feed(zip_code="94102", snapshot_date=second_date)],
    )

    zip_entry = get_zip_entry("94102", snapshot_root=second_root)
    assert not isinstance(zip_entry, dict)
    assert getattr(zip_entry, "ok", False) is True
    write_planned_files(
        [
            PlannedFile.from_bytes(
                zip_entry_path("94102"),
                serialize_payload(zip_entry.data),
            )
        ],
        second_root,
    )

    write_history_aggregate([first_root, second_root], aggregate_root)
    (aggregate_root / "zip" / "94102.json").unlink()
    (aggregate_root / current_member_lookup_path()).unlink()

    result = get_zip_entry("94102", snapshot_root=aggregate_root)

    assert result.ok is True
    assert result.data.zip_feed.zip_code == "94102"
    assert result.data.snapshot.snapshot_id == "2026-01-13"
