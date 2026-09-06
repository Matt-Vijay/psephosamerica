"""Artifact download and DB-record helpers for disclosure PDFs.

Owns three explicit steps:
  1. download_artifact_bytes  — fetch raw bytes over HTTP
  2. sha256_bytes             — content-hash those bytes
  3. store_downloaded_artifact — write a source_artifact row

No file I/O; bytes in, DB row out.  Object storage is a later step.
"""

from __future__ import annotations

import hashlib
from typing import Any

import httpx

from src.db.repositories import fetch_all
from src.parse.disclosures.acquire import ArtifactMeta
from src.parse.disclosures.source_urls import validate_official_disclosure_artifact_url
from src.provenance.artifacts import create_source_artifact

_MIME_BY_KIND = {
    "pdf": "application/pdf",
    "html": "text/html",
}
_MAX_ARTIFACT_BYTES = 50 * 1024 * 1024


def _content_length(response: httpx.Response) -> int | None:
    raw = response.headers.get("content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def download_artifact_bytes(
    url: str,
    *,
    timeout: float = 30.0,
    max_bytes: int = _MAX_ARTIFACT_BYTES,
) -> bytes:
    """Return raw bytes from *url* via HTTP GET.

    Raises httpx.HTTPStatusError on a non-2xx response.
    """
    validate_official_disclosure_artifact_url(url)
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")

    with httpx.stream("GET", url, timeout=timeout, follow_redirects=False) as response:
        _validate_response_url(url, response)
        if 300 <= response.status_code < 400:
            raise ValueError(f"redirect response rejected for disclosure artifact URL: {url!r}")
        response.raise_for_status()

        length = _content_length(response)
        if length is not None and length > max_bytes:
            raise ValueError(
                f"disclosure artifact exceeds maximum size of {max_bytes} bytes: {length}"
            )

        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"disclosure artifact exceeds maximum size of {max_bytes} bytes")
            chunks.append(chunk)
    return b"".join(chunks)


def _validate_response_url(requested_url: str, response: httpx.Response) -> None:
    response_url = getattr(response, "url", None)
    if response_url is None:
        return
    final_url = str(response_url)
    if not final_url:
        return
    try:
        validate_official_disclosure_artifact_url(final_url)
    except ValueError as exc:
        raise ValueError("off-origin disclosure artifact response URL") from exc
    if final_url != requested_url:
        raise ValueError("off-origin disclosure artifact response URL")


def sha256_bytes(data: bytes) -> str:
    """Return the lowercase 64-char hex SHA-256 digest of *data*."""
    return hashlib.sha256(data).hexdigest()


def _fetch_data_source_id(conn: Any, slug: str) -> int:
    rows = fetch_all(conn, "SELECT id FROM data_source WHERE slug = %s", (slug,))
    if not rows:
        raise ValueError(f"data_source slug not found: {slug!r}")
    raw_id = rows[0]["id"]
    if isinstance(raw_id, bool) or not isinstance(raw_id, int):
        raise ValueError(
            f"data_source id for slug {slug!r} must be an integer, got {type(raw_id).__name__}"
        )
    return int(raw_id)


def store_downloaded_artifact(
    conn: Any,
    meta: ArtifactMeta,
    data: bytes,
    *,
    ingestion_run_id: int | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Insert a source_artifact row for a downloaded disclosure artifact.

    Resolves the data_source_id from meta.source_slug, computes the
    sha256 from *data*, then delegates to create_source_artifact.
    Does not write to object storage. Pass commit=False when the caller
    owns a larger transaction.
    """
    data_source_id = _fetch_data_source_id(conn, meta.source_slug)
    digest = sha256_bytes(data)
    mime_type = _MIME_BY_KIND.get(meta.artifact_kind.value)
    return create_source_artifact(
        conn,
        data_source_id=data_source_id,
        artifact_kind=meta.artifact_kind.value,
        storage_uri=meta.storage_key,
        sha256=digest,
        ingestion_run_id=ingestion_run_id,
        source_url=meta.source_url,
        mime_type=mime_type,
        fetched_at=meta.fetched_at,
        source_record_id=meta.source_record_id,
        commit=commit,
    )
