"""One function per operator action.

Each command opens a connection from the RuntimeContext and delegates to
the appropriate runtime orchestrator.  No provenance logic lives here —
that is the responsibility of each runtime module.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from src.parse.disclosures.transform import DisclosureTransformResult
from src.pipeline.congress_load_run import CongressIngestInputs
from src.pipeline.publish_snapshot_run import ZipBundleInputs
from src.runtime.congress import CongressLoadResult, run_congress_load_runtime
from src.runtime.context import RuntimeContext, open_connection
from src.runtime.disclosures import DisclosuresLoadRuntimeResult, run_disclosures_load_runtime
from src.runtime.publish import PublishRuntimeResult, run_publish_runtime
from src.runtime.recompute import RuntimeRecomputeResult, run_recompute_runtime


def load_congress(
    ctx: RuntimeContext,
    inputs: CongressIngestInputs,
) -> CongressLoadResult:
    """Open a connection and run the Congress canonical load."""
    conn = open_connection(ctx)
    return run_congress_load_runtime(conn, inputs)


def load_disclosures(
    ctx: RuntimeContext,
    results: list[DisclosureTransformResult],
) -> DisclosuresLoadRuntimeResult:
    """Open a connection and run the disclosures load."""
    conn = open_connection(ctx)
    return run_disclosures_load_runtime(conn, results)


def recompute_snapshot(
    ctx: RuntimeContext,
    snapshot_date: dt.date,
) -> RuntimeRecomputeResult:
    """Open a connection and run a full conflict-of-interest recompute."""
    conn = open_connection(ctx)
    return run_recompute_runtime(
        conn,
        snapshot_date,
        taxonomy=ctx.taxonomy,
        issuer_sector_resolver=ctx.issuer_sector_resolver,
    )


def publish_snapshot(
    ctx: RuntimeContext,
    snapshot_date: dt.date,
    target_dir: Path,
    zip_bundle_inputs: ZipBundleInputs,
) -> PublishRuntimeResult:
    """Open a connection and publish an immutable snapshot."""
    conn = open_connection(ctx)
    return run_publish_runtime(conn, snapshot_date, target_dir, zip_bundle_inputs)
