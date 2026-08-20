"""Build and audit the one pilot's bounded, row-faithful OpenStates cases."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.transfer_eval.contract import CONTRACT_VERSION

PILOT_ROOT = Path(__file__).with_name("pilot")
CASES_ROOT = PILOT_ROOT / "cases"

_MEMBERS = {
    "bills": "_bills.csv",
    "bill_actions": "_bill_actions.csv",
    "bill_sources": "_bill_sources.csv",
    "votes": "_votes.csv",
    "vote_people": "_vote_people.csv",
    "vote_counts": "_vote_counts.csv",
    "vote_sources": "_vote_sources.csv",
    "organizations": "_organizations.csv",
}
_CASE_REQUIRED = {
    "README",
    "SOURCE.json",
    *(f"{key}.csv" for key in _MEMBERS if key != "organizations"),
}


@dataclass(frozen=True)
class ArtifactSelection:
    inventory_name: str
    label: str
    bill_ids: tuple[str, ...] = ()
    vote_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PilotCase:
    case_id: str
    cutoff: str
    split: str
    artifacts: tuple[ArtifactSelection, ...]


PILOT_CASES = (
    PilotCase(
        "ga_hb579_cutoff",
        "2026-06-30T23:59:59Z",
        "public",
        (
            ArtifactSelection(
                "Georgia_2025_2026_Regular_Session.zip",
                "ga_2025_26",
                ("ocd-bill/b37a923c-e7f8-4f09-8497-9b9417eef7a6",),
            ),
        ),
    ),
    PilotCase(
        "mn_hf3718_cutoff",
        "2026-06-26T17:00:00Z",
        "hidden",
        (
            ArtifactSelection(
                "Minnesota_2025_2026_Regular_Session.zip",
                "mn_2025_26",
                ("ocd-bill/30535aa1-9373-49f0-8a5e-811a8534dcc7",),
            ),
        ),
    ),
    PilotCase(
        "nc_reused_vote_id",
        "2026-06-25T12:00:00Z",
        "hidden",
        (
            ArtifactSelection(
                "North_Carolina_2023_2024_Session.zip",
                "nc_2023",
                ("ocd-bill/d5a239b6-f32a-4621-983e-5ecc1ac71ee4",),
                ("ocd-vote/016e5383-fb49-445a-bf95-ccc9b3e845a8",),
            ),
            ArtifactSelection(
                "North_Carolina_2025_2026_Session.zip",
                "nc_2025",
                ("ocd-bill/272a294d-2a9e-415b-99c6-f0e0d910696a",),
                ("ocd-vote/016e5383-fb49-445a-bf95-ccc9b3e845a8",),
            ),
        ),
    ),
    PilotCase(
        "mo_missing_org",
        "2026-06-27T00:00:00Z",
        "hidden",
        (ArtifactSelection("Missouri_2017_First_Extraordinary_Session.zip", "mo_2017s1"),),
    ),
)


def case_path(case: PilotCase, root: Path = CASES_ROOT) -> Path:
    return root / case.split / case.case_id


def load_request(path: Path) -> dict[str, Any]:
    mode = path.lstat().st_mode
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ValueError(f"case request must be a regular file: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("contract_version") != CONTRACT_VERSION:
        raise ValueError(f"invalid case request: {path}")
    return value


def build_cases(inventory_path: Path, output_root: Path = CASES_ROOT) -> list[Path]:
    """Materialize every fixed case from inventory-verified local parent ZIPs."""
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    entries = {Path(str(row["absolute_path"])).name: row for row in inventory.get("artifacts", [])}
    built: list[Path] = []
    for case in PILOT_CASES:
        destination = case_path(case, output_root)
        if destination.exists() and any(destination.iterdir()):
            raise FileExistsError(f"refusing to overwrite case: {destination}")
        destination.mkdir(parents=True, exist_ok=True)
        request_artifacts: list[dict[str, Any]] = []
        for selection in case.artifacts:
            entry = entries.get(selection.inventory_name)
            if entry is None:
                raise FileNotFoundError(f"inventory lacks {selection.inventory_name}")
            artifact_dir = destination / "input" / selection.label
            request_artifacts.append(_extract_selection(selection, entry, artifact_dir))
        request = {
            "contract_version": CONTRACT_VERSION,
            "case_id": (
                case.case_id
                if case.split == "public"
                else f"case-{hashlib.sha256(case.case_id.encode()).hexdigest()[:12]}"
            ),
            "cutoff": case.cutoff,
            "artifacts": request_artifacts,
        }
        _write_json(destination / "request.json", request)
        built.append(destination)
    return built


def _extract_selection(
    selection: ArtifactSelection, entry: dict[str, Any], destination: Path
) -> dict[str, Any]:
    parent = Path(str(entry["absolute_path"]))
    if _sha256_file(parent) != entry["content_sha256"]:
        raise ValueError(f"parent hash differs from inventory: {parent}")
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(parent) as archive:
        member_names = _member_names(archive)
        readme_name = member_names.pop("README", None)
        if readme_name is not None:
            (destination / "README").write_bytes(archive.read(readme_name))
        tables = {
            key: _read_csv(archive, member)
            for key, member in member_names.items()
            if member is not None
        }
        select_all = not selection.bill_ids
        selected_bills = set(selection.bill_ids)
        if select_all:
            selected_bills = {row.get("id", "") for row in tables.get("bills", ([], []))[1]}
        votes = [
            row
            for row in tables.get("votes", ([], []))[1]
            if select_all
            or (bool(selection.vote_ids) and row.get("id") in selection.vote_ids)
            or (not selection.vote_ids and row.get("bill_id") in selected_bills)
        ]
        selected_votes = {row.get("id", "") for row in votes}
        selected_orgs = {
            row.get("organization_id", "")
            for key in ("bill_actions",)
            for row in tables.get(key, ([], []))[1]
            if row.get("bill_id") in selected_bills
        }
        selected_orgs.update(row.get("organization_id", "") for row in votes)
        filters = {
            "bills": lambda row: row.get("id") in selected_bills,
            "bill_actions": lambda row: row.get("bill_id") in selected_bills,
            "bill_sources": lambda row: row.get("bill_id") in selected_bills,
            "votes": lambda row: row.get("id") in selected_votes,
            "vote_people": lambda row: row.get("vote_event_id") in selected_votes,
            "vote_counts": lambda row: row.get("vote_event_id") in selected_votes,
            "vote_sources": lambda row: row.get("vote_event_id") in selected_votes,
            "organizations": lambda row: row.get("id") in selected_orgs,
        }
        original_members: dict[str, str] = {}
        for key, (fieldnames, rows) in tables.items():
            chosen = [row for row in rows if filters[key](row)]
            _write_csv(destination / f"{key}.csv", fieldnames, chosen)
            original_member = member_names[key]
            if original_member is None:
                raise AssertionError(f"missing original member for {key}")
            original_members[f"{key}.csv"] = original_member

        context = _case_context(destination)
        source = {
            "format": "openstates-csv-row-filter-v0",
            "parent": {
                "source_id": entry["source_artifact_id"],
                "url": entry["source_url"],
                "sha256": entry["content_sha256"],
                "byte_count": entry["byte_count"],
                "observed_at": entry["observed_at"],
                "generated_at": context["generated_at"],
                "inventory_path": entry["relative_path"],
            },
            "members": original_members,
            "fixture_sha256": _fixture_digest(destination),
        }
        _write_json(destination / "SOURCE.json", source)
    return {
        "source_id": entry["source_artifact_id"],
        "path": f"input/{selection.label}",
        "source_url": entry["source_url"],
        "content_sha256": entry["content_sha256"],
        "byte_count": entry["byte_count"],
        "available_at": entry["observed_at"],
        "jurisdiction_id": context["jurisdiction_id"],
        "session": context["session"],
    }


def audit_case(case_dir: Path) -> dict[str, Any]:
    """Verify public metadata, fixture hashes, and regular-file boundaries."""
    request, _, artifact_dirs = _case_layout(case_dir)
    checked = 0
    for artifact, artifact_dir in zip(request["artifacts"], artifact_dirs, strict=True):
        source = json.loads((artifact_dir / "SOURCE.json").read_text(encoding="utf-8"))
        parent = source["parent"]
        expected = {
            "source_id": artifact["source_id"],
            "url": artifact["source_url"],
            "sha256": artifact["content_sha256"],
            "byte_count": artifact["byte_count"],
            "observed_at": artifact["available_at"],
        }
        if any(parent.get(key) != value for key, value in expected.items()):
            raise ValueError(f"SOURCE.json disagrees with request in {artifact_dir}")
        if source.get("fixture_sha256") != _fixture_digest(artifact_dir):
            raise ValueError(f"fixture hash mismatch: {artifact_dir}")
        for item in artifact_dir.iterdir():
            mode = item.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise ValueError(f"non-regular fixture path: {item}")
        checked += 1
    return {"case_id": request["case_id"], "artifacts": checked, "status": "PASS"}


def case_input_files(case_dir: Path) -> tuple[Path, ...]:
    """Return the exact audited files that may be mounted for a candidate."""
    audit_case(case_dir)
    return _case_layout(case_dir)[1]


def audit_public_bundle(bundle: Path) -> dict[str, Any]:
    """Fail if a cleanroom bundle contains anything outside the public allow-list."""
    case_dir = bundle / "case"
    audit_case(case_dir)
    allowed_files = {
        "TASK.md",
        "schema.json",
        "solution.ts",
        "case/request.json",
        "case/dev_expected.jsonl",
    }
    allowed_files.update(path.relative_to(bundle).as_posix() for path in case_input_files(case_dir))
    allowed_dirs = {str(Path(rel).parent) for rel in allowed_files if str(Path(rel).parent) != "."}
    allowed_dirs.update({"case", "case/input"})
    actual_files: set[str] = set()
    actual_dirs: set[str] = set()
    for path in bundle.rglob("*"):
        rel = path.relative_to(bundle).as_posix()
        if path.is_symlink():
            raise ValueError(f"public bundle contains symlink: {rel}")
        if path.is_file():
            actual_files.add(rel)
        elif path.is_dir():
            actual_dirs.add(rel)
        else:
            raise ValueError(f"public bundle contains special path: {rel}")
    if actual_files != allowed_files or actual_dirs != allowed_dirs:
        raise ValueError(
            f"public allow-list mismatch: files={sorted(actual_files ^ allowed_files)}, "
            f"dirs={sorted(actual_dirs ^ allowed_dirs)}"
        )
    forbidden = (b"time_machine.duckdb", b"source_artifacts.parquet", b".git/")
    for rel in actual_files:
        payload = (bundle / rel).read_bytes().lower()
        if any(token in payload for token in forbidden):
            raise ValueError(f"private marker in public bundle: {rel}")
    return {"files": len(actual_files), "status": "PASS"}


def _case_layout(case_dir: Path) -> tuple[dict[str, Any], tuple[Path, ...], tuple[Path, ...]]:
    request = load_request(case_dir / "request.json")
    input_path = case_dir / "input"
    try:
        input_metadata = input_path.lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"case input directory missing: {input_path}") from exc
    if not stat.S_ISDIR(input_metadata.st_mode) or input_path.is_symlink():
        raise ValueError(f"case input must be a non-symlink directory: {input_path}")
    input_root = input_path.resolve()
    allowed: set[Path] = set()
    artifact_dirs: list[Path] = []
    for artifact in request.get("artifacts", []):
        relative = Path(str(artifact.get("path", "")))
        artifact_dir = (case_dir / relative).resolve()
        if relative.is_absolute() or not artifact_dir.is_relative_to(input_root):
            raise ValueError("artifact path escapes case input directory")
        artifact_dirs.append(artifact_dir)
        for name in _CASE_REQUIRED:
            path = artifact_dir / name
            if not path.is_file() or path.is_symlink():
                raise ValueError(f"required regular case file missing: {path}")
            allowed.add(path)
        optional = artifact_dir / "organizations.csv"
        if optional.exists():
            if not optional.is_file() or optional.is_symlink():
                raise ValueError(f"invalid optional case file: {optional}")
            allowed.add(optional)
    actual: set[Path] = set()
    for path in input_root.rglob("*"):
        if path.is_symlink() or (not path.is_file() and not path.is_dir()):
            raise ValueError(f"case input contains non-regular path: {path}")
        if path.is_file():
            actual.add(path.resolve())
    if actual != allowed:
        raise ValueError(f"case input allow-list mismatch: {sorted(map(str, actual ^ allowed))}")
    return request, tuple(sorted(allowed)), tuple(artifact_dirs)


def copy_public_case(destination: Path) -> None:
    source = case_path(PILOT_CASES[0])
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite: {destination}")
    shutil.copytree(source, destination, symlinks=True)


def _member_names(archive: zipfile.ZipFile) -> dict[str, str | None]:
    result: dict[str, str | None] = {key: None for key in _MEMBERS}
    result["README"] = None
    for name in archive.namelist():
        if name.rsplit("/", 1)[-1].upper() == "README":
            result["README"] = name
        for key, suffix in _MEMBERS.items():
            if name.endswith(suffix) and result[key] is None:
                result[key] = name
    required = set(_MEMBERS).difference({"organizations"})
    missing = sorted(key for key in required if result[key] is None)
    if missing:
        raise ValueError(f"OpenStates archive missing required members: {missing}")
    return result


def _read_csv(
    archive: zipfile.ZipFile, member: str | None
) -> tuple[list[str], list[dict[str, str]]]:
    if member is None:
        return [], []
    with archive.open(member) as raw:
        reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
        return list(reader.fieldnames or ()), list(reader)


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _case_context(directory: Path) -> dict[str, str]:
    readme: dict[str, str] = {}
    readme_path = directory / "README"
    if readme_path.exists():
        for line in readme_path.read_text(encoding="utf-8", errors="replace").splitlines():
            key, marker, value = line.partition(":")
            if marker:
                readme[key.strip().lower()] = value.strip()
    state = readme.get("state", "").lower()
    session = readme.get("session", "")
    jurisdictions: set[str] = set()
    organizations = directory / "organizations.csv"
    if organizations.exists():
        with organizations.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("jurisdiction_id"):
                    jurisdictions.add(row["jurisdiction_id"].strip())
    if not state or not session:
        raise ValueError(f"README lacks state/session in {directory}")
    division = "district" if state == "dc" else "state"
    jurisdiction = (
        sorted(jurisdictions)[0]
        if jurisdictions
        else f"ocd-jurisdiction/country:us/{division}:{state}/government"
    )
    return {
        "jurisdiction_id": jurisdiction,
        "session": session,
        "generated_at": readme.get("generated at", ""),
    }


def _fixture_digest(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if path.name == "SOURCE.json":
            continue
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


__all__ = [
    "CASES_ROOT",
    "PILOT_CASES",
    "PILOT_ROOT",
    "PilotCase",
    "audit_case",
    "audit_public_bundle",
    "build_cases",
    "case_input_files",
    "case_path",
    "copy_public_case",
    "load_request",
]
