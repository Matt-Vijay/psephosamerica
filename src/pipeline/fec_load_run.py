from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.db.load_executor import Resolvers, execute_load_plan
from src.db.load_report import (
    LoadSummary,
    WarnErrorSummary,
    build_load_summary,
    merge_load_summaries as _merge_load_summaries,
)
from src.db.repositories import (
    commit_or_rollback,
    ensure_transactional_for_commit,
    rollback_if_available,
)
from src.db.runtime_lookups import load_lookup_bundle
from src.ingest.fec.models import (
    CandidateCommitteeLinkage,
    CommitteeRecord,
    ContributionRecord,
)
from src.load.fec import plan_fec_load


@dataclass
class FecLoadInputs:
    committees: list[CommitteeRecord] = field(default_factory=list)
    contributions: list[ContributionRecord] = field(default_factory=list)
    linkages: list[CandidateCommitteeLinkage] = field(default_factory=list)
    committee_source_artifact_id: int | None = None
    contribution_source_artifact_id: int | None = None
    linkage_source_artifact_id: int | None = None


def run_fec_load(
    inputs: FecLoadInputs,
    conn: Any,
    *,
    run_id: int,
    commit: bool = True,
) -> LoadSummary:
    """Load canonical FEC committee, contribution, and CCL rows.

    FEC contributions carry raw recipient committee IDs, so committee rows are
    written first and lookup maps are refreshed before contribution writes.
    """
    _validate_required_artifacts(inputs)
    operations = plan_fec_load(inputs.committees, inputs.contributions, inputs.linkages)
    _inject_source_artifact_id(operations[0], inputs.committee_source_artifact_id)
    _inject_source_artifact_id(operations[1], inputs.contribution_source_artifact_id)
    _inject_source_artifact_id(operations[2], inputs.linkage_source_artifact_id)

    summaries: list[LoadSummary] = []
    ensure_transactional_for_commit(conn, commit=commit)
    try:
        summaries.append(
            execute_load_plan(
                conn,
                [operations[0]],
                run_id=run_id,
                commit=False,
            )
        )
        lookup_bundle = load_lookup_bundle(conn)
        skipped_linkages = _filter_linkages_to_known_committees(
            operations[2],
            lookup_bundle.fec_committee_map,
        )
        if skipped_linkages:
            warning = WarnErrorSummary()
            warning.add_warning(
                "skipped "
                f"{skipped_linkages} FEC candidate-committee linkage row(s) "
                "whose committee was absent from committee master"
            )
            summaries.append(build_load_summary([], warn_error=warning, run_id=run_id))
        summaries.append(
            execute_load_plan(
                conn,
                operations[1:],
                resolvers=_build_fec_resolvers(lookup_bundle.fec_committee_map),
                run_id=run_id,
                commit=False,
            )
        )
    except Exception:
        if commit:
            rollback_if_available(conn)
        raise

    if commit:
        commit_or_rollback(conn)
    return _merge_load_summaries(summaries, run_id=run_id)


def _validate_required_artifacts(inputs: FecLoadInputs) -> None:
    if inputs.committees and inputs.committee_source_artifact_id is None:
        raise ValueError("committee_source_artifact_id is required when committee rows are present")
    if inputs.contributions and inputs.contribution_source_artifact_id is None:
        raise ValueError(
            "contribution_source_artifact_id is required when contribution rows are present"
        )
    if inputs.linkages and inputs.linkage_source_artifact_id is None:
        raise ValueError("linkage_source_artifact_id is required when linkage rows are present")


def _inject_source_artifact_id(op: dict[str, Any], source_artifact_id: int | None) -> None:
    if source_artifact_id is None:
        return
    for row in op.get("rows", []):
        row["source_artifact_id"] = source_artifact_id


def _build_fec_resolvers(fec_committee_map: dict[str, int]) -> Resolvers:
    def _resolve_recipient_committee(row: dict[str, Any]) -> int | None:
        key = row.get("recipient_fec_committee_id_raw")
        return fec_committee_map.get(key) if isinstance(key, str) else None

    return {
        "recipient_fec_committee_id_raw": (
            "recipient_fec_committee_id",
            _resolve_recipient_committee,
        )
    }


def _filter_linkages_to_known_committees(
    op: dict[str, Any],
    fec_committee_map: dict[str, int],
) -> int:
    rows = op.get("rows")
    if not isinstance(rows, list):
        return 0
    filtered = [
        row
        for row in rows
        if isinstance(row.get("fec_committee_id"), str)
        and row["fec_committee_id"] in fec_committee_map
    ]
    op["rows"] = filtered
    return len(rows) - len(filtered)
