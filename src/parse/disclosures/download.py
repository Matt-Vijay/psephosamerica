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
from src.provenance.artifacts import create_source_artifact


_MIME_BY_KIND = {
    "pdf": "application/pdf",
    "html": "text/html",
}


def download_artifact_bytes(url: str, *, timeout: float = 30.0) -> bytes:
    """Return raw bytes from *url* via HTTP GET.

    Raises httpx.HTTPStatusError on a non-2xx response.
    """
    response = httpx.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    return response.content


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
) -> dict[str, Any]:
    """Insert a source_artifact row for a downloaded disclosure artifact.

    Resolves the data_source_id from meta.source_slug, computes the
    sha256 from *data*, then delegates to create_source_artifact.
    Does not write to object storage.
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
        source_url=meta.source_url,
        mime_type=mime_type,
        fetched_at=meta.fetched_at,
        source_record_id=meta.source_record_id,
    )
