"""Deterministic storage-key builders for provenance artifacts.

Key scheme (all lowercase, forward-slash separated):
  raw/       raw source artifacts      raw/<source_slug>/<date>/<sha256[:8]>/<filename>
  parsed/    parser outputs            parsed/<source_slug>/<parser>/<version>/<artifact_sha256[:8]>/<filename>
  snapshots/ score snapshots           snapshots/<snapshot_date>/<bioguide_id>/<filename>
  archives/  snapshot manifests        archives/<snapshot_date>/manifest.json

All builders return plain strings with no I/O side effects.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Union

_DATE_T = Union[date, datetime, str]


def _as_date_str(d: _DATE_T) -> str:
    """Normalise a date-like value to 'YYYY-MM-DD'."""
    if isinstance(d, (date, datetime)):
        return d.strftime("%Y-%m-%d")
    # Expect ISO format string; validate shape.
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        raise ValueError(f"date must be YYYY-MM-DD, got: {d!r}")
    return d


def _safe_slug(value: str) -> str:
    """Return a path-safe lowercase slug (alphanumeric + hyphens/underscores)."""
    slug = value.lower().strip()
    if not re.fullmatch(r"[a-z0-9_\-]+", slug):
        raise ValueError(f"Unsafe key segment: {slug!r}")
    return slug


# ---------------------------------------------------------------------------
# Raw artifact keys
# ---------------------------------------------------------------------------


def raw_artifact_key(
    source_slug: str,
    fetched_date: _DATE_T,
    sha256: str,
    filename: str,
) -> str:
    """Storage key for an immutable raw source artifact.

    Example:
        raw/congress-api/2025-06-01/3d4f1a2b/members_all.json
    """
    if len(sha256) < 8 or not re.fullmatch(r"[0-9a-f]+", sha256):
        raise ValueError(f"Invalid sha256: {sha256!r}")
    return "/".join(
        [
            "raw",
            _safe_slug(source_slug),
            _as_date_str(fetched_date),
            sha256[:8],
            filename,
        ]
    )


# ---------------------------------------------------------------------------
# Parsed output keys
# ---------------------------------------------------------------------------


def parsed_output_key(
    source_slug: str,
    parser_name: str,
    parser_version: str,
    artifact_sha256: str,
    filename: str,
) -> str:
    """Storage key for a parsed output derived from a raw artifact.

    Example:
        parsed/efdsearch-senate/disclosure-pdf/0.3.1/a1b2c3d4/holdings.json
    """
    if len(artifact_sha256) < 8 or not re.fullmatch(r"[0-9a-f]+", artifact_sha256):
        raise ValueError(f"Invalid artifact_sha256: {artifact_sha256!r}")
    # parser_version may contain dots; allow that.
    version_safe = parser_version.lower().strip()
    if not re.fullmatch(r"[a-z0-9_\-\.]+", version_safe):
        raise ValueError(f"Unsafe parser_version: {version_safe!r}")
    return "/".join(
        [
            "parsed",
            _safe_slug(source_slug),
            _safe_slug(parser_name),
            version_safe,
            artifact_sha256[:8],
            filename,
        ]
    )


# ---------------------------------------------------------------------------
# Snapshot output keys
# ---------------------------------------------------------------------------


def snapshot_output_key(
    snapshot_date: _DATE_T,
    bioguide_id: str,
    filename: str,
) -> str:
    """Storage key for a per-member score snapshot export.

    Example:
        snapshots/2025-06-01/A000001/profile.json
    """
    bid = bioguide_id.upper().strip()
    if not re.fullmatch(r"[A-Z][0-9]{6}", bid):
        raise ValueError(f"Invalid bioguide_id format: {bid!r}")
    return "/".join(
        [
            "snapshots",
            _as_date_str(snapshot_date),
            bid,
            filename,
        ]
    )


# ---------------------------------------------------------------------------
# Archive manifest keys
# ---------------------------------------------------------------------------


def archive_manifest_key(snapshot_date: _DATE_T) -> str:
    """Storage key for the dated public snapshot manifest.

    Example:
        archives/2025-06-01/manifest.json
    """
    return "/".join(
        [
            "archives",
            _as_date_str(snapshot_date),
            "manifest.json",
        ]
    )
