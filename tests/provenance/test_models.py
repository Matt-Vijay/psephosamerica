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
