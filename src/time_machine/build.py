"""One deterministic build path from inventoried local artifacts to Parquet."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.time_machine.catalog import PARQUET_DIRNAME, create_catalog
from src.time_machine.federal import ingest_federal
from src.time_machine.govinfo_text import (
    load_govinfo_manifest,
    parse_bill_text_xml,
    parse_package_id,
)
from src.time_machine.integrity import generate_integrity_report
from src.time_machine.inventory import InventoryEntry, inventory_by_path, load_inventory
from src.time_machine.model import (
    TABLE_SCHEMAS,
    ParquetSink,
    federal_bill_id,
    provenance,
    utc_datetime,
)
from src.time_machine.states import OpenStatesBuildState, normalize_openstates_zip

Progress = Callable[[str], None]
BUILDER_VERSION = 1


@dataclass(frozen=True)
class BuildConfig:
    source_root: Path
    output_root: Path
    state_limit: int | None = None
    include_states: bool = True
    force: bool = False


@dataclass(frozen=True)
class BuildResult:
    status: str
    output_root: str
    input_digest: str
    table_rows: dict[str, int]
    federal: dict[str, Any]
    states: dict[str, Any]
    govinfo_text_versions: int
    integrity_status: str


class _DeduplicatingSink:
    def __init__(self, sink: ParquetSink, key: str) -> None:
        self.sink = sink
        self.key = key
        self.seen: set[str] = set()

    def write(self, row: Mapping[str, Any]) -> None:
        value = str(row[self.key])
        if value in self.seen:
            return
        self.seen.add(value)
        self.sink.write(row)


def _inventory_digest(entries: list[InventoryEntry], config: BuildConfig) -> str:
    payload = {
        "builder_version": BUILDER_VERSION,
        "state_limit": config.state_limit,
        "include_states": config.include_states,
        "artifacts": [
            [row.source_artifact_id, row.relative_path, row.byte_count, row.observed_at]
            for row in entries
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _prior_result(config: BuildConfig, digest: str) -> BuildResult | None:
    path = config.output_root / "build.json"
    if config.force or not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("status") != "complete" or raw.get("input_digest") != digest:
            return None
        return BuildResult(
            status="unchanged",
            output_root=str(config.output_root.resolve()),
            input_digest=digest,
            table_rows={str(k): int(v) for k, v in raw.get("table_rows", {}).items()},
            federal=raw.get("federal", {}),
            states=raw.get("states", {}),
            govinfo_text_versions=int(raw.get("govinfo_text_versions", 0)),
            integrity_status=str(raw.get("integrity_status") or "unknown"),
        )
    except (OSError, ValueError, TypeError):
        return None


def _artifact_row(entry: InventoryEntry, inventoried_at: datetime) -> dict[str, Any]:
    observed = utc_datetime(entry.observed_at)
    return {
        "source_artifact_id": entry.source_artifact_id,
        "source_family": entry.source_family,
        "relative_path": entry.relative_path,
        "source_url": entry.source_url,
        "media_type": entry.media_type,
        "content_sha256": entry.content_sha256,
        "byte_count": entry.byte_count,
        "retained": True,
        "modified_at": utc_datetime(entry.modified_at),
        "published_at": None,
        "available_at": observed,
        "availability_basis": "local_observation",
        "observed_at": observed,
        "inventoried_at": inventoried_at,
    }


def _ingest_govinfo_text(
    output_root: Path,
    entries: Mapping[Path, InventoryEntry],
    sink: ParquetSink,
) -> int:
    count = 0
    for record in load_govinfo_manifest(output_root):
        relative = str(getattr(record, "relative_path"))
        raw_path = Path(relative)
        if not raw_path.is_absolute():
            raw_path = output_root / raw_path
        entry = entries.get(raw_path.resolve())
        if entry is None:
            raise KeyError(
                f"GovInfo text object is not inventoried: {raw_path}; rerun inventory after fetch-text"
            )
        parsed = parse_bill_text_xml(raw_path.read_bytes())
        package = parse_package_id(str(getattr(record, "package_id")))
        issued = parsed.issued_date or getattr(record, "issued_date", None)
        observed = getattr(record, "observed_at")
        available = issued if issued is not None else observed
        basis = "official_publication" if issued is not None else "local_observation"
        revision = int(getattr(record, "revision"))
        artifact_url = (
            entry.source_url
            or getattr(record, "acquisition_url", None)
            or str(getattr(record, "source_url"))
        )
        sink.write(
            {
                "text_version_id": (
                    f"text-version:govinfo:{package.package_id}:revision:{revision}"
                ),
                "bill_id": federal_bill_id(
                    package.congress, package.bill_type, package.bill_number
                ),
                "version_code": package.version_code,
                "version_name": package.version_code,
                "issued_date": issued,
                "media_type": "application/xml",
                "content_path": entry.relative_path,
                "text_content": parsed.text_content,
                "is_full_text": True,
                **provenance(
                    event_at=issued,
                    available_at=available,
                    availability_basis=basis,
                    observed_at=observed,
                    source_artifact_id=entry.source_artifact_id,
                    source_family="govinfo_bills_text",
                    # The URL and hash must describe the same acquired bytes.
                    source_url=artifact_url,
                    content_sha256=entry.content_sha256,
                    valid_from=issued,
                ),
            }
        )
        count += 1
    return count


def _state_entries(entries: list[InventoryEntry], limit: int | None) -> list[InventoryEntry]:
    rows = sorted(
        (row for row in entries if row.source_family == "openstates_bulk"),
        key=lambda row: row.relative_path,
    )
    # The three OpenStates US-Congress snapshots duplicate the dedicated
    # federal path and are intentionally inventoried but not normalized here.
    rows = [row for row in rows if not row.path.name.startswith("United_States_")]
    return rows if limit is None else rows[:limit]


def build_time_machine(
    config: BuildConfig,
    *,
    progress: Progress | None = None,
) -> BuildResult:
    """Build all canonical tables and publish only after hard invariants pass."""
    source_root = config.source_root.resolve()
    output_root = config.output_root.resolve()
    entries = load_inventory(output_root)
    digest = _inventory_digest(entries, config)
    prior = _prior_result(config, digest)
    if prior is not None:
        if progress:
            progress("build: unchanged inventory; existing canonical build is current")
        return prior

    output_root.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".time-machine-stage-", dir=output_root.parent))
    build_at = datetime.now(UTC)
    sinks: dict[str, ParquetSink] = {}
    federal_payload: dict[str, Any] = {}
    states_payload: dict[str, Any] = {
        "archives": 0,
        "sessions": 0,
        "bills": 0,
        "actions": 0,
        "bill_text_versions": 0,
        "roll_calls": 0,
        "member_votes": 0,
        "people": 0,
        "person_ids": 0,
        "unresolved_member_votes": 0,
        "skipped_member_votes": 0,
    }
    govinfo_count = 0
    try:
        with ExitStack() as stack:
            for table, schema in TABLE_SCHEMAS.items():
                sinks[table] = stack.enter_context(
                    ParquetSink(stage / PARQUET_DIRNAME / f"{table}.parquet", schema)
                )
            artifact_sink = _DeduplicatingSink(sinks["source_artifacts"], "source_artifact_id")
            ingestion_sinks: dict[str, Any] = dict(sinks)
            ingestion_sinks["source_artifacts"] = artifact_sink

            # OpenStates supplies a better publisher timestamp from each ZIP's
            # README, so its processed ZIP artifacts are written by states.py.
            selected_states = (
                _state_entries(entries, config.state_limit) if config.include_states else []
            )
            selected_state_paths = {row.path.resolve() for row in selected_states}
            for entry in entries:
                if entry.path.resolve() not in selected_state_paths:
                    artifact_sink.write(_artifact_row(entry, build_at))

            by_path = inventory_by_path(entries)
            federal = ingest_federal(
                source_root,
                by_path,
                ingestion_sinks,
                progress=progress,
            )
            federal_payload = federal.payload()

            if selected_states:
                state = OpenStatesBuildState()
                for index, entry in enumerate(selected_states, start=1):
                    if progress and (
                        index == 1 or index % 10 == 0 or index == len(selected_states)
                    ):
                        progress(
                            f"states: archive {index}/{len(selected_states)} ({entry.path.name})"
                        )
                    report = normalize_openstates_zip(
                        entry.path, entry, ingestion_sinks, build_state=state
                    )
                    states_payload["archives"] += 1
                    for field in (
                        "sessions",
                        "bills",
                        "actions",
                        "bill_text_versions",
                        "roll_calls",
                        "member_votes",
                        "people",
                        "person_ids",
                        "unresolved_member_votes",
                        "skipped_member_votes",
                    ):
                        states_payload[field] += int(getattr(report, field))

            govinfo_count = _ingest_govinfo_text(output_root, by_path, sinks["bill_text_versions"])

        table_rows = {table: sink.row_count for table, sink in sinks.items()}
        if progress:
            progress("build: creating temporal catalog and running exact integrity checks")
        create_catalog(stage)
        integrity = generate_integrity_report(stage, source_root=source_root)
        if integrity["status"] != "pass":
            raise ValueError(
                "hard integrity invariants failed; staged build retained at "
                f"{stage} (see integrity.json)"
            )

        parquet_root = output_root / PARQUET_DIRNAME
        parquet_root.mkdir(parents=True, exist_ok=True)
        for table in TABLE_SCHEMAS:
            os.replace(
                stage / PARQUET_DIRNAME / f"{table}.parquet",
                parquet_root / f"{table}.parquet",
            )
        for filename in ("integrity.json", "integrity.md"):
            os.replace(stage / filename, output_root / filename)
        create_catalog(output_root)
        result = BuildResult(
            status="complete",
            output_root=str(output_root),
            input_digest=digest,
            table_rows=table_rows,
            federal=federal_payload,
            states=states_payload,
            govinfo_text_versions=govinfo_count,
            integrity_status="pass",
        )
        build_payload = {
            **asdict(result),
            "builder_version": BUILDER_VERSION,
            "built_at": build_at.isoformat().replace("+00:00", "Z"),
            "source_root": str(source_root),
        }
        output_root.mkdir(parents=True, exist_ok=True)
        (output_root / "build.json").write_text(
            json.dumps(build_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        shutil.rmtree(stage)
        return result
    except Exception:
        # A failed stage is evidence: keep it for diagnosis rather than erasing
        # the prior successful build or concealing a partial report.
        raise


__all__ = ["BuildConfig", "BuildResult", "build_time_machine"]
