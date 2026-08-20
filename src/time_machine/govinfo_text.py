"""Small, official-only GovInfo BILLS text cache.

BILLSTATUS titles, subjects, and CRS summaries are metadata.  This module only
accepts the XML rendition of a ``BILLS-*`` package and only extracts its
substantive legislative body.  Raw bytes are retained by SHA-256, while a
manifest records every content revision observed for each package.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit
from xml.etree.ElementTree import Element

import httpx
from defusedxml import ElementTree as SafeElementTree

from src.time_machine.inventory import load_inventory

GOVINFO_CONTENT_ROOT = "https://www.govinfo.gov/content/pkg"
MANIFEST_VERSION = 1
RAW_RELATIVE_ROOT = Path("raw/govinfo_bills")
MANIFEST_NAME = "manifest.json"

_PACKAGE_RE = re.compile(
    r"BILLS-(?P<congress>[1-9]\d*)"
    r"(?P<bill_type>hconres|sconres|hjres|sjres|hres|sres|hr|s)"
    r"(?P<bill_number>[1-9]\d*)"
    r"(?P<version_code>[a-z][a-z0-9]*)"
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_ISO_DATE_RE = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")
_BODY_TAGS = frozenset({"legis-body", "resolution-body", "main", "amendmain"})
_METADATA_TAGS = frozenset(
    {
        "meta",
        "metadata",
        "summary",
        "summaries",
        "crs-summary",
        "crssummary",
    }
)
_ISSUED_TAGS = frozenset({"dateissued", "docdate", "issueddate", "publicationdate"})


class GovInfoTextError(RuntimeError):
    """Base error for GovInfo text acquisition and parsing."""


class InventoryRequiredError(GovInfoTextError):
    """Raised before HTTP when the hash-first inventory has not been built."""


class BillTextParseError(GovInfoTextError):
    """Raised when XML is invalid or contains no substantive legislative body."""


class CacheIntegrityError(GovInfoTextError):
    """Raised when an immutable object or its manifest no longer agrees."""


@dataclass(frozen=True)
class GovInfoPackage:
    package_id: str
    congress: int
    bill_type: str
    bill_number: int
    version_code: str

    @property
    def source_url(self) -> str:
        return f"{GOVINFO_CONTENT_ROOT}/{self.package_id}/xml/{self.package_id}.xml"

    @property
    def bill_key(self) -> tuple[int, str, int]:
        return (self.congress, self.bill_type, self.bill_number)


@dataclass(frozen=True)
class ParsedBillText:
    text_content: str
    root_tag: str
    body_tags: tuple[str, ...]
    issued_date: date | None


@dataclass(frozen=True)
class GovInfoManifestEntry:
    package_id: str
    congress: int
    bill_type: str
    bill_number: int
    version_code: str
    revision: int
    source_url: str
    relative_path: str
    content_sha256: str
    byte_count: int
    observed_at: str
    issued_date: str | None
    root_tag: str
    body_tags: tuple[str, ...]
    acquisition_url: str | None = None


@dataclass(frozen=True)
class BillTextArtifact:
    package: GovInfoPackage
    revision: int
    source_url: str
    raw_path: Path
    content_sha256: str
    byte_count: int
    observed_at: datetime
    issued_date: date | None
    text_content: str
    root_tag: str
    body_tags: tuple[str, ...]
    from_cache: bool
    acquisition_url: str | None = None


def parse_package_id(value: str) -> GovInfoPackage:
    """Parse one canonical GovInfo BILLS package ID, rejecting paths and aliases."""
    match = _PACKAGE_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"invalid GovInfo BILLS package ID: {value!r}")
    fields = match.groupdict()
    return GovInfoPackage(
        package_id=value,
        congress=int(fields["congress"]),
        bill_type=fields["bill_type"],
        bill_number=int(fields["bill_number"]),
        version_code=fields["version_code"],
    )


def parse_package_filename(value: str) -> GovInfoPackage:
    """Parse the exact XML filename emitted by the GovInfo BILLS bulk tree."""
    if not value.endswith(".xml") or Path(value).name != value:
        raise ValueError(f"invalid GovInfo BILLS filename: {value!r}")
    return parse_package_id(value.removesuffix(".xml"))


def _validated_acquisition_url(package: GovInfoPackage, value: str | None) -> str:
    url = value or package.source_url
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid GovInfo text acquisition URL") from exc
    expected_name = f"{package.package_id}.xml".lower()
    filename_matches = PurePosixPath(parsed.path).name.lower() == expected_name
    common_valid = (
        parsed.scheme == "https"
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
        and not parsed.fragment
        and filename_matches
    )
    govinfo_path = urlsplit(package.source_url).path
    is_govinfo = parsed.hostname == "www.govinfo.gov" and parsed.path == govinfo_path
    is_gpo_github = parsed.hostname in {"github.com", "raw.githubusercontent.com"} and (
        parsed.path.startswith("/usgpo/")
    )
    if not common_valid or not (is_govinfo or is_gpo_github):
        raise ValueError(
            f"acquisition URL must identify {package.package_id} on an official GPO host"
        )
    return url


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _normalized_tag(tag: str) -> str:
    return _local_name(tag).replace("_", "").replace("-", "").lower()


def _body_nodes(root: Element) -> list[Element]:
    selected: list[Element] = []

    def visit(node: Element) -> None:
        if _local_name(node.tag).lower() in _BODY_TAGS:
            selected.append(node)
            return
        for child in node:
            visit(child)

    visit(root)
    return selected


def _substantive_text(node: Element) -> str:
    parts: list[str] = []

    def visit(current: Element) -> None:
        if current is not node and _local_name(current.tag).lower() in _METADATA_TAGS:
            return
        if current.text:
            parts.append(current.text)
        for child in current:
            visit(child)
            if child.tail:
                parts.append(child.tail)

    visit(node)
    fragments = [" ".join(part.split()) for part in parts]
    return " ".join(fragment for fragment in fragments if fragment)


def _issued_date(root: Element) -> date | None:
    for node in root.iter():
        if _normalized_tag(node.tag) not in _ISSUED_TAGS:
            continue
        candidates = [
            value
            for key, value in node.attrib.items()
            if _normalized_tag(key) in {"date", "value", "datetime", "dateissued"}
        ]
        if node.text:
            candidates.append(node.text)
        for candidate in candidates:
            match = _ISO_DATE_RE.search(candidate)
            if match is not None:
                try:
                    return date.fromisoformat(match.group(1))
                except ValueError:
                    continue
    return None


def parse_bill_text_xml(xml_bytes: bytes) -> ParsedBillText:
    """Extract legislative text without falling back to BILLSTATUS/CRS metadata."""
    try:
        root = SafeElementTree.fromstring(xml_bytes)
    except Exception as exc:  # defusedxml and ElementTree expose several parse errors
        raise BillTextParseError("invalid GovInfo BILLS XML") from exc
    bodies = _body_nodes(root)
    if not bodies:
        raise BillTextParseError(
            "XML has no legis-body, resolution-body, USLM main, or amendment main"
        )
    texts = [text for body in bodies if (text := _substantive_text(body))]
    if not texts:
        raise BillTextParseError("legislative body is empty")
    return ParsedBillText(
        text_content="\n\n".join(texts),
        root_tag=_local_name(root.tag),
        body_tags=tuple(_local_name(body.tag) for body in bodies),
        issued_date=_issued_date(root),
    )


def _manifest_path(output_root: Path) -> Path:
    return output_root.resolve() / RAW_RELATIVE_ROOT / MANIFEST_NAME


def _object_path(output_root: Path, digest: str) -> Path:
    return output_root.resolve() / RAW_RELATIVE_ROOT / "sha256" / digest[:2] / f"{digest}.xml"


def _manifest_entry(raw: dict[str, Any]) -> GovInfoManifestEntry:
    try:
        row = dict(raw)
        row.setdefault("acquisition_url", None)
        if not isinstance(row["body_tags"], list):
            raise TypeError("body_tags must be a list")
        row["body_tags"] = tuple(row["body_tags"])
        entry = GovInfoManifestEntry(**row)
        package = parse_package_id(entry.package_id)
        observed = datetime.fromisoformat(entry.observed_at.replace("Z", "+00:00"))
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if entry.issued_date is not None:
            date.fromisoformat(entry.issued_date)
        _validated_acquisition_url(package, entry.acquisition_url)
    except (KeyError, TypeError, ValueError) as exc:
        raise CacheIntegrityError("invalid GovInfo BILLS manifest entry") from exc
    if (
        package.congress != entry.congress
        or package.bill_type != entry.bill_type
        or package.bill_number != entry.bill_number
        or package.version_code != entry.version_code
        or entry.source_url != package.source_url
        or entry.revision < 1
        or entry.byte_count < 1
        or _SHA256_RE.fullmatch(entry.content_sha256) is None
        or not entry.root_tag
        or not entry.body_tags
        or any(tag.lower() not in _BODY_TAGS for tag in entry.body_tags)
    ):
        raise CacheIntegrityError(f"inconsistent manifest entry for {entry.package_id}")
    return entry


def load_govinfo_manifest(output_root: Path) -> tuple[GovInfoManifestEntry, ...]:
    """Load and validate the revision manifest; a missing manifest is an empty cache."""
    path = _manifest_path(output_root)
    if not path.exists():
        return ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("manifest must be an object")
        if payload.get("schema_version") != MANIFEST_VERSION:
            raise CacheIntegrityError(f"unsupported GovInfo manifest schema: {path}")
        rows = payload["artifacts"]
        if not isinstance(rows, list):
            raise TypeError("artifacts must be a list")
        entries = tuple(_manifest_entry(row) for row in rows)
        revision_keys = {(entry.package_id, entry.revision) for entry in entries}
        if len(revision_keys) != len(entries):
            raise CacheIntegrityError("duplicate package revision in GovInfo manifest")
        revisions_by_package: dict[str, list[int]] = {}
        for entry in entries:
            revisions_by_package.setdefault(entry.package_id, []).append(entry.revision)
        for package_id, revisions in revisions_by_package.items():
            revisions.sort()
            if revisions != list(range(1, len(revisions) + 1)):
                raise CacheIntegrityError(f"non-contiguous revisions for {package_id}")
        return entries
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise CacheIntegrityError(f"cannot read GovInfo BILLS manifest: {path}") from exc


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _store_immutable(path: Path, content: bytes, digest: str) -> None:
    if path.exists():
        if _sha256(path.read_bytes()) != digest:
            raise CacheIntegrityError(f"immutable GovInfo object is corrupt: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if _sha256(path.read_bytes()) != digest:
                raise CacheIntegrityError(f"immutable GovInfo object collision: {path}")
    finally:
        temporary.unlink(missing_ok=True)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write_manifest(output_root: Path, entries: list[GovInfoManifestEntry]) -> None:
    payload = {
        "schema_version": MANIFEST_VERSION,
        "artifacts": [asdict(entry) for entry in entries],
    }
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    _atomic_write(_manifest_path(output_root), encoded)


def _require_inventory(output_root: Path) -> None:
    try:
        load_inventory(output_root.resolve())
    except (FileNotFoundError, OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise InventoryRequiredError(
            f"run the hash-first inventory before fetching GovInfo text: "
            f"{output_root.resolve() / 'inventory.json'}"
        ) from exc


def _aware_utc(value: datetime | None) -> datetime:
    result = value or datetime.now(UTC)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    return result.astimezone(UTC)


def _entry_path(output_root: Path, entry: GovInfoManifestEntry) -> Path:
    expected = _object_path(output_root, entry.content_sha256)
    actual = (output_root.resolve() / entry.relative_path).resolve()
    if actual != expected:
        raise CacheIntegrityError(
            f"manifest object path is not SHA-addressed: {entry.relative_path}"
        )
    return actual


def _artifact_from_entry(
    output_root: Path,
    entry: GovInfoManifestEntry,
    *,
    from_cache: bool,
) -> BillTextArtifact:
    path = _entry_path(output_root, entry)
    if not path.is_file():
        raise CacheIntegrityError(f"manifest references missing GovInfo object: {path}")
    content = path.read_bytes()
    if len(content) != entry.byte_count or _sha256(content) != entry.content_sha256:
        raise CacheIntegrityError(f"GovInfo object does not match manifest: {path}")
    parsed = parse_bill_text_xml(content)
    if parsed.issued_date != (date.fromisoformat(entry.issued_date) if entry.issued_date else None):
        raise CacheIntegrityError(f"GovInfo parsed metadata does not match manifest: {path}")
    return BillTextArtifact(
        package=parse_package_id(entry.package_id),
        revision=entry.revision,
        source_url=entry.source_url,
        raw_path=path,
        content_sha256=entry.content_sha256,
        byte_count=entry.byte_count,
        observed_at=datetime.fromisoformat(entry.observed_at.replace("Z", "+00:00")),
        issued_date=parsed.issued_date,
        text_content=parsed.text_content,
        root_tag=parsed.root_tag,
        body_tags=parsed.body_tags,
        from_cache=from_cache,
        acquisition_url=entry.acquisition_url,
    )


def cache_bill_text_bytes(
    package_id: str,
    output_root: Path,
    content: bytes,
    *,
    observed_at: datetime | None = None,
    acquisition_url: str | None = None,
) -> BillTextArtifact:
    """Validate and cache already-acquired official BILLS XML bytes.

    This is the transport-neutral half of :func:`fetch_bill_text`.  It is used
    when the caller has obtained the same official GPO bytes through an allowed
    browser/download channel.  The canonical GovInfo package URL remains the
    evidence URL; ``acquisition_url`` records the exact official transport.
    """
    package = parse_package_id(package_id)
    output_root = output_root.resolve()
    _require_inventory(output_root)
    transport_url = _validated_acquisition_url(package, acquisition_url)
    parsed = parse_bill_text_xml(content)
    digest = _sha256(content)
    object_path = _object_path(output_root, digest)
    _store_immutable(object_path, content, digest)
    entries = list(load_govinfo_manifest(output_root))
    revisions = [entry for entry in entries if entry.package_id == package.package_id]
    latest = max(revisions, key=lambda entry: entry.revision, default=None)
    if latest is not None and latest.content_sha256 == digest:
        return _artifact_from_entry(output_root, latest, from_cache=True)
    observed = _aware_utc(observed_at)
    entry = GovInfoManifestEntry(
        package_id=package.package_id,
        congress=package.congress,
        bill_type=package.bill_type,
        bill_number=package.bill_number,
        version_code=package.version_code,
        revision=(latest.revision + 1) if latest is not None else 1,
        source_url=package.source_url,
        relative_path=str(object_path.relative_to(output_root)),
        content_sha256=digest,
        byte_count=len(content),
        observed_at=observed.isoformat().replace("+00:00", "Z"),
        issued_date=parsed.issued_date.isoformat() if parsed.issued_date else None,
        root_tag=parsed.root_tag,
        body_tags=parsed.body_tags,
        acquisition_url=transport_url,
    )
    entries.append(entry)
    _write_manifest(output_root, entries)
    return _artifact_from_entry(output_root, entry, from_cache=False)


def fetch_bill_text(
    package_id: str,
    output_root: Path,
    *,
    client: httpx.Client | None = None,
    observed_at: datetime | None = None,
    refresh: bool = False,
) -> BillTextArtifact:
    """Fetch one official BILLS XML package or resume from its verified cache.

    ``output_root/inventory.json`` must exist before any HTTP request.  The
    default is local-first: a verified manifest revision is returned without
    contacting GovInfo.  ``refresh=True`` checks the official URL; identical
    bytes are deduplicated, while changed bytes become a new manifest revision.
    """
    package = parse_package_id(package_id)
    output_root = output_root.resolve()
    _require_inventory(output_root)
    entries = list(load_govinfo_manifest(output_root))
    revisions = [entry for entry in entries if entry.package_id == package.package_id]
    latest = max(revisions, key=lambda entry: entry.revision, default=None)
    if latest is not None and not refresh:
        return _artifact_from_entry(output_root, latest, from_cache=True)

    supplied_observation = _aware_utc(observed_at) if observed_at is not None else None
    owns_client = client is None
    http = client or httpx.Client(follow_redirects=True, timeout=60.0)
    try:
        response = http.get(package.source_url, headers={"Accept": "application/xml"})
        response.raise_for_status()
        content = response.content
    finally:
        if owns_client:
            http.close()
    result = cache_bill_text_bytes(
        package.package_id,
        output_root,
        content,
        observed_at=supplied_observation,
        acquisition_url=package.source_url,
    )
    return replace(result, from_cache=False)


__all__ = [
    "BillTextArtifact",
    "BillTextParseError",
    "CacheIntegrityError",
    "GovInfoManifestEntry",
    "GovInfoPackage",
    "GovInfoTextError",
    "InventoryRequiredError",
    "ParsedBillText",
    "cache_bill_text_bytes",
    "fetch_bill_text",
    "load_govinfo_manifest",
    "parse_bill_text_xml",
    "parse_package_filename",
    "parse_package_id",
]
