"""Homepage-feed DB → publish roundtrip verification.

Verifies that the published ``homepage/feed.json`` matches the payload
that would be assembled from the current DB state using the same query
and assembly path used at publish time.

Public surface
--------------
verify_published_homepage_roundtrip(conn, root, snapshot_date)
    Load the published homepage file, build a fresh DB payload, compare
    the two typed ``HomepageFeedPayload`` objects field-by-field, and
    return a ``PublishRoundtripStageResult``.

Design notes
------------
- Homepage is not part of the snapshot manifest; this stage runs
  independently of the manifest verification pass.
- Typed payloads are compared, not raw JSON bytes.  Pydantic field equality
  on ``date`` and ``list[str]`` gives deterministic, readable mismatches.
- File load is attempted before the DB is queried; a missing file is a
  terminal error that skips the DB stage.
- ``fetch_homepage_feed_rows`` is the sole DB boundary; it is patched
  in tests as an irreducible external dependency.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from src.homepage.contracts import HomepageFeedPayload
from src.query.homepage_feed import assemble_homepage_payload
from src.query.published_rows import fetch_homepage_feed_rows
from src.runtime.publish_roundtrip_types import (
    IssueSeverity,
    PublishRoundtripIssue,
    PublishRoundtripStageResult,
)

_STAGE = "homepage"
_HOMEPAGE_PATH = "homepage/feed.json"
_HOMEPAGE_FEED_LIMIT = 20


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _issue(
    message: str,
    *,
    path: str | None = _HOMEPAGE_PATH,
    severity: IssueSeverity = "error",
) -> PublishRoundtripIssue:
    return PublishRoundtripIssue(
        stage=_STAGE,
        message=message,
        severity=severity,
        path=path,
    )


def _load_published_homepage(
    root: Path,
) -> tuple[HomepageFeedPayload | None, PublishRoundtripIssue | None]:
    """Read and parse ``homepage/feed.json`` under *root*.

    Returns ``(payload, None)`` on success or ``(None, issue)`` on failure.
    The file is read with :func:`json.loads` and validated via Pydantic so
    any schema error surfaces as a typed issue rather than a traceback.
    """
    file = root / _HOMEPAGE_PATH
    if not file.exists():
        return None, _issue(f"homepage feed file missing: {_HOMEPAGE_PATH}")
    try:
        data = json.loads(file.read_bytes())
        payload = HomepageFeedPayload.model_validate(data)
        return payload, None
    except (json.JSONDecodeError, ValueError, Exception) as exc:
        return None, _issue(f"homepage feed failed to parse: {exc}")


def _build_db_payload(conn: Any, snapshot_date: dt.date) -> HomepageFeedPayload:
    """Fetch homepage rows from DB and assemble a ``HomepageFeedPayload``."""
    rows = fetch_homepage_feed_rows(conn, limit=_HOMEPAGE_FEED_LIMIT)
    return assemble_homepage_payload(rows, snapshot_date=snapshot_date)


def _compare_payloads(
    published: HomepageFeedPayload,
    db: HomepageFeedPayload,
) -> list[PublishRoundtripIssue]:
    """Compare two typed ``HomepageFeedPayload`` objects field by field.

    Checks four fields in order:
    1. ``snapshot_date`` — exact equality.
    2. ``top_changes`` — ordered slug list.
    3. ``recent_events`` — ordered ``feed_event_id`` list.
    4. ``recent_evidence_card_ids`` — ordered deduplicated card-ID list.

    Each mismatch produces one ``PublishVerifyIssue`` with a message
    that names the field and shows both sides.

    Returns an empty list when the payloads agree on all four fields.
    """
    issues: list[PublishRoundtripIssue] = []

    if published.snapshot_date != db.snapshot_date:
        issues.append(
            _issue(
                f"snapshot_date mismatch: "
                f"published={published.snapshot_date!r} db={db.snapshot_date!r}"
            )
        )

    pub_slugs = [s.slug for s in published.top_changes]
    db_slugs = [s.slug for s in db.top_changes]
    if pub_slugs != db_slugs:
        issues.append(
            _issue(
                f"top_changes slugs mismatch: "
                f"published={pub_slugs!r} db={db_slugs!r}"
            )
        )

    pub_event_ids = [e.feed_event_id for e in published.recent_events]
    db_event_ids = [e.feed_event_id for e in db.recent_events]
    if pub_event_ids != db_event_ids:
        issues.append(
            _issue(
                f"recent_events feed_event_ids mismatch: "
                f"published={pub_event_ids!r} db={db_event_ids!r}"
            )
        )

    if published.recent_evidence_card_ids != db.recent_evidence_card_ids:
        issues.append(
            _issue(
                f"recent_evidence_card_ids mismatch: "
                f"published={published.recent_evidence_card_ids!r} "
                f"db={db.recent_evidence_card_ids!r}"
            )
        )

    return issues


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def verify_published_homepage_roundtrip(
    conn: Any,
    root: Path,
    snapshot_date: dt.date,
) -> PublishRoundtripStageResult:
    """Verify the published homepage feed matches the DB read side.

    Runs three explicit stages in order:

    1. **Load** — read ``homepage/feed.json`` from *root* and parse it as a
       typed ``HomepageFeedPayload``.  A missing or unparseable file is a
       terminal error; the DB is not queried.
    2. **Query** — call ``fetch_homepage_feed_rows`` and assemble a fresh
       ``HomepageFeedPayload`` using *snapshot_date*.
    3. **Compare** — compare the two payloads field-by-field using typed
       equality on ``snapshot_date``, ``top_changes``, ``recent_events``,
       and ``recent_evidence_card_ids``.

    Parameters
    ----------
    conn:
        Live database connection.  Passed unchanged to
        ``fetch_homepage_feed_rows``.
    root:
        Publish snapshot root directory (holds ``homepage/``,
        ``members/``, ``evidence/``, etc.).
    snapshot_date:
        Date used when assembling the DB-side payload.  Should match
        the snapshot_date used during the publish run being verified.

    Returns
    -------
    PublishRoundtripStageResult
        Stage name is ``"homepage"``.  ``checked`` is ``1`` when the file
        loads and both payloads are comparable, ``0`` when the file is
        missing or unparseable.  ``ok`` is ``True`` when ``issues`` is
        empty.
    """
    # Stage 1: load published file (terminal on failure).
    published, load_issue = _load_published_homepage(root)
    if load_issue is not None:
        return PublishRoundtripStageResult(
            stage=_STAGE, checked=0, issues=(load_issue,)
        )

    # Stage 2: build DB payload.
    db_payload = _build_db_payload(conn, snapshot_date)

    # Stage 3: compare typed payloads.
    issues = _compare_payloads(published, db_payload)

    return PublishRoundtripStageResult(
        stage=_STAGE,
        checked=1,
        issues=tuple(issues),
    )
