"""Validate taxonomy artifacts: sectors.yaml, committee_sector_map.csv, crp_to_sector.csv."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

ALLOWED_MAPPING_TIERS = {"deterministic", "review_required", "out_of_scope"}

REQUIRED_SECTOR_FIELDS = {"sector_id", "label", "description"}

REQUIRED_COMMITTEE_MAP_COLUMNS = {
    "congress",
    "chamber",
    "committee_name",
    "sector_id",
    "mapping_tier",
    "jurisdiction_basis",
}

REQUIRED_CRP_CROSSWALK_COLUMNS = {
    "crp_category",
    "crp_label",
    "sector_id",
    "mapping_tier",
}


class ValidationError:
    def __init__(self, artifact: str, message: str) -> None:
        self.artifact = artifact
        self.message = message

    def __repr__(self) -> str:
        return f"ValidationError({self.artifact!r}, {self.message!r})"


def _load_yaml(path: Path) -> Any:
    with open(path) as f:
        return yaml.safe_load(f)


def _load_csv_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    return fieldnames, rows


def validate_sectors_yaml(path: Path) -> tuple[set[str], list[ValidationError]]:
    errors: list[ValidationError] = []
    sector_ids: set[str] = set()
    name = "sectors.yaml"

    data = _load_yaml(path)

    if not isinstance(data, dict):
        errors.append(ValidationError(name, "Root must be a mapping"))
        return sector_ids, errors

    for field in ("version", "taxonomy_name", "sectors"):
        if field not in data:
            errors.append(ValidationError(name, f"Missing required field: {field}"))

    sectors = data.get("sectors")
    if not isinstance(sectors, list):
        errors.append(ValidationError(name, "'sectors' must be a list"))
        return sector_ids, errors

    seen: set[str] = set()
    for i, sector in enumerate(sectors):
        if not isinstance(sector, dict):
            errors.append(ValidationError(name, f"Sector at index {i} is not a mapping"))
            continue
        missing = REQUIRED_SECTOR_FIELDS - sector.keys()
        if missing:
            errors.append(
                ValidationError(name, f"Sector at index {i} missing fields: {sorted(missing)}")
            )
        sid = sector.get("sector_id")
        if sid is None:
            continue
        if sid in seen:
            errors.append(ValidationError(name, f"Duplicate sector_id: {sid}"))
        seen.add(sid)
        sector_ids.add(sid)

    return sector_ids, errors


def validate_committee_sector_map(path: Path, valid_sector_ids: set[str]) -> list[ValidationError]:
    errors: list[ValidationError] = []
    name = "committee_sector_map.csv"

    fieldnames, rows = _load_csv_rows(path)
    missing_cols = REQUIRED_COMMITTEE_MAP_COLUMNS - set(fieldnames)
    if missing_cols:
        errors.append(ValidationError(name, f"Missing columns: {sorted(missing_cols)}"))
        return errors

    for i, row in enumerate(rows, start=2):
        sid = row.get("sector_id", "").strip()
        if sid and sid not in valid_sector_ids:
            errors.append(ValidationError(name, f"Row {i}: unknown sector_id '{sid}'"))

        tier = row.get("mapping_tier", "").strip()
        if tier and tier not in ALLOWED_MAPPING_TIERS:
            errors.append(ValidationError(name, f"Row {i}: invalid mapping_tier '{tier}'"))

        basis = row.get("jurisdiction_basis", "").strip()
        if not basis:
            errors.append(ValidationError(name, f"Row {i}: empty jurisdiction_basis"))

    return errors


def validate_crp_crosswalk(path: Path, valid_sector_ids: set[str]) -> list[ValidationError]:
    errors: list[ValidationError] = []
    name = "crp_to_sector.csv"

    fieldnames, rows = _load_csv_rows(path)
    missing_cols = REQUIRED_CRP_CROSSWALK_COLUMNS - set(fieldnames)
    if missing_cols:
        errors.append(ValidationError(name, f"Missing columns: {sorted(missing_cols)}"))
        return errors

    for i, row in enumerate(rows, start=2):
        sid = row.get("sector_id", "").strip()
        if sid and sid not in valid_sector_ids:
            errors.append(ValidationError(name, f"Row {i}: unknown sector_id '{sid}'"))

        tier = row.get("mapping_tier", "").strip()
        if tier and tier not in ALLOWED_MAPPING_TIERS:
            errors.append(ValidationError(name, f"Row {i}: invalid mapping_tier '{tier}'"))

    return errors


def validate_all(data_root: Path) -> list[ValidationError]:
    """Run all taxonomy validations. Returns a list of errors (empty = pass)."""
    sectors_path = data_root / "taxonomy" / "sectors.yaml"
    committee_map_path = data_root / "taxonomy" / "committee_sector_map.csv"
    crp_path = data_root / "crosswalks" / "crp_to_sector.csv"

    all_errors: list[ValidationError] = []

    sector_ids, sector_errors = validate_sectors_yaml(sectors_path)
    all_errors.extend(sector_errors)

    if committee_map_path.exists():
        all_errors.extend(validate_committee_sector_map(committee_map_path, sector_ids))

    if crp_path.exists():
        all_errors.extend(validate_crp_crosswalk(crp_path, sector_ids))

    return all_errors


if __name__ == "__main__":
    import sys

    root = Path(__file__).resolve().parents[2] / "data"
    errors = validate_all(root)
    if errors:
        for e in errors:
            print(f"  FAIL  {e.artifact}: {e.message}", file=sys.stderr)
        sys.exit(1)
    else:
        print("All taxonomy validations passed.")
