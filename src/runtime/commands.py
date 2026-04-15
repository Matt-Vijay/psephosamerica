"""One function per operator action.

Each command opens a connection from the RuntimeContext and delegates to
the appropriate runtime orchestrator.  No provenance logic lives here —
that is the responsibility of each runtime module.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from src.parse.disclosures.transform import DisclosureTransformResult
from src.pipeline.publish_snapshot_run import ZipBundleInputs
from src.runtime.congress import CongressLoadResult
from src.runtime.congress_archive import run_congress_archive_load
from src.runtime.congress_live_full import run_live_congress_load_full
from src.runtime.congress_options import CongressLoadOptions
from src.runtime.context import RuntimeContext, open_connection
from src.runtime.disclosures import DisclosuresLoadRuntimeResult, run_disclosures_load_runtime
from src.runtime.disclosures_bundle import DisclosuresBundle
from src.runtime.disclosures_bundle_process import (
    DisclosuresBundleProcessResult,
    run_disclosures_bundle_process,
)
from src.runtime.oracle_contracts import LocalOracleOptions, LocalOracleRunResult
from src.runtime.oracle_local import run_oracle_local
from src.runtime.publish import PublishRuntimeResult, run_publish_runtime
from src.runtime.publish_roundtrip import verify_roundtrip as _verify_roundtrip
from src.runtime.publish_roundtrip_types import PublishRoundtripResult
from src.runtime.publish_verify import verify_local_publish as _verify_local_publish
from src.runtime.publish_verify_types import PublishVerifyResult
from src.runtime.recompute import RuntimeRecomputeResult, run_recompute_runtime


def load_congress(
    ctx: RuntimeContext,
    options: CongressLoadOptions,
) -> CongressLoadResult:
    """Open a connection and run a live Congress load."""
    conn = open_connection(ctx)
    return run_live_congress_load_full(
        conn,
        ctx.settings,
        congress=options.congress,
        include_votes=options.include_votes,
        house_vote_year=options.house_vote_year,
        senate_session=options.senate_session,
    )


def load_congress_local(
    ctx: RuntimeContext,
    archive: Path,
    options: CongressLoadOptions,
) -> CongressLoadResult:
    """Open a connection and run a Congress load from a local archive bundle."""
    conn = open_connection(ctx)
    return run_congress_archive_load(conn, archive, options)


def load_disclosures(
    ctx: RuntimeContext,
    results: list[DisclosureTransformResult],
) -> DisclosuresLoadRuntimeResult:
    """Open a connection and run the disclosures load."""
    conn = open_connection(ctx)
    return run_disclosures_load_runtime(conn, results)


def process_disclosures_local(
    ctx: RuntimeContext,
    bundle: DisclosuresBundle,
    *,
    local_root: Path | None = None,
) -> DisclosuresBundleProcessResult:
    """Open a connection and run the bundle-process disclosure pipeline."""
    conn = open_connection(ctx)
    return run_disclosures_bundle_process(conn, bundle, local_root=local_root)


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


def verify_publish_local(publish_root: Path) -> PublishVerifyResult:
    """Verify a local publish tree without a database connection.

    This is a pure filesystem check; no RuntimeContext or DB connection is
    required.  Delegates to the four-stage verifier in publish_verify.

    Args:
        publish_root: Root directory of the published snapshot tree.

    Returns:
        A :class:`PublishVerifyResult` summarising all stage outcomes.
        Check ``.ok`` to determine whether the publish tree is sound.
    """
    return _verify_local_publish(publish_root)


def verify_publish_roundtrip_local(
    ctx: RuntimeContext,
    publish_root: Path,
) -> PublishRoundtripResult:
    """Verify a local publish tree using the DB as the source of truth.

    Opens a DB connection from *ctx* and runs the five-stage roundtrip
    verifier, cross-checking what is in the database against what is present
    on disk at *publish_root*.

    Unlike :func:`verify_publish_local` this function requires a live DB
    connection; it is the DB-backed complement to the pure-filesystem check.

    Args:
        ctx:          Runtime context providing DB connection settings.
        publish_root: Root directory of the published snapshot tree.

    Returns:
        A :class:`PublishRoundtripResult` summarising all stage outcomes.
        Inspect ``.ok`` to determine whether the publish tree is sound.
    """
    conn = open_connection(ctx)
    return _verify_roundtrip(conn, publish_root)


def run_oracle_local_command(
    ctx: RuntimeContext,
    congress_archive: Path,
    disclosures_bundle: DisclosuresBundle,
    options: LocalOracleOptions,
) -> LocalOracleRunResult:
    """Open a connection and run the full local oracle pipeline."""
    conn = open_connection(ctx)
    return run_oracle_local(conn, congress_archive, disclosures_bundle, options)
