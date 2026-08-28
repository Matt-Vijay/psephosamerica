"""Hash-first, official-only acquisition receipts for the legislative patch probe.

This module is deliberately not a bill-version crawler.  It inventories bytes
already retained by the Time Machine or acquires one explicitly named official
resource at a time.  It never infers that a version is the result of an
amendment, and it labels metadata as metadata rather than legislative text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence
from urllib.parse import parse_qsl, urlsplit

import httpx
from defusedxml import ElementTree as SafeElementTree

from src.time_machine.govinfo_text import (
    CacheIntegrityError,
    GovInfoManifestEntry,
    load_govinfo_manifest,
    parse_package_id,
)

SCHEMA_VERSION = 1
DEFAULT_METADATA_CAP_BYTES = 500 * 1024 * 1024
MAX_PILOT_CAP_BYTES = 5 * 1024 * 1024 * 1024
_SECRET_QUERY_KEYS = frozenset({"api_key", "apikey", "key", "token", "access_token"})
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_EXACT_XML_KINDS = frozenset({"official_bill_version_xml", "official_amendment_xml"})
_BILL_ROOTS = frozenset({"bill", "resolution", "legislativedoc", "legislative-doc"})
_AMENDMENT_ROOTS = frozenset({"engrossedamendment", "amendmentdoc", "amendment-doc"})
_CONGRESS_FILE_RE = re.compile(r"/[1-9]\d*/(?:bills|amdt|crec)/")


class ProbeError(RuntimeError):
    """Raised when an acquisition or receipt would violate the probe contract."""


def _utc_iso(value: datetime | None = None) -> str:
    stamp = value or datetime.now(UTC)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("acquisition time must be timezone-aware")
    return stamp.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _within(root: Path, relative: str) -> Path:
    root = root.resolve()
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise ProbeError(f"artifact path escapes source root: {relative}")
    return candidate


def _official_url(value: str) -> str:
    """Validate the narrow official hosts and paths used by this probe."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ProbeError("invalid acquisition URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.fragment
    ):
        raise ProbeError("official acquisition URLs must be plain HTTPS URLs")
    if any(key.lower() in _SECRET_QUERY_KEYS for key, _value in parse_qsl(parsed.query)):
        raise ProbeError("receipt URLs may not contain secrets")

    host = (parsed.hostname or "").lower()
    path = parsed.path
    accepted = (
        (host == "www.govinfo.gov" and path.startswith(("/content/pkg/", "/bulkdata/")))
        or (host == "api.govinfo.gov" and path.startswith("/"))
        or (host == "api.congress.gov" and path.startswith("/v3/"))
        or (
            host == "www.congress.gov"
            and (
                path.startswith(("/bill/", "/amendment/", "/committee-report/"))
                or _CONGRESS_FILE_RE.match(path) is not None
            )
        )
        or (host == "github.com" and path.startswith("/usgpo/"))
        or (host == "raw.githubusercontent.com" and path.startswith("/usgpo/"))
    )
    if not accepted:
        raise ProbeError(f"URL is outside the primary federal-source allowlist: {value}")
    return value


def _bill_relationship(entry: GovInfoManifestEntry) -> dict[str, str]:
    target = f"bill:us:{entry.congress}:{entry.bill_type}:{entry.bill_number}"
    return {"type": "belongs_to_bill_identity", "target": target}


def _entry_kind(entry: GovInfoManifestEntry) -> str:
    if entry.root_tag.lower() in {"engrossedamendment", "amendmentdoc", "amendment-doc"}:
        return "official_gpo_sample_amendment_xml"
    return "official_gpo_sample_bill_version_xml"


def inventory_existing(time_machine_root: Path, display_root: str | None = None) -> dict[str, Any]:
    """Re-hash the existing official GPO sample cache and emit a receipt."""
    root = time_machine_root.resolve()
    entries = sorted(load_govinfo_manifest(root), key=lambda item: (item.package_id, item.revision))
    artifacts: list[dict[str, Any]] = []
    for entry in entries:
        path = _within(root, entry.relative_path)
        if not path.is_file():
            raise CacheIntegrityError(f"missing retained GovInfo object: {path}")
        digest, byte_count = _sha256_file(path)
        if digest != entry.content_sha256 or byte_count != entry.byte_count:
            raise CacheIntegrityError(f"retained GovInfo object failed re-hash: {path}")
        artifacts.append(
            {
                "artifact_id": f"sha256:{digest}",
                "identifier": entry.package_id,
                "kind": _entry_kind(entry),
                "content_semantics": "exact_official_gpo_sample_xml_bytes",
                "source_url": _official_url(entry.source_url),
                "source_url_role": "canonical_govinfo_package_url_byte_identity_unverified",
                "acquisition_url": _official_url(entry.acquisition_url or entry.source_url),
                "acquisition_url_role": "exact_official_gpo_sample_transport",
                "content_path": entry.relative_path,
                "media_type": "application/xml",
                "byte_count": byte_count,
                "sha256": digest,
                "published_at": entry.issued_date,
                "acquired_at": entry.observed_at,
                "http_etag": None,
                "http_last_modified": None,
                "source_relationships": [_bill_relationship(entry)],
            }
        )
    kinds: dict[str, int] = {}
    for row in artifacts:
        kind = str(row["kind"])
        kinds[kind] = kinds.get(kind, 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "manifest_kind": "legislative_patch_source_receipt",
        "source_root": display_root or str(time_machine_root),
        "generated_from_observations_through": max(
            (str(row["acquired_at"]) for row in artifacts), default=None
        ),
        "totals": {
            "artifacts": len(artifacts),
            "bytes": sum(int(row["byte_count"]) for row in artifacts),
            "published_at_present": sum(row["published_at"] is not None for row in artifacts),
            "published_at_missing": sum(row["published_at"] is None for row in artifacts),
            "by_kind": dict(sorted(kinds.items())),
        },
        "artifacts": artifacts,
    }


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_receipt(path: Path, *, missing_ok: bool = True) -> dict[str, Any]:
    if not path.exists():
        if not missing_ok:
            raise ProbeError(f"acquisition receipt does not exist: {path}")
        return {
            "schema_version": SCHEMA_VERSION,
            "manifest_kind": "legislative_patch_acquisition_receipt",
            "totals": {
                "artifacts": 0,
                "objects": 0,
                "retained_bytes": 0,
                "network_bytes_acquired": 0,
            },
            "artifacts": [],
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeError(f"cannot read receipt: {path}") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("manifest_kind") != "legislative_patch_acquisition_receipt"
        or not isinstance(payload.get("totals"), dict)
        or not isinstance(payload.get("artifacts"), list)
    ):
        raise ProbeError(f"invalid acquisition receipt: {path}")
    return payload


def _suffix(url: str, media_type: str) -> str:
    suffix = PurePosixPath(urlsplit(url).path).suffix.lower()
    if suffix in {".xml", ".json", ".xsd", ".dtd", ".rng", ".sch", ".html", ".txt"}:
        return suffix
    base_type = media_type.partition(";")[0].strip().lower()
    return {
        "application/xml": ".xml",
        "text/xml": ".xml",
        "application/json": ".json",
        "text/html": ".html",
        "text/plain": ".txt",
    }.get(base_type, ".bin")


def _content_semantics(kind: str, url: str, media_type: str) -> str:
    """Keep indexes/API/BILLSTATUS responses from being mislabeled as bill text."""
    parsed = urlsplit(url)
    base_type = media_type.partition(";")[0].strip().lower()
    if (
        "metadata" in kind.lower()
        or base_type == "application/json"
        or parsed.hostname in {"api.govinfo.gov", "api.congress.gov"}
        or parsed.path.startswith("/bulkdata/json/")
        or parsed.path.startswith("/bulkdata/BILLSTATUS/")
    ):
        return "metadata_only"
    if kind in _EXACT_XML_KINDS:
        return "exact_official_xml_bytes"
    if kind == "official_schema":
        return "official_schema_bytes"
    return "exact_official_response_bytes_unclassified"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1].lower()


def _validate_exact_payload(
    *,
    kind: str,
    identifier: str,
    source_url: str,
    acquisition_url: str,
    media_type: str,
    path: Path,
) -> str:
    """Derive semantics only after exact-document kinds pass structural checks."""
    semantics = _content_semantics(kind, acquisition_url, media_type)
    if kind not in _EXACT_XML_KINDS:
        if kind == "official_schema":
            suffix = PurePosixPath(urlsplit(acquisition_url).path).suffix.lower()
            if suffix not in {".xsd", ".dtd", ".rng", ".sch"}:
                raise ProbeError("official_schema must resolve to a schema file")
            base_type = media_type.partition(";")[0].strip().lower()
            if base_type not in {
                "application/xml",
                "text/xml",
                "text/plain",
                "application/octet-stream",
            }:
                raise ProbeError("official_schema response has a non-schema media type")
            if suffix == ".dtd":
                with path.open("rb") as handle:
                    prefix = handle.read(256 * 1024).lstrip()
                if b"<!ELEMENT" not in prefix and b"<!ENTITY" not in prefix:
                    raise ProbeError("official DTD response has no DTD declarations")
            else:
                try:
                    root = SafeElementTree.parse(path).getroot()
                except Exception as exc:
                    raise ProbeError("official XML schema is not safe, well-formed XML") from exc
                if root is None or _local_name(root.tag) not in {"schema", "grammar", "element"}:
                    raise ProbeError("official XML schema has an incompatible root")
        return semantics

    try:
        package = parse_package_id(identifier)
    except ValueError as exc:
        raise ProbeError("exact BILLS XML requires a canonical BILLS package identifier") from exc
    expected_name = f"{package.package_id}.xml".lower()
    for label, value in (("source", source_url), ("acquisition", acquisition_url)):
        if PurePosixPath(urlsplit(value).path).name.lower() != expected_name:
            raise ProbeError(f"exact BILLS XML {label} URL does not match {package.package_id}")
    base_type = media_type.partition(";")[0].strip().lower()
    if base_type not in {"application/xml", "text/xml", "application/octet-stream"}:
        raise ProbeError("exact BILLS XML response has a non-XML media type")
    try:
        root = SafeElementTree.parse(path).getroot()
    except Exception as exc:
        raise ProbeError("exact BILLS XML response is not safe, well-formed XML") from exc
    if root is None:
        raise ProbeError("exact BILLS XML response has no document element")
    root_name = _local_name(root.tag)
    allowed_roots = _AMENDMENT_ROOTS if kind == "official_amendment_xml" else _BILL_ROOTS
    if root_name not in allowed_roots:
        raise ProbeError(f"{kind} has incompatible XML root: {root_name}")
    return semantics


def _parse_relationships(values: Iterable[str]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for value in values:
        relation, separator, target = value.partition("=")
        if not separator or not relation.strip() or not target.strip():
            raise ProbeError("relationships must use TYPE=TARGET")
        result.append({"type": relation.strip(), "target": target.strip()})
    return result


def _validate_optional_time(value: Any, field: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise ProbeError(f"{field} must be null or an ISO date/time string")
    try:
        if "T" in value:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is None or stamp.utcoffset() is None:
                raise ValueError
        else:
            datetime.fromisoformat(value)
    except ValueError as exc:
        raise ProbeError(f"invalid {field} in acquisition receipt") from exc


def _receipt_totals(rows: Sequence[dict[str, Any]]) -> dict[str, int]:
    unique_objects: dict[str, int] = {}
    for row in rows:
        digest = str(row["sha256"])
        size = int(row["byte_count"])
        previous = unique_objects.setdefault(digest, size)
        if previous != size:
            raise ProbeError("one content hash has conflicting byte counts")
    return {
        "artifacts": len(rows),
        "objects": len(unique_objects),
        "retained_bytes": sum(unique_objects.values()),
        "network_bytes_acquired": sum(int(row["byte_count"]) for row in rows),
    }


def _verify_acquisition_artifact(store_root: Path, row: dict[str, Any]) -> None:
    for field in ("identifier", "kind", "source_url", "acquisition_url", "media_type"):
        if not isinstance(row.get(field), str) or not row[field].strip():
            raise ProbeError(f"{field} must be a non-empty string")
    try:
        digest = str(row["sha256"])
        relative = str(row["content_path"])
        expected_size = int(row["byte_count"])
        identifier = str(row["identifier"])
        kind = str(row["kind"])
        source_url = _official_url(str(row["source_url"]))
        acquisition_url = _official_url(str(row["acquisition_url"]))
        media_type = str(row["media_type"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ProbeError("invalid artifact row in acquisition receipt") from exc
    if _SHA256_RE.fullmatch(digest) is None:
        raise ProbeError("invalid SHA-256 in acquisition receipt")
    if row.get("artifact_id") != f"sha256:{digest}":
        raise ProbeError("artifact_id does not match SHA-256")
    pure_path = PurePosixPath(relative)
    if (
        pure_path.is_absolute()
        or pure_path.parent != PurePosixPath("sha256") / digest[:2]
        or not pure_path.name.startswith(digest)
        or pure_path.name.removeprefix(digest)
        not in {".xml", ".json", ".xsd", ".dtd", ".rng", ".sch", ".html", ".txt", ".bin"}
    ):
        raise ProbeError("content_path is not canonical SHA-addressed storage")
    path = _within(store_root, relative)
    if not path.is_file():
        raise ProbeError(f"acquisition receipt references a missing object: {path}")
    actual_digest, actual_size = _sha256_file(path)
    if actual_digest != digest or actual_size != expected_size:
        raise ProbeError(f"acquisition object failed re-hash: {path}")
    semantics = _validate_exact_payload(
        kind=kind,
        identifier=identifier,
        source_url=source_url,
        acquisition_url=acquisition_url,
        media_type=media_type,
        path=path,
    )
    if row.get("content_semantics") != semantics:
        raise ProbeError("content semantics do not match source, media type, and kind")
    _validate_optional_time(row.get("published_at"), "published_at")
    acquired = row.get("acquired_at")
    _validate_optional_time(acquired, "acquired_at")
    if not isinstance(acquired, str) or "T" not in acquired:
        raise ProbeError("acquired_at must be a timezone-aware timestamp")
    for header in ("http_etag", "http_last_modified"):
        if row.get(header) is not None and not isinstance(row.get(header), str):
            raise ProbeError(f"{header} must be null or a string")
    relationships = row.get("source_relationships")
    if not isinstance(relationships, list):
        raise ProbeError("source_relationships must be a list")
    seen_relationships: set[tuple[str, str]] = set()
    for relationship in relationships:
        if not isinstance(relationship, dict) or set(relationship) != {"type", "target"}:
            raise ProbeError("invalid source relationship")
        relation = relationship["type"]
        target = relationship["target"]
        if not isinstance(relation, str) or not relation or not isinstance(target, str) or not target:
            raise ProbeError("empty source relationship")
        key = (relation, target)
        if key in seen_relationships:
            raise ProbeError("duplicate source relationship")
        seen_relationships.add(key)


def verify_acquisition_receipt(store_root: Path) -> dict[str, int]:
    """Validate receipt metadata and re-hash every referenced object."""
    store_root = store_root.resolve()
    payload = _load_receipt(store_root / "manifest.json", missing_ok=False)
    seen_rows: set[str] = set()
    for row in payload["artifacts"]:
        if not isinstance(row, dict):
            raise ProbeError("artifact row must be an object")
        _verify_acquisition_artifact(store_root, row)
        key = str(row["source_url"])
        if key in seen_rows:
            raise ProbeError("duplicate artifact row in acquisition receipt")
        seen_rows.add(key)
    totals = _receipt_totals(payload["artifacts"])
    if payload["totals"] != totals:
        raise ProbeError("acquisition receipt totals do not match artifact rows")
    return totals


def acquire_one(
    *,
    store_root: Path,
    url: str,
    identifier: str,
    kind: str,
    relationships: Sequence[str] = (),
    published_at: str | None = None,
    cap_bytes: int = DEFAULT_METADATA_CAP_BYTES,
    acquired_at: datetime | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Acquire one named official resource into a capped SHA-256 store.

    The cap is cumulative across network bytes recorded by the store. Existing
    verified receipts are reused without a request. Exact-document semantics
    are derived only after URL, media-type, package-ID, and XML-root checks.
    """
    if cap_bytes < 1 or cap_bytes > MAX_PILOT_CAP_BYTES:
        raise ProbeError("cap must be between one byte and the 5 GiB pilot ceiling")
    if not identifier.strip() or not kind.strip():
        raise ProbeError("identifier and kind are required")
    official_url = _official_url(url)
    store_root = store_root.resolve()
    manifest_path = store_root / "manifest.json"
    payload = _load_receipt(manifest_path)
    existing_rows = payload["artifacts"]
    requested_relationships = _parse_relationships(relationships)
    if manifest_path.exists():
        verify_acquisition_receipt(store_root)
    for existing in existing_rows:
        if not isinstance(existing, dict):
            raise ProbeError("artifact row must be an object")
        if existing.get("source_url") == official_url:
            if existing.get("identifier") != identifier:
                raise ProbeError("existing receipt uses a different identifier for this source URL")
            if existing.get("kind") != kind:
                raise ProbeError("existing receipt uses a different kind for this identifier and URL")
            if requested_relationships and existing.get("source_relationships") != requested_relationships:
                raise ProbeError("existing receipt uses different source relationships")
            return existing

    network_bytes = int(payload["totals"]["network_bytes_acquired"])
    if network_bytes >= cap_bytes:
        raise ProbeError("cumulative acquisition cap is already exhausted")
    store_root.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".download.", dir=store_root)
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    byte_count = 0
    owns_client = client is None
    http = client or httpx.Client(follow_redirects=True, timeout=60.0)
    response_headers: dict[str, str] = {}
    final_url = official_url
    try:
        with os.fdopen(descriptor, "wb") as handle:
            with http.stream("GET", official_url) as response:
                response.raise_for_status()
                final_url = _official_url(str(response.url))
                response_headers = {key.lower(): value for key, value in response.headers.items()}
                declared = response_headers.get("content-length")
                if declared is not None:
                    try:
                        declared_size = int(declared)
                    except ValueError as exc:
                        raise ProbeError("invalid Content-Length from official source") from exc
                    if network_bytes + declared_size > cap_bytes:
                        raise ProbeError("declared response size exceeds cumulative acquisition cap")
                for chunk in response.iter_bytes():
                    byte_count += len(chunk)
                    if network_bytes + byte_count > cap_bytes:
                        raise ProbeError("response exceeded cumulative acquisition cap")
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
        if byte_count == 0:
            raise ProbeError("official source returned an empty response")
        hexdigest = digest.hexdigest()
        media_type = response_headers.get("content-type", "application/octet-stream")
        semantics = _validate_exact_payload(
            kind=kind,
            identifier=identifier,
            source_url=official_url,
            acquisition_url=final_url,
            media_type=media_type,
            path=temporary,
        )
        object_path = store_root / "sha256" / hexdigest[:2] / f"{hexdigest}{_suffix(final_url, media_type)}"
        object_path.parent.mkdir(parents=True, exist_ok=True)
        if object_path.exists():
            actual_digest, actual_size = _sha256_file(object_path)
            if actual_digest != hexdigest or actual_size != byte_count:
                raise ProbeError(f"content-addressed object collision: {object_path}")
        else:
            os.replace(temporary, object_path)

        row = {
            "artifact_id": f"sha256:{hexdigest}",
            "identifier": identifier,
            "kind": kind,
            "content_semantics": semantics,
            "source_url": official_url,
            "acquisition_url": final_url,
            "content_path": str(object_path.relative_to(store_root)),
            "media_type": media_type,
            "byte_count": byte_count,
            "sha256": hexdigest,
            "published_at": published_at,
            "acquired_at": _utc_iso(acquired_at),
            "http_etag": response_headers.get("etag"),
            "http_last_modified": response_headers.get("last-modified"),
            "source_relationships": requested_relationships,
        }
        existing_rows.append(row)
        payload["totals"] = _receipt_totals(existing_rows)
        _atomic_json(manifest_path, payload)
        return row
    finally:
        if owns_client:
            http.close()
        temporary.unlink(missing_ok=True)


def _positive_mib(value: str) -> int:
    try:
        amount = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("MiB cap must be an integer") from exc
    if amount < 1 or amount > 5120:
        raise argparse.ArgumentTypeError("MiB cap must be between 1 and 5120")
    return amount


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    inventory = commands.add_parser("inventory", help="re-hash the existing official GPO sample cache")
    inventory.add_argument("--time-machine-root", type=Path, default=Path("data/time_machine"))
    inventory.add_argument("--output", type=Path, required=True)

    acquire = commands.add_parser("acquire", help="acquire one explicit official resource")
    acquire.add_argument("--store-root", type=Path, required=True)
    acquire.add_argument("--url", required=True)
    acquire.add_argument("--identifier", required=True)
    acquire.add_argument("--kind", required=True)
    acquire.add_argument("--relationship", action="append", default=[])
    acquire.add_argument("--published-at")
    acquire.add_argument("--cap-mib", type=_positive_mib, default=500)

    verify = commands.add_parser("verify", help="re-hash a probe acquisition store")
    verify.add_argument("--store-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "inventory":
        payload = inventory_existing(
            args.time_machine_root,
            display_root=str(args.time_machine_root),
        )
        _atomic_json(args.output, payload)
        print(json.dumps(payload["totals"], sort_keys=True))
        return 0
    if args.command == "acquire":
        row = acquire_one(
            store_root=args.store_root,
            url=args.url,
            identifier=args.identifier,
            kind=args.kind,
            relationships=args.relationship,
            published_at=args.published_at,
            cap_bytes=args.cap_mib * 1024 * 1024,
        )
        print(json.dumps(row, sort_keys=True))
        return 0
    totals = verify_acquisition_receipt(args.store_root)
    print(json.dumps(totals, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
