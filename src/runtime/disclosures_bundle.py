"""Disclosure bundle contracts and loaders for offline processing.

Two bundle contracts live here:

1. **Index bundle** (``DisclosuresLookup``) — keyed lookup of live index rows
   used by ``disclosures_index_provider.bundle_index_matches``.  Loaded via
   ``load_disclosures_index_bundle``.

2. **Artifact bundle** (``DisclosuresBundle``) — the primary offline contract.
   Each entry packages enough data to:
     - stage a ``source_artifact`` row
     - locate the local PDF bytes via (local_root / storage_uri)
     - deterministically resolve the filer's bioguide_id via bundled
       official index-row data (chamber-specific fields kept explicit)
   Loaded via ``load_disclosures_bundle``.

Artifact bundle JSON shape
--------------------------
{
    "artifacts": [
        {
            "source_record_id": "12345",
            "chamber": "house",
            "filing_year": 2024,
            "storage_uri": "house/2024/12345.pdf",
            "source_url": "https://disclosures.house.gov/public_disc/financial-pdfs/2024/12345.pdf",
            "source_slug": "house-disclosures",
            "artifact_kind": "pdf",
            "sha256": "<64-char hex>",
            "index_row": {
                "last_name": "Smith",
                "first_name": "John",
                "suffix": "",
                "raw_filing_type": "O",
                "state_dst": "CA08",
                "filing_date": "2024-01-15",
                "doc_id": "12345",
                "filing_kind": "annual"
            }
        },
        {
            "source_record_id": "uuid-xyz",
            "chamber": "senate",
            "filing_year": 2024,
            "storage_uri": "senate/2024/uuid-xyz.pdf",
            "source_url": "https://efdsearch.senate.gov/search/view/paper/uuid-xyz/",
            "source_slug": "senate-disclosures",
            "artifact_kind": "pdf",
            "sha256": "<64-char hex>",
            "index_row": {
                "first_name": "Jane",
                "last_name": "Doe",
                "office": "Senator, TX",
                "report_type": "Annual Report for CY2023",
                "date_filed": "01/15/2024",
                "doc_id": "uuid-xyz"
            }
        }
    ]
}

Chamber determines which index_row fields are required.
Extra keys in index_row are ignored.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from src.core.files import write_text_atomic as _write_text_atomic
from src.core.path_safety import require_confined_relative_path
from src.parse.disclosures.house_index import HouseFilingKind, HouseIndexRow
from src.parse.disclosures.senate_index import SenateIndexRow
from src.parse.disclosures.source_urls import (
    DisclosureSourceChamber,
    validate_official_disclosure_artifact_url,
)
from src.runtime.sources import canonical_source_slug

# Index bundle types (used by disclosures_index_provider)

IndexRow = HouseIndexRow | SenateIndexRow

# (chamber, year) -> {doc_id: IndexRow}
DisclosuresLookup = dict[tuple[str, int], dict[str, IndexRow]]


# Index bundle — row constructors and loaders


def _house_row_from_dict(raw: dict[str, Any]) -> HouseIndexRow:
    return HouseIndexRow(
        last_name=raw["last_name"],
        first_name=raw["first_name"],
        suffix=raw.get("suffix", ""),
        raw_filing_type=raw["raw_filing_type"],
        state_dst=raw["state_dst"],
        year=int(raw["year"]),
        filing_date=date.fromisoformat(raw["filing_date"]),
        doc_id=raw["doc_id"],
        filing_kind=HouseFilingKind(raw["filing_kind"]),
    )


def _senate_row_from_dict(raw: dict[str, Any]) -> SenateIndexRow:
    return SenateIndexRow(
        first_name=raw["first_name"],
        last_name=raw["last_name"],
        office=raw["office"],
        report_type=raw["report_type"],
        date_filed=raw["date_filed"],
        doc_id=raw["doc_id"],
        filing_year=int(raw["filing_year"]),
    )


def disclosures_index_bundle_from_dict(data: dict[str, Any]) -> DisclosuresLookup:
    """Build a DisclosuresLookup from a parsed index-bundle JSON dict.

    Raises ValueError if data is not a dict or a row is malformed.
    """
    if not isinstance(data, dict):
        raise ValueError(
            f"disclosures index bundle must be a JSON object, got {type(data).__name__}"
        )

    lookup: DisclosuresLookup = {}

    for chamber in ("house", "senate"):
        years_data = data.get(chamber)
        if years_data is None:
            continue
        if not isinstance(years_data, dict):
            raise ValueError(
                f"disclosures index bundle[{chamber!r}] must be an object, "
                f"got {type(years_data).__name__}"
            )
        for year_str, docs in years_data.items():
            year = int(year_str)
            key: tuple[str, int] = (chamber, year)
            entries: dict[str, IndexRow] = {}
            if not isinstance(docs, dict):
                raise ValueError(
                    f"disclosures index bundle[{chamber!r}][{year_str!r}] must be an "
                    f"object, got {type(docs).__name__}"
                )
            for doc_id, raw in docs.items():
                if not isinstance(raw, dict):
                    raise ValueError(
                        f"disclosures index bundle[{chamber!r}][{year_str!r}][{doc_id!r}] "
                        f"must be an object, got {type(raw).__name__}"
                    )
                if chamber == "house":
                    entries[doc_id] = _house_row_from_dict(raw)
                else:
                    entries[doc_id] = _senate_row_from_dict(raw)
            lookup[key] = entries

    return lookup


def load_disclosures_index_bundle(path: Path) -> DisclosuresLookup:
    """Read a disclosures index-bundle JSON file and return a DisclosuresLookup.

    Raises FileNotFoundError if the file does not exist.
    Raises ValueError if the JSON shape does not match the expected schema.
    Raises json.JSONDecodeError if the file is not valid JSON.
    """
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(
            f"disclosures index bundle file must contain a JSON object, got {type(data).__name__}"
        )
    return disclosures_index_bundle_from_dict(data)


# Artifact bundle — index-row contracts (chamber-specific, explicit fields)


@dataclass(frozen=True)
class HouseBundledIndexRow:
    """Bundled House disclosure index row for member resolution.

    state_dst carries the combined state abbreviation and zero-padded district
    number (e.g. "CA08") consumed by house_identity() in the resolution layer.
    """

    last_name: str
    first_name: str
    suffix: str  # empty string when absent in the source index
    raw_filing_type: str  # "O", "A", "T", "P" etc.
    state_dst: str  # e.g. "CA08" — state abbrev + zero-padded district
    filing_date: str  # ISO date string "YYYY-MM-DD"
    doc_id: str
    filing_kind: str  # "ptr" | "annual"


@dataclass(frozen=True)
class SenateBundledIndexRow:
    """Bundled Senate disclosure index row for member resolution.

    office carries the full EFD office string (e.g. "Senator, TX") consumed
    by senate_identity() in the resolution layer.
    """

    first_name: str
    last_name: str
    office: str  # e.g. "Senator, TX"
    report_type: str  # e.g. "Annual Report for CY2023"
    date_filed: str  # raw EFD date string, e.g. "01/15/2024"
    doc_id: str


BundledIndexRow = HouseBundledIndexRow | SenateBundledIndexRow


# Artifact bundle — per-entry and container contracts


@dataclass(frozen=True)
class DisclosureArtifactEntry:
    """One artifact entry in a disclosures bundle.

    Carries the minimum fields to:
      - stage a source_artifact row (source_slug, artifact_kind, source_url,
        storage_uri, sha256, source_record_id)
      - locate local bytes via local_root / storage_uri
      - deterministically resolve the filer via index_row
    """

    source_record_id: str
    chamber: str  # "house" | "senate"
    filing_year: int
    storage_uri: str
    source_url: str
    source_slug: str
    artifact_kind: str
    sha256: str  # 64-char hex SHA-256
    index_row: BundledIndexRow


@dataclass(frozen=True)
class DisclosuresBundle:
    """Validated local disclosures artifact bundle ready for offline processing."""

    artifacts: tuple[DisclosureArtifactEntry, ...]


# Artifact bundle — public entry points


def load_disclosures_bundle(path: Path) -> DisclosuresBundle:
    """Read a disclosures artifact bundle JSON file and return a DisclosuresBundle.

    Raises FileNotFoundError if the file does not exist.
    Raises ValueError if the JSON shape does not match the expected schema.
    Raises json.JSONDecodeError if the file is not valid JSON.
    """
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(
            f"disclosures bundle file must contain a JSON object, got {type(data).__name__}"
        )
    return disclosures_bundle_from_dict(data)


def disclosures_bundle_from_dict(data: dict[str, Any]) -> DisclosuresBundle:
    """Build a DisclosuresBundle from a parsed JSON dict.

    Raises ValueError if any required key is absent or any entry is malformed.
    """
    if "artifacts" not in data:
        raise ValueError("disclosures bundle JSON missing required key: 'artifacts'")
    raw_artifacts = data["artifacts"]
    if not isinstance(raw_artifacts, list):
        raise ValueError("'artifacts' must be a list")
    return DisclosuresBundle(
        artifacts=tuple(_parse_entry(raw, i) for i, raw in enumerate(raw_artifacts))
    )


def disclosures_bundle_to_dict(bundle: DisclosuresBundle) -> dict[str, Any]:
    """Return the canonical JSON-serializable dict for a DisclosuresBundle."""
    return {
        "artifacts": [_entry_to_dict(entry) for entry in bundle.artifacts],
    }


def write_disclosures_bundle(path: Path, bundle: DisclosuresBundle) -> Path:
    """Serialize *bundle* to *path* as canonical JSON and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text_atomic(
        path,
        json.dumps(disclosures_bundle_to_dict(bundle), indent=2, sort_keys=True),
    )
    return path


# Artifact bundle — entry and index-row parsing

_VALID_CHAMBERS = {"house", "senate"}
_VALID_ARTIFACT_KINDS = {"pdf", "xml", "csv", "json", "html", "txt", "other"}
_SHA256_LEN = 64
_SHA256_HEX_CHARS = set("0123456789abcdefABCDEF")


def _validate_storage_uri(value: str, idx: int) -> None:
    require_confined_relative_path(value, label=f"artifacts[{idx}].storage_uri")


def _is_sha256_hex(value: str) -> bool:
    return all(char in _SHA256_HEX_CHARS for char in value)


def _parse_entry(raw: Any, idx: int) -> DisclosureArtifactEntry:
    if not isinstance(raw, dict):
        raise ValueError(f"artifacts[{idx}] must be an object, got {type(raw).__name__}")

    source_record_id = _str(raw, "source_record_id", "artifacts", idx)
    chamber = _str(raw, "chamber", "artifacts", idx)
    if chamber not in _VALID_CHAMBERS:
        raise ValueError(f"artifacts[{idx}].chamber must be 'house' or 'senate', got {chamber!r}")
    source_chamber: DisclosureSourceChamber = "house" if chamber == "house" else "senate"
    filing_year = _int(raw, "filing_year", "artifacts", idx)
    storage_uri = _str(raw, "storage_uri", "artifacts", idx)
    _validate_storage_uri(storage_uri, idx)
    source_url = _str(raw, "source_url", "artifacts", idx)
    validate_official_disclosure_artifact_url(
        source_url,
        chamber=source_chamber,
        label=f"artifacts[{idx}].source_url must be an official {chamber} disclosure URL",
    )
    source_slug = canonical_source_slug(_str(raw, "source_slug", "artifacts", idx))
    artifact_kind = _str(raw, "artifact_kind", "artifacts", idx)
    if artifact_kind not in _VALID_ARTIFACT_KINDS:
        raise ValueError(
            f"artifacts[{idx}].artifact_kind must be one of "
            f"{sorted(_VALID_ARTIFACT_KINDS)}, got {artifact_kind!r}"
        )
    sha256 = _str(raw, "sha256", "artifacts", idx)
    if len(sha256) != _SHA256_LEN or not _is_sha256_hex(sha256):
        raise ValueError(
            f"artifacts[{idx}].sha256 must be a {_SHA256_LEN}-char hex string, "
            f"got length {len(sha256)}"
        )

    if "index_row" not in raw:
        raise ValueError(f"artifacts[{idx}] missing required field: 'index_row'")
    raw_index = raw["index_row"]
    if not isinstance(raw_index, dict):
        raise ValueError(
            f"artifacts[{idx}].index_row must be an object, got {type(raw_index).__name__}"
        )

    return DisclosureArtifactEntry(
        source_record_id=source_record_id,
        chamber=chamber,
        filing_year=filing_year,
        storage_uri=storage_uri,
        source_url=source_url,
        source_slug=source_slug,
        artifact_kind=artifact_kind,
        sha256=sha256,
        index_row=_parse_index_row(chamber, raw_index, idx),
    )


def _parse_index_row(chamber: str, raw: dict[str, Any], entry_idx: int) -> BundledIndexRow:
    loc = f"artifacts[{entry_idx}].index_row"
    if chamber == "house":
        suffix_raw = raw.get("suffix", "")
        suffix = suffix_raw if isinstance(suffix_raw, str) else ""
        return HouseBundledIndexRow(
            last_name=_index_str(raw, "last_name", loc),
            first_name=_index_str(raw, "first_name", loc),
            suffix=suffix,
            raw_filing_type=_index_str(raw, "raw_filing_type", loc),
            state_dst=_index_str(raw, "state_dst", loc),
            filing_date=_index_str(raw, "filing_date", loc),
            doc_id=_index_str(raw, "doc_id", loc),
            filing_kind=_index_str(raw, "filing_kind", loc),
        )
    # chamber == "senate" (validated upstream)
    return SenateBundledIndexRow(
        first_name=_index_str(raw, "first_name", loc),
        last_name=_index_str(raw, "last_name", loc),
        office=_index_str(raw, "office", loc),
        report_type=_index_str(raw, "report_type", loc),
        date_filed=_index_str(raw, "date_filed", loc),
        doc_id=_index_str(raw, "doc_id", loc),
    )


def _entry_to_dict(entry: DisclosureArtifactEntry) -> dict[str, Any]:
    return {
        "source_record_id": entry.source_record_id,
        "chamber": entry.chamber,
        "filing_year": entry.filing_year,
        "storage_uri": entry.storage_uri,
        "source_url": entry.source_url,
        "source_slug": entry.source_slug,
        "artifact_kind": entry.artifact_kind,
        "sha256": entry.sha256,
        "index_row": _index_row_to_dict(entry.index_row),
    }


def _index_row_to_dict(row: BundledIndexRow) -> dict[str, Any]:
    if isinstance(row, HouseBundledIndexRow):
        return {
            "last_name": row.last_name,
            "first_name": row.first_name,
            "suffix": row.suffix,
            "raw_filing_type": row.raw_filing_type,
            "state_dst": row.state_dst,
            "filing_date": row.filing_date,
            "doc_id": row.doc_id,
            "filing_kind": row.filing_kind,
        }
    return {
        "first_name": row.first_name,
        "last_name": row.last_name,
        "office": row.office,
        "report_type": row.report_type,
        "date_filed": row.date_filed,
        "doc_id": row.doc_id,
    }


# Typed field extractors (internal)


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
    # Reject bool (a subclass of int in Python) and floats.
    if not isinstance(v, int) or isinstance(v, bool):
        raise ValueError(f"{section}[{idx}].{field} must be an integer, got {type(v).__name__}")
    return v


def _index_str(raw: dict[str, Any], field: str, loc: str) -> str:
    if field not in raw:
        raise ValueError(f"{loc} missing required field: {field!r}")
    v = raw[field]
    if not isinstance(v, str):
        raise ValueError(f"{loc}.{field} must be a string, got {type(v).__name__}")
    return v
