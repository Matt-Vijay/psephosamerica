"""Deterministic public ID builders.

Scheme: normalize inputs → join with ``|`` → BLAKE2b (digest_size=10) → base32 (no
padding, lowercase) → prepend 2-char type prefix → 19-char URL-safe string.

Prefixes: ``ec`` evidence card · ``ss`` score snapshot · ``fe`` feed event
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import re
import unicodedata

# Normalization helpers


def normalize_bioguide_id(raw: str) -> str:
    # Format: one letter + six digits, e.g. A000001.
    return raw.strip().upper()


def normalize_date(d: dt.date | str) -> str:
    # Raises ValueError for unparseable strings.
    if isinstance(d, dt.datetime):
        return d.date().isoformat()
    if isinstance(d, dt.date):
        return d.isoformat()
    return dt.date.fromisoformat(d.strip()).isoformat()


def normalize_rule_id(raw: str) -> str:
    # Internal spaces → underscores so "late disclosure" == "late_disclosure".
    return re.sub(r"\s+", "_", raw.strip().lower())


def normalize_dimension(raw: str) -> str:
    # Spaces and hyphens → underscores so all three forms produce the same key:
    # "conflict of interest risk", "conflict-of-interest-risk", "conflict_of_interest_risk".
    stripped = unicodedata.normalize("NFC", raw.strip()).lower()
    return re.sub(r"[\s-]+", "_", stripped)


def normalize_zip(raw: str) -> str:
    # Accepts ZIP+4 (strips the +4). Raises ValueError if result is not 5 digits.
    digits = re.sub(r"\D", "", raw.strip())
    if len(digits) >= 9:
        digits = digits[:5]
    if len(digits) != 5:
        raise ValueError(f"Cannot parse '{raw}' as a 5-digit ZIP code")
    return digits.zfill(5)


# Internal digest helper


_DIGEST_SIZE = 10  # 80 bits — ample for non-adversarial collision resistance


def _digest(parts: list[str], prefix: str) -> str:
    payload = "|".join(parts).encode("utf-8")
    raw = hashlib.blake2b(payload, digest_size=_DIGEST_SIZE).digest()
    encoded = base64.b32encode(raw).decode("ascii").rstrip("=").lower()
    return f"{prefix}-{encoded}"


# Public ID builders


def build_evidence_card_id(
    bioguide_id: str,
    rule_id: str,
    dimension: str,
    fired_date: dt.date | str,
) -> str:
    """Stable public ID for an evidence card.

    Pass the *rule fire date*, not the snapshot date — an amended disclosure
    that re-fires the same rule on a different date must produce a distinct ID.
    """
    parts = [
        normalize_bioguide_id(bioguide_id),
        normalize_rule_id(rule_id),
        normalize_dimension(dimension),
        normalize_date(fired_date),
    ]
    return _digest(parts, "ec")


def build_snapshot_id(
    bioguide_id: str,
    snapshot_date: dt.date | str,
) -> str:
    """Stable public ID for a score snapshot (one per member per date)."""
    parts = [
        normalize_bioguide_id(bioguide_id),
        normalize_date(snapshot_date),
    ]
    return _digest(parts, "ss")


def build_feed_event_id(
    zip_code: str,
    bioguide_id: str,
    event_type: str,
    event_date: dt.date | str,
) -> str:
    """Stable public ID for a ZIP-feed event.

    ``zip_code`` is included so the same evidence card appearing in two
    different ZIP feeds gets distinct IDs — required for stable pagination
    cursors and per-feed deduplication.
    """
    parts = [
        normalize_zip(zip_code),
        normalize_bioguide_id(bioguide_id),
        normalize_dimension(event_type),
        normalize_date(event_date),
    ]
    return _digest(parts, "fe")


def build_history_event_id(
    bioguide_id: str,
    snapshot_date: dt.date | str,
    rule_id: str,
    dimension: str,
    *,
    evidence_card_id: str | None = None,
    fired_at: dt.datetime | None = None,
    ordinal: int = 1,
) -> str:
    """Stable public ID for a member-history timeline event."""
    parts = [
        normalize_bioguide_id(bioguide_id),
        normalize_date(snapshot_date),
        normalize_rule_id(rule_id),
        normalize_dimension(dimension),
        (evidence_card_id or "").strip().lower(),
        fired_at.astimezone(dt.UTC).isoformat() if fired_at is not None else "",
        str(ordinal),
    ]
    return _digest(parts, "he")
