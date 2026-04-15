"""Full oracle smoke: process disclosures → recompute → publish.

smoke_oracle_path chains the three runtime stages and returns a compact
structured summary keyed by stage.  No CLI, no network, no subprocesses.
The caller owns the DB connection.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from src.pipeline.publish_snapshot_run import ZipBundleInputs
from src.runtime.publish import run_publish_runtime
from src.runtime.recompute import run_recompute_runtime
from src.runtime.smoke_process_disclosures import smoke_process_disclosures


def smoke_oracle_path(
    conn: Any,
    local_root: Path,
    snapshot_date: dt.date,
    target_dir: Path,
    zip_bundle_inputs: ZipBundleInputs,
    *,
    chamber: str | None = None,
    limit: int | None = None,
    parser_name: str = "text_extract_v1",
    parser_version: str = "1",
    taxonomy: Any | None = None,
    issuer_sector_resolver: Any | None = None,
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    """Run the full oracle path and return a compact three-stage summary.

    Stages:
        1. process disclosures  (smoke_process_disclosures)
        2. recompute            (run_recompute_runtime)
        3. publish              (run_publish_runtime)

    Returns:
        {
            "disclosures": {...},   # keys from smoke_process_disclosures
            "recompute":   {...},   # run_id, source_slug, rule_fires, evidence_cards
            "publish":     {...},   # run_id, snapshot_id, source_slug, written_count, succeeded
        }
    """
    disclosures_summary = smoke_process_disclosures(
        conn,
        local_root,
        chamber=chamber,
        limit=limit,
        parser_name=parser_name,
        parser_version=parser_version,
    )

    recompute_result = run_recompute_runtime(
        conn,
        snapshot_date,
        taxonomy=taxonomy,
        issuer_sector_resolver=issuer_sector_resolver,
    )
    recompute_summary: dict[str, Any] = {
        "run_id": recompute_result.run_id,
        "source_slug": recompute_result.data_source.get("slug"),
        "rule_fires": len(recompute_result.recompute_result.rule_fires),
        "evidence_cards": len(recompute_result.recompute_result.evidence_cards),
    }

    publish_result = run_publish_runtime(
        conn,
        snapshot_date,
        target_dir,
        zip_bundle_inputs,
        snapshot_id=snapshot_id,
    )
    publish_summary: dict[str, Any] = {
        "run_id": publish_result.run_id,
        "snapshot_id": publish_result.snapshot_id,
        "source_slug": publish_result.data_source.get("slug"),
        "written_count": publish_result.publish_result.written_count,
        "succeeded": publish_result.publish_result.succeeded,
    }

    return {
        "disclosures": disclosures_summary,
        "recompute": recompute_summary,
        "publish": publish_summary,
    }
