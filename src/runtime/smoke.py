"""Smoke helpers for quick local confidence checks against the runtime boundary.

smoke_recompute and smoke_publish exercise the main runtime entrypoints and
return compact summary dicts.  Callers supply an explicit DB connection so the
connection lifecycle stays outside this module.

No subprocesses, no CLI, no network.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from src.pipeline.publish_snapshot_run import ZipBundleInputs
from src.runtime.publish import run_publish_runtime
from src.runtime.recompute import run_recompute_runtime


def smoke_recompute(
    conn: Any,
    snapshot_date: dt.date,
    *,
    taxonomy: Any = None,
    issuer_sector_resolver: Any = None,
) -> dict[str, Any]:
    """Run a full recompute and return a compact summary dict.

    Keys: run_id, source_slug, rule_fires, evidence_cards.
    """
    result = run_recompute_runtime(
        conn,
        snapshot_date,
        taxonomy=taxonomy,
        issuer_sector_resolver=issuer_sector_resolver,
    )
    return {
        "run_id": result.run_id,
        "source_slug": result.data_source.get("slug"),
        "rule_fires": len(result.recompute_result.rule_fires),
        "evidence_cards": len(result.recompute_result.evidence_cards),
    }


def smoke_publish(
    conn: Any,
    snapshot_date: dt.date,
    target_dir: Path,
    zip_bundle_inputs: ZipBundleInputs,
    *,
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    """Run a snapshot publish and return a compact summary dict.

    Keys: run_id, snapshot_id, source_slug, written_count, succeeded.
    """
    result = run_publish_runtime(
        conn,
        snapshot_date,
        target_dir,
        zip_bundle_inputs,
        snapshot_id=snapshot_id,
    )
    return {
        "run_id": result.run_id,
        "snapshot_id": result.snapshot_id,
        "source_slug": result.data_source.get("slug"),
        "written_count": result.publish_result.written_count,
        "succeeded": result.publish_result.succeeded,
    }
