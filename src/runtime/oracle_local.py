"""Local oracle runtime orchestrator.

One public entry point:

    run_oracle_local(conn, congress_archive, disclosures_bundle, options)

Flow (linear, explicit):
  1. Local Congress load      — run_congress_archive_load
                                 (members / committees / bills from a local
                                 archive; vote loading stays off here)
  2. Local disclosure process — run_disclosures_bundle_process
                                 (process a prebuilt local bundle only)
  3. Recompute                — run_recompute_runtime
  4. Publish                  — run_publish_runtime
                                 (uses an empty ZIP bundle; no ZIP feeds)
  5. Verify                   — _run_verify (verify_local_publish)
  6. Roundtrip                — _run_roundtrip (verify_publish_roundtrip)

All I/O comes from the caller-supplied archive path and bundle.
No network calls are made by this module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.pipeline.publish_snapshot_run import ZipBundleInputs
from src.runtime.congress_archive import run_congress_archive_load
from src.runtime.congress_options import CongressLoadOptions
from src.runtime.disclosures_bundle_process import (
    DisclosuresBundleProcessResult,
    run_disclosures_bundle_process,
)
from src.runtime.oracle_contracts import (
    CongressStageSummary,
    LocalOracleOptions,
    LocalOracleRunResult,
)
from src.runtime.publish import PublishRuntimeResult, run_publish_runtime
from src.runtime.publish_roundtrip import verify_publish_roundtrip
from src.runtime.publish_roundtrip_types import PublishRoundtripResult
from src.runtime.publish_verify import verify_local_publish
from src.runtime.publish_verify_types import PublishVerifyResult
from src.runtime.recompute import RuntimeRecomputeResult, run_recompute_runtime


def _required_source_slug(data_source: dict[str, Any]) -> str:
    slug = data_source.get("slug")
    if not isinstance(slug, str) or not slug:
        raise ValueError("runtime result is missing a data_source slug")
    return slug


# ---------------------------------------------------------------------------
def _run_verify(target_dir: Path) -> PublishVerifyResult:
    """Thin wrapper around verify_local_publish for test patching."""
    return verify_local_publish(target_dir)


# ---------------------------------------------------------------------------
def _run_roundtrip(conn: Any, target_dir: Path) -> PublishRoundtripResult:
    """Thin wrapper around verify_publish_roundtrip for test patching."""
    return verify_publish_roundtrip(conn, target_dir)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_oracle_local(
    conn: Any,
    congress_archive: Path,
    disclosures_bundle: Any,
    options: LocalOracleOptions,
) -> LocalOracleRunResult:
    """Run the full local oracle pipeline and return a typed result.

    Steps:
      1. Local Congress load — read members, committees, bills, sponsors,
         and cosponsors from congress_archive; load into the canonical DB.
         Local-oracle runs do not request vote records on this surface.
      2. Local disclosure process — stage, parse, transform, and load
         disclosures from disclosures_bundle. The returned summary reports the
         requested chamber / limit so the processed scope is explicit.
      3. Recompute — fire conflict-of-interest rules; produce evidence cards.
      4. Publish — write a dated snapshot to options.target_dir using an
         empty zip bundle (local oracle runs do not populate ZIP feeds).
      5. Verify — check the published tree for manifest integrity, missing
         files, and structural coherence.  Failures are surfaced in the
         returned verify field; no exception is raised for verify failures.
      6. Roundtrip — confirm published artifacts match the canonical DB
         across snapshot, profiles, evidence, zip, and homepage stages.
         Failures are surfaced in the returned roundtrip field.

    Args:
        conn:               Open psycopg connection (or compatible mock).
        congress_archive:   Path to a local Congress archive directory.
        disclosures_bundle: Pre-fetched DisclosureBundle for local processing.
        options:            Typed run options: snapshot_date, target_dir,
                            congress_options, artifact_root, snapshot_id.

    Returns:
        Frozen LocalOracleRunResult with the resolved snapshot_id,
        per-stage summary dicts for disclosures, recompute, and publish,
        a typed PublishVerifyResult for the verify stage, and a typed
        PublishRoundtripResult for the roundtrip stage.
    """
    # 1. Local Congress load
    congress_load_options = CongressLoadOptions(
        congress=options.congress_options.congress,
        include_votes=False,
    )
    congress_load_result = run_congress_archive_load(conn, congress_archive, congress_load_options)
    congress_summary = CongressStageSummary(
        run_id=congress_load_result.run_id,
        source_slug=_required_source_slug(congress_load_result.data_source),
        total_inserted=congress_load_result.load_summary.total_inserted,
        total_written=congress_load_result.load_summary.total_written,
        load_ok=congress_load_result.load_summary.ok,
        configured_congress=options.congress_options.congress,
        congress_source=options.congress_options.congress_source,
        include_votes=False,
    )

    # 2. Local disclosure process
    disclosures_result: DisclosuresBundleProcessResult = run_disclosures_bundle_process(
        conn,
        disclosures_bundle,
        local_root=options.artifact_root,
    )

    # 3. Recompute
    recompute_result: RuntimeRecomputeResult = run_recompute_runtime(
        conn,
        options.snapshot_date,
    )

    # 4. Publish — empty zip bundle: local oracle runs do not generate ZIP feeds
    zip_inputs = ZipBundleInputs(
        zip5_codes=[],
        zip_district_rows=[],
        district_member_rows=[],
        senator_rows=[],
    )
    publish_result: PublishRuntimeResult = run_publish_runtime(
        conn,
        options.snapshot_date,
        options.target_dir,
        zip_inputs,
        snapshot_id=options.snapshot_id,
    )

    # 5. Verify — run after publish; failures are included in the result, not raised
    verify_result: PublishVerifyResult = _run_verify(options.target_dir)

    # 6. Roundtrip — DB→publish check; failures are included in the result, not raised
    roundtrip_result: PublishRoundtripResult = _run_roundtrip(conn, options.target_dir)

    load_summary = disclosures_result.load_result.load_summary

    return LocalOracleRunResult(
        snapshot_id=options.resolved_snapshot_id(),
        congress=congress_summary,
        disclosures={
            "run_id": disclosures_result.load_result.run_id,
            "source_slug": disclosures_result.load_result.data_source.get("slug"),
            "requested_chamber": options.congress_options.chamber or "both",
            "artifact_limit": options.congress_options.limit,
            "parse_succeeded": disclosures_result.parse_result.succeeded_count,
            "parse_failed": disclosures_result.parse_result.failed_count,
            "transform_count": disclosures_result.transform_count,
            "total_written": load_summary.total_written,
            "load_ok": load_summary.ok,
        },
        recompute={
            "run_id": recompute_result.run_id,
            "source_slug": recompute_result.data_source.get("slug"),
            "rule_fires": len(recompute_result.recompute_result.rule_fires),
            "evidence_cards": len(recompute_result.recompute_result.evidence_cards),
        },
        publish={
            "run_id": publish_result.run_id,
            "snapshot_id": publish_result.snapshot_id,
            "source_slug": publish_result.data_source.get("slug"),
            "written_count": publish_result.publish_result.written_count,
            "succeeded": publish_result.publish_result.succeeded,
            "zip_feeds_generated": False,
        },
        verify=verify_result,
        roundtrip=roundtrip_result,
    )
