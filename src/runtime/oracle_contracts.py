"""Typed local oracle contracts.

Pure models only — no execution logic.  Callers pass these in; the oracle
pipeline reads them.  All types are immutable (frozen dataclasses).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from src.runtime.disclosures_bundle import DisclosuresBundle
from src.runtime.publish_roundtrip_types import PublishRoundtripResult
from src.runtime.publish_verify_types import PublishVerifyResult


# ---------------------------------------------------------------------------
# Congress-specific oracle options
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CongressOracleOptions:
    """Options controlling which Congress data the oracle processes.

    Attributes:
        congress:       Congress number (e.g. 119).
        chamber:        "house", "senate", or None for both chambers.
        limit:          Cap on disclosure filings processed; None means all.
        parser_name:    Disclosure parser implementation to use.
        parser_version: Version string passed to the parser dispatch.
    """

    congress: int
    chamber: str | None = None
    limit: int | None = None
    parser_name: str = "text_extract_v1"
    parser_version: str = "1"


# ---------------------------------------------------------------------------
# Local oracle run inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LocalOracleInputs:
    """Caller-supplied data inputs for a single local oracle run.

    Attributes:
        congress_archive:   Path to a local Congress archive directory.
                            The directory must follow the conventional layout
                            expected by CongressArchive (members.json,
                            committees.json, bills.json, member_details/, …).
        disclosures_bundle: Pre-fetched artifact bundle for offline processing.
                            Each entry supplies enough data to stage, parse,
                            and resolve one disclosure filing without network
                            access.
    """

    congress_archive: Path
    disclosures_bundle: DisclosuresBundle


# ---------------------------------------------------------------------------
# Full local oracle run options
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LocalOracleOptions:
    """Full options for a single local oracle run.

    Attributes:
        congress_options: Congress-specific settings (see CongressOracleOptions).
        snapshot_date:    Date this snapshot represents; used to derive the
                          default snapshot_id and label published artifacts.
        target_dir:       Directory where published artifacts are written.
        snapshot_id:      Explicit snapshot identifier; defaults to
                          snapshot_date.isoformat() when None.
        artifact_root:    Root of the local disclosure artifact tree.  None
                          when artifacts live in the default repo location.
    """

    congress_options: CongressOracleOptions
    snapshot_date: dt.date
    target_dir: Path
    snapshot_id: str | None = None
    artifact_root: Path | None = None

    def resolved_snapshot_id(self) -> str:
        """Return the effective snapshot identifier.

        Uses the explicit snapshot_id when set; falls back to the ISO-8601
        representation of snapshot_date.
        """
        return self.snapshot_id if self.snapshot_id is not None else self.snapshot_date.isoformat()


# ---------------------------------------------------------------------------
# Congress stage summary
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CongressStageSummary:
    """Typed summary of the congress-archive load stage within a local oracle run.

    Attributes:
        run_id:         Ingestion run row ID created for this stage.
        source_slug:    Slug of the data_source row used (e.g. "congress_core").
        total_inserted: Total rows inserted across all canonical tables.
        total_written:  Total rows inserted or updated (inserted + updated).
        load_ok:        False if the load produced any errors.
    """

    run_id: int
    source_slug: str
    total_inserted: int
    total_written: int
    load_ok: bool


# ---------------------------------------------------------------------------
# Local oracle run result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LocalOracleRunResult:
    """Compact structured result from a completed local oracle run.

    Each stage summary mirrors the outcome of its runtime function so callers
    can inspect or log results without re-running.

    Attributes:
        snapshot_id:  The snapshot identifier used during the run.
        congress:     Stage-0 typed summary from the congress-archive load.
        disclosures:  Stage-1 summary from the process-disclosures step.
        recompute:    Stage-2 summary (run_id, rule_fires, evidence_cards …).
        publish:      Stage-3 summary (written_count, succeeded …).
        verify:       Stage-4 typed filesystem verification result across
                      manifest, profiles, evidence, and zip stages.
        roundtrip:    Stage-5 typed DB→publish roundtrip result confirming
                      that published artifacts match the canonical store
                      across snapshot, profiles, evidence, zip, and homepage
                      stages.
    """

    snapshot_id: str
    congress: CongressStageSummary
    disclosures: Mapping[str, Any]
    recompute: Mapping[str, Any]
    publish: Mapping[str, Any]
    verify: PublishVerifyResult
    roundtrip: PublishRoundtripResult
