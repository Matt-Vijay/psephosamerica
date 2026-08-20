"""Hash-first inventory of the immutable inputs used by the time machine."""

from __future__ import annotations

import json
import mimetypes
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.time_machine.model import artifact_id, sha256_file, utc_datetime

INVENTORY_FILENAME = "inventory.json"


@dataclass(frozen=True)
class InventoryEntry:
    source_artifact_id: str
    source_family: str
    relative_path: str
    absolute_path: str
    source_url: str | None
    media_type: str
    content_sha256: str
    byte_count: int
    modified_at: str
    modified_ns: int
    observed_at: str

    @property
    def path(self) -> Path:
        return Path(self.absolute_path)


def _read_as_of(path: Path) -> datetime | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = payload.get("as_of")
    return utc_datetime(value) if value else None


def _family_observation_times(source_root: Path) -> dict[str, datetime]:
    candidates = {
        "govinfo_billstatus": source_root / "data/exports/govinfo_bills/manifest.json",
        "federal_roll_calls": source_root
        / "data/exports/govinfo_bills/federal_floor_and_votes_meta.json",
        "openstates_bulk": source_root / "data/exports/openstates/bulk_ingest_meta.json",
        "contract_records": source_root / "data/exports/contract_records/manifest.json",
    }
    return {family: value for family, path in candidates.items() if (value := _read_as_of(path))}


def discover_inputs(
    source_root: Path,
    *,
    time_machine_root: Path | None = None,
    state_limit: int | None = None,
) -> list[tuple[str, Path, str | None]]:
    """Return only artifacts consumed by the canonical build.

    Large derivative files that the new path does not read (the 22 GB combined
    OpenStates edge JSONL, embeddings, GraphRAG corpora) are intentionally not
    hashed.  Every OpenStates session ZIP *is* an input and is hashed in full.
    """
    roster = source_root / "data/raw/acquisitions/congress_legislators/repo"
    govinfo = source_root / "data/exports/govinfo_bills"
    contract = source_root / "data/exports/contract_records"
    openstates = source_root / "data/raw/openstates_bulk"
    inputs: list[tuple[str, Path, str | None]] = [
        (
            "congress_legislators",
            roster / "legislators-current.yaml",
            "https://github.com/unitedstates/congress-legislators/blob/main/legislators-current.yaml",
        ),
        (
            "congress_legislators",
            roster / "legislators-historical.yaml",
            "https://github.com/unitedstates/congress-legislators/blob/main/legislators-historical.yaml",
        ),
        (
            "govinfo_billstatus",
            govinfo / "bill_content_full.jsonl",
            "https://www.govinfo.gov/bulkdata/BILLSTATUS",
        ),
        (
            "govinfo_billstatus",
            govinfo / "records.jsonl",
            "https://www.govinfo.gov/bulkdata/BILLSTATUS",
        ),
        (
            "contract_records",
            contract / "records.jsonl",
            None,
        ),
        (
            "federal_roll_calls",
            govinfo / "house_vote_edges.jsonl",
            "https://clerk.house.gov/Votes",
        ),
        (
            "federal_roll_calls",
            govinfo / "senate_vote_edges.jsonl",
            "https://www.senate.gov/legislative/votes_new.htm",
        ),
        (
            "senate_roll_calls",
            source_root / "data/real/senate_113_119_rich_v2.jsonl",
            "https://www.senate.gov/legislative/votes_new.htm",
        ),
        (
            "senate_roll_calls",
            source_root / "data/real/senate_113_119_rich.jsonl",
            "https://www.senate.gov/legislative/votes_new.htm",
        ),
    ]
    openstates_manifest = openstates / "_manifest.json"
    state_urls = _openstates_urls(openstates_manifest)
    state_paths = sorted(openstates.glob("*.zip"))
    if state_limit is not None:
        state_paths = state_paths[:state_limit]
    inputs.extend(("openstates_bulk", path, state_urls.get(path.name)) for path in state_paths)
    inputs.append(
        (
            "openstates_bulk_manifest",
            openstates_manifest,
            "https://data.openstates.org/",
        )
    )
    if time_machine_root is not None:
        manifest = time_machine_root / "raw/govinfo_bills/manifest.json"
        if manifest.exists():
            inputs.append(("govinfo_bills_text_manifest", manifest, None))
            inputs.extend(_govinfo_cached_inputs(manifest, time_machine_root))
    return [(family, path, url) for family, path, url in inputs if path.exists()]


def _openstates_urls(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    candidates: dict[str, list[str]] = {}
    for row in rows:
        label = str(row.get("text") or "")
        name = re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_") + ".zip"
        candidates.setdefault(name, []).append(str(row.get("href") or ""))
    # A duplicate label caused one known Virginia archive collision.  An
    # ambiguous URL is left null and surfaced by the integrity report.
    return {name: urls[0] for name, urls in candidates.items() if len(urls) == 1 and urls[0]}


def _govinfo_cached_inputs(
    manifest_path: Path, output_root: Path
) -> list[tuple[str, Path, str | None]]:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("artifacts") or payload.get("records") or []
    result: list[tuple[str, Path, str | None]] = []
    seen: set[Path] = set()
    for row in rows:
        relative = row.get("local_path") or row.get("content_path") or row.get("relative_path")
        if not relative:
            continue
        path = Path(str(relative))
        if not path.is_absolute():
            path = output_root / path
        resolved = path.resolve()
        if resolved in seen or not resolved.exists():
            continue
        seen.add(resolved)
        result.append(("govinfo_bills_text", resolved, row.get("source_url")))
    return result


def _load_cached(path: Path) -> dict[str, InventoryEntry]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {row["absolute_path"]: InventoryEntry(**row) for row in payload.get("artifacts", [])}
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return {}


def inventory_inputs(
    source_root: Path,
    output_root: Path,
    *,
    state_limit: int | None = None,
    now: datetime | None = None,
) -> list[InventoryEntry]:
    """Hash every consumed input and persist a resumable inventory manifest."""
    source_root = source_root.resolve()
    output_root = output_root.resolve()
    generated = (now or datetime.now(UTC)).astimezone(UTC)
    manifest_path = output_root / INVENTORY_FILENAME
    cached = _load_cached(manifest_path)
    observed_by_family = _family_observation_times(source_root)
    entries: list[InventoryEntry] = []
    for family, path, source_url in discover_inputs(
        source_root, time_machine_root=output_root, state_limit=state_limit
    ):
        resolved = path.resolve()
        stat = resolved.stat()
        prior = cached.get(str(resolved))
        if (
            prior is not None
            and prior.byte_count == stat.st_size
            and prior.modified_ns == stat.st_mtime_ns
        ):
            digest = prior.content_sha256
        else:
            digest = sha256_file(resolved)
        modified = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        observed = observed_by_family.get(family, modified)
        try:
            relative = str(resolved.relative_to(source_root))
        except ValueError:
            relative = str(resolved.relative_to(output_root))
        media_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        entries.append(
            InventoryEntry(
                source_artifact_id=artifact_id(digest),
                source_family=family,
                relative_path=relative,
                absolute_path=str(resolved),
                source_url=source_url,
                media_type=media_type,
                content_sha256=digest,
                byte_count=stat.st_size,
                modified_at=modified.isoformat().replace("+00:00", "Z"),
                modified_ns=stat.st_mtime_ns,
                observed_at=observed.isoformat().replace("+00:00", "Z"),
            )
        )
    entries.sort(key=lambda row: (row.source_family, row.relative_path))
    output_root.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": generated.isoformat().replace("+00:00", "Z"),
        "source_root": str(source_root),
        "state_limit": state_limit,
        "artifact_count": len(entries),
        "byte_count": sum(row.byte_count for row in entries),
        "artifacts": [asdict(row) for row in entries],
    }
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return entries


def load_inventory(output_root: Path) -> list[InventoryEntry]:
    path = output_root / INVENTORY_FILENAME
    if not path.exists():
        raise FileNotFoundError(f"inventory missing: run inventory first ({path})")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [InventoryEntry(**row) for row in payload["artifacts"]]


def inventory_by_path(entries: list[InventoryEntry]) -> dict[Path, InventoryEntry]:
    return {entry.path.resolve(): entry for entry in entries}


__all__ = [
    "INVENTORY_FILENAME",
    "InventoryEntry",
    "discover_inputs",
    "inventory_inputs",
    "inventory_by_path",
    "load_inventory",
]
