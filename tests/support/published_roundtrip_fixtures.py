"""Reusable roundtrip fixture support for published snapshots plus aligned DB rows.

Creates temp publish trees from inline Python fixtures AND the matching
row sets needed to re-assemble each surface from the DB side.

Public API
----------
MemberRowSet
    DB row sets for one member's profile surface (``assemble_member_profile``
    parameter shapes).

EvidenceCardRowSet
    Flat merged DB row for one evidence card (``assemble_evidence_card``
    parameter shape).

ZipFeedRowSet
    DB row sets for one ZIP feed surface (``build_zip_feed`` member_rows
    format).

HomepageFeedRowSet
    DB rows for the homepage feed surface (``fetch_homepage_feed_rows``
    column shape).

PublishedRoundtrip
    Published snapshot on disk plus all aligned row sets.

PublishedRoundtripBuilder
    Fluent builder; assembles payloads *from* rows, writes the publish tree,
    and returns a ``PublishedRoundtrip``.  The tree is therefore always aligned
    with the rows.

make_roundtrip(tmp_path, **kwargs) -> PublishedRoundtrip
    One-call convenience wrapper.

make_member_row_set / make_evidence_card_row_set / make_zip_feed_row_set /
make_homepage_feed_row_set
    Factory functions for building ad-hoc row sets in tests.

All data is generated from inline Python dicts; no binary blobs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from src.export.builders import build_zip_feed
from src.export.contracts import EvidenceCardPayload, MemberProfilePayload, ZipFeedPayload
from src.export.filesystem import write_planned_files
from src.export.writer import plan_snapshot
from src.homepage.contracts import HomepageFeedPayload
from src.query.evidence_card import assemble_evidence_card
from src.query.homepage_feed import assemble_homepage_payload
from src.query.member_profile import assemble_member_profile
from tests.support.published_snapshot_fixtures import PublishedSnapshot


# ---------------------------------------------------------------------------
# Shared inline defaults
# ---------------------------------------------------------------------------

_SNAPSHOT_DATE = date(2026, 1, 1)
_CREATED_AT = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
_BIOGUIDE_ID = "P000197"
_SLUG = "nancy-pelosi"
_ZIP_CODE = "94102"
_DISTRICT = "CA-11"


def _default_member_row() -> dict[str, Any]:
    return {
        "id": 1,
        "bioguide_id": _BIOGUIDE_ID,
        "full_name": "Nancy Pelosi",
        "slug": _SLUG,
        "state": "CA",
        "district": 11,
        "chamber": "house",
        "party": "Democrat",
    }


def _default_score_snapshot_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "member_id": 1,
            "snapshot_at": _SNAPSHOT_DATE,
            "score_total": 5.0,
            "dimension_scores": {"conflict_of_interest_risk": 5.0},
            "published_at": None,
        }
    ]


def _default_rule_fire_rows() -> list[dict[str, Any]]:
    # Shape matches fetch_member_rule_fire_rows: rule_fire JOIN evidence_card.
    return [
        {
            "id": 1,
            "rule_id": "committee_sector_trade",
            "dimension": "conflict_of_interest_risk",
            "severity": "medium",
            "explanation": "Traded energy stocks while serving on energy committee.",
            "fired_at": _CREATED_AT,
            "evidence_card_id": "ec-0001",
            "short_explanation": "Traded energy stocks while serving on energy committee.",
            "score_delta": 5.0,
            "snapshot_date": _SNAPSHOT_DATE,
        }
    ]


def _default_committee_rows() -> list[dict[str, Any]]:
    # Shape matches fetch_member_committee_rows: committee_membership JOIN committee.
    return [
        {
            "id": 1,
            "role": "Member",
            "committee_name": "Energy and Commerce",
            "is_current": True,
        }
    ]


def _default_evidence_card_row() -> dict[str, Any]:
    # Flat merged row: evidence_card + member + rule_fire columns.
    # Shape matches assemble_evidence_card's expected input.
    return {
        "public_id": "ec-0001",
        "member_bioguide_id": _BIOGUIDE_ID,
        "member_full_name": "Nancy Pelosi",
        "member_slug": _SLUG,
        "dimension": "conflict_of_interest_risk",
        "rule_id": "committee_sector_trade",
        "rule_version": 1,
        "score_delta": 5.0,
        "short_explanation": "Traded energy stocks while serving on energy committee.",
        "facts": ["PTR discloses sale of XYZ Energy stock on 2025-03-01."],
        "inferences": ["Member served on Energy and Commerce at time of trade."],
        "normative_judgments": None,
        "source_anchors": [
            {
                "source_type": "financial_disclosure",
                "source_id": "fd-001",
                "url": None,
                "label": "2025 PTR filing",
            }
        ],
        "confidence_label": "HIGH",
        "rendered_at": _SNAPSHOT_DATE,
        "created_at": _CREATED_AT,
    }


def _default_zip_member_rows() -> list[dict[str, Any]]:
    # Shape matches build_zip_feed's member_rows parameter.
    return [
        {
            "bioguide_id": _BIOGUIDE_ID,
            "name": "Nancy Pelosi",
            "slug": _SLUG,
            "chamber": "house",
            "party": "Democrat",
            "scores": [
                {
                    "dimension": "conflict_of_interest_risk",
                    "current_score": 5.0,
                    "rule_fire_count": 1,
                }
            ],
            "top_evidence_card_ids": ["ec-0001"],
        }
    ]


def _default_homepage_feed_rows() -> list[dict[str, Any]]:
    # Shape matches fetch_homepage_feed_rows columns.
    return [
        {
            "public_id": "ec-0001",
            "dimension": "conflict_of_interest_risk",
            "score_delta": 5.0,
            "short_explanation": "Traded energy stocks while serving on energy committee.",
            "confidence_label": "high",
            "rendered_at": _SNAPSHOT_DATE,
            "member_full_name": "Nancy Pelosi",
            "member_slug": _SLUG,
            "state": "CA",
            "chamber": "house",
            "party": "Democrat",
        }
    ]


# ---------------------------------------------------------------------------
# Row set types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemberRowSet:
    """DB row sets for one member profile surface.

    Fields mirror the parameters of ``query.member_profile.assemble_member_profile``.
    """

    member_row: dict[str, Any]
    score_snapshot_rows: list[dict[str, Any]]
    rule_fire_rows: list[dict[str, Any]]
    committee_rows: list[dict[str, Any]]


@dataclass(frozen=True)
class EvidenceCardRowSet:
    """Merged DB row for one evidence card.

    ``row`` is the flat dict expected by ``query.evidence_card.assemble_evidence_card``
    — evidence_card + member + rule_fire columns merged by the query layer.
    """

    row: dict[str, Any]


@dataclass(frozen=True)
class ZipFeedRowSet:
    """DB row sets for one ZIP feed surface.

    ``member_rows`` follows ``export.builders.build_zip_feed``'s member_rows format:
    each entry carries bioguide_id, name, slug, chamber, party,
    scores (list of {dimension, current_score, rule_fire_count}),
    and top_evidence_card_ids.
    """

    zip_code: str
    district: str | None
    ambiguity_note: str | None
    member_rows: list[dict[str, Any]]


@dataclass(frozen=True)
class HomepageFeedRowSet:
    """DB rows for the homepage feed surface.

    ``rows`` follows the ``published_rows.fetch_homepage_feed_rows`` column shape:
    public_id, dimension, score_delta, short_explanation, confidence_label,
    rendered_at, member_full_name, member_slug, state, chamber, party.
    """

    rows: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PublishedRoundtrip:
    """Published snapshot on disk plus aligned DB row sets.

    The publish tree was written by assembling payloads *from* the row sets,
    so re-assembling from the rows produces payloads equal to those in the tree.

    Attributes:
        snapshot:               On-disk publish tree root and snapshot_id.
        snapshot_date:          Date stamped on all assembled payloads.
        member_row_sets:        One set per member profile written to the tree.
        evidence_card_row_sets: One set per evidence card written to the tree.
        zip_feed_row_sets:      One set per ZIP file written to the tree.
        homepage_feed_row_set:  Row set for rebuilding the homepage feed payload.
    """

    snapshot: PublishedSnapshot
    snapshot_date: date
    member_row_sets: list[MemberRowSet]
    evidence_card_row_sets: list[EvidenceCardRowSet]
    zip_feed_row_sets: list[ZipFeedRowSet]
    homepage_feed_row_set: HomepageFeedRowSet


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class PublishedRoundtripBuilder:
    """Assemble payloads from row sets, write a publish tree, return a roundtrip.

    The payloads written to disk are produced by the same assemblers that tests
    call on the row sets, so the two sides are always aligned.

    Usage::

        rt = (
            PublishedRoundtripBuilder(tmp_path, snapshot_id="2026-01-01")
            .build()
            .roundtrip()
        )
    """

    def __init__(self, root: Path, snapshot_id: str = "2026-01-01") -> None:
        self._root = root
        self._snapshot_id = snapshot_id
        self._snapshot_date: date = _SNAPSHOT_DATE
        self._member_row_sets: list[MemberRowSet] = [
            MemberRowSet(
                member_row=_default_member_row(),
                score_snapshot_rows=_default_score_snapshot_rows(),
                rule_fire_rows=_default_rule_fire_rows(),
                committee_rows=_default_committee_rows(),
            )
        ]
        self._evidence_card_row_sets: list[EvidenceCardRowSet] = [
            EvidenceCardRowSet(row=_default_evidence_card_row())
        ]
        self._zip_feed_row_sets: list[ZipFeedRowSet] = [
            ZipFeedRowSet(
                zip_code=_ZIP_CODE,
                district=_DISTRICT,
                ambiguity_note=None,
                member_rows=_default_zip_member_rows(),
            )
        ]
        self._homepage_feed_row_set: HomepageFeedRowSet = HomepageFeedRowSet(
            rows=_default_homepage_feed_rows()
        )

    # -----------------------------------------------------------------------
    # Fluent setters
    # -----------------------------------------------------------------------

    def with_snapshot_date(self, d: date) -> "PublishedRoundtripBuilder":
        self._snapshot_date = d
        return self

    def with_member_row_sets(
        self, sets: list[MemberRowSet]
    ) -> "PublishedRoundtripBuilder":
        self._member_row_sets = sets
        return self

    def with_evidence_card_row_sets(
        self, sets: list[EvidenceCardRowSet]
    ) -> "PublishedRoundtripBuilder":
        self._evidence_card_row_sets = sets
        return self

    def with_zip_feed_row_sets(
        self, sets: list[ZipFeedRowSet]
    ) -> "PublishedRoundtripBuilder":
        self._zip_feed_row_sets = sets
        return self

    def no_zip_feeds(self) -> "PublishedRoundtripBuilder":
        self._zip_feed_row_sets = []
        return self

    def with_homepage_feed_row_set(
        self, row_set: HomepageFeedRowSet
    ) -> "PublishedRoundtripBuilder":
        self._homepage_feed_row_set = row_set
        return self

    # -----------------------------------------------------------------------
    # Internal assembly
    # -----------------------------------------------------------------------

    def _assemble_member_profiles(self) -> list[MemberProfilePayload]:
        return [
            assemble_member_profile(
                member_row=rs.member_row,
                score_snapshot_rows=rs.score_snapshot_rows,
                rule_fire_rows=rs.rule_fire_rows,
                committee_rows=rs.committee_rows,
            )
            for rs in self._member_row_sets
        ]

    def _assemble_evidence_cards(self) -> list[EvidenceCardPayload]:
        return [assemble_evidence_card(rs.row) for rs in self._evidence_card_row_sets]

    def _assemble_zip_feeds(self) -> list[ZipFeedPayload]:
        return [
            build_zip_feed(
                zip_code=zrs.zip_code,
                district=zrs.district,
                ambiguity_note=zrs.ambiguity_note,
                member_rows=zrs.member_rows,
                snapshot_date=self._snapshot_date,
            )
            for zrs in self._zip_feed_row_sets
        ]

    # -----------------------------------------------------------------------
    # Build
    # -----------------------------------------------------------------------

    def build(self) -> "PublishedRoundtripBuilder":
        """Assemble payloads from rows, write the publish tree; returns self."""
        self._root.mkdir(parents=True, exist_ok=True)
        planned = plan_snapshot(
            snapshot_id=self._snapshot_id,
            member_profiles=self._assemble_member_profiles(),
            zip_feeds=self._assemble_zip_feeds(),
            evidence_cards=self._assemble_evidence_cards(),
        )
        write_planned_files(planned, self._root)
        return self

    def roundtrip(self) -> PublishedRoundtrip:
        """Return a PublishedRoundtrip rooted at the builder's directory."""
        return PublishedRoundtrip(
            snapshot=PublishedSnapshot(root=self._root, snapshot_id=self._snapshot_id),
            snapshot_date=self._snapshot_date,
            member_row_sets=list(self._member_row_sets),
            evidence_card_row_sets=list(self._evidence_card_row_sets),
            zip_feed_row_sets=list(self._zip_feed_row_sets),
            homepage_feed_row_set=self._homepage_feed_row_set,
        )


# ---------------------------------------------------------------------------
# Convenience wrapper
# ---------------------------------------------------------------------------


def make_roundtrip(
    tmp_path: Path,
    *,
    snapshot_id: str = "2026-01-01",
    snapshot_date: date = _SNAPSHOT_DATE,
    member_row_sets: list[MemberRowSet] | None = None,
    evidence_card_row_sets: list[EvidenceCardRowSet] | None = None,
    zip_feed_row_sets: list[ZipFeedRowSet] | None = None,
    homepage_feed_row_set: HomepageFeedRowSet | None = None,
) -> PublishedRoundtrip:
    """Write a publish tree and return a PublishedRoundtrip.

    Args:
        tmp_path:               Root directory (typically pytest's tmp_path).
        snapshot_id:            Identifier used in the manifest path.
        snapshot_date:          Date stamped on all assembled payloads.
        member_row_sets:        Override default member row sets.
        evidence_card_row_sets: Override default evidence card row sets.
        zip_feed_row_sets:      Override default ZIP feed row sets.
        homepage_feed_row_set:  Override default homepage feed row set.
    """
    builder = PublishedRoundtripBuilder(tmp_path, snapshot_id=snapshot_id)
    builder.with_snapshot_date(snapshot_date)
    if member_row_sets is not None:
        builder.with_member_row_sets(member_row_sets)
    if evidence_card_row_sets is not None:
        builder.with_evidence_card_row_sets(evidence_card_row_sets)
    if zip_feed_row_sets is not None:
        builder.with_zip_feed_row_sets(zip_feed_row_sets)
    if homepage_feed_row_set is not None:
        builder.with_homepage_feed_row_set(homepage_feed_row_set)
    builder.build()
    return builder.roundtrip()


# ---------------------------------------------------------------------------
# Row set factory helpers
# ---------------------------------------------------------------------------


def make_member_row_set(
    *,
    bioguide_id: str = _BIOGUIDE_ID,
    slug: str = _SLUG,
    full_name: str = "Nancy Pelosi",
    state: str = "CA",
    district: int | None = 11,
    chamber: str = "house",
    party: str = "Democrat",
    dimension: str = "conflict_of_interest_risk",
    current_score: float = 5.0,
    evidence_card_id: str = "ec-0001",
    score_delta: float = 5.0,
    snapshot_date: date = _SNAPSHOT_DATE,
) -> MemberRowSet:
    """Return a MemberRowSet with caller-controlled key fields."""
    member_row: dict[str, Any] = {
        "id": 1,
        "bioguide_id": bioguide_id,
        "full_name": full_name,
        "slug": slug,
        "state": state,
        "district": district,
        "chamber": chamber,
        "party": party,
    }
    score_snapshot_rows: list[dict[str, Any]] = [
        {
            "id": 1,
            "member_id": 1,
            "snapshot_at": snapshot_date,
            "score_total": current_score,
            "dimension_scores": {dimension: current_score},
            "published_at": None,
        }
    ]
    rule_fire_rows: list[dict[str, Any]] = [
        {
            "id": 1,
            "rule_id": "committee_sector_trade",
            "dimension": dimension,
            "severity": "medium",
            "explanation": "Test explanation.",
            "fired_at": _CREATED_AT,
            "evidence_card_id": evidence_card_id,
            "short_explanation": "Test explanation.",
            "score_delta": score_delta,
            "snapshot_date": snapshot_date,
        }
    ]
    committee_rows: list[dict[str, Any]] = [
        {
            "id": 1,
            "role": "Member",
            "committee_name": "Energy and Commerce",
            "is_current": True,
        }
    ]
    return MemberRowSet(
        member_row=member_row,
        score_snapshot_rows=score_snapshot_rows,
        rule_fire_rows=rule_fire_rows,
        committee_rows=committee_rows,
    )


def make_evidence_card_row_set(
    *,
    public_id: str = "ec-0001",
    bioguide_id: str = _BIOGUIDE_ID,
    member_slug: str = _SLUG,
    member_full_name: str = "Nancy Pelosi",
    dimension: str = "conflict_of_interest_risk",
    score_delta: float = 5.0,
    snapshot_date: date = _SNAPSHOT_DATE,
) -> EvidenceCardRowSet:
    """Return an EvidenceCardRowSet with caller-controlled key fields."""
    row: dict[str, Any] = {
        "public_id": public_id,
        "member_bioguide_id": bioguide_id,
        "member_full_name": member_full_name,
        "member_slug": member_slug,
        "dimension": dimension,
        "rule_id": "committee_sector_trade",
        "rule_version": 1,
        "score_delta": score_delta,
        "short_explanation": "Test explanation.",
        "facts": ["Test fact."],
        "inferences": ["Test inference."],
        "normative_judgments": None,
        "source_anchors": [
            {
                "source_type": "financial_disclosure",
                "source_id": "fd-001",
                "url": None,
                "label": "Test filing",
            }
        ],
        "confidence_label": "HIGH",
        "rendered_at": snapshot_date,
        "created_at": _CREATED_AT,
    }
    return EvidenceCardRowSet(row=row)


def make_zip_feed_row_set(
    *,
    zip_code: str = _ZIP_CODE,
    district: str | None = _DISTRICT,
    ambiguity_note: str | None = None,
    bioguide_id: str = _BIOGUIDE_ID,
    member_name: str = "Nancy Pelosi",
    member_slug: str = _SLUG,
    chamber: str = "house",
    party: str = "Democrat",
    dimension: str = "conflict_of_interest_risk",
    current_score: float = 5.0,
    top_evidence_card_ids: list[str] | None = None,
) -> ZipFeedRowSet:
    """Return a ZipFeedRowSet with caller-controlled key fields."""
    member_rows: list[dict[str, Any]] = [
        {
            "bioguide_id": bioguide_id,
            "name": member_name,
            "slug": member_slug,
            "chamber": chamber,
            "party": party,
            "scores": [
                {
                    "dimension": dimension,
                    "current_score": current_score,
                    "rule_fire_count": 1,
                }
            ],
            "top_evidence_card_ids": top_evidence_card_ids if top_evidence_card_ids is not None else ["ec-0001"],
        }
    ]
    return ZipFeedRowSet(
        zip_code=zip_code,
        district=district,
        ambiguity_note=ambiguity_note,
        member_rows=member_rows,
    )


def make_homepage_feed_row_set(
    *,
    public_id: str = "ec-0001",
    member_slug: str = _SLUG,
    member_full_name: str = "Nancy Pelosi",
    dimension: str = "conflict_of_interest_risk",
    score_delta: float = 5.0,
    state: str = "CA",
    chamber: str = "house",
    party: str = "Democrat",
    snapshot_date: date = _SNAPSHOT_DATE,
) -> HomepageFeedRowSet:
    """Return a HomepageFeedRowSet with caller-controlled key fields."""
    rows: list[dict[str, Any]] = [
        {
            "public_id": public_id,
            "dimension": dimension,
            "score_delta": score_delta,
            "short_explanation": "Test explanation.",
            "confidence_label": "high",
            "rendered_at": snapshot_date,
            "member_full_name": member_full_name,
            "member_slug": member_slug,
            "state": state,
            "chamber": chamber,
            "party": party,
        }
    ]
    return HomepageFeedRowSet(rows=rows)


# ---------------------------------------------------------------------------
# Assembly helpers (for tests that want to re-assemble without building a tree)
# ---------------------------------------------------------------------------


def assemble_from_member_row_set(rs: MemberRowSet) -> MemberProfilePayload:
    """Re-assemble a MemberProfilePayload from a MemberRowSet."""
    return assemble_member_profile(
        member_row=rs.member_row,
        score_snapshot_rows=rs.score_snapshot_rows,
        rule_fire_rows=rs.rule_fire_rows,
        committee_rows=rs.committee_rows,
    )


def assemble_from_evidence_card_row_set(rs: EvidenceCardRowSet) -> EvidenceCardPayload:
    """Re-assemble an EvidenceCardPayload from an EvidenceCardRowSet."""
    return assemble_evidence_card(rs.row)


def assemble_from_zip_feed_row_set(
    rs: ZipFeedRowSet, *, snapshot_date: date = _SNAPSHOT_DATE
) -> ZipFeedPayload:
    """Re-assemble a ZipFeedPayload from a ZipFeedRowSet."""
    return build_zip_feed(
        zip_code=rs.zip_code,
        district=rs.district,
        ambiguity_note=rs.ambiguity_note,
        member_rows=rs.member_rows,
        snapshot_date=snapshot_date,
    )


def assemble_from_homepage_feed_row_set(
    rs: HomepageFeedRowSet, *, snapshot_date: date = _SNAPSHOT_DATE
) -> HomepageFeedPayload:
    """Re-assemble a HomepageFeedPayload from a HomepageFeedRowSet."""
    return assemble_homepage_payload(rs.rows, snapshot_date=snapshot_date)
