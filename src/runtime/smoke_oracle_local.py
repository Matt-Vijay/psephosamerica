"""Compact local oracle smoke helper.

smoke_oracle_local drives run_oracle_local and returns a compact structured
summary suitable for operator confidence checks.  The summary shape is
produced by summarize_local_oracle_run_result and is identical to the
run-oracle-local CLI output, so operators see the same keys in both paths.

No CLI, no network, no subprocesses.  The caller owns the DB connection.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.runtime.oracle_contracts import LocalOracleOptions
from src.runtime.oracle_local import run_oracle_local
from src.runtime.output import summarize_local_oracle_run_result


def smoke_oracle_local(
    conn: Any,
    congress_archive: Path,
    disclosures_bundle: Any,
    options: LocalOracleOptions,
) -> dict[str, Any]:
    """Run the local oracle path and return a compact operator summary.

    Keys returned:
        snapshot_id  — effective snapshot identifier for this run
        congress     — stage-0 summary (run_id, source_slug, total_inserted,
                        total_written, load_ok)
        disclosures  — stage-1 summary (run_id, source_slug, parse_succeeded,
                        parse_failed, transform_count, total_written, load_ok)
        recompute    — stage-2 summary (run_id, source_slug, rule_fires,
                        evidence_cards)
        publish      — stage-3 summary (run_id, snapshot_id, source_slug,
                        written_count, succeeded)
        verify       — stage-4 filesystem verification summary (ok,
                        total_checked, total_errors, total_warnings, stages)
        roundtrip    — stage-5 DB→publish roundtrip summary (ok,
                        total_checked, total_errors, total_warnings, stages)
    """
    result = run_oracle_local(conn, congress_archive, disclosures_bundle, options)
    return summarize_local_oracle_run_result(result)
