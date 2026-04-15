"""ZIP-feed DB->publish roundtrip verification.

Entry point: verify_published_zip_roundtrip(conn, root, manifest_payload, snapshot_date)

Walks each ZIP entry in *manifest_payload*, loads the published ZipFeedPayload
from *root*, re-assembles an equivalent payload from the DB, and compares the
two typed payloads field-by-field.

ZIP feeds are optional: zero ZIP entries is valid and returns ok with checked=0.

Re-assembly strategy
--------------------
Geographic structure (which members represent the ZIP) is read from the
published payload itself.  This lets the function work without external
crosswalk files.  The DB-sourced fields — per-member score summaries and
top evidence card IDs — are re-fetched and compared against the published
values, which is the meaningful roundtrip boundary.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from src.export.contracts import ZipFeedPayload
from src.export.local_store import load_zip_feed
from src.export.manifest import SnapshotManifest
from src.query.zip_feed import assemble_zip_feed
from src.query.zip_rows import (
    fetch_recent_evidence_ids_by_bioguide,
    fetch_zip_member_summary_rows,
)
from src.runtime.publish_roundtrip_types import (
    PublishRoundtripIssue,
    PublishRoundtripStageResult,
)
from src.zip.resolve import (
    FederalBundle,
    MemberRef,
    PluralityDistrict,
)

_STAGE = "zip"
_ZIP_PREFIX = "zip/"
_ZIP_SUFFIX = ".json"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _zip_code_from_path(path: str) -> str | None:
    """Extract zip code from a manifest path like ``zip/90210.json``."""
    if path.startswith(_ZIP_PREFIX) and path.endswith(_ZIP_SUFFIX):
        name = path[len(_ZIP_PREFIX): -len(_ZIP_SUFFIX)]
        if name:
            return name
    return None


# ---------------------------------------------------------------------------
# Bundle reconstruction from published payload
# ---------------------------------------------------------------------------


def _parse_district(congressional_district: str | None) -> tuple[str, int] | None:
    """Parse ``"CA-30"`` into ``("CA", 30)``.  Returns None on bad or absent input."""
    if not congressional_district:
        return None
    parts = congressional_district.split("-", 1)
    if len(parts) != 2:
        return None
    state, district_str = parts
    try:
        return state, int(district_str)
    except ValueError:
        return None


def _bundle_from_payload(published: ZipFeedPayload) -> FederalBundle | None:
    """Reconstruct a FederalBundle from a published ZipFeedPayload.

    Geographic structure is taken as given from the published payload so that
    the roundtrip can be run without external crosswalk data.  Returns None
    when ``congressional_district`` is absent or malformed.
    """
    parsed = _parse_district(published.congressional_district)
    if parsed is None:
        return None
    state, district = parsed

    plurality = PluralityDistrict(
        state=state,
        district=district,
        population_share=1.0,
        is_ambiguous=published.ambiguity_note is not None,
        ambiguity_note=published.ambiguity_note,
    )

    house_member: MemberRef | None = None
    senators: list[MemberRef] = []
    for m in published.members:
        ref = MemberRef(
            bioguide_id=m.bioguide_id,
            full_name=m.name,
            party=m.party,
            slug=m.slug,
            chamber=m.chamber,
        )
        if m.chamber == "house":
            house_member = ref
        else:
            senators.append(ref)

    return FederalBundle(
        zip5=published.zip_code,
        plurality_district=plurality,
        house_member=house_member,
        senators=tuple(senators),
    )


# ---------------------------------------------------------------------------
# Payload comparison
# ---------------------------------------------------------------------------


def _compare_payloads(
    published: ZipFeedPayload,
    reassembled: ZipFeedPayload,
    path: str,
) -> list[PublishRoundtripIssue]:
    """Return issues where DB-sourced fields in *reassembled* differ from *published*.

    Checks per-member score summaries and top evidence card IDs; these are the
    fields whose values come from the DB rather than the geographic crosswalk.
    """
    issues: list[PublishRoundtripIssue] = []

    reassembled_by_bioguide = {m.bioguide_id: m for m in reassembled.members}
    published_by_bioguide = {m.bioguide_id: m for m in published.members}

    for bio_id, pub_member in published_by_bioguide.items():
        if bio_id not in reassembled_by_bioguide:
            issues.append(PublishRoundtripIssue(
                stage=_STAGE,
                message=(
                    f"member {bio_id!r} present in published payload but absent "
                    f"from reassembled payload ({path})"
                ),
                severity="error",
                path=path,
            ))
            continue

        re_member = reassembled_by_bioguide[bio_id]

        pub_scores = sorted(
            (s.dimension, s.current_score, s.rule_fire_count)
            for s in pub_member.scores
        )
        re_scores = sorted(
            (s.dimension, s.current_score, s.rule_fire_count)
            for s in re_member.scores
        )
        if pub_scores != re_scores:
            issues.append(PublishRoundtripIssue(
                stage=_STAGE,
                message=(
                    f"score mismatch for member {bio_id!r} in {path}: "
                    f"published={pub_scores!r}, reassembled={re_scores!r}"
                ),
                severity="error",
                path=path,
            ))

        if pub_member.top_evidence_card_ids != re_member.top_evidence_card_ids:
            issues.append(PublishRoundtripIssue(
                stage=_STAGE,
                message=(
                    f"top_evidence_card_ids mismatch for member {bio_id!r} in {path}: "
                    f"published={pub_member.top_evidence_card_ids!r}, "
                    f"reassembled={re_member.top_evidence_card_ids!r}"
                ),
                severity="error",
                path=path,
            ))

    return issues


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def verify_published_zip_roundtrip(
    conn: Any,
    root: Path,
    manifest_payload: SnapshotManifest,
    snapshot_date: date,
) -> PublishRoundtripStageResult:
    """Verify DB->publish roundtrip for all ZIP feed artifacts in *manifest_payload*.

    For each zip entry:
    1. Loads the published ZipFeedPayload from *root*.
    2. Reconstructs the FederalBundle from the published payload's geographic fields.
    3. Queries *conn* for current score summaries and evidence card IDs.
    4. Re-assembles a ZipFeedPayload and compares DB-sourced fields against the
       published ones.

    ZIP feeds are optional: zero ZIP entries returns ok with ``checked=0``.

    Parameters
    ----------
    conn:
        Active DB connection forwarded to the query helpers.
    root:
        Publish snapshot root directory.
    manifest_payload:
        Parsed manifest for the snapshot being verified.
    snapshot_date:
        Snapshot date used when the feeds were originally assembled.
    """
    zip_entries = [
        (e.path, code)
        for e in manifest_payload.entries
        if (code := _zip_code_from_path(e.path)) is not None
    ]

    if not zip_entries:
        return PublishRoundtripStageResult(stage=_STAGE, checked=0, issues=())

    issues: list[PublishRoundtripIssue] = []

    for path, zip_code in zip_entries:
        try:
            published = load_zip_feed(root, zip_code)
        except FileNotFoundError:
            issues.append(PublishRoundtripIssue(
                stage=_STAGE,
                message=f"zip feed file missing: {path}",
                severity="error",
                path=path,
            ))
            continue
        except Exception as exc:
            issues.append(PublishRoundtripIssue(
                stage=_STAGE,
                message=f"zip feed failed to load ({path}): {exc}",
                severity="error",
                path=path,
            ))
            continue

        bundle = _bundle_from_payload(published)
        if bundle is None:
            issues.append(PublishRoundtripIssue(
                stage=_STAGE,
                message=(
                    f"cannot reconstruct federal bundle from published payload ({path}): "
                    f"congressional_district={published.congressional_district!r}"
                ),
                severity="error",
                path=path,
            ))
            continue

        refs = []
        if bundle.house_member is not None:
            refs.append(bundle.house_member)
        refs.extend(bundle.senators)
        bioguide_ids = [ref.bioguide_id for ref in refs]

        score_rows = fetch_zip_member_summary_rows(conn, bioguide_ids=bioguide_ids)
        evidence_ids = fetch_recent_evidence_ids_by_bioguide(conn, bioguide_ids=bioguide_ids)

        reassembled = assemble_zip_feed(bundle, score_rows, evidence_ids, snapshot_date)

        issues.extend(_compare_payloads(published, reassembled, path))

    return PublishRoundtripStageResult(
        stage=_STAGE,
        checked=len(zip_entries),
        issues=tuple(issues),
    )
