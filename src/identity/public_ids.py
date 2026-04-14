"""Deterministic public ID builders for Open Pact.

All builders are pure functions with no I/O.  IDs are:
- Deterministic: same inputs always produce the same ID.
- URL-safe: only lowercase alphanumeric characters plus hyphens.
- Short enough for public use: a 2-char prefix + 16-char digest = 19 chars.

Scheme
------
1. Normalize all inputs to a canonical string form.
2. Concatenate with ``|`` as the field separator.
3. BLAKE2b-digest the UTF-8 bytes (digest_size=10 → 80 bits of collision resistance).
4. Encode with base32 (no padding, lowercase) → 16 URL-safe characters.
5. Prepend a 2-char type prefix so IDs are self-describing at a glance.

Prefixes
--------
- ``ec`` → evidence card
- ``ss`` → score snapshot
- ``fe`` → feed event
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import re
import unicodedata


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def normalize_bioguide_id(raw: str) -> str:
    """Return the canonical form of a bioguide ID: stripped, uppercase.

    Bioguide IDs are one letter followed by six digits, e.g. ``A000001``.
    """
    return raw.strip().upper()


def normalize_date(d: dt.date | str) -> str:
    """Return an ISO-8601 date string (``YYYY-MM-DD``) from a date or string.

    Accepts :class:`datetime.date`, :class:`datetime.datetime` (date part
    only), or a string already in ``YYYY-MM-DD`` form.

    Raises:
        ValueError: If a string cannot be parsed as an ISO-8601 date.
    """
    if isinstance(d, dt.datetime):
        return d.date().isoformat()
    if isinstance(d, dt.date):
        return d.isoformat()
    # string path — validate by parsing
    parsed = dt.date.fromisoformat(d.strip())
    return parsed.isoformat()


def normalize_rule_id(raw: str) -> str:
    """Return the canonical form of a rule ID: stripped, lowercase.

    Leading/trailing whitespace and any surrounding punctuation are removed.
    Internal whitespace is collapsed to a single underscore.
    """
    stripped = raw.strip().lower()
    normalized = re.sub(r"\s+", "_", stripped)
    return normalized


def normalize_dimension(raw: str) -> str:
    """Return the canonical form of a dimension string: stripped, lowercase.

    Spaces are replaced with underscores so ``'conflict of interest risk'``
    and ``'conflict_of_interest_risk'`` produce the same key.
    """
    stripped = unicodedata.normalize("NFC", raw.strip()).lower()
    return re.sub(r"[\s-]+", "_", stripped)


def normalize_zip(raw: str) -> str:
    """Return the canonical ZIP code: digits only, zero-padded to 5 chars.

    Accepts ``'01234'``, ``' 01234 '``, ``'01234-5678'`` (ZIP+4 is stripped).

    Raises:
        ValueError: If the result is not 5 digits.
    """
    digits = re.sub(r"\D", "", raw.strip())
    if len(digits) >= 9:
        digits = digits[:5]
    if len(digits) != 5:
        raise ValueError(f"Cannot parse '{raw}' as a 5-digit ZIP code")
    return digits.zfill(5)


# ---------------------------------------------------------------------------
# Internal digest helper
# ---------------------------------------------------------------------------


_DIGEST_SIZE = 10  # 80 bits — ample for non-adversarial collision resistance


def _digest(parts: list[str], prefix: str) -> str:
    """Hash *parts* joined by ``|`` and return a prefixed, URL-safe ID.

    The digest is BLAKE2b truncated to *_DIGEST_SIZE* bytes, base32-encoded
    (RFC 4648), stripped of ``=`` padding, and lowercased.
    """
    payload = "|".join(parts).encode("utf-8")
    raw = hashlib.blake2b(payload, digest_size=_DIGEST_SIZE).digest()
    encoded = base64.b32encode(raw).decode("ascii").rstrip("=").lower()
    return f"{prefix}-{encoded}"


# ---------------------------------------------------------------------------
# Public ID builders
# ---------------------------------------------------------------------------


def build_evidence_card_id(
    bioguide_id: str,
    rule_id: str,
    dimension: str,
    fired_date: dt.date | str,
) -> str:
    """Build a deterministic public ID for an evidence card.

    The ID is stable across recomputes as long as the same member, rule, and
    rule-fire date are supplied.  Use the *date of the rule fire event* (not
    the snapshot date) as ``fired_date`` so an amended disclosure that
    re-fires the same rule on a different date produces a distinct card.

    Args:
        bioguide_id: Member's bioguide ID (e.g. ``'A000001'``).
        rule_id: Rule identifier (e.g. ``'committee_sector_trade'``).
        dimension: Score dimension (e.g. ``'conflict_of_interest_risk'``).
        fired_date: Date the rule fired.

    Returns:
        A 19-character URL-safe string starting with ``'ec-'``.
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
    """Build a deterministic public ID for a score snapshot.

    One snapshot per member per date — the combination is the natural key.

    Args:
        bioguide_id: Member's bioguide ID.
        snapshot_date: Date of the published snapshot.

    Returns:
        A 19-character URL-safe string starting with ``'ss-'``.
    """
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
    """Build a deterministic public ID for a ZIP-feed event.

    A feed event represents one evidence card appearing in a specific ZIP
    feed on a specific date.  Including ``zip_code`` ensures that the same
    evidence card shown in two different ZIP feeds gets distinct feed-event
    IDs, which matters for stable pagination cursors and deduplication.

    Args:
        zip_code: 5-digit ZIP code for the feed.
        bioguide_id: Member's bioguide ID.
        event_type: Category string (e.g. ``'evidence_card'``, ``'snapshot'``).
        event_date: Date the event belongs to.

    Returns:
        A 19-character URL-safe string starting with ``'fe-'``.
    """
    parts = [
        normalize_zip(zip_code),
        normalize_bioguide_id(bioguide_id),
        normalize_dimension(event_type),
        normalize_date(event_date),
    ]
    return _digest(parts, "fe")
