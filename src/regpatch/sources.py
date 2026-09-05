"""Official source bytes, acquisition receipts, and metadata/version discovery."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx

SCHEMA_VERSION = 1

_EPISODE_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{2,127}")

_SHA256_RE = re.compile(r"[0-9a-f]{64}")

_SECRET_QUERY_KEYS = frozenset({"api_key", "apikey", "key", "token", "access_token"})

_SAFE_HEADER_NAMES = frozenset(
    {
        "content-length",
        "content-encoding",
        "content-type",
        "date",
        "etag",
        "last-modified",
        "x-ratelimit-limit",
        "x-ratelimit-remaining",
        "x-request-id",
    }
)

DEFAULT_METADATA_CAP_BYTES = 500 * 1024 * 1024

DEFAULT_SOURCE_CAP_BYTES = 5 * 1024 * 1024 * 1024

_PROBE_FIELDS = (
    "document_number",
    "publication_date",
    "effective_on",
    "full_text_xml_url",
    "json_url",
    "citation",
    "agencies",
    "cfr_references",
    "type",
    "action",
    "title",
    "start_page",
    "end_page",
    "volume",
)


class CompileError(RuntimeError):
    """A machine-classifiable reason that an episode cannot be compiled."""

    def __init__(self, code: str, detail: str, *, evidence: Mapping[str, Any] | None = None):
        self.code = code
        self.detail = detail
        self.evidence = dict(evidence or {})
        super().__init__(f"{code}: {detail}")

    def as_rejection(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "detail": self.detail}
        if self.evidence:
            result["evidence"] = self.evidence
        return result


@dataclass(frozen=True)
class ArtifactSpec:
    path: Path
    source_url: str
    acquired_at: str
    response_headers: Mapping[str, str]
    acquired_at_basis: str = "supplied_http_receipt"
    expected_sha256: str | None = None
    media_type: str | None = None


@dataclass(frozen=True)
class _Artifact:
    spec: ArtifactSpec
    content: bytes
    sha256: str
    byte_count: int


def acquire_official_source(
    store_root: Path,
    *,
    url: str,
    identifier: str,
    role: str,
    cap_bytes: int = DEFAULT_SOURCE_CAP_BYTES,
    client: httpx.Client | None = None,
    acquired_at: datetime | None = None,
) -> dict[str, Any]:
    """Acquire one allowlisted official resource into a content-addressed store.

    The acquisition receipt is the resume index: a repeated request re-hashes and
    reuses its retained object without touching the network.  ``cap_bytes`` limits
    cumulative network bytes in this store, not merely the current response.
    """
    source_url = _official_url(url)
    if not identifier or len(identifier) > 256 or any(ord(char) < 32 for char in identifier):
        raise CompileError("ACQUISITION_IDENTIFIER_INVALID", "identifier is missing or invalid")
    if not role or len(role) > 128:
        raise CompileError("ACQUISITION_ROLE_INVALID", "role is missing or invalid")
    if cap_bytes <= 0:
        raise CompileError("ACQUISITION_CAP_INVALID", "cap_bytes must be positive")

    root = store_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    receipt_path = root / "acquisition.json"
    receipt = _load_acquisition_receipt(receipt_path)
    matching_url = [row for row in receipt["artifacts"] if row.get("request_url") == source_url]
    for row in matching_url:
        object_path = _content_path(root, str(row["content_path"]))
        digest, byte_count = _sha256_file(object_path)
        if digest != row.get("sha256") or byte_count != row.get("byte_count"):
            raise CompileError(
                "ACQUISITION_CACHE_CORRUPT",
                f"retained object failed re-hash: {row.get('content_path')}",
            )
    if matching_url:
        identities = {
            (str(row.get("sha256")), str(row.get("content_path"))) for row in matching_url
        }
        if len(identities) != 1:
            raise CompileError(
                "ACQUISITION_IDENTITY_CONFLICT",
                "one request URL maps to incompatible retained objects",
            )
        for row in matching_url:
            if row.get("identifier") == identifier and row.get("role") == role:
                return dict(row)
        reference_time = acquired_at or datetime.now(UTC)
        if reference_time.tzinfo is None or reference_time.utcoffset() is None:
            raise CompileError("SOURCE_TIMESTAMP_INVALID", "acquired_at must be timezone-aware")
        reused = dict(matching_url[0])
        reused.update(
            {
                "identifier": identifier,
                "role": role,
                "network_byte_count": 0,
                "reused_artifact_id": reused["artifact_id"],
                "logical_reference_recorded_at": reference_time.astimezone(UTC)
                .isoformat()
                .replace("+00:00", "Z"),
            }
        )
        receipt["artifacts"].append(reused)
        receipt["artifacts"].sort(key=lambda value: (str(value["role"]), str(value["identifier"])))
        receipt["totals"] = _acquisition_totals(
            receipt["artifacts"],
            network_bytes=int(receipt["totals"]["network_bytes_acquired"]),
        )
        _write_json_atomic(receipt_path, receipt)
        return dict(reused)

    prior_network_bytes = int(receipt["totals"]["network_bytes_acquired"])
    if prior_network_bytes >= cap_bytes:
        raise CompileError(
            "ACQUISITION_CAP_EXCEEDED",
            "the acquisition store has already reached its cumulative byte cap",
            evidence={"cap_bytes": cap_bytes, "network_bytes_acquired": prior_network_bytes},
        )

    owns_client = client is None
    transport = client or httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(60.0, connect=20.0),
        headers={
            "Accept-Encoding": "identity",
            "User-Agent": "Psephos-RegPatch/1.0 (+local research compiler)",
        },
    )
    temporary_path: Path | None = None
    try:
        with transport.stream(
            "GET", source_url, headers={"Accept-Encoding": "identity"}
        ) as response:
            if response.status_code != 200:
                raise CompileError(
                    "ACQUISITION_HTTP_ERROR",
                    f"official source returned HTTP {response.status_code}",
                    evidence={"url": source_url, "status_code": response.status_code},
                )
            final_url = _official_url(str(response.url))
            safe_headers = _safe_headers(response.headers)
            declared_length = _content_length(safe_headers.get("content-length"))
            remaining = cap_bytes - prior_network_bytes
            if declared_length is not None and declared_length > remaining:
                raise CompileError(
                    "ACQUISITION_CAP_EXCEEDED",
                    "Content-Length exceeds the remaining acquisition cap",
                    evidence={"content_length": declared_length, "remaining_bytes": remaining},
                )

            object_root = root / "sha256"
            object_root.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(prefix=".download.", dir=object_root)
            temporary_path = Path(temporary_name)
            hasher = hashlib.sha256()
            byte_count = 0
            network_byte_count = 0
            content_encoding = safe_headers.get("content-encoding", "identity").strip().lower()
            if content_encoding in {"", "identity"}:
                decoder: Any | None = None
            elif content_encoding == "gzip":
                decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            elif content_encoding == "deflate":
                decoder = zlib.decompressobj()
            else:
                raise CompileError(
                    "ACQUISITION_ENCODING_UNSUPPORTED",
                    f"unsupported HTTP Content-Encoding: {content_encoding}",
                )
            predecoded = response.is_stream_consumed
            with os.fdopen(descriptor, "wb") as handle:
                try:
                    chunks = (response.content,) if predecoded else response.iter_raw()
                    if predecoded and content_encoding not in {"", "identity"}:
                        decoder = None
                        network_byte_count = declared_length or len(response.content)
                        if network_byte_count > remaining:
                            raise CompileError(
                                "ACQUISITION_CAP_EXCEEDED",
                                "response exceeds the remaining acquisition cap",
                                evidence={"remaining_bytes": remaining},
                            )
                    for raw_chunk in chunks:
                        if not raw_chunk:
                            continue
                        if not predecoded or content_encoding in {"", "identity"}:
                            network_byte_count += len(raw_chunk)
                        if network_byte_count > remaining:
                            raise CompileError(
                                "ACQUISITION_CAP_EXCEEDED",
                                "raw response exceeds the remaining acquisition cap",
                                evidence={"remaining_bytes": remaining},
                            )
                        chunk = decoder.decompress(raw_chunk) if decoder else raw_chunk
                        if not chunk:
                            continue
                        byte_count += len(chunk)
                        if byte_count > remaining:
                            raise CompileError(
                                "ACQUISITION_DECODED_CAP_EXCEEDED",
                                "decoded response exceeds the remaining acquisition cap",
                                evidence={"remaining_bytes": remaining},
                            )
                        hasher.update(chunk)
                        handle.write(chunk)
                    final_chunk = decoder.flush() if decoder else b""
                except zlib.error as exc:
                    raise CompileError(
                        "ACQUISITION_ENCODING_INVALID",
                        f"cannot decode HTTP Content-Encoding {content_encoding}",
                    ) from exc
                if final_chunk:
                    byte_count += len(final_chunk)
                    if byte_count > remaining:
                        raise CompileError(
                            "ACQUISITION_DECODED_CAP_EXCEEDED",
                            "decoded response exceeds the remaining acquisition cap",
                            evidence={"remaining_bytes": remaining},
                        )
                    hasher.update(final_chunk)
                    handle.write(final_chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if (
                declared_length is not None
                and (not predecoded or content_encoding in {"", "identity"})
                and network_byte_count != declared_length
            ):
                raise CompileError(
                    "ACQUISITION_LENGTH_MISMATCH",
                    "raw response size disagrees with Content-Length",
                    evidence={"declared": declared_length, "actual": network_byte_count},
                )
            hexdigest = hasher.hexdigest()
            media_type = safe_headers.get("content-type", "application/octet-stream")
            suffix = _source_suffix(final_url, media_type)
            relative = f"sha256/{hexdigest[:2]}/{hexdigest}{suffix}"
            object_path = root / relative
            object_path.parent.mkdir(parents=True, exist_ok=True)
            if object_path.exists():
                existing_digest, existing_size = _sha256_file(object_path)
                if existing_digest != hexdigest or existing_size != byte_count:
                    raise CompileError(
                        "ACQUISITION_CACHE_CORRUPT",
                        f"content-addressed target is corrupt: {relative}",
                    )
                temporary_path.unlink()
            else:
                os.replace(temporary_path, object_path)
            temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        if owns_client:
            transport.close()

    acquired = acquired_at or datetime.now(UTC)
    if acquired.tzinfo is None or acquired.utcoffset() is None:
        raise CompileError("SOURCE_TIMESTAMP_INVALID", "acquired_at must be timezone-aware")
    row = {
        "artifact_id": f"sha256:{hexdigest}",
        "identifier": identifier,
        "role": role,
        "request_url": source_url,
        "source_url": final_url,
        "content_path": relative,
        "media_type": media_type.partition(";")[0].strip().lower(),
        "byte_count": byte_count,
        "network_byte_count": network_byte_count,
        "content_decoded_for_storage": content_encoding not in {"", "identity"},
        "sha256": hexdigest,
        "acquired_at": acquired.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "response_headers": safe_headers,
        "http_status": 200,
    }
    receipt["artifacts"].append(row)
    receipt["artifacts"].sort(key=lambda value: (str(value["role"]), str(value["identifier"])))
    receipt["totals"] = _acquisition_totals(
        receipt["artifacts"],
        network_bytes=prior_network_bytes + network_byte_count,
    )
    _write_json_atomic(receipt_path, receipt)
    return dict(row)


def probe_federal_register(
    store_root: Path,
    *,
    start_date: date,
    end_date: date,
    max_candidates: int = 100,
    page: int = 1,
    per_page: int = 1000,
    cap_bytes: int = DEFAULT_METADATA_CAP_BYTES,
    client: httpx.Client | None = None,
    acquired_at: datetime | None = None,
) -> dict[str, Any]:
    """Probe FederalRegister.gov rule metadata and emit transition requests.

    This step downloads metadata only.  Each candidate is a title/part projection
    of a real document with an official XML URL; strict before/rule/after
    validation occurs only after ``acquire_candidate_sources`` and
    ``compile_episode``.
    """
    if end_date < start_date:
        raise CompileError("PROBE_WINDOW_INVALID", "end_date precedes start_date")
    if not 1 <= max_candidates <= 10000:
        raise CompileError("PROBE_LIMIT_INVALID", "max_candidates must be 1..10000")
    if not 1 <= page or not 1 <= per_page <= 1000:
        raise CompileError("PROBE_LIMIT_INVALID", "page/per_page are outside API bounds")

    query: list[tuple[str, str | int]] = [
        ("conditions[publication_date][gte]", start_date.isoformat()),
        ("conditions[publication_date][lte]", end_date.isoformat()),
        ("conditions[type][]", "RULE"),
        ("order", "oldest"),
        ("page", page),
        ("per_page", per_page),
    ]
    query.extend(("fields[]", field) for field in _PROBE_FIELDS)
    url = f"https://www.federalregister.gov/api/v1/documents.json?{urlencode(query)}"
    root = store_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    request_path = root / "probe-request.json"
    request_receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "manifest_kind": "regpatch_metadata_probe_request",
        "status": "REQUESTED",
        "requested_at": (acquired_at or datetime.now(UTC))
        .astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z"),
        "window": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "limits": {
            "max_candidates": max_candidates,
            "page": page,
            "per_page": per_page,
            "cap_bytes": cap_bytes,
        },
        "requests": [{"method": "GET", "url": url, "role": "federal_register_search"}],
    }
    _write_json_atomic(request_path, request_receipt)
    try:
        artifact = acquire_official_source(
            root,
            url=url,
            identifier=(
                f"federal-register-rules-{start_date.isoformat()}-{end_date.isoformat()}-p{page}"
            ),
            role="federal_register_search_metadata",
            cap_bytes=cap_bytes,
            client=client,
            acquired_at=acquired_at,
        )
        payload = json.loads(_content_path(root, str(artifact["content_path"])).read_bytes())
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise CompileError("PROBE_RESPONSE_INVALID", "Federal Register response lacks results")
        candidates, rejections = _probe_candidates(payload["results"], max_candidates)
        report: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "manifest_kind": "regpatch_metadata_probe",
            "status": "COMPLETE",
            "window": request_receipt["window"],
            "search_artifact": artifact,
            "api_count": payload.get("count"),
            "api_total_pages": payload.get("total_pages"),
            "candidates": candidates,
            "rejections": rejections,
            "totals": _probe_totals(candidates, rejections),
        }
        _write_json_atomic(root / "probe.json", report)
        request_receipt["status"] = "COMPLETE"
        request_receipt["result_path"] = "probe.json"
        _write_json_atomic(request_path, request_receipt)
        return report
    except Exception as exc:
        request_receipt["status"] = "FAILED"
        request_receipt["failure"] = (
            exc.as_rejection()
            if isinstance(exc, CompileError)
            else {"code": "PROBE_FAILED", "detail": str(exc)}
        )
        _write_json_atomic(request_path, request_receipt)
        raise


def probe_ecfr_versions(
    store_root: Path,
    *,
    title: int,
    part: str,
    issue_date_start: date,
    issue_date_end: date,
    page: int = 1,
    cap_bytes: int = DEFAULT_METADATA_CAP_BYTES,
    client: httpx.Client | None = None,
    acquired_at: datetime | None = None,
) -> dict[str, Any]:
    """Probe observed eCFR content-version clocks for one title/part.

    Candidate windows are derived from returned ``amendment_date`` records.  The
    Federal Register publication date is deliberately not assumed to be the eCFR
    state-transition date.
    """
    if not 1 <= title <= 999 or not part or any(char in part for char in "/\\"):
        raise CompileError("VERSION_PROBE_SCOPE_INVALID", "invalid CFR title/part")
    if issue_date_end < issue_date_start:
        raise CompileError("VERSION_PROBE_WINDOW_INVALID", "issue-date window is reversed")
    if page < 1:
        raise CompileError("VERSION_PROBE_PAGE_INVALID", "page must be positive")
    query = urlencode(
        [
            ("issue_date[gte]", issue_date_start.isoformat()),
            ("issue_date[lte]", issue_date_end.isoformat()),
            ("part", part),
            ("page", page),
        ]
    )
    url = f"https://www.ecfr.gov/api/versioner/v1/versions/title-{title}.json?{query}"
    root = store_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    stem = f"versions-title-{title}-part-{_safe_slug(part)}-page-{page}"
    request_path = root / f"{stem}-request.json"
    request: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "manifest_kind": "regpatch_ecfr_version_probe_request",
        "status": "REQUESTED",
        "scope": {"title": title, "part": part},
        "issue_date_window": {
            "gte": issue_date_start.isoformat(),
            "lte": issue_date_end.isoformat(),
        },
        "page": page,
        "cap_bytes": cap_bytes,
        "requests": [{"method": "GET", "url": url, "role": "ecfr_versions_metadata"}],
    }
    _write_json_atomic(request_path, request)
    try:
        artifact = acquire_official_source(
            root,
            url=url,
            identifier=stem,
            role="ecfr_versions_metadata",
            cap_bytes=cap_bytes,
            client=client,
            acquired_at=acquired_at,
        )
        payload = json.loads(_content_path(root, str(artifact["content_path"])).read_bytes())
        if not isinstance(payload, dict) or not isinstance(payload.get("content_versions"), list):
            raise CompileError("VERSION_PROBE_RESPONSE_INVALID", "response lacks content_versions")
        records, rejections = _version_records(payload["content_versions"], title=title, part=part)
        windows = _version_windows(records, title=title, part=part)
        report: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "manifest_kind": "regpatch_ecfr_version_probe",
            "status": "COMPLETE",
            "scope": request["scope"],
            "issue_date_window": request["issue_date_window"],
            "page": page,
            "source_artifact": artifact,
            "api_meta": payload.get("meta"),
            "content_versions": records,
            "candidate_windows": windows,
            "rejections": rejections,
            "totals": {
                "content_versions": len(records),
                "substantive_versions": sum(row["substantive"] is True for row in records),
                "candidate_windows": len(windows),
                "rejected_rows": len(rejections),
            },
        }
        result_path = root / f"{stem}.json"
        _write_json_atomic(result_path, report)
        request["status"] = "COMPLETE"
        request["result_path"] = result_path.name
        _write_json_atomic(request_path, request)
        return report
    except Exception as exc:
        request["status"] = "FAILED"
        request["failure"] = (
            exc.as_rejection()
            if isinstance(exc, CompileError)
            else {"code": "VERSION_PROBE_FAILED", "detail": str(exc)}
        )
        _write_json_atomic(request_path, request)
        raise


def expand_candidate_windows(
    candidate: Mapping[str, Any], version_report: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Join one FR metadata candidate to observed eCFR amendment windows."""
    try:
        base_id = str(candidate["candidate_id"])
        title = int(candidate["title"])
        part = str(candidate["part"])
        publication_date = date.fromisoformat(str(candidate["publication_date"]))
        report_scope = _mapping(version_report["scope"], "version_report.scope")
        windows = _sequence(version_report["candidate_windows"], "candidate_windows")
    except (KeyError, TypeError, ValueError) as exc:
        raise CompileError(
            "VERSION_JOIN_INVALID", f"invalid candidate/version report: {exc}"
        ) from exc
    if int(report_scope.get("title", -1)) != title or str(report_scope.get("part", "")) != part:
        raise CompileError("VERSION_JOIN_SCOPE_MISMATCH", "candidate and version scope disagree")
    expanded: list[dict[str, Any]] = []
    for raw_window in windows:
        window = _mapping(raw_window, "candidate_window")
        amendment_date = date.fromisoformat(str(window["ecfr_amendment_date"]))
        issue_date = date.fromisoformat(str(window["ecfr_issue_date"]))
        if amendment_date < publication_date:
            continue
        row = dict(candidate)
        row.update(
            {
                "candidate_id": f"{base_id}-amend-{amendment_date.isoformat()}",
                "base_date": (amendment_date - timedelta(days=1)).isoformat(),
                "successor_date": amendment_date.isoformat(),
                "ecfr_amendment_date": amendment_date.isoformat(),
                "ecfr_issue_date": issue_date.isoformat(),
                "before_ecfr_url": _ecfr_full_url(amendment_date - timedelta(days=1), title, part),
                "after_ecfr_url": _ecfr_full_url(amendment_date, title, part),
                "version_evidence": {
                    "identifiers": list(_sequence(window["identifiers"], "identifiers")),
                    "record_count": int(window["record_count"]),
                    "substantive_record_count": int(window["substantive_record_count"]),
                },
                "status": "OBSERVED_VERSION_WINDOW_UNVALIDATED_CAUSALITY",
            }
        )
        expanded.append(row)
    return expanded


def acquire_candidate_sources(
    store_root: Path,
    candidate: Mapping[str, Any],
    *,
    cap_bytes: int = DEFAULT_SOURCE_CAP_BYTES,
    client: httpx.Client | None = None,
    acquired_at: datetime | None = None,
) -> Path:
    """Acquire a probed source quartet and write a compiler-ready request."""
    try:
        candidate_id = str(candidate["candidate_id"])
        document_number = str(candidate["document_number"])
        date.fromisoformat(str(candidate["publication_date"]))
        base_date = date.fromisoformat(str(candidate["base_date"]))
        successor_date = date.fromisoformat(str(candidate["successor_date"]))
        ecfr_amendment_date = date.fromisoformat(str(candidate["ecfr_amendment_date"]))
        ecfr_issue_date = date.fromisoformat(str(candidate["ecfr_issue_date"]))
        title = int(candidate["title"])
        part = str(candidate["part"])
        metadata_url = _official_url(str(candidate["metadata_url"]))
        rule_url = _official_url(str(candidate["rule_xml_url"]))
        before_url = _official_url(str(candidate["before_ecfr_url"]))
        after_url = _official_url(str(candidate["after_ecfr_url"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise CompileError("CANDIDATE_INVALID", f"invalid probe candidate: {exc}") from exc
    if _EPISODE_ID_RE.fullmatch(candidate_id) is None:
        raise CompileError("CANDIDATE_INVALID", "candidate_id is not a safe episode identifier")
    if base_date >= successor_date or ecfr_amendment_date != successor_date:
        raise CompileError(
            "CANDIDATE_WINDOW_INVALID",
            "candidate must use the observed amendment date as successor and an earlier base",
        )
    if ecfr_issue_date < ecfr_amendment_date:
        raise CompileError("CANDIDATE_WINDOW_INVALID", "eCFR issue date precedes amendment date")
    if before_url != _ecfr_full_url(base_date, title, part) or after_url != _ecfr_full_url(
        successor_date, title, part
    ):
        raise CompileError(
            "CANDIDATE_SOURCE_URL_MISMATCH",
            "eCFR source URLs disagree with the observed version window",
        )

    root = store_root.resolve()
    request_dir = root / "candidates" / candidate_id
    request_dir.mkdir(parents=True, exist_ok=True)
    requests = [
        ("federal_register_document_metadata", metadata_url, f"{document_number}-metadata"),
        ("federal_register_rule_xml", rule_url, f"{document_number}-xml"),
        ("base_ecfr", before_url, f"title-{title}-part-{part}-{base_date}"),
        ("successor_ecfr", after_url, f"title-{title}-part-{part}-{successor_date}"),
    ]
    acquisition_request: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "manifest_kind": "regpatch_candidate_acquisition_request",
        "candidate_id": candidate_id,
        "status": "REQUESTED",
        "cap_bytes": cap_bytes,
        "requests": [
            {"order": order, "method": "GET", "role": role, "url": url}
            for order, (role, url, _identifier) in enumerate(requests, start=1)
        ],
    }
    _write_json_atomic(request_dir / "acquisition-request.json", acquisition_request)
    artifacts: dict[str, dict[str, Any]] = {}
    try:
        for role, url, identifier in requests:
            artifacts[role] = acquire_official_source(
                root,
                url=url,
                identifier=identifier,
                role=role,
                cap_bytes=cap_bytes,
                client=client,
                acquired_at=acquired_at,
            )
    except Exception as exc:
        acquisition_request["status"] = "FAILED"
        acquisition_request["failure"] = (
            exc.as_rejection()
            if isinstance(exc, CompileError)
            else {"code": "ACQUISITION_FAILED", "detail": str(exc)}
        )
        _write_json_atomic(request_dir / "acquisition-request.json", acquisition_request)
        raise

    def source_row(role: str, media_type: str) -> dict[str, Any]:
        artifact = artifacts[role]
        object_path = _content_path(root, str(artifact["content_path"]))
        return {
            "path": os.path.relpath(object_path, request_dir),
            "source_url": artifact["source_url"],
            "sha256": artifact["sha256"],
            "acquired_at": artifact["acquired_at"],
            "acquired_at_basis": "captured_http_response",
            "response_headers": artifact["response_headers"],
            "media_type": media_type,
        }

    spec = {
        "episode_id": candidate_id,
        "scope": {
            "title": title,
            "parts": [part],
            "sections": [],
            "granularity": "part",
        },
        "window": {
            "base_date": base_date.isoformat(),
            "successor_date": successor_date.isoformat(),
        },
        "sources": {
            "base": source_row("base_ecfr", "application/xml"),
            "target": source_row("successor_ecfr", "application/xml"),
            "rules": [
                {
                    "order": 1,
                    "ecfr_amendment_date": ecfr_amendment_date.isoformat(),
                    "ecfr_issue_date": ecfr_issue_date.isoformat(),
                    "xml": source_row("federal_register_rule_xml", "application/xml"),
                    "metadata": source_row(
                        "federal_register_document_metadata", "application/json"
                    ),
                }
            ],
        },
    }
    spec_path = request_dir / "source-spec.json"
    _write_json_atomic(spec_path, spec)
    acquisition_request["status"] = "COMPLETE"
    acquisition_request["source_spec"] = "source-spec.json"
    acquisition_request["artifacts"] = artifacts
    _write_json_atomic(request_dir / "acquisition-request.json", acquisition_request)
    return spec_path


def _artifact_spec(payload: Mapping[str, Any], base_dir: Path) -> ArtifactSpec:
    local_path = Path(str(payload["path"]))
    path = local_path if local_path.is_absolute() else base_dir / local_path
    headers = _mapping(payload.get("response_headers", {}), "response_headers")
    acquired_at = str(payload["acquired_at"])
    _parse_timestamp(acquired_at)
    expected = payload.get("sha256")
    expected_sha256 = str(expected) if expected is not None else None
    if expected_sha256 is not None and _SHA256_RE.fullmatch(expected_sha256) is None:
        raise CompileError("SPEC_INVALID", "artifact sha256 must be 64 lowercase hex characters")
    return ArtifactSpec(
        path=path.resolve(),
        source_url=_official_url(str(payload["source_url"])),
        acquired_at=acquired_at,
        response_headers=_safe_headers(headers),
        acquired_at_basis=str(payload.get("acquired_at_basis", "supplied_http_receipt")),
        expected_sha256=expected_sha256,
        media_type=str(payload["media_type"]) if payload.get("media_type") else None,
    )


def _load_artifact(spec: ArtifactSpec, *, expected_media: str) -> _Artifact:
    if not spec.path.is_file():
        raise CompileError("SOURCE_PATH_MISSING", f"source file does not exist: {spec.path.name}")
    digest, size = _sha256_file(spec.path)
    if spec.expected_sha256 is not None and digest != spec.expected_sha256:
        raise CompileError(
            "SOURCE_HASH_MISMATCH",
            f"source failed SHA-256 verification: {spec.path.name}",
            evidence={"expected": spec.expected_sha256, "actual": digest},
        )
    media_type = (spec.media_type or expected_media).partition(";")[0].strip().lower()
    if media_type != expected_media:
        raise CompileError(
            "SOURCE_MEDIA_TYPE_INVALID",
            f"expected {expected_media}, found {media_type}: {spec.path.name}",
        )
    try:
        content = spec.path.read_bytes()
    except OSError as exc:
        raise CompileError("SOURCE_READ_FAILED", f"cannot read source: {spec.path.name}") from exc
    return _Artifact(spec=spec, content=content, sha256=digest, byte_count=size)


def _public_artifact_receipt(artifact: _Artifact) -> dict[str, Any]:
    return {
        "source_url": artifact.spec.source_url,
        "sha256": artifact.sha256,
        "byte_count": artifact.byte_count,
        "acquired_at": artifact.spec.acquired_at,
        "acquired_at_basis": artifact.spec.acquired_at_basis,
        "response_headers": dict(sorted(artifact.spec.response_headers.items())),
    }


def _private_artifact_receipt(artifact: _Artifact) -> dict[str, Any]:
    return {
        **_public_artifact_receipt(artifact),
        "artifact_id": f"sha256:{artifact.sha256}",
    }


def _load_acquisition_receipt(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": SCHEMA_VERSION,
            "manifest_kind": "regpatch_acquisition_receipt",
            "artifacts": [],
            "totals": {
                "artifacts": 0,
                "objects": 0,
                "retained_bytes": 0,
                "network_bytes_acquired": 0,
            },
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompileError("ACQUISITION_RECEIPT_INVALID", f"cannot read receipt: {path}") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("manifest_kind") != "regpatch_acquisition_receipt"
        or not isinstance(payload.get("artifacts"), list)
        or not isinstance(payload.get("totals"), dict)
    ):
        raise CompileError("ACQUISITION_RECEIPT_INVALID", f"invalid receipt: {path}")
    return payload


def _acquisition_totals(
    artifacts: Sequence[Mapping[str, Any]], *, network_bytes: int
) -> dict[str, int]:
    unique = {str(value["sha256"]): int(value["byte_count"]) for value in artifacts}
    return {
        "artifacts": len(artifacts),
        "objects": len(unique),
        "retained_bytes": sum(unique.values()),
        "network_bytes_acquired": network_bytes,
    }


def _content_path(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts:
        raise CompileError("ACQUISITION_RECEIPT_INVALID", "content path escapes acquisition root")
    candidate = (root / Path(*pure.parts)).resolve()
    resolved_root = root.resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise CompileError("ACQUISITION_RECEIPT_INVALID", "content path escapes acquisition root")
    return candidate


def _content_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        result = int(value)
    except ValueError as exc:
        raise CompileError("ACQUISITION_HEADER_INVALID", "Content-Length is not numeric") from exc
    if result < 0:
        raise CompileError("ACQUISITION_HEADER_INVALID", "Content-Length is negative")
    return result


def _source_suffix(url: str, media_type: str) -> str:
    suffix = PurePosixPath(urlsplit(url).path).suffix.lower()
    if suffix in {".xml", ".json", ".txt", ".html", ".pdf"}:
        return suffix
    normalized = media_type.partition(";")[0].strip().lower()
    return {
        "application/json": ".json",
        "application/xml": ".xml",
        "text/xml": ".xml",
        "text/plain": ".txt",
        "text/html": ".html",
        "application/pdf": ".pdf",
    }.get(normalized, ".bin")


def _probe_candidates(
    raw_results: Sequence[Any], max_candidates: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    for index, value in enumerate(raw_results):
        if len(candidates) >= max_candidates:
            break
        if not isinstance(value, Mapping):
            rejections.append(
                {"code": "PROBE_RESULT_INVALID", "result_index": index, "detail": "not an object"}
            )
            continue
        document_number = str(value.get("document_number") or "")
        if not document_number:
            rejections.append(
                {
                    "code": "PROBE_DOCUMENT_NUMBER_MISSING",
                    "result_index": index,
                    "detail": "result has no document number",
                }
            )
            continue
        try:
            publication = date.fromisoformat(str(value["publication_date"]))
        except (KeyError, ValueError):
            rejections.append(
                {
                    "code": "PROBE_PUBLICATION_DATE_MISSING",
                    "document_number": document_number,
                }
            )
            continue
        rule_url = value.get("full_text_xml_url")
        if not rule_url:
            rejections.append(
                {
                    "code": "PROBE_RULE_XML_URL_MISSING",
                    "document_number": document_number,
                }
            )
            continue
        try:
            rule_url = _official_url(str(rule_url))
            metadata_url = _official_url(
                str(value.get("json_url") or _document_metadata_url(document_number))
            )
        except CompileError as exc:
            rejections.append(
                {
                    **exc.as_rejection(),
                    "document_number": document_number,
                }
            )
            continue
        references = value.get("cfr_references")
        if not isinstance(references, list) or not references:
            rejections.append(
                {
                    "code": "PROBE_CFR_REFERENCE_MISSING",
                    "document_number": document_number,
                }
            )
            continue
        accepted_reference = False
        for reference in references:
            if len(candidates) >= max_candidates:
                break
            if not isinstance(reference, Mapping):
                continue
            try:
                title = int(reference["title"])
                part = str(reference["part"])
            except (KeyError, TypeError, ValueError):
                continue
            if not 1 <= title <= 999 or not part or any(char in part for char in "/\\"):
                continue
            key = (document_number, title, part)
            if key in seen:
                continue
            seen.add(key)
            accepted_reference = True
            agencies = value.get("agencies", [])
            agency_names = sorted(
                {
                    str(row.get("name") or row.get("raw_name"))
                    for row in agencies
                    if isinstance(row, Mapping) and (row.get("name") or row.get("raw_name"))
                }
            )
            safe_part = re.sub(r"[^a-z0-9.-]+", "-", part.lower()).strip("-")
            candidate_id = (
                f"fr-{document_number}-title-{title}-part-{safe_part}-{publication.isoformat()}"
            )
            candidates.append(
                {
                    "candidate_id": candidate_id,
                    "document_number": document_number,
                    "document_type": value.get("type"),
                    "action": value.get("action"),
                    "document_title": value.get("title"),
                    "citation": value.get("citation"),
                    "volume": value.get("volume"),
                    "start_page": value.get("start_page"),
                    "end_page": value.get("end_page"),
                    "publication_date": publication.isoformat(),
                    "effective_on": value.get("effective_on"),
                    "title": title,
                    "part": part,
                    "agencies": agency_names,
                    "metadata_url": metadata_url,
                    "rule_xml_url": rule_url,
                    "status": "REQUIRES_ECFR_VERSION_PROBE",
                }
            )
        if not accepted_reference:
            rejections.append(
                {
                    "code": "PROBE_CFR_REFERENCE_INVALID",
                    "document_number": document_number,
                }
            )
    return candidates, rejections


def _probe_totals(
    candidates: Sequence[Mapping[str, Any]], rejections: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    agencies = {
        str(agency)
        for candidate in candidates
        for agency in _sequence(candidate.get("agencies", []), "candidate.agencies")
    }
    return {
        "metadata_candidates": len(candidates),
        "distinct_documents": len({candidate["document_number"] for candidate in candidates}),
        "distinct_titles": len({candidate["title"] for candidate in candidates}),
        "distinct_agencies": len(agencies),
        "rejected_metadata_rows": len(rejections),
    }


def _version_records(
    raw_records: Sequence[Any], *, title: int, part: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    for index, value in enumerate(raw_records):
        if not isinstance(value, Mapping):
            rejections.append(
                {"code": "VERSION_ROW_INVALID", "row_index": index, "detail": "not an object"}
            )
            continue
        try:
            row_title = int(value["title"])
            row_part = str(value["part"])
            identifier = str(value["identifier"])
            amendment_date = date.fromisoformat(str(value["amendment_date"]))
            issue_date = date.fromisoformat(str(value["issue_date"]))
            row_type = str(value["type"])
            substantive = value["substantive"] is True
            removed = value.get("removed") is True
        except (KeyError, TypeError, ValueError) as exc:
            rejections.append(
                {
                    "code": "VERSION_ROW_INVALID",
                    "row_index": index,
                    "detail": f"missing/invalid field: {exc}",
                }
            )
            continue
        if row_title != title or row_part != part:
            rejections.append(
                {
                    "code": "VERSION_ROW_SCOPE_MISMATCH",
                    "row_index": index,
                    "title": row_title,
                    "part": row_part,
                }
            )
            continue
        if row_type not in {"section", "appendix"} or not identifier:
            rejections.append(
                {
                    "code": "VERSION_ROW_IDENTITY_INVALID",
                    "row_index": index,
                    "type": row_type,
                    "identifier": identifier,
                }
            )
            continue
        if issue_date < amendment_date:
            rejections.append(
                {
                    "code": "VERSION_ROW_CLOCK_INVALID",
                    "row_index": index,
                    "amendment_date": amendment_date.isoformat(),
                    "issue_date": issue_date.isoformat(),
                }
            )
            continue
        records.append(
            {
                "title": row_title,
                "part": row_part,
                "type": row_type,
                "identifier": identifier,
                "name": value.get("name"),
                "subpart": value.get("subpart"),
                "amendment_date": amendment_date.isoformat(),
                "issue_date": issue_date.isoformat(),
                "substantive": substantive,
                "removed": removed,
            }
        )
    records.sort(
        key=lambda row: (
            str(row["amendment_date"]),
            str(row["issue_date"]),
            str(row["type"]),
            _natural_key(str(row["identifier"])),
        )
    )
    return records, rejections


def _version_windows(
    records: Sequence[Mapping[str, Any]], *, title: int, part: str
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in records:
        if row.get("substantive") is not True:
            continue
        key = (str(row["amendment_date"]), str(row["issue_date"]))
        grouped.setdefault(key, []).append(row)
    return [
        {
            "title": title,
            "part": part,
            "base_date": (date.fromisoformat(amendment) - timedelta(days=1)).isoformat(),
            "successor_date": amendment,
            "ecfr_amendment_date": amendment,
            "ecfr_issue_date": issue,
            "identifiers": sorted({str(row["identifier"]) for row in rows}, key=_natural_key),
            "record_count": len(rows),
            "substantive_record_count": sum(row.get("substantive") is True for row in rows),
            "removed_record_count": sum(row.get("removed") is True for row in rows),
        }
        for (amendment, issue), rows in sorted(grouped.items())
    ]


def _safe_slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9.-]+", "-", value.casefold()).strip("-")
    if not result:
        raise CompileError("IDENTIFIER_INVALID", "value cannot form a safe slug")
    return result


def _document_metadata_url(document_number: str) -> str:
    return f"https://www.federalregister.gov/api/v1/documents/{document_number}.json"


def _document_number_from_url(value: str) -> str:
    official = _official_url(value)
    name = PurePosixPath(urlsplit(official).path).name.removesuffix(".json")
    if re.fullmatch(r"(?:C[0-9]+-)?[0-9]{4}-[0-9]{4,}", name) is None:
        raise CompileError("METADATA_INVALID", "correction URL lacks a document number")
    return name


def _ecfr_full_url(as_of: date, title: int, part: str) -> str:
    return (
        f"https://www.ecfr.gov/api/versioner/v1/full/{as_of.isoformat()}/"
        f"title-{title}.xml?{urlencode({'part': part})}"
    )


def _official_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise CompileError("SOURCE_URL_NOT_OFFICIAL", "invalid source URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.fragment
        or any(key.lower() in _SECRET_QUERY_KEYS for key, _ in parse_qsl(parsed.query))
    ):
        raise CompileError("SOURCE_URL_NOT_OFFICIAL", "source URL must be secret-free HTTPS")
    host = (parsed.hostname or "").lower()
    path = parsed.path
    accepted = (
        (host == "www.ecfr.gov" and path.startswith("/api/versioner/v1/"))
        or (
            host == "www.federalregister.gov"
            and path.startswith(("/api/v1/", "/documents/full_text/xml/"))
        )
        or (host == "www.govinfo.gov" and path.startswith(("/content/pkg/", "/bulkdata/")))
    )
    if not accepted:
        raise CompileError(
            "SOURCE_URL_NOT_OFFICIAL",
            f"URL is outside the RegPatch official-source allowlist: {value}",
        )
    return value


def _safe_headers(value: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_name, raw_value in value.items():
        name = str(raw_name).strip().lower()
        if name in _SAFE_HEADER_NAMES:
            result[name] = str(raw_value).strip()
    return result


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CompileError("SOURCE_TIMESTAMP_INVALID", "acquired_at must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CompileError("SOURCE_TIMESTAMP_INVALID", "acquired_at must be timezone-aware")
    return parsed.astimezone(UTC)


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CompileError("SPEC_INVALID", f"{field} must be an object")
    return value


def _sequence(value: Any, field: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise CompileError("SPEC_INVALID", f"{field} must be an array")
    return value


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
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


def _natural_key(value: str) -> tuple[Any, ...]:
    return tuple(int(part) if part.isdigit() else part for part in re.split(r"([0-9]+)", value))
