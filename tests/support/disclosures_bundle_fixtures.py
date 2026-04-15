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

Realistic text payload helpers (generate plain-ASCII text resembling
extracted disclosure PDF content; no binary blobs):

make_house_ptr_text(source_record_id, ...)  -> bytes
make_senate_annual_text(source_record_id, ...) -> bytes

Convenience multi-artifact fixture:

build_house_senate_fixture(tmp_path, ...) -> DisclosureBundleFixture
    Two-artifact fixture: one House PTR + one Senate annual.

SHA-256 digests are computed from the actual artifact content written to
disk, so the fixture passes real SHA-256 verification.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
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

    When ``text_payload`` is supplied it is used verbatim as the artifact
    file content (must be bytes).  When None a deterministic placeholder is
    generated from the source_record_id.
    """

    source_record_id: str
    chamber: str           # "house" | "senate"
    filing_year: int
    storage_uri: str       # relative path within local_root
    source_url: str
    source_slug: str
    index_row: dict[str, Any]   # raw index_row dict (chamber-specific fields)
    artifact_kind: str = "pdf"
    text_payload: bytes | None = None  # explicit content; None → placeholder


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
    sha256s:
        Mapping from ``source_record_id`` to the computed SHA-256 hex digest
        of the content actually written to disk.
    """

    bundle_json_path: Path
    local_root: Path
    artifact_paths: dict[str, Path]     # source_record_id -> path
    index_rows: list[dict[str, Any]]    # one per artifact, same order as specs
    sha256s: dict[str, str] = field(default_factory=dict)  # source_record_id -> hex


# ---------------------------------------------------------------------------
# Placeholder and realistic text payload generators
# ---------------------------------------------------------------------------

_PLACEHOLDER_TEMPLATE = "disclosure-artifact placeholder: {source_record_id}\n"


def _placeholder_bytes(source_record_id: str) -> bytes:
    """Return deterministic placeholder bytes for a given record ID.

    Using plain ASCII so no binary blobs enter the test suite.
    """
    return _PLACEHOLDER_TEMPLATE.format(source_record_id=source_record_id).encode()


def make_house_ptr_text(
    source_record_id: str,
    *,
    last_name: str = "Smith",
    first_name: str = "John",
    state_dst: str = "CA08",
    filing_date: str = "01/15/2024",
    filing_year: int = 2024,
    transactions: list[dict[str, Any]] | None = None,
) -> bytes:
    """Generate realistic plain-text content for a House PTR disclosure.

    Produces multi-line ASCII text resembling the output of text extraction
    from a real House Periodic Transaction Report PDF.  Suitable as a
    ``text_payload`` in BundleArtifactSpec.

    Transactions, if supplied, are each expected to have keys: date, ticker,
    asset_name, transaction_type, amount.
    """
    default_txns: list[dict[str, Any]] = [
        {
            "date": "01/10/2024",
            "ticker": "AAPL",
            "asset_name": "Apple Inc. Common Stock",
            "transaction_type": "Purchase",
            "amount": "$1,001 - $15,000",
        },
    ]
    txns = transactions if transactions is not None else default_txns

    lines: list[str] = [
        "U.S. House of Representatives",
        "Financial Disclosure Report",
        f"Name: {last_name}, {first_name}",
        f"State/District: {state_dst}",
        f"Filing Date: {filing_date}",
        f"Calendar Year: {filing_year}",
        f"Document ID: {source_record_id}",
        "Report Type: Periodic Transaction Report (ptr)",
        "",
        "TRANSACTIONS",
        "Date       Ticker  Asset Name                       Type      Amount",
        "-" * 70,
    ]
    for txn in txns:
        lines.append(
            f"{txn['date']}  {txn['ticker']:<6}  {txn['asset_name']:<32} "
            f"{txn['transaction_type']:<10}  {txn['amount']}"
        )
    lines += [
        "",
        "I certify that the statements I have made on this form and all",
        "attached schedules are true, complete, and correct.",
        "",
        f"Signature: {first_name} {last_name}",
    ]
    return "\n".join(lines).encode("ascii", errors="replace")


def make_senate_annual_text(
    source_record_id: str,
    *,
    last_name: str = "Doe",
    first_name: str = "Jane",
    office: str = "Senator, TX",
    report_type: str = "Annual Report for CY2023",
    date_filed: str = "01/15/2024",
    holdings: list[dict[str, Any]] | None = None,
) -> bytes:
    """Generate realistic plain-text content for a Senate annual disclosure.

    Produces multi-line ASCII text resembling the output of text extraction
    from a real Senate Annual Financial Disclosure PDF.  Suitable as a
    ``text_payload`` in BundleArtifactSpec.

    Holdings, if supplied, are each expected to have keys: asset_name,
    asset_type, value_range, income_type, income_amount.
    """
    default_holdings: list[dict[str, Any]] = [
        {
            "asset_name": "U.S. Treasury Notes",
            "asset_type": "Government Security",
            "value_range": "$15,001 - $50,000",
            "income_type": "Interest",
            "income_amount": "$201 - $1,000",
        },
    ]
    holdings_data = holdings if holdings is not None else default_holdings

    lines: list[str] = [
        "United States Senate",
        "Financial Disclosure Report",
        f"Senator: {first_name} {last_name}",
        f"Office: {office}",
        f"Report Type: {report_type}",
        f"Date Filed: {date_filed}",
        f"Document ID: {source_record_id}",
        "",
        "PART III - ASSETS AND UNEARNED INCOME",
        "Asset Name                    Type                Value Range         Income",
        "-" * 80,
    ]
    for h in holdings_data:
        lines.append(
            f"{h['asset_name']:<30} {h['asset_type']:<20} {h['value_range']:<20}"
            f" {h.get('income_type', '')} {h.get('income_amount', '')}"
        )
    lines += [
        "",
        "I certify that the information contained herein is true, accurate,",
        "and complete to the best of my knowledge and belief.",
        "",
        f"Signature: {first_name} {last_name}",
        f"Date: {date_filed}",
    ]
    return "\n".join(lines).encode("ascii", errors="replace")


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
    realistic_text: bool = False,
) -> BundleArtifactSpec:
    """Return a BundleArtifactSpec for a House disclosure artifact.

    When ``realistic_text`` is True the artifact content is generated by
    ``make_house_ptr_text`` rather than a short placeholder, making the
    fixture closer to what real text-extraction would produce.
    """
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
    text_payload: bytes | None = None
    if realistic_text:
        text_payload = make_house_ptr_text(
            source_record_id,
            last_name=last_name,
            first_name=first_name,
            state_dst=state_dst,
            filing_date=filing_date.replace("-", "/"),
            filing_year=filing_year,
        )
    return BundleArtifactSpec(
        source_record_id=source_record_id,
        chamber="house",
        filing_year=filing_year,
        storage_uri=storage_uri,
        source_url=source_url,
        source_slug=source_slug,
        index_row=index_row,
        artifact_kind="pdf",
        text_payload=text_payload,
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
    realistic_text: bool = False,
) -> BundleArtifactSpec:
    """Return a BundleArtifactSpec for a Senate disclosure artifact.

    When ``realistic_text`` is True the artifact content is generated by
    ``make_senate_annual_text`` rather than a short placeholder.
    """
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
    text_payload: bytes | None = None
    if realistic_text:
        text_payload = make_senate_annual_text(
            source_record_id,
            last_name=last_name,
            first_name=first_name,
            office=office,
            report_type=report_type,
            date_filed=date_filed,
        )
    return BundleArtifactSpec(
        source_record_id=source_record_id,
        chamber="senate",
        filing_year=filing_year,
        storage_uri=storage_uri,
        source_url=source_url,
        source_slug=source_slug,
        index_row=index_row,
        artifact_kind="pdf",
        text_payload=text_payload,
    )


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def build_bundle_fixture(
    tmp_path: Path,
    specs: list[BundleArtifactSpec],
) -> DisclosureBundleFixture:
    """Generate a disclosure bundle fixture in *tmp_path*.

    For each spec:
    - writes artifact content at ``tmp_path / spec.storage_uri``
      (``spec.text_payload`` if set, otherwise a deterministic placeholder)
    - computes the real SHA-256 of that content
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
        Handle with all generated paths, raw index_row dicts, and SHA-256
        digests keyed by source_record_id.
    """
    artifact_entries: list[dict[str, Any]] = []
    artifact_paths: dict[str, Path] = {}
    index_rows: list[dict[str, Any]] = []
    sha256s: dict[str, str] = {}

    for spec in specs:
        data = (
            spec.text_payload
            if spec.text_payload is not None
            else _placeholder_bytes(spec.source_record_id)
        )
        artifact_file = tmp_path / spec.storage_uri
        artifact_file.parent.mkdir(parents=True, exist_ok=True)
        artifact_file.write_bytes(data)

        sha256 = hashlib.sha256(data).hexdigest()
        artifact_paths[spec.source_record_id] = artifact_file
        index_rows.append(spec.index_row)
        sha256s[spec.source_record_id] = sha256

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
        sha256s=sha256s,
    )


def build_house_senate_fixture(
    tmp_path: Path,
    *,
    house_record_id: str = "12345",
    senate_record_id: str = "uuid-xyz",
    house_filing_year: int = 2024,
    senate_filing_year: int = 2023,
    realistic_text: bool = True,
) -> DisclosureBundleFixture:
    """Build a two-artifact fixture: one House PTR + one Senate annual report.

    Both artifacts use realistic text payloads by default so SHA-256 digests
    cover non-trivial content.  Suitable for E2E tests that exercise the full
    validate → stage → parse inputs → parse → transform → load path.
    """
    specs = [
        make_house_spec(
            house_record_id,
            filing_year=house_filing_year,
            last_name="Smith",
            first_name="John",
            state_dst="CA08",
            filing_date=f"{house_filing_year}-01-15",
            filing_kind="annual",
            realistic_text=realistic_text,
        ),
        make_senate_spec(
            senate_record_id,
            filing_year=senate_filing_year,
            last_name="Doe",
            first_name="Jane",
            office="Senator, TX",
            date_filed="01/15/2024",
            realistic_text=realistic_text,
        ),
    ]
    return build_bundle_fixture(tmp_path, specs)
