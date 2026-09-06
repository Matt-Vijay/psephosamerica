"""Reusable temp published-snapshot fixture support.

Creates a temporary publish tree from inline Python fixtures so tests
can exercise snapshot-reading, verification, and oracle code without
static binary fixtures or network calls.

Public API
----------
PublishedSnapshotBuilder
    Fluent builder that writes a complete publish tree into a real
    temp directory using the production plan_snapshot / write_planned_files
    pipeline.

make_snapshot(tmp_path, **kwargs) -> PublishedSnapshot
    One-call convenience wrapper.  Returns a PublishedSnapshot whose root
    is *tmp_path*.

All data is generated from inline Python dicts; no binary blobs are created.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from src.export.contracts import (
    CommitteeMembership,
    ConfidenceLabel,
    EvidenceBlock,
    EvidenceCardPayload,
    EvidenceSection,
    HistoricalCommitteeMembership,
    MemberHistoryEvent,
    MemberHistoryPayload,
    MemberHistorySnapshot,
    MemberProfilePayload,
    RecentRuleFire,
    ScoreSummary,
    SourceAnchor,
    ZipFeedPayload,
    ZipMemberSummary,
)
from src.export.filesystem import write_planned_files
from src.export.writer import plan_snapshot
from src.ontology.contracts import OntologyEdgePayload
from src.prediction.contracts import PredictionReadinessPayload

# ---------------------------------------------------------------------------
# Minimal inline defaults
# ---------------------------------------------------------------------------

_SNAPSHOT_DATE = date(2026, 1, 1)
_CREATED_AT = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

_DEFAULT_EVIDENCE_CARD = EvidenceCardPayload(
    evidence_card_id="ec-0001",
    member_bioguide_id="P000197",
    member_name="Nancy Pelosi",
    member_slug="nancy-pelosi",
    dimension="conflict_of_interest_risk",
    rule_id="committee_sector_trade",
    rule_version=1,
    score_delta=5.0,
    short_explanation="Traded energy stocks while serving on energy committee.",
    blocks=[
        EvidenceBlock(
            section=EvidenceSection.FACT,
            text="PTR discloses sale of XYZ Energy stock on 2025-03-01.",
        ),
        EvidenceBlock(
            section=EvidenceSection.INFERENCE,
            text="Member served on Energy and Commerce at time of trade.",
        ),
    ],
    source_anchors=[
        SourceAnchor(
            source_type="financial_disclosure",
            source_id="fd-001",
            url="https://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd-001.pdf",
            label="2025 PTR filing",
        ),
    ],
    confidence=ConfidenceLabel.HIGH,
    snapshot_date=_SNAPSHOT_DATE,
    created_at=_CREATED_AT,
)

_DEFAULT_MEMBER_PROFILE = MemberProfilePayload(
    bioguide_id="P000197",
    name="Nancy Pelosi",
    slug="nancy-pelosi",
    state="CA",
    district="11",
    chamber="house",
    party="Democrat",
    scores=[
        ScoreSummary(
            dimension="conflict_of_interest_risk",
            current_score=5.0,
            rule_fire_count=1,
        )
    ],
    recent_rule_fires=[
        RecentRuleFire(
            rule_id="committee_sector_trade",
            evidence_card_id="ec-0001",
            short_explanation="Traded energy stocks while serving on energy committee.",
            score_delta=5.0,
            snapshot_date=_SNAPSHOT_DATE,
        )
    ],
    top_evidence_card_ids=["ec-0001"],
    committees=[CommitteeMembership(committee_name="Energy and Commerce", role="Member")],
    total_evidence_cards=1,
    snapshot_date=_SNAPSHOT_DATE,
)

_DEFAULT_ZIP_MEMBER_SUMMARY = ZipMemberSummary(
    bioguide_id="P000197",
    name="Nancy Pelosi",
    slug="nancy-pelosi",
    chamber="house",
    party="Democrat",
    scores=[
        ScoreSummary(
            dimension="conflict_of_interest_risk",
            current_score=5.0,
            rule_fire_count=1,
        )
    ],
    top_evidence_card_ids=["ec-0001"],
)


def _snapshot_date_from_id(snapshot_id: str) -> date:
    return date.fromisoformat(snapshot_id)


def _snapshot_timestamp(snapshot_date: date) -> datetime:
    return datetime.combine(snapshot_date, datetime.min.time(), tzinfo=UTC)


def _default_evidence_card_for_snapshot(snapshot_date: date) -> EvidenceCardPayload:
    return _DEFAULT_EVIDENCE_CARD.model_copy(
        update={
            "snapshot_date": snapshot_date,
            "created_at": _snapshot_timestamp(snapshot_date),
        }
    )


def _default_member_profile_for_snapshot(snapshot_date: date) -> MemberProfilePayload:
    return _DEFAULT_MEMBER_PROFILE.model_copy(
        update={
            "snapshot_date": snapshot_date,
            "recent_rule_fires": [
                recent_fire.model_copy(update={"snapshot_date": snapshot_date})
                for recent_fire in _DEFAULT_MEMBER_PROFILE.recent_rule_fires
            ],
        }
    )


def _default_member_history_for_snapshot(snapshot_date: date) -> MemberHistoryPayload:
    timestamp = _snapshot_timestamp(snapshot_date)
    return make_member_history(
        snapshot_date=snapshot_date,
        published_at=timestamp,
        fired_at=timestamp,
    )


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PublishedSnapshot:
    """A written publish tree ready for test assertions.

    Attributes:
        root:        Directory that holds members/, evidence/, zip/, snapshots/.
        snapshot_id: Identifier string used when building the manifest path.
    """

    root: Path
    snapshot_id: str


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class PublishedSnapshotBuilder:
    """Write an in-memory publish tree to a real temp directory.

    Usage::

        builder = PublishedSnapshotBuilder(tmp_path, snapshot_id="2026-01-01")
        builder.with_member_profiles([...]).with_evidence_cards([...]).build()
        snap = builder.snapshot()
    """

    def __init__(self, root: Path, snapshot_id: str = "2026-01-01") -> None:
        self._root = root
        self._snapshot_id = snapshot_id
        snapshot_date = _snapshot_date_from_id(snapshot_id)
        self._member_profiles: list[MemberProfilePayload] = [
            _default_member_profile_for_snapshot(snapshot_date)
        ]
        self._member_histories: list[MemberHistoryPayload] = [
            _default_member_history_for_snapshot(snapshot_date)
        ]
        self._evidence_cards: list[EvidenceCardPayload] = [
            _default_evidence_card_for_snapshot(snapshot_date)
        ]
        self._ontology_edges: list[OntologyEdgePayload] | None = None
        self._prediction_readiness: PredictionReadinessPayload | None = None
        self._zip_feeds: list[ZipFeedPayload] = []

    # ------------------------------------------------------------------
    # Fluent setters
    # ------------------------------------------------------------------

    def with_member_profiles(
        self, profiles: list[MemberProfilePayload]
    ) -> PublishedSnapshotBuilder:
        self._member_profiles = profiles
        return self

    def with_evidence_cards(self, cards: list[EvidenceCardPayload]) -> PublishedSnapshotBuilder:
        self._evidence_cards = cards
        return self

    def with_member_histories(
        self, histories: list[MemberHistoryPayload]
    ) -> PublishedSnapshotBuilder:
        self._member_histories = histories
        return self

    def with_ontology_edges(self, edges: list[OntologyEdgePayload]) -> PublishedSnapshotBuilder:
        self._ontology_edges = edges
        return self

    def with_prediction_readiness(
        self, payload: PredictionReadinessPayload
    ) -> PublishedSnapshotBuilder:
        self._prediction_readiness = payload
        return self

    def with_zip_feeds(self, feeds: list[ZipFeedPayload]) -> PublishedSnapshotBuilder:
        self._zip_feeds = feeds
        return self

    def no_zip_feeds(self) -> PublishedSnapshotBuilder:
        self._zip_feeds = []
        return self

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(self) -> PublishedSnapshotBuilder:
        """Plan and write all publish-tree files; returns self for chaining."""
        self._root.mkdir(parents=True, exist_ok=True)
        planned = plan_snapshot(
            snapshot_id=self._snapshot_id,
            member_profiles=self._member_profiles,
            zip_feeds=self._zip_feeds,
            evidence_cards=self._evidence_cards,
            member_histories=self._member_histories,
            ontology_edges=self._ontology_edges,
            prediction_readiness=self._prediction_readiness,
            snapshot_date=(
                self._member_profiles[0].snapshot_date if self._member_profiles else _SNAPSHOT_DATE
            ),
        )
        write_planned_files(planned, self._root)
        return self

    def snapshot(self) -> PublishedSnapshot:
        """Return a PublishedSnapshot rooted at the builder's directory."""
        return PublishedSnapshot(root=self._root, snapshot_id=self._snapshot_id)


# ---------------------------------------------------------------------------
# Convenience wrapper
# ---------------------------------------------------------------------------


def make_snapshot(
    tmp_path: Path,
    *,
    snapshot_id: str = "2026-01-01",
    member_profiles: list[MemberProfilePayload] | None = None,
    member_histories: list[MemberHistoryPayload] | None = None,
    ontology_edges: list[OntologyEdgePayload] | None = None,
    prediction_readiness: PredictionReadinessPayload | None = None,
    evidence_cards: list[EvidenceCardPayload] | None = None,
    zip_feeds: list[ZipFeedPayload] | None = None,
) -> PublishedSnapshot:
    """Write a publish tree and return a PublishedSnapshot.

    Args:
        tmp_path:        Root directory (typically pytest's tmp_path fixture).
        snapshot_id:     Identifier used in the manifest path.
        member_profiles: Override the default member profile list.
        evidence_cards:  Override the default evidence card list.
        zip_feeds:       Provide ZIP feed files (empty by default).

    Returns:
        A PublishedSnapshot with root=*tmp_path* and the requested content written.
    """
    builder = PublishedSnapshotBuilder(tmp_path, snapshot_id=snapshot_id)

    if member_profiles is not None:
        builder.with_member_profiles(member_profiles)
    if member_histories is not None:
        builder.with_member_histories(member_histories)
        if evidence_cards is None:
            evidence_cards = _evidence_cards_for_histories(member_histories)
    if ontology_edges is not None:
        builder.with_ontology_edges(ontology_edges)
    if prediction_readiness is not None:
        builder.with_prediction_readiness(prediction_readiness)
    if evidence_cards is not None:
        builder.with_evidence_cards(evidence_cards)
    if zip_feeds is not None:
        builder.with_zip_feeds(zip_feeds)

    builder.build()
    return builder.snapshot()


def _evidence_cards_for_histories(
    histories: list[MemberHistoryPayload],
) -> list[EvidenceCardPayload]:
    seen_ids: set[str] = set()
    cards: list[EvidenceCardPayload] = []
    for history in histories:
        for event in history.events:
            card_id = event.evidence_card_id
            if not card_id or card_id in seen_ids:
                continue
            seen_ids.add(card_id)
            cards.append(
                make_evidence_card(
                    evidence_card_id=card_id,
                    member_slug=history.slug,
                    member_bioguide_id=history.bioguide_id,
                    member_name=history.name,
                    score_delta=event.score_delta,
                    snapshot_date=event.snapshot_date
                    or (
                        history.snapshots[-1].snapshot_date if history.snapshots else _SNAPSHOT_DATE
                    ),
                    created_at=event.fired_at,
                )
            )
    return cards


# ---------------------------------------------------------------------------
# Internal helpers for building ad-hoc payloads in tests
# ---------------------------------------------------------------------------


def make_evidence_card(
    *,
    evidence_card_id: str = "ec-0001",
    member_slug: str = "nancy-pelosi",
    member_bioguide_id: str = "P000197",
    member_name: str = "Nancy Pelosi",
    score_delta: float = 5.0,
    snapshot_date: date = _SNAPSHOT_DATE,
    created_at: datetime | None = None,
) -> EvidenceCardPayload:
    """Return a minimal EvidenceCardPayload with caller-controlled key fields."""
    return EvidenceCardPayload(
        evidence_card_id=evidence_card_id,
        member_bioguide_id=member_bioguide_id,
        member_name=member_name,
        member_slug=member_slug,
        dimension="conflict_of_interest_risk",
        rule_id="committee_sector_trade",
        rule_version=1,
        score_delta=score_delta,
        short_explanation="Test explanation.",
        blocks=[EvidenceBlock(section=EvidenceSection.FACT, text="Test fact.")],
        source_anchors=[
            SourceAnchor(
                source_type="financial_disclosure",
                source_id="fd-001",
                url="https://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd-001.pdf",
                label="Test filing",
            )
        ],
        confidence=ConfidenceLabel.HIGH,
        snapshot_date=snapshot_date,
        created_at=created_at or _CREATED_AT,
    )


def make_member_profile(
    *,
    bioguide_id: str = "P000197",
    slug: str = "nancy-pelosi",
    name: str = "Nancy Pelosi",
    chamber: Literal["house", "senate"] = "house",
    state: str = "CA",
    snapshot_date: date = _SNAPSHOT_DATE,
) -> MemberProfilePayload:
    """Return a minimal MemberProfilePayload with caller-controlled key fields."""
    return MemberProfilePayload(
        bioguide_id=bioguide_id,
        name=name,
        slug=slug,
        state=state,
        district=None,
        chamber=chamber,
        party="Democrat",
        scores=[],
        recent_rule_fires=[],
        committees=[],
        total_evidence_cards=0,
        snapshot_date=snapshot_date,
    )


def make_member_history(
    *,
    bioguide_id: str = "P000197",
    slug: str = "nancy-pelosi",
    name: str = "Nancy Pelosi",
    chamber: Literal["house", "senate"] = "house",
    state: str = "CA",
    snapshot_date: date = _SNAPSHOT_DATE,
    published_at: datetime | None = None,
    fired_at: datetime | None = None,
) -> MemberHistoryPayload:
    resolved_published_at = published_at or _CREATED_AT
    resolved_fired_at = fired_at or _CREATED_AT
    return MemberHistoryPayload(
        bioguide_id=bioguide_id,
        name=name,
        slug=slug,
        state=state,
        district=None,
        chamber=chamber,
        party="Democrat",
        snapshots=[
            MemberHistorySnapshot(
                snapshot_date=snapshot_date,
                score_total=5.0,
                score_total_delta=None,
                dimension_scores={"conflict_of_interest_risk": 5.0},
                published_at=resolved_published_at,
            )
        ],
        events=[
            MemberHistoryEvent(
                rule_id="committee_sector_trade",
                dimension="conflict_of_interest_risk",
                severity="high",
                evidence_card_id="ec-0001",
                short_explanation="Test explanation.",
                score_delta=5.0,
                snapshot_date=snapshot_date,
                fired_at=resolved_fired_at,
            )
        ],
        committee_history=[
            HistoricalCommitteeMembership(
                committee_name="Energy and Commerce",
                role="Member",
                start_date=snapshot_date,
                end_date=None,
                is_current=True,
                chamber=chamber,
                committee_type="standing",
            )
        ],
    )


def make_zip_feed(
    *,
    zip_code: str = "94102",
    members: list[ZipMemberSummary] | None = None,
    snapshot_date: date = _SNAPSHOT_DATE,
) -> ZipFeedPayload:
    """Return a minimal ZipFeedPayload with caller-controlled key fields."""
    return ZipFeedPayload(
        zip_code=zip_code,
        congressional_district=None,
        ambiguity_note=None,
        members=members if members is not None else [_DEFAULT_ZIP_MEMBER_SUMMARY],
        snapshot_date=snapshot_date,
    )
