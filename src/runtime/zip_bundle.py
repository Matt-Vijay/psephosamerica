"""ZIP bundle file-loader for publish runs.

Reads a single JSON file produced by the data-prep step and returns a
ZipBundleInputs ready to pass into publish_snapshot_run.

Expected JSON shape
-------------------
{
    "zip5_codes": ["90210", ...],
    "zip_district_rows": [
        {"zip5": "90210", "state": "CA", "district": 30, "population_share": 1.0},
        ...
    ],
    "district_member_rows": [
        {"state": "CA", "district": 30, "bioguide_id": "B001",
         "full_name": "Alice Smith", "party": "D", "slug": "alice-smith"},
        ...
    ],
    "senator_rows": [
        {"state": "CA", "bioguide_id": "S001", "full_name": "Sen One",
         "party": "D", "slug": "sen-one", "seat": 1},
        ...
    ]
}

All four top-level keys must be present.  Extra keys are ignored.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.pipeline.publish_snapshot_run import ZipBundleInputs
from src.zip.resolve import DistrictMemberRow, SenatorRow, ZipDistrictRow


# ---------------------------------------------------------------------------
# Required top-level keys
# ---------------------------------------------------------------------------

_TOP_LEVEL_KEYS = ("zip5_codes", "zip_district_rows", "district_member_rows", "senator_rows")


# ---------------------------------------------------------------------------
# Row constructors with field-level validation
# ---------------------------------------------------------------------------

def _zip_district_row(raw: Any, idx: int) -> ZipDistrictRow:
    _require_dict(raw, "zip_district_rows", idx)
    return ZipDistrictRow(
        zip5=_str(raw, "zip5", "zip_district_rows", idx),
        state=_str(raw, "state", "zip_district_rows", idx),
        district=_int(raw, "district", "zip_district_rows", idx),
        population_share=_float(raw, "population_share", "zip_district_rows", idx),
    )


def _district_member_row(raw: Any, idx: int) -> DistrictMemberRow:
    _require_dict(raw, "district_member_rows", idx)
    return DistrictMemberRow(
        state=_str(raw, "state", "district_member_rows", idx),
        district=_int(raw, "district", "district_member_rows", idx),
        bioguide_id=_str(raw, "bioguide_id", "district_member_rows", idx),
        full_name=_str(raw, "full_name", "district_member_rows", idx),
        party=_str(raw, "party", "district_member_rows", idx),
        slug=_str(raw, "slug", "district_member_rows", idx),
    )


def _senator_row(raw: Any, idx: int) -> SenatorRow:
    _require_dict(raw, "senator_rows", idx)
    return SenatorRow(
        state=_str(raw, "state", "senator_rows", idx),
        bioguide_id=_str(raw, "bioguide_id", "senator_rows", idx),
        full_name=_str(raw, "full_name", "senator_rows", idx),
        party=_str(raw, "party", "senator_rows", idx),
        slug=_str(raw, "slug", "senator_rows", idx),
        seat=_int(raw, "seat", "senator_rows", idx),
    )


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def zip_bundle_from_dict(data: dict[str, Any]) -> ZipBundleInputs:
    """Build ZipBundleInputs from a parsed JSON dict.

    Raises ValueError if any required key is absent or any row is malformed.
    """
    for key in _TOP_LEVEL_KEYS:
        if key not in data:
            raise ValueError(f"zip bundle JSON missing required key: {key!r}")

    zip5_codes = data["zip5_codes"]
    if not isinstance(zip5_codes, list):
        raise ValueError("zip5_codes must be a list")
    for i, z in enumerate(zip5_codes):
        if not isinstance(z, str):
            raise ValueError(f"zip5_codes[{i}] must be a string, got {type(z).__name__}")

    return ZipBundleInputs(
        zip5_codes=list(zip5_codes),
        zip_district_rows=[_zip_district_row(r, i) for i, r in enumerate(data["zip_district_rows"])],
        district_member_rows=[_district_member_row(r, i) for i, r in enumerate(data["district_member_rows"])],
        senator_rows=[_senator_row(r, i) for i, r in enumerate(data["senator_rows"])],
    )


def load_zip_bundle(path: Path) -> ZipBundleInputs:
    """Read a ZIP bundle JSON file and return validated ZipBundleInputs.

    Raises FileNotFoundError if the file does not exist.
    Raises ValueError if the JSON shape does not match the expected schema.
    Raises json.JSONDecodeError if the file is not valid JSON.
    """
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"zip bundle file must contain a JSON object, got {type(data).__name__}")
    return zip_bundle_from_dict(data)


# ---------------------------------------------------------------------------
# Typed field extractors (internal)
# ---------------------------------------------------------------------------

def _require_dict(raw: Any, section: str, idx: int) -> None:
    if not isinstance(raw, dict):
        raise ValueError(f"{section}[{idx}] must be an object, got {type(raw).__name__}")


def _str(raw: dict[str, Any], field: str, section: str, idx: int) -> str:
    if field not in raw:
        raise ValueError(f"{section}[{idx}] missing required field: {field!r}")
    v = raw[field]
    if not isinstance(v, str):
        raise ValueError(f"{section}[{idx}].{field} must be a string, got {type(v).__name__}")
    return v


def _int(raw: dict[str, Any], field: str, section: str, idx: int) -> int:
    if field not in raw:
        raise ValueError(f"{section}[{idx}] missing required field: {field!r}")
    v = raw[field]
    # JSON integers arrive as int; reject float even if whole-valued.
    if not isinstance(v, int) or isinstance(v, bool):
        raise ValueError(f"{section}[{idx}].{field} must be an integer, got {type(v).__name__}")
    return v


def _float(raw: dict[str, Any], field: str, section: str, idx: int) -> float:
    if field not in raw:
        raise ValueError(f"{section}[{idx}] missing required field: {field!r}")
    v = raw[field]
    if isinstance(v, bool):
        raise ValueError(f"{section}[{idx}].{field} must be a number, got bool")
    if not isinstance(v, (int, float)):
        raise ValueError(f"{section}[{idx}].{field} must be a number, got {type(v).__name__}")
    return float(v)
