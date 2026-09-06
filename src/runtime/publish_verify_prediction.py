"""Verify published prediction/simulation artifacts."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

from src.export.local_store import (
    load_prediction_bootstrap,
    load_prediction_committee_context,
    load_prediction_committee_readiness,
    load_prediction_member_context,
    load_prediction_member_readiness,
    load_prediction_readiness,
    load_prediction_readiness_index,
    load_prediction_sector_context,
    load_prediction_sector_readiness,
    load_prediction_source_context,
    load_prediction_source_index,
    load_prediction_topology,
)
from src.export.manifest import SnapshotManifest
from src.export.writer import (
    prediction_bootstrap_path,
    prediction_committee_context_path,
    prediction_committee_readiness_path,
    prediction_member_context_path,
    prediction_member_readiness_path,
    prediction_readiness_index_path,
    prediction_readiness_path,
    prediction_sector_context_path,
    prediction_sector_readiness_path,
    prediction_source_index_path,
    prediction_topology_path,
)
from src.prediction.contracts import (
    PredictionBootstrapPayload,
    PredictionReadinessPayload,
    PredictionTopologyPayload,
)
from src.runtime.publish_verify_types import (
    IssueSeverity,
    PublishVerifyIssue,
    PublishVerifyStageResult,
    path_is_confined,
)

_STAGE = "prediction"


def _issue(
    message: str,
    *,
    path: str | None = None,
    severity: IssueSeverity = "error",
) -> PublishVerifyIssue:
    return PublishVerifyIssue(stage=_STAGE, message=message, severity=severity, path=path)


def verify_local_prediction_artifacts(
    root: Path,
    manifest_payload: SnapshotManifest,
) -> PublishVerifyStageResult:
    """Verify optional frontend-fast prediction artifacts.

    The manifest stage already checks existence, hashes, and root digest. This
    stage validates payload schemas and the topology/index relationships clients
    rely on for fast prediction and simulation lookups.
    """
    entries = [entry for entry in manifest_payload.entries if _is_prediction_path(entry.path)]
    if not entries:
        return PublishVerifyStageResult(stage=_STAGE, checked=0, issues=())

    issues: list[PublishVerifyIssue] = []
    manifest_paths = {entry.path for entry in entries}
    all_manifest_paths = {entry.path for entry in manifest_payload.entries}

    for entry in entries:
        if not path_is_confined(entry.path):
            issues.append(_issue("entry path escapes publish root", path=entry.path))
            continue
        _verify_prediction_entry(root, entry.path, issues)

    _verify_cross_artifact_contracts(root, manifest_paths, all_manifest_paths, issues)

    return PublishVerifyStageResult(
        stage=_STAGE,
        checked=len(entries),
        issues=tuple(issues),
    )


def _is_prediction_path(path: str) -> bool:
    pure = PurePosixPath(path)
    return bool(pure.parts) and pure.parts[0] == "prediction"


def _verify_prediction_entry(
    root: Path,
    path: str,
    issues: list[PublishVerifyIssue],
) -> None:
    file = root / path
    if not file.exists():
        issues.append(_issue(f"prediction artifact missing: {path}", path=path))
        return

    try:
        payload = _load_prediction_entry(root, path)
    except Exception as exc:
        mismatch_message = _prediction_path_identity_mismatch_message(path, exc)
        if mismatch_message is not None:
            issues.append(_issue(mismatch_message, path=path))
            return
        issues.append(_issue(f"prediction artifact failed to load ({path}): {exc}", path=path))
        return

    source_key = _id_from_path(path, prefix=("prediction", "source-context"))
    if source_key is not None:
        embedded_source_key = getattr(getattr(payload, "source", None), "source_key", None)
        if embedded_source_key != source_key:
            issues.append(
                _issue(
                    "prediction source context path does not match embedded source_key",
                    path=path,
                )
            )


def _prediction_path_identity_mismatch_message(path: str, exc: Exception) -> str | None:
    message = str(exc)
    if (
        _id_from_path(path, prefix=("prediction", "source-context")) is not None
        and "source_key does not match requested key" in message
    ):
        return "prediction source context path does not match embedded source_key"
    if (
        _id_from_path(path, prefix=("prediction", "sector-context")) is not None
        and "sector_id does not match requested id" in message
    ):
        return "prediction sector context path does not match embedded sector_id"
    if (
        _id_from_path(path, prefix=("prediction", "committee-context")) is not None
        and "committee_id does not match requested id" in message
    ):
        return "prediction committee context path does not match embedded committee_id"
    if (
        _id_from_path(path, prefix=("prediction", "members")) is not None
        and "bioguide_id does not match requested id" in message
    ):
        return "prediction member readiness path does not match embedded bioguide_id"
    if (
        _id_from_path(path, prefix=("prediction", "member-context")) is not None
        and "bioguide_id does not match requested id" in message
    ):
        return "prediction member context path does not match embedded bioguide_id"
    return None


def _load_prediction_entry(root: Path, path: str) -> object:
    if path == prediction_bootstrap_path():
        return load_prediction_bootstrap(root)
    if path == prediction_topology_path():
        return load_prediction_topology(root)
    if path == prediction_readiness_path():
        return load_prediction_readiness(root)
    if path == prediction_readiness_index_path():
        return load_prediction_readiness_index(root)
    if path == prediction_source_index_path():
        return load_prediction_source_index(root)
    if path == prediction_sector_readiness_path():
        return load_prediction_sector_readiness(root)
    if path == prediction_committee_readiness_path():
        return load_prediction_committee_readiness(root)

    source_key = _id_from_path(path, prefix=("prediction", "source-context"))
    if source_key is not None:
        return load_prediction_source_context(root, source_key)

    sector_id = _id_from_path(path, prefix=("prediction", "sector-context"))
    if sector_id is not None:
        return load_prediction_sector_context(root, sector_id)

    committee_id = _id_from_path(path, prefix=("prediction", "committee-context"))
    if committee_id is not None:
        return load_prediction_committee_context(root, committee_id)

    member_readiness_id = _id_from_path(path, prefix=("prediction", "members"))
    if member_readiness_id is not None:
        return load_prediction_member_readiness(root, member_readiness_id)

    member_context_id = _id_from_path(path, prefix=("prediction", "member-context"))
    if member_context_id is not None:
        return load_prediction_member_context(root, member_context_id)

    raise ValueError(f"unknown prediction artifact path: {path}")


def _id_from_path(path: str, *, prefix: tuple[str, str]) -> str | None:
    pure = PurePosixPath(path)
    if len(pure.parts) != 3 or pure.parts[:2] != prefix:
        return None
    name = pure.parts[2]
    if not name.endswith(".json"):
        return None
    identifier = name.removesuffix(".json")
    return identifier or None


def _verify_cross_artifact_contracts(
    root: Path,
    manifest_paths: set[str],
    all_manifest_paths: set[str],
    issues: list[PublishVerifyIssue],
) -> None:
    try:
        bootstrap = load_prediction_bootstrap(root)
    except FileNotFoundError:
        bootstrap = None
    except Exception:
        bootstrap = None

    if bootstrap is not None:
        _check_bootstrap_paths(bootstrap, issues)
        if bootstrap.topology_path is None:
            if prediction_topology_path() in manifest_paths:
                issues.append(
                    _issue(
                        "prediction bootstrap topology_path missing while topology artifact is published",
                        path=prediction_bootstrap_path(),
                    )
                )
        elif bootstrap.topology_path != prediction_topology_path():
            issues.append(
                _issue(
                    "prediction bootstrap topology_path is not canonical",
                    path=prediction_bootstrap_path(),
                )
            )
        elif bootstrap.topology_path not in manifest_paths:
            _require_manifest_path(
                bootstrap.topology_path,
                manifest_paths,
                issues,
                owner_path=prediction_bootstrap_path(),
                message="prediction bootstrap topology_path is not listed in manifest",
            )
    if bootstrap is not None:
        for path in (
            bootstrap.readiness_path,
            bootstrap.index_path,
            bootstrap.source_index_path,
            bootstrap.sector_readiness_path,
            bootstrap.committee_readiness_path,
        ):
            _require_manifest_path(
                path,
                manifest_paths,
                issues,
                owner_path=prediction_bootstrap_path(),
                message=f"prediction bootstrap referenced path is not listed in manifest: {path}",
            )
        try:
            readiness = load_prediction_readiness(root)
            if (
                bootstrap.snapshot_id != readiness.snapshot_id
                or bootstrap.snapshot_date != readiness.snapshot_date
            ):
                issues.append(
                    _issue(
                        "prediction bootstrap snapshot does not match readiness",
                        path=prediction_bootstrap_path(),
                    )
                )
            _check_bootstrap_readiness_summary(bootstrap, readiness, issues)
            if bootstrap.jurisdictions != readiness.jurisdictions:
                issues.append(
                    _issue(
                        "prediction bootstrap jurisdictions do not match readiness",
                        path=prediction_bootstrap_path(),
                    )
                )
        # Keep later topology checks available even if readiness is invalid or absent.
        except Exception:  # nosec B110
            pass

    if prediction_topology_path() not in manifest_paths:
        return

    try:
        topology = load_prediction_topology(root)
    except Exception:
        _check_raw_topology_readiness_counts(root, issues)
        return

    _check_topology_paths(topology, issues)
    for path in (
        topology.readiness_path,
        topology.index_path,
        topology.source_index_path,
        topology.sector_readiness_path,
        topology.committee_readiness_path,
    ):
        _require_manifest_path(
            path,
            manifest_paths,
            issues,
            owner_path=prediction_topology_path(),
            message=f"prediction topology referenced path is not listed in manifest: {path}",
        )

    try:
        readiness = load_prediction_readiness(root)
        if (
            topology.snapshot_id != readiness.snapshot_id
            or topology.snapshot_date != readiness.snapshot_date
        ):
            issues.append(
                _issue(
                    "prediction topology snapshot does not match readiness",
                    path=prediction_topology_path(),
                )
            )
        if topology.member_count != readiness.member_count:
            issues.append(_issue("prediction topology member_count does not match readiness"))
        if topology.jurisdiction_count != len(readiness.jurisdictions):
            issues.append(_issue("prediction topology jurisdiction_count does not match readiness"))
        implemented_jurisdiction_count = sum(
            1
            for jurisdiction in readiness.jurisdictions
            if jurisdiction.implementation_status == "implemented"
        )
        if topology.implemented_jurisdiction_count != implemented_jurisdiction_count:
            issues.append(
                _issue(
                    "prediction topology implemented_jurisdiction_count does not match readiness"
                )
            )
        portable_jurisdiction_count = sum(
            1
            for jurisdiction in readiness.jurisdictions
            if jurisdiction.implementation_status == "portable_contract"
        )
        if topology.portable_jurisdiction_count != portable_jurisdiction_count:
            issues.append(
                _issue("prediction topology portable_jurisdiction_count does not match readiness")
            )
        expected_jurisdiction_ids = [
            jurisdiction.jurisdiction_id for jurisdiction in readiness.jurisdictions
        ]
        _check_topology_readiness_ids(
            topology.jurisdiction_ids,
            expected_jurisdiction_ids,
            "prediction topology jurisdiction_ids does not match readiness",
            issues,
        )
        expected_implemented_jurisdiction_ids = [
            jurisdiction.jurisdiction_id
            for jurisdiction in readiness.jurisdictions
            if jurisdiction.implementation_status == "implemented"
        ]
        _check_topology_readiness_ids(
            topology.implemented_jurisdiction_ids,
            expected_implemented_jurisdiction_ids,
            "prediction topology implemented_jurisdiction_ids does not match readiness",
            issues,
        )
        expected_portable_jurisdiction_ids = [
            jurisdiction.jurisdiction_id
            for jurisdiction in readiness.jurisdictions
            if jurisdiction.implementation_status == "portable_contract"
        ]
        _check_topology_readiness_ids(
            topology.portable_jurisdiction_ids,
            expected_portable_jurisdiction_ids,
            "prediction topology portable_jurisdiction_ids does not match readiness",
            issues,
        )
        expected_legislative_body_ids = sorted(
            {
                f"{jurisdiction.jurisdiction_id}:{body_id}"
                for jurisdiction in readiness.jurisdictions
                for body_id in jurisdiction.legislative_body_ids
            }
        )
        if topology.legislative_body_count != len(expected_legislative_body_ids):
            issues.append(
                _issue("prediction topology legislative_body_count does not match readiness")
            )
        _check_topology_readiness_ids(
            topology.legislative_body_ids,
            expected_legislative_body_ids,
            "prediction topology legislative_body_ids does not match readiness",
            issues,
        )
        expected_legislative_session_ids = sorted(
            {
                f"{jurisdiction.jurisdiction_id}:{session_id}"
                for jurisdiction in readiness.jurisdictions
                for session_id in jurisdiction.legislative_session_ids
            }
        )
        if topology.legislative_session_count != len(expected_legislative_session_ids):
            issues.append(
                _issue("prediction topology legislative_session_count does not match readiness")
            )
        _check_topology_readiness_ids(
            topology.legislative_session_ids,
            expected_legislative_session_ids,
            "prediction topology legislative_session_ids does not match readiness",
            issues,
        )
        _check_readiness_jurisdiction_capabilities(issues, readiness)
    # Optional cross-artifact check is best-effort.
    except Exception:  # nosec B110
        pass

    try:
        source_index = load_prediction_source_index(root)
        if topology.source_count != source_index.source_count:
            issues.append(_issue("prediction topology source_count does not match source index"))
        try:
            readiness = load_prediction_readiness(root)
            if (
                source_index.snapshot_id != readiness.snapshot_id
                or source_index.snapshot_date != readiness.snapshot_date
            ):
                issues.append(
                    _issue(
                        "prediction source index snapshot does not match readiness",
                        path=prediction_source_index_path(),
                    )
                )
        # Keep source-row validation available even if readiness is invalid or absent.
        except Exception:  # nosec B110
            pass
        for source in source_index.sources:
            _require_manifest_path(
                source.source_context_path,
                manifest_paths,
                issues,
                owner_path=prediction_source_index_path(),
                message=(
                    "prediction source index context path is not listed in manifest: "
                    f"{source.source_context_path}"
                ),
            )
            _check_source_index_member_memberships(root, source, issues)
            try:
                source_context = load_prediction_source_context(root, source.source_key)
            # Keep validating remaining source contexts.
            except Exception:  # nosec B112
                continue
            if source_context.source != source:
                issues.append(
                    _issue(
                        "prediction source index row does not match source context",
                        path=prediction_source_index_path(),
                    )
                )
            try:
                readiness = load_prediction_readiness(root)
                if (
                    source_context.snapshot_id != readiness.snapshot_id
                    or source_context.snapshot_date != readiness.snapshot_date
                ):
                    issues.append(
                        _issue(
                            "prediction source context snapshot does not match readiness",
                            path=source.source_context_path,
                        )
                    )
            # Keep source-row validation available even if readiness is invalid or absent.
            except Exception:  # nosec B110
                pass
            _check_nested_member_contexts_match(
                root,
                source_context.members,
                issues,
                owner_path=source.source_context_path,
                message="prediction source context member does not match member context",
            )
            _check_source_index_sector_memberships(root, source, issues)
            _check_source_index_committee_memberships(root, source, issues)
    # Optional cross-artifact check is best-effort.
    except Exception:  # nosec B110
        pass

    try:
        sector_readiness = load_prediction_sector_readiness(root)
        if topology.sector_count != sector_readiness.sector_count:
            issues.append(
                _issue("prediction topology sector_count does not match sector readiness")
            )
        try:
            readiness = load_prediction_readiness(root)
            if (
                sector_readiness.snapshot_id != readiness.snapshot_id
                or sector_readiness.snapshot_date != readiness.snapshot_date
            ):
                issues.append(
                    _issue(
                        "prediction sector readiness snapshot does not match readiness",
                        path=prediction_sector_readiness_path(),
                    )
                )
        # Keep sector-row validation available even if readiness is invalid or absent.
        except Exception:  # nosec B110
            pass
        for sector in sector_readiness.sectors:
            _require_manifest_path(
                prediction_sector_context_path(sector.sector_id),
                manifest_paths,
                issues,
                owner_path=prediction_sector_readiness_path(),
                message=(
                    "prediction sector readiness context path is not listed in manifest: "
                    f"{prediction_sector_context_path(sector.sector_id)}"
                ),
            )
            for path in sector.source_context_paths:
                _require_manifest_path(
                    path,
                    manifest_paths,
                    issues,
                    owner_path=prediction_sector_readiness_path(),
                    message=(
                        "prediction sector readiness source context path is not listed in "
                        f"manifest: {path}"
                    ),
                )
                _check_source_context_summary_includes(
                    root,
                    path,
                    field_name="sector_ids",
                    expected_id=sector.sector_id,
                    issues=issues,
                    owner_path=prediction_sector_readiness_path(),
                    message=("prediction sector readiness source context does not include sector"),
                )
                _check_source_context_listed_in_index(
                    root,
                    path,
                    issues,
                    owner_path=prediction_sector_readiness_path(),
                    message="prediction sector readiness source is not listed in source index",
                )
            try:
                sector_context = load_prediction_sector_context(root, sector.sector_id)
            # Keep validating remaining sector contexts.
            except Exception:  # nosec B112
                continue
            if sector_context.sector != sector:
                issues.append(
                    _issue(
                        "prediction sector readiness row does not match sector context",
                        path=prediction_sector_readiness_path(),
                    )
                )
            try:
                readiness = load_prediction_readiness(root)
                if (
                    sector_context.snapshot_id != readiness.snapshot_id
                    or sector_context.snapshot_date != readiness.snapshot_date
                ):
                    issues.append(
                        _issue(
                            "prediction sector context snapshot does not match readiness",
                            path=prediction_sector_context_path(sector.sector_id),
                        )
                    )
            # Keep sector-row validation available even if readiness is invalid or absent.
            except Exception:  # nosec B110
                pass
            _check_nested_member_contexts_match(
                root,
                sector_context.members,
                issues,
                owner_path=prediction_sector_context_path(sector.sector_id),
                message="prediction sector context member does not match member context",
            )
    # Optional cross-artifact check is best-effort.
    except Exception:  # nosec B110
        pass

    try:
        committee_readiness = load_prediction_committee_readiness(root)
        if topology.committee_count != committee_readiness.committee_count:
            issues.append(
                _issue("prediction topology committee_count does not match committee readiness")
            )
        try:
            readiness = load_prediction_readiness(root)
            if (
                committee_readiness.snapshot_id != readiness.snapshot_id
                or committee_readiness.snapshot_date != readiness.snapshot_date
            ):
                issues.append(
                    _issue(
                        "prediction committee readiness snapshot does not match readiness",
                        path=prediction_committee_readiness_path(),
                    )
                )
        # Keep committee-row validation available even if readiness is invalid or absent.
        except Exception:  # nosec B110
            pass
        for committee in committee_readiness.committees:
            _require_manifest_path(
                prediction_committee_context_path(committee.committee_id),
                manifest_paths,
                issues,
                owner_path=prediction_committee_readiness_path(),
                message=(
                    "prediction committee readiness context path is not listed in manifest: "
                    f"{prediction_committee_context_path(committee.committee_id)}"
                ),
            )
            for path in committee.source_context_paths:
                _require_manifest_path(
                    path,
                    manifest_paths,
                    issues,
                    owner_path=prediction_committee_readiness_path(),
                    message=(
                        "prediction committee readiness source context path is not listed in "
                        f"manifest: {path}"
                    ),
                )
                _check_source_context_summary_includes(
                    root,
                    path,
                    field_name="committee_ids",
                    expected_id=committee.committee_id,
                    issues=issues,
                    owner_path=prediction_committee_readiness_path(),
                    message=(
                        "prediction committee readiness source context does not include committee"
                    ),
                )
                _check_source_context_listed_in_index(
                    root,
                    path,
                    issues,
                    owner_path=prediction_committee_readiness_path(),
                    message=("prediction committee readiness source is not listed in source index"),
                )
            try:
                committee_context = load_prediction_committee_context(root, committee.committee_id)
            # Keep validating remaining committee contexts.
            except Exception:  # nosec B112
                continue
            if committee_context.committee != committee:
                issues.append(
                    _issue(
                        "prediction committee readiness row does not match committee context",
                        path=prediction_committee_readiness_path(),
                    )
                )
            try:
                readiness = load_prediction_readiness(root)
                if (
                    committee_context.snapshot_id != readiness.snapshot_id
                    or committee_context.snapshot_date != readiness.snapshot_date
                ):
                    issues.append(
                        _issue(
                            "prediction committee context snapshot does not match readiness",
                            path=prediction_committee_context_path(committee.committee_id),
                        )
                    )
            # Keep committee-row validation available even if readiness is invalid or absent.
            except Exception:  # nosec B110
                pass
            _check_nested_member_contexts_match(
                root,
                committee_context.members,
                issues,
                owner_path=prediction_committee_context_path(committee.committee_id),
                message="prediction committee context member does not match member context",
            )
    # Optional cross-artifact check is best-effort.
    except Exception:  # nosec B110
        pass

    try:
        readiness_index = load_prediction_readiness_index(root)
        try:
            readiness = load_prediction_readiness(root)
            readiness_members = {member.bioguide_id: member for member in readiness.members}
            if (
                readiness_index.snapshot_id != readiness.snapshot_id
                or readiness_index.snapshot_date != readiness.snapshot_date
            ):
                issues.append(
                    _issue(
                        "prediction readiness index snapshot does not match readiness",
                        path=prediction_readiness_index_path(),
                    )
                )
            if readiness_index.member_count != readiness.member_count:
                issues.append(
                    _issue(
                        "prediction readiness index member_count does not match readiness",
                        path=prediction_readiness_index_path(),
                    )
                )
            if set(readiness_index.members_by_bioguide) != set(readiness_members):
                issues.append(
                    _issue(
                        "prediction readiness index member ids do not match readiness",
                        path=prediction_readiness_index_path(),
                    )
                )
            for bioguide_id, indexed_member in readiness_index.members_by_bioguide.items():
                readiness_member = readiness_members.get(bioguide_id)
                if readiness_member is not None and indexed_member != readiness_member:
                    issues.append(
                        _issue(
                            "prediction readiness index member does not match readiness",
                            path=prediction_readiness_index_path(),
                        )
                    )
        # Keep manifest-path checks available even if readiness is invalid or absent.
        except Exception:  # nosec B110
            pass
        for bioguide_id in readiness_index.members_by_bioguide:
            for path in (
                prediction_member_readiness_path(bioguide_id),
                prediction_member_context_path(bioguide_id),
            ):
                _require_manifest_path(
                    path,
                    manifest_paths,
                    issues,
                    owner_path=prediction_readiness_index_path(),
                    message=(
                        f"prediction readiness index member path is not listed in manifest: {path}"
                    ),
                )
    # Optional cross-artifact check is best-effort.
    except Exception:  # nosec B110
        pass

    for path in sorted(manifest_paths):
        member_readiness_id = _id_from_path(path, prefix=("prediction", "members"))
        if member_readiness_id is not None:
            try:
                member_readiness = load_prediction_member_readiness(root, member_readiness_id)
                readiness = load_prediction_readiness(root)
                readiness_members = {member.bioguide_id: member for member in readiness.members}
                expected_member = readiness_members.get(member_readiness_id)
                if expected_member is None:
                    issues.append(
                        _issue(
                            "prediction member readiness file is not listed in readiness",
                            path=path,
                        )
                    )
                elif member_readiness != expected_member:
                    issues.append(
                        _issue(
                            "prediction member readiness file does not match readiness",
                            path=path,
                        )
                    )
            # Keep validating remaining member artifacts.
            except Exception:  # nosec B110
                pass

        member_context_id = _id_from_path(path, prefix=("prediction", "member-context"))
        if member_context_id is None:
            continue
        try:
            member_context = load_prediction_member_context(root, member_context_id)
        # Keep validating remaining member contexts.
        except Exception:  # nosec B112
            continue
        try:
            readiness = load_prediction_readiness(root)
            if (
                member_context.snapshot_id != readiness.snapshot_id
                or member_context.snapshot_date != readiness.snapshot_date
            ):
                issues.append(
                    _issue(
                        "prediction member context snapshot does not match readiness",
                        path=path,
                    )
                )
            readiness_members = {member.bioguide_id: member for member in readiness.members}
            expected_member = readiness_members.get(member_context_id)
            if expected_member is None:
                issues.append(
                    _issue(
                        "prediction member context is not listed in readiness",
                        path=path,
                    )
                )
            elif member_context.member_readiness != expected_member:
                issues.append(
                    _issue(
                        "prediction member context readiness does not match readiness",
                        path=path,
                    )
                )
        # Keep source/path validation available even if readiness is invalid or absent.
        except Exception:  # nosec B110
            pass
        for source_path in member_context.source_context_paths:
            _require_manifest_path(
                source_path,
                manifest_paths,
                issues,
                owner_path=path,
                message=(
                    "prediction member context source context path is not listed in manifest: "
                    f"{source_path}"
                ),
            )
            _check_member_source_context_backlink(
                root,
                member_context_id,
                source_path,
                issues,
                owner_path=path,
            )
            _check_source_context_listed_in_index(
                root,
                source_path,
                issues,
                owner_path=path,
                message="prediction member context source is not listed in source index",
            )
        if member_context.artifact_refs is not None:
            expected_member_readiness_path = prediction_member_readiness_path(member_context_id)
            if (
                member_context.artifact_refs.prediction_member_readiness_path
                != expected_member_readiness_path
            ):
                issues.append(
                    _issue(
                        "prediction member context artifact_refs "
                        "prediction_member_readiness_path is not canonical",
                        path=path,
                    )
                )
            expected_member_context_path = prediction_member_context_path(member_context_id)
            if (
                member_context.artifact_refs.prediction_member_context_path
                != expected_member_context_path
            ):
                issues.append(
                    _issue(
                        "prediction member context artifact_refs "
                        "prediction_member_context_path is not canonical",
                        path=path,
                    )
                )
            for ref_path in (
                member_context.artifact_refs.member_profile_path,
                member_context.artifact_refs.member_page_path,
                member_context.artifact_refs.prediction_member_readiness_path,
                member_context.artifact_refs.prediction_member_context_path,
                member_context.artifact_refs.ontology_member_features_path,
                member_context.artifact_refs.ontology_member_graph_path,
            ):
                if ref_path is None:
                    continue
                _require_manifest_path(
                    ref_path,
                    all_manifest_paths,
                    issues,
                    owner_path=path,
                    message=(
                        "prediction member context artifact ref is not listed in manifest: "
                        f"{ref_path}"
                    ),
                )


def _check_readiness_jurisdiction_capabilities(
    issues: list[PublishVerifyIssue],
    readiness: object,
) -> None:
    jurisdictions = getattr(readiness, "jurisdictions", None)
    if not jurisdictions:
        issues.append(
            _issue(
                "prediction readiness must declare jurisdiction capabilities",
                path=prediction_readiness_path(),
            )
        )
        return

    congress_capability = next(
        (
            jurisdiction
            for jurisdiction in jurisdictions
            if jurisdiction.jurisdiction_id == "us_congress"
        ),
        None,
    )
    if congress_capability is None:
        issues.append(
            _issue(
                "prediction readiness must include us_congress jurisdiction capability",
                path=prediction_readiness_path(),
            )
        )
        return
    if congress_capability.implementation_status != "implemented":
        issues.append(
            _issue(
                "prediction readiness us_congress capability must be implemented",
                path=prediction_readiness_path(),
            )
        )
    required_roles = set(congress_capability.required_source_roles)
    for role in ("bills", "members", "source_anchors", "votes"):
        if role not in required_roles:
            issues.append(
                _issue(
                    f"prediction readiness us_congress capability missing source role: {role}",
                    path=prediction_readiness_path(),
                )
            )


def _check_bootstrap_readiness_summary(
    bootstrap: PredictionBootstrapPayload,
    readiness: PredictionReadinessPayload,
    issues: list[PublishVerifyIssue],
) -> None:
    for field in (
        "member_count",
        "ready_member_count",
        "partial_member_count",
        "blocked_member_count",
        "vote_event_count",
        "vote_cast_count",
    ):
        if getattr(bootstrap, field) != getattr(readiness, field):
            issues.append(
                _issue(
                    f"prediction bootstrap {field} does not match readiness",
                    path=prediction_bootstrap_path(),
                )
            )
    if bootstrap.coverage != readiness.coverage:
        issues.append(
            _issue(
                "prediction bootstrap coverage does not match readiness",
                path=prediction_bootstrap_path(),
            )
        )


def _check_nested_member_contexts_match(
    root: Path,
    members: object,
    issues: list[PublishVerifyIssue],
    *,
    owner_path: str,
    message: str,
) -> None:
    if not isinstance(members, list):
        return
    for member_context in members:
        bioguide_id = getattr(member_context, "member_bioguide_id", None)
        if not isinstance(bioguide_id, str) or not bioguide_id:
            continue
        try:
            expected_context = load_prediction_member_context(root, bioguide_id)
        # Keep checking remaining nested members.
        except Exception:  # nosec B112
            continue
        if member_context != expected_context:
            issues.append(_issue(message, path=owner_path))


def _check_member_source_context_backlink(
    root: Path,
    bioguide_id: str,
    source_path: str,
    issues: list[PublishVerifyIssue],
    *,
    owner_path: str,
) -> None:
    source_key = _id_from_path(source_path, prefix=("prediction", "source-context"))
    if source_key is None:
        return
    try:
        source_context = load_prediction_source_context(root, source_key)
    # Keep checking remaining source paths.
    except Exception:  # nosec B110
        return
    source_member_ids = getattr(source_context.source, "member_bioguide_ids", ())
    if bioguide_id not in source_member_ids:
        issues.append(
            _issue(
                "prediction member context source context does not include member",
                path=owner_path,
            )
        )


def _check_source_context_summary_includes(
    root: Path,
    source_path: str,
    *,
    field_name: str,
    expected_id: str,
    issues: list[PublishVerifyIssue],
    owner_path: str,
    message: str,
) -> None:
    source_key = _id_from_path(source_path, prefix=("prediction", "source-context"))
    if source_key is None:
        return
    try:
        source_context = load_prediction_source_context(root, source_key)
    # Keep checking remaining source paths.
    except Exception:  # nosec B110
        return
    source_ids = getattr(source_context.source, field_name, ())
    if expected_id not in source_ids:
        issues.append(_issue(message, path=owner_path))


def _check_source_context_listed_in_index(
    root: Path,
    source_path: str,
    issues: list[PublishVerifyIssue],
    *,
    owner_path: str,
    message: str,
) -> None:
    source_key = _id_from_path(source_path, prefix=("prediction", "source-context"))
    if source_key is None:
        return
    try:
        source_index = load_prediction_source_index(root)
    # Keep checking remaining source paths.
    except Exception:  # nosec B110
        return
    source_index_keys = {source.source_key for source in source_index.sources}
    if source_key not in source_index_keys:
        issues.append(
            _issue(
                f"{message}: {source_key}",
                path=owner_path,
            )
        )


def _check_source_index_sector_memberships(
    root: Path,
    source: object,
    issues: list[PublishVerifyIssue],
) -> None:
    source_key = getattr(source, "source_key", None)
    sector_ids = getattr(source, "sector_ids", ())
    if not isinstance(source_key, str):
        return
    try:
        sector_readiness = load_prediction_sector_readiness(root)
    # Keep checking remaining source rows.
    except Exception:  # nosec B110
        return
    sectors_by_id = {sector.sector_id: sector for sector in sector_readiness.sectors}
    for sector_id in sector_ids:
        sector = sectors_by_id.get(sector_id)
        if sector is None or source_key not in sector.source_keys:
            issues.append(
                _issue(
                    "prediction source index sector is not listed in sector readiness: "
                    f"{sector_id}",
                    path=prediction_source_index_path(),
                )
            )


def _check_source_index_committee_memberships(
    root: Path,
    source: object,
    issues: list[PublishVerifyIssue],
) -> None:
    source_key = getattr(source, "source_key", None)
    committee_ids = getattr(source, "committee_ids", ())
    if not isinstance(source_key, str):
        return
    try:
        committee_readiness = load_prediction_committee_readiness(root)
    # Keep checking remaining source rows.
    except Exception:  # nosec B110
        return
    committees_by_id = {
        committee.committee_id: committee for committee in committee_readiness.committees
    }
    for committee_id in committee_ids:
        committee = committees_by_id.get(committee_id)
        if committee is None or source_key not in committee.source_keys:
            issues.append(
                _issue(
                    "prediction source index committee is not listed in committee "
                    f"readiness: {committee_id}",
                    path=prediction_source_index_path(),
                )
            )


def _check_source_index_member_memberships(
    root: Path,
    source: object,
    issues: list[PublishVerifyIssue],
) -> None:
    source_key = getattr(source, "source_key", None)
    member_ids = getattr(source, "member_bioguide_ids", ())
    if not isinstance(source_key, str):
        return
    try:
        readiness = load_prediction_readiness(root)
    # Keep checking remaining source rows.
    except Exception:  # nosec B110
        return
    readiness_member_ids = {member.bioguide_id for member in readiness.members}
    for member_id in member_ids:
        if member_id not in readiness_member_ids:
            issues.append(
                _issue(
                    f"prediction source index member is not listed in readiness: {member_id}",
                    path=prediction_source_index_path(),
                )
            )
            continue
        try:
            member_context = load_prediction_member_context(root, member_id)
        # Keep checking remaining source-index members.
        except Exception:  # nosec B112
            continue
        if source_key not in member_context.source_keys:
            issues.append(
                _issue(
                    f"prediction source index member does not reference source: {member_id}",
                    path=prediction_source_index_path(),
                )
            )


def _check_topology_readiness_ids(
    actual: list[str],
    expected: list[str],
    message: str,
    issues: list[PublishVerifyIssue],
) -> None:
    if actual != expected:
        issues.append(_issue(message))


def _check_raw_topology_readiness_counts(
    root: Path,
    issues: list[PublishVerifyIssue],
) -> None:
    try:
        raw_topology = json.loads((root / prediction_topology_path()).read_text(encoding="utf-8"))
        readiness = load_prediction_readiness(root)
    except Exception:  # nosec B110
        return
    if not isinstance(raw_topology, dict):
        return
    _check_raw_topology_readiness_count(
        raw_topology,
        "member_count",
        readiness.member_count,
        "prediction topology member_count does not match readiness",
        issues,
    )
    _check_raw_topology_readiness_count(
        raw_topology,
        "jurisdiction_count",
        len(readiness.jurisdictions),
        "prediction topology jurisdiction_count does not match readiness",
        issues,
    )
    implemented_jurisdiction_count = sum(
        1
        for jurisdiction in readiness.jurisdictions
        if jurisdiction.implementation_status == "implemented"
    )
    _check_raw_topology_readiness_count(
        raw_topology,
        "implemented_jurisdiction_count",
        implemented_jurisdiction_count,
        "prediction topology implemented_jurisdiction_count does not match readiness",
        issues,
    )
    portable_jurisdiction_count = sum(
        1
        for jurisdiction in readiness.jurisdictions
        if jurisdiction.implementation_status == "portable_contract"
    )
    _check_raw_topology_readiness_count(
        raw_topology,
        "portable_jurisdiction_count",
        portable_jurisdiction_count,
        "prediction topology portable_jurisdiction_count does not match readiness",
        issues,
    )


def _check_raw_topology_readiness_count(
    raw_topology: dict[str, Any],
    key: str,
    expected: int,
    message: str,
    issues: list[PublishVerifyIssue],
) -> None:
    value = raw_topology.get(key)
    if type(value) is int and value != expected:
        issues.append(_issue(message))


def _check_topology_paths(
    topology: PredictionTopologyPayload,
    issues: list[PublishVerifyIssue],
) -> None:
    expected = {
        "readiness_path": prediction_readiness_path(),
        "index_path": prediction_readiness_index_path(),
        "source_index_path": prediction_source_index_path(),
        "sector_readiness_path": prediction_sector_readiness_path(),
        "committee_readiness_path": prediction_committee_readiness_path(),
        "member_readiness_path_template": "prediction/members/{bioguide}.json",
        "member_context_path_template": "prediction/member-context/{bioguide}.json",
        "source_context_path_template": "prediction/source-context/{source_key}.json",
        "sector_context_path_template": "prediction/sector-context/{sector}.json",
        "committee_context_path_template": "prediction/committee-context/{committee}.json",
    }
    for field, value in expected.items():
        if getattr(topology, field) != value:
            issues.append(_issue(f"prediction topology {field} is not canonical"))


def _check_bootstrap_paths(
    bootstrap: object,
    issues: list[PublishVerifyIssue],
) -> None:
    expected = {
        "readiness_path": prediction_readiness_path(),
        "index_path": prediction_readiness_index_path(),
        "source_index_path": prediction_source_index_path(),
        "sector_readiness_path": prediction_sector_readiness_path(),
        "committee_readiness_path": prediction_committee_readiness_path(),
        "member_readiness_path_template": "prediction/members/{bioguide}.json",
        "member_context_path_template": "prediction/member-context/{bioguide}.json",
        "source_context_path_template": "prediction/source-context/{source_key}.json",
        "sector_context_path_template": "prediction/sector-context/{sector}.json",
        "committee_context_path_template": "prediction/committee-context/{committee}.json",
    }
    for field, value in expected.items():
        if getattr(bootstrap, field) != value:
            issues.append(
                _issue(
                    f"prediction bootstrap {field} is not canonical",
                    path=prediction_bootstrap_path(),
                )
            )


def _require_manifest_path(
    path: str,
    manifest_paths: set[str],
    issues: list[PublishVerifyIssue],
    *,
    owner_path: str,
    message: str,
) -> None:
    if not path_is_confined(path):
        issues.append(_issue("prediction referenced path escapes publish root", path=owner_path))
        return
    if path not in manifest_paths:
        issues.append(_issue(message, path=owner_path))
