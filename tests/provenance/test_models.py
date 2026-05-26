from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.provenance.models import SourceArtifactMeta, StageEvent


def test_source_artifact_meta_rejects_non_hex_sha256() -> None:
    with pytest.raises(ValueError, match="sha256"):
        SourceArtifactMeta(
            data_source_slug="congress-api",
            artifact_kind="json",
            storage_uri="raw/congress-api/2026-01-01/bad/file.json",
            sha256="z" * 64,
        )


def test_stage_event_rejects_invalid_artifact_sha256() -> None:
    with pytest.raises(ValueError, match="artifact_sha256"):
        StageEvent(
            stage="fetch",
            status="succeeded",
            artifact_sha256="z" * 64,
            occurred_at=datetime.now(tz=timezone.utc),
        )


def test_stage_event_rejects_invalid_payload_hash() -> None:
    with pytest.raises(ValueError, match="payload_hash"):
        StageEvent(
            stage="parse",
            status="succeeded",
            artifact_sha256="a" * 64,
            payload_hash="not-a-sha256",
            occurred_at=datetime.now(tz=timezone.utc),
        )


def test_stage_log_append_and_final_status() -> None:
    from src.provenance.models import StageLog

    now = datetime.now(timezone.utc)
    log = StageLog(run_id=1)
    assert log.final_status is None  # empty log has no final status

    running = StageEvent(stage="fetch", status="running", artifact_sha256=None, occurred_at=now)
    done = StageEvent(stage="parse", status="succeeded", artifact_sha256=None, occurred_at=now)
    log.append(running)
    log.append(done)
    assert log.events == [running, done]
    assert log.final_status == "succeeded"  # status of the last appended event
