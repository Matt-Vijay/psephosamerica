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
from pathlib import Path
from typing import Any

from src.api.contracts import (
    ArtifactCounts,
    HomepageBootstrapPayload,
    MovementFeedPayload,
    SnapshotSummaryPayload,
)
from src.export.local_store import (
    list_artifact_paths,
    load_current_member_lookup,
    load_homepage_bootstrap,
    load_homepage_feed,
    load_latest_manifest,
)
from src.homepage.builders import build_featured_lookup_entries
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
_BOOTSTRAP_PATH = "homepage/bootstrap.json"
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
    try:
        return load_homepage_feed(root), None
    except Exception as exc:
        if isinstance(exc, FileNotFoundError):
            return None, _issue(f"homepage feed file missing: {_HOMEPAGE_PATH}")
        return None, _issue(f"homepage feed failed to parse: {exc}")


def _load_published_homepage_bootstrap(
    root: Path,
) -> tuple[HomepageBootstrapPayload | None, PublishRoundtripIssue | None]:
    try:
        return load_homepage_bootstrap(root), None
    except Exception as exc:
        if isinstance(exc, FileNotFoundError):
            return None, _issue(f"homepage bootstrap file missing: {_BOOTSTRAP_PATH}", path=_BOOTSTRAP_PATH)
        return None, _issue(f"homepage bootstrap failed to parse: {exc}", path=_BOOTSTRAP_PATH)


def _build_db_payload(conn: Any, snapshot_date: dt.date) -> HomepageFeedPayload:
    """Fetch homepage rows from DB and assemble a ``HomepageFeedPayload``."""
    rows = fetch_homepage_feed_rows(conn, limit=_HOMEPAGE_FEED_LIMIT)
    return assemble_homepage_payload(rows, snapshot_date=snapshot_date)


def _build_expected_bootstrap(
    homepage_payload: HomepageFeedPayload,
    *,
    root: Path,
) -> HomepageBootstrapPayload | PublishRoundtripIssue:
    try:
        lookup = load_current_member_lookup(root)
        manifest = load_latest_manifest(root)
    except Exception as exc:
        if isinstance(exc, FileNotFoundError):
            return _issue(f"homepage bootstrap prerequisite missing: {exc}")
        return _issue(f"homepage bootstrap prerequisite failed to load: {exc}")

    artifact_paths = list_artifact_paths(manifest)
    snapshot = SnapshotSummaryPayload(
        snapshot_id=manifest.snapshot_id,
        snapshot_date=homepage_payload.snapshot_date,
        published_at=manifest.created_at,
        root_sha256=manifest.root_sha256,
        total_files=manifest.total_files,
        total_bytes=manifest.total_bytes,
        artifact_counts=ArtifactCounts(
            members=sum(1 for path in artifact_paths if path.startswith("members/")),
            evidence=sum(1 for path in artifact_paths if path.startswith("evidence/")),
            zip_feeds=sum(1 for path in artifact_paths if path.startswith("zip/")),
            homepage_feeds=1,
            current_member_lookups=sum(
                1 for path in artifact_paths if path == "identity/current-member-lookup.json"
            ),
        ),
    )
    return HomepageBootstrapPayload(
        snapshot=snapshot,
        movement=MovementFeedPayload(
            snapshot_date=homepage_payload.snapshot_date,
            top_changes=homepage_payload.top_changes,
            recent_events=homepage_payload.recent_events,
            recent_evidence_card_ids=homepage_payload.recent_evidence_card_ids,
        ),
        featured_lookup_entries=build_featured_lookup_entries(
            homepage_payload.top_changes,
            homepage_payload.recent_events,
            lookup.members,
        ),
    )


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

    Each mismatch produces one ``PublishRoundtripIssue`` with a message
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

    pub_top_changes = [_top_change_signature(summary) for summary in published.top_changes]
    db_top_changes = [_top_change_signature(summary) for summary in db.top_changes]
    top_changes_mismatch = _first_sequence_mismatch(pub_top_changes, db_top_changes)
    if top_changes_mismatch is not None:
        issues.append(
            _issue(
                "top_changes mismatch: "
                f"{top_changes_mismatch}"
            )
        )

    pub_recent_events = [_recent_event_signature(event) for event in published.recent_events]
    db_recent_events = [_recent_event_signature(event) for event in db.recent_events]
    recent_events_mismatch = _first_sequence_mismatch(pub_recent_events, db_recent_events)
    if recent_events_mismatch is not None:
        issues.append(
            _issue(
                "recent_events mismatch: "
                f"{recent_events_mismatch}"
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


def _compare_bootstrap_payloads(
    published: HomepageBootstrapPayload,
    expected: HomepageBootstrapPayload,
) -> list[PublishRoundtripIssue]:
    issues: list[PublishRoundtripIssue] = []
    if published.snapshot != expected.snapshot:
        issues.append(
            _issue(
                f"bootstrap snapshot mismatch: published={published.snapshot.model_dump(mode='json')!r} "
                f"expected={expected.snapshot.model_dump(mode='json')!r}",
                path=_BOOTSTRAP_PATH,
            )
        )
    if published.movement != expected.movement:
        issues.append(
            _issue(
                "bootstrap movement mismatch",
                path=_BOOTSTRAP_PATH,
            )
        )
    if published.featured_lookup_entries != expected.featured_lookup_entries:
        issues.append(
            _issue(
                "bootstrap featured_lookup_entries mismatch",
                path=_BOOTSTRAP_PATH,
            )
        )
    return issues


def _top_change_signature(summary: Any) -> dict[str, Any]:
    return {
        "bioguide_id": summary.bioguide_id,
        "name": summary.name,
        "slug": summary.slug,
        "chamber": summary.chamber,
        "party": summary.party,
        "state": summary.state,
        "dimension": summary.dimension,
        "score_delta": summary.score_delta,
        "abs_delta": summary.abs_delta,
        "event_count": summary.event_count,
        "top_evidence_card_ids": summary.top_evidence_card_ids,
    }


def _recent_event_signature(event: Any) -> dict[str, Any]:
    return {
        "feed_event_id": event.feed_event_id,
        "member_bioguide_id": event.member_bioguide_id,
        "member_name": event.member_name,
        "member_slug": event.member_slug,
        "dimension": event.dimension,
        "score_delta": event.score_delta,
        "short_explanation": event.short_explanation,
        "evidence_card_id": event.evidence_card_id,
        "occurred_at": event.occurred_at,
    }


def _first_sequence_mismatch(
    published: list[dict[str, Any]],
    db: list[dict[str, Any]],
) -> str | None:
    if len(published) != len(db):
        return f"length published={len(published)!r} db={len(db)!r}"

    for index, (published_item, db_item) in enumerate(zip(published, db)):
        if published_item != db_item:
            return (
                f"index={index} published={published_item!r} "
                f"db={db_item!r}"
            )
    return None


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
    assert published is not None
    bootstrap, bootstrap_issue = _load_published_homepage_bootstrap(root)
    if bootstrap_issue is not None:
        return PublishRoundtripStageResult(
            stage=_STAGE, checked=0, issues=(bootstrap_issue,)
        )
    assert bootstrap is not None

    # Stage 2: build DB payload.
    db_payload = _build_db_payload(conn, snapshot_date)
    expected_bootstrap = _build_expected_bootstrap(db_payload, root=root)
    if isinstance(expected_bootstrap, PublishRoundtripIssue):
        return PublishRoundtripStageResult(
            stage=_STAGE, checked=0, issues=(expected_bootstrap,)
        )

    # Stage 3: compare typed payloads.
    issues = _compare_payloads(published, db_payload)
    issues.extend(_compare_bootstrap_payloads(bootstrap, expected_bootstrap))

    return PublishRoundtripStageResult(
        stage=_STAGE,
        checked=1,
        issues=tuple(issues),
    )
