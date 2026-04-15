"""Reusable temp disclosure bundle fixture support.

Generates deterministic, self-contained disclosure bundle fixtures from
inline JSON/text data.  No network calls, no binary blobs.

Public API
----------
BundleArtifactSpec
    Typed spec for one artifact entry in a bundle.

DisclosureBundleFixture
    Result handle: bundle_json_path, local_root, artifact_paths, index_rows.

make_house_spec(source_record_id, ...)  -> BundleArtifactSpec
make_senate_spec(source_record_id, ...) -> BundleArtifactSpec
build_bundle_fixture(tmp_path, specs)   -> DisclosureBundleFixture

Placeholder artifact bytes are plain ASCII so no binary blobs enter the
test suite.  SHA-256 digests are computed from the actual placeholder
content, so the fixture passes real SHA-256 verification.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Spec types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BundleArtifactSpec:
    """Typed spec for one artifact entry in a disclosure bundle fixture.

    Callers normally build specs via ``make_house_spec`` or
    ``make_senate_spec`` rather than constructing this directly.
    """

    source_record_id: str
    chamber: str           # "house" | "senate"
    filing_year: int
    storage_uri: str       # relative path within local_root
    source_url: str
    source_slug: str
    index_row: dict[str, Any]   # raw index_row dict (chamber-specific fields)
    artifact_kind: str = "pdf"


# ---------------------------------------------------------------------------
# Fixture result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DisclosureBundleFixture:
    """Temp disclosure bundle fixture.

    Attributes
    ----------
    bundle_json_path:
        Absolute path to the written bundle JSON file.  Can be passed
        directly to ``load_disclosures_bundle``.
    local_root:
        Root directory under which all artifact files live.  Pass this
        as ``local_root`` when using ``entry_local_path`` / ``read_entry_bytes``.
    artifact_paths:
        Mapping from ``source_record_id`` to the absolute path of that
        artifact file, for tests that need to inspect or replace bytes.
    index_rows:
        The raw ``index_row`` dicts from the bundle, in the same order as
        the input specs.  Useful for assertions without re-parsing the JSON.
    """

    bundle_json_path: Path
    local_root: Path
    artifact_paths: dict[str, Path]     # source_record_id -> path
    index_rows: list[dict[str, Any]]    # one per artifact, same order as specs


# ---------------------------------------------------------------------------
# Placeholder byte generator
# ---------------------------------------------------------------------------

_PLACEHOLDER_TEMPLATE = "disclosure-artifact placeholder: {source_record_id}\n"


def _placeholder_bytes(source_record_id: str) -> bytes:
    """Return deterministic placeholder bytes for a given record ID.

    Using plain ASCII so no binary blobs enter the test suite.
    """
    return _PLACEHOLDER_TEMPLATE.format(source_record_id=source_record_id).encode()


# ---------------------------------------------------------------------------
# Convenience spec constructors
# ---------------------------------------------------------------------------


def make_house_spec(
    source_record_id: str,
    *,
    filing_year: int = 2024,
    last_name: str = "Smith",
    first_name: str = "John",
    suffix: str = "",
    raw_filing_type: str = "O",
    state_dst: str = "CA08",
    filing_date: str = "2024-01-15",
    filing_kind: str = "annual",
    source_slug: str = "house_disclosures",
) -> BundleArtifactSpec:
    """Return a BundleArtifactSpec for a House disclosure artifact."""
    storage_uri = f"house/{filing_year}/{source_record_id}.pdf"
    source_url = (
        f"https://disclosures.house.gov/public_disc/ptr-pdfs/"
        f"{filing_year}/{source_record_id}.pdf"
    )
    index_row: dict[str, Any] = {
        "last_name": last_name,
        "first_name": first_name,
        "suffix": suffix,
        "raw_filing_type": raw_filing_type,
        "state_dst": state_dst,
        "filing_date": filing_date,
        "doc_id": source_record_id,
        "filing_kind": filing_kind,
    }
    return BundleArtifactSpec(
        source_record_id=source_record_id,
        chamber="house",
        filing_year=filing_year,
        storage_uri=storage_uri,
        source_url=source_url,
        source_slug=source_slug,
        index_row=index_row,
        artifact_kind="pdf",
    )


def make_senate_spec(
    source_record_id: str,
    *,
    filing_year: int = 2024,
    last_name: str = "Doe",
    first_name: str = "Jane",
    office: str = "Senator, TX",
    report_type: str = "Annual Report for CY2023",
    date_filed: str = "01/15/2024",
    source_slug: str = "senate_disclosures",
) -> BundleArtifactSpec:
    """Return a BundleArtifactSpec for a Senate disclosure artifact."""
    storage_uri = f"senate/{filing_year}/{source_record_id}.pdf"
    source_url = (
        f"https://efdsearch.senate.gov/search/view/paper/{source_record_id}/"
    )
    index_row: dict[str, Any] = {
        "first_name": first_name,
        "last_name": last_name,
        "office": office,
        "report_type": report_type,
        "date_filed": date_filed,
        "doc_id": source_record_id,
        "filing_year": filing_year,
    }
    return BundleArtifactSpec(
        source_record_id=source_record_id,
        chamber="senate",
        filing_year=filing_year,
        storage_uri=storage_uri,
        source_url=source_url,
        source_slug=source_slug,
        index_row=index_row,
        artifact_kind="pdf",
    )


# ---------------------------------------------------------------------------
# Fixture builder
# ---------------------------------------------------------------------------


def build_bundle_fixture(
    tmp_path: Path,
    specs: list[BundleArtifactSpec],
) -> DisclosureBundleFixture:
    """Generate a disclosure bundle fixture in *tmp_path*.

    For each spec:
    - writes a placeholder artifact file at ``tmp_path / spec.storage_uri``
    - computes the real SHA-256 of that placeholder content
    - adds the artifact entry to the bundle JSON

    The bundle JSON is written to ``tmp_path / "bundle.json"``.

    Parameters
    ----------
    tmp_path:
        Directory to write all fixture files into (typically pytest's
        ``tmp_path`` fixture or a sub-directory thereof).
    specs:
        Ordered list of ``BundleArtifactSpec`` objects.  An empty list
        produces a valid bundle JSON with zero artifacts.

    Returns
    -------
    DisclosureBundleFixture
        Handle with all generated paths and the raw index_row dicts.
    """
    artifact_entries: list[dict[str, Any]] = []
    artifact_paths: dict[str, Path] = {}
    index_rows: list[dict[str, Any]] = []

    for spec in specs:
        # Write placeholder artifact bytes.
        data = _placeholder_bytes(spec.source_record_id)
        artifact_file = tmp_path / spec.storage_uri
        artifact_file.parent.mkdir(parents=True, exist_ok=True)
        artifact_file.write_bytes(data)

        sha256 = hashlib.sha256(data).hexdigest()
        artifact_paths[spec.source_record_id] = artifact_file
        index_rows.append(spec.index_row)

        artifact_entries.append(
            {
                "source_record_id": spec.source_record_id,
                "chamber": spec.chamber,
                "filing_year": spec.filing_year,
                "storage_uri": spec.storage_uri,
                "source_url": spec.source_url,
                "source_slug": spec.source_slug,
                "artifact_kind": spec.artifact_kind,
                "sha256": sha256,
                "index_row": spec.index_row,
            }
        )

    bundle_data: dict[str, Any] = {"artifacts": artifact_entries}
    bundle_json_path = tmp_path / "bundle.json"
    bundle_json_path.write_text(json.dumps(bundle_data, indent=2), encoding="utf-8")

    return DisclosureBundleFixture(
        bundle_json_path=bundle_json_path,
        local_root=tmp_path,
        artifact_paths=artifact_paths,
        index_rows=index_rows,
    )
