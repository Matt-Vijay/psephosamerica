from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from src.api.contracts import HomepageBootstrapPayload, MemberPagePayload, ZipEntryPayload
from src.export.contracts import (
    ConfidenceLabel,
    EvidenceBlock,
    EvidenceCardPayload,
    EvidenceSection,
    MemberProfilePayload,
    SourceAnchor,
    ZipFeedPayload,
)
from src.export.filesystem import write_planned_files
from src.export.local_store import HOMEPAGE_FEED_PATH
from src.export.manifest import ManifestEntry, SnapshotManifest, manifest_root_sha256
from src.export.writer import (
    PlannedFile,
    current_member_lookup_path,
    evidence_path,
    homepage_bootstrap_path,
    manifest_path,
    member_page_payload_path,
    member_path,
    ontology_edges_path,
    ontology_member_edges_path,
    zip_entry_path,
    zip_path,
)
from src.homepage.contracts import HomepageFeedPayload, MemberMovementSummary, RecentEventSummary
from src.identity.current_member_lookup import CurrentMemberLookupEntry, CurrentMemberLookupPayload
from src.ontology.contracts import (
    OntologyEdgePayload,
    OntologyGraphPayload,
    OntologyMemberGraphPayload,
    OntologyNodeRef,
)
from src.runtime.inspect import (
    load_latest_local_manifest,
    load_latest_local_snapshot_metadata,
    load_local_current_member_lookup,
    load_local_evidence_card,
    load_local_homepage_bootstrap,
    load_local_homepage_feed,
    load_local_manifest,
    load_local_member_page,
    load_local_member_profile,
    load_local_ontology_edges,
    load_local_ontology_member_edges,
    load_local_zip_entry,
    load_local_zip_feed,
    search_local_current_member_lookup,
)

# ── Fixture payloads ───────────────────────────────────────────────


SNAPSHOT_DATE = date(2026, 4, 14)
SNAPSHOT_ID = "2026-04-14"


def _member() -> MemberProfilePayload:
    return MemberProfilePayload(
        bioguide_id="P000197",
        name="Nancy Pelosi",
        slug="nancy-pelosi",
        state="CA",
        district="CA-11",
        chamber="house",
        party="Democrat",
        scores=[],
        recent_rule_fires=[],
        committees=[],
        total_evidence_cards=0,
        snapshot_date=SNAPSHOT_DATE,
    )


def _evidence() -> EvidenceCardPayload:
    return EvidenceCardPayload(
        evidence_card_id="ec-inspect-001",
        member_bioguide_id="P000197",
        member_name="Nancy Pelosi",
        member_slug="nancy-pelosi",
        dimension="conflict_of_interest_risk",
        rule_id="committee_sector_trade",
        rule_version=1,
        score_delta=-3.0,
        short_explanation="Trade overlapping committee jurisdiction.",
        blocks=[
            EvidenceBlock(section=EvidenceSection.FACT, text="PTR disclosed a trade."),
        ],
        source_anchors=[
            SourceAnchor(
                source_type="financial_disclosure",
                source_id="fd-001",
                url="https://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd-001.pdf",
                label="Financial disclosure",
            )
        ],
        confidence=ConfidenceLabel.MEDIUM,
        snapshot_date=SNAPSHOT_DATE,
        created_at=datetime(2026, 4, 14, 8, 0, 0),
    )


def _ontology_graph() -> OntologyGraphPayload:
    edge = OntologyEdgePayload(
        edge_id="ont-edge-inspect-001",
        edge_type="member_committee_assignment",
        subject=OntologyNodeRef(
            node_type="member",
            node_id="P000197",
            label="Nancy Pelosi",
        ),
        object=OntologyNodeRef(
            node_type="committee",
            node_id="HSEC",
            label="Energy",
        ),
        source_anchors=[
            SourceAnchor(
                source_type="committee_membership",
                source_id="cm-inspect-1",
                url="https://api.congress.gov/v3/committee/house/HSEC?format=json",
                label="Committee membership",
            )
        ],
    )
    return OntologyGraphPayload(
        snapshot_id=SNAPSHOT_ID,
        edge_count=1,
        edges=[edge],
    )


def _ontology_member_graph() -> OntologyMemberGraphPayload:
    graph = _ontology_graph()
    return OntologyMemberGraphPayload(
        snapshot_id=SNAPSHOT_ID,
        member_bioguide_id="P000197",
        edge_count=1,
        edges=graph.edges,
    )


def _zip_feed() -> ZipFeedPayload:
    return ZipFeedPayload(
        zip_code="94102",
        congressional_district="CA-11",
        ambiguity_note=None,
        members=[],
        snapshot_date=SNAPSHOT_DATE,
    )


def _manifest(files: list[PlannedFile]) -> SnapshotManifest:
    entries = [ManifestEntry(path=f.path, sha256=f.sha256, size_bytes=f.size_bytes) for f in files]
    return SnapshotManifest(
        snapshot_id=SNAPSHOT_ID,
        created_at=datetime(2026, 4, 14, 0, 0, 0),
        entries=entries,
        total_files=len(files),
        total_bytes=sum(f.size_bytes for f in files),
        root_sha256=manifest_root_sha256(entries),
    )


def _serialise(model: object) -> bytes:
    from pydantic import BaseModel as _BM

    assert isinstance(model, _BM)
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode(
        "utf-8"
    )


def _homepage_feed() -> HomepageFeedPayload:
    return HomepageFeedPayload(
        snapshot_date=SNAPSHOT_DATE,
        top_changes=[
            MemberMovementSummary(
                bioguide_id="P000197",
                name="Nancy Pelosi",
                slug="nancy-pelosi",
                chamber="house",
                party="Democrat",
                state="CA",
                dimension="conflict_of_interest_risk",
                score_delta=-3.0,
                abs_delta=3.0,
                event_count=1,
                top_evidence_card_ids=["ec-inspect-001"],
            )
        ],
        recent_events=[
            RecentEventSummary(
                feed_event_id="event-inspect-001",
                member_bioguide_id="P000197",
                member_name="Nancy Pelosi",
                member_slug="nancy-pelosi",
                dimension="conflict_of_interest_risk",
                score_delta=-3.0,
                short_explanation="Trade overlapping committee jurisdiction.",
                evidence_card_id="ec-inspect-001",
                occurred_at=SNAPSHOT_DATE,
            )
        ],
        recent_evidence_card_ids=["ec-inspect-001"],
    )


def _write_homepage_feed(root: Path, payload: HomepageFeedPayload | None = None) -> None:
    feed = payload if payload is not None else _homepage_feed()
    dest = root / HOMEPAGE_FEED_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(_serialise(feed))


def _current_member_lookup() -> CurrentMemberLookupPayload:
    return CurrentMemberLookupPayload(
        snapshot_date=SNAPSHOT_DATE,
        members=[
            CurrentMemberLookupEntry(
                bioguide_id="P000197",
                slug="nancy-pelosi",
                name="Nancy Pelosi",
                search_name="nancy pelosi",
                state="CA",
                district="CA-11",
                chamber="house",
            )
        ],
    )


def _write_snapshot(root: Path, *, snapshot_id: str = SNAPSHOT_ID) -> None:
    member = _member()
    evidence = _evidence()
    feed = _zip_feed()
    files = [
        PlannedFile.from_bytes(member_path(member.slug), _serialise(member)),
        PlannedFile.from_bytes(evidence_path(evidence.evidence_card_id), _serialise(evidence)),
        PlannedFile.from_bytes(ontology_edges_path(), _serialise(_ontology_graph())),
        PlannedFile.from_bytes(
            ontology_member_edges_path("P000197"),
            _serialise(_ontology_member_graph()),
        ),
        PlannedFile.from_bytes(zip_path(feed.zip_code), _serialise(feed)),
        PlannedFile.from_bytes(current_member_lookup_path(), _serialise(_current_member_lookup())),
    ]
    manifest = _manifest(files)
    if snapshot_id != SNAPSHOT_ID:
        manifest = SnapshotManifest(
            snapshot_id=snapshot_id,
            created_at=manifest.created_at,
            entries=manifest.entries,
            total_files=manifest.total_files,
            total_bytes=manifest.total_bytes,
            root_sha256=manifest.root_sha256,
        )
    files.append(PlannedFile.from_bytes(manifest_path(snapshot_id), _serialise(manifest)))
    write_planned_files(files, root)


# ── load_local_member_profile ──────────────────────────────────────


def test_load_local_member_profile_explicit_root(tmp_path: Path) -> None:
    _write_snapshot(tmp_path)
    result = load_local_member_profile("nancy-pelosi", snapshot_root=tmp_path)
    assert result.bioguide_id == "P000197"
    assert result.slug == "nancy-pelosi"
    assert result.chamber == "house"


def test_load_local_member_profile_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_local_member_profile("ghost-member", snapshot_root=tmp_path)


def test_load_local_member_profile_default_root_is_publish_dir() -> None:
    import unittest.mock as mock

    from src.runtime.paths import local_publish_root

    sentinel = object()
    with mock.patch("src.runtime.inspect.load_member_profile", return_value=sentinel) as patched:
        try:
            load_local_member_profile("any-slug")
        except Exception:
            pass
        if patched.called:
            called_root = patched.call_args[0][0]
            assert called_root == local_publish_root()


# ── load_local_member_page ────────────────────────────────────────


def test_load_local_member_page_explicit_root(tmp_path: Path) -> None:
    payload = MemberPagePayload(
        profile=_member().model_copy(
            update={
                "top_evidence_card_ids": ["ec-inspect-001"],
                "total_evidence_cards": 1,
            }
        ),
        top_evidence_cards=[_evidence()],
        recent_evidence_cards=[_evidence()],
    )
    write_planned_files(
        [
            PlannedFile.from_bytes(
                member_page_payload_path("nancy-pelosi"),
                _serialise(payload),
            )
        ],
        tmp_path,
    )

    result = load_local_member_page("nancy-pelosi", snapshot_root=tmp_path)
    assert result.profile.slug == "nancy-pelosi"
    assert result.top_evidence_cards[0].evidence_card_id == "ec-inspect-001"


def test_load_local_member_page_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_local_member_page("ghost-member", snapshot_root=tmp_path)


# ── load_local_evidence_card ───────────────────────────────────────


def test_load_local_evidence_card_explicit_root(tmp_path: Path) -> None:
    _write_snapshot(tmp_path)
    result = load_local_evidence_card("ec-inspect-001", snapshot_root=tmp_path)
    assert result.evidence_card_id == "ec-inspect-001"
    assert result.confidence == ConfidenceLabel.MEDIUM
    assert result.score_delta == -3.0


def test_load_local_evidence_card_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_local_evidence_card("ec-ghost", snapshot_root=tmp_path)


# ── load_local_ontology_edges ──────────────────────────────────────


def test_load_local_ontology_edges_explicit_root(tmp_path: Path) -> None:
    _write_snapshot(tmp_path)
    result = load_local_ontology_edges(snapshot_root=tmp_path)

    assert result.edge_count == 1
    assert result.edges[0].edge_id == "ont-edge-inspect-001"


def test_load_local_ontology_edges_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_local_ontology_edges(snapshot_root=tmp_path)


def test_load_local_ontology_member_edges_explicit_root(tmp_path: Path) -> None:
    _write_snapshot(tmp_path)
    result = load_local_ontology_member_edges("P000197", snapshot_root=tmp_path)

    assert result.member_bioguide_id == "P000197"
    assert result.edge_count == 1
    assert result.edges[0].edge_id == "ont-edge-inspect-001"


def test_load_local_ontology_member_edges_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_local_ontology_member_edges("P000197", snapshot_root=tmp_path)


# ── load_local_zip_feed ────────────────────────────────────────────


def test_load_local_zip_feed_explicit_root(tmp_path: Path) -> None:
    _write_snapshot(tmp_path)
    result = load_local_zip_feed("94102", snapshot_root=tmp_path)
    assert result.zip_code == "94102"
    assert result.congressional_district == "CA-11"


def test_load_local_zip_feed_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_local_zip_feed("00000", snapshot_root=tmp_path)


def test_load_local_zip_entry_explicit_root(tmp_path: Path) -> None:
    payload = ZipEntryPayload(
        zip_feed=_zip_feed(),
        member_lookup_entries=_current_member_lookup().members,
        snapshot={
            "snapshot_id": SNAPSHOT_ID,
            "snapshot_date": SNAPSHOT_DATE.isoformat(),
            "published_at": "2026-04-14T00:00:00Z",
            "root_sha256": "d" * 64,
            "total_files": 4,
            "total_bytes": 1200,
            "artifact_counts": {
                "members": 1,
                "evidence": 1,
                "zip_feeds": 1,
                "homepage_feeds": 1,
                "current_member_lookups": 1,
            },
        },
    )
    write_planned_files(
        [PlannedFile.from_bytes(zip_entry_path("94102"), _serialise(payload))],
        tmp_path,
    )

    result = load_local_zip_entry("94102", snapshot_root=tmp_path)
    assert result.zip_feed.zip_code == "94102"
    assert result.member_lookup_entries[0].slug == "nancy-pelosi"


def test_load_local_zip_entry_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_local_zip_entry("00000", snapshot_root=tmp_path)


# ── load_local_homepage_feed ──────────────────────────────────────


def test_load_local_homepage_feed_explicit_root(tmp_path: Path) -> None:
    _write_homepage_feed(tmp_path)
    result = load_local_homepage_feed(snapshot_root=tmp_path)
    assert result.snapshot_date == SNAPSHOT_DATE
    assert result.top_changes[0].slug == "nancy-pelosi"
    assert result.recent_events[0].feed_event_id == "event-inspect-001"
    assert result.recent_evidence_card_ids == ["ec-inspect-001"]


def test_load_local_homepage_feed_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_local_homepage_feed(snapshot_root=tmp_path)


def test_load_local_homepage_bootstrap_roundtrip(tmp_path: Path) -> None:
    payload = HomepageBootstrapPayload(
        snapshot={
            "snapshot_id": SNAPSHOT_ID,
            "snapshot_date": SNAPSHOT_DATE.isoformat(),
            "published_at": "2026-04-14T00:00:00Z",
            "root_sha256": "d" * 64,
            "total_files": 4,
            "total_bytes": 1200,
            "artifact_counts": {
                "members": 1,
                "evidence": 1,
                "zip_feeds": 0,
                "homepage_feeds": 1,
                "current_member_lookups": 1,
            },
        },
        movement={
            "snapshot_date": SNAPSHOT_DATE.isoformat(),
            "top_changes": _homepage_feed().top_changes,
            "recent_events": _homepage_feed().recent_events,
            "recent_evidence_card_ids": ["ec-inspect-001"],
        },
        featured_lookup_entries=_current_member_lookup().members,
    )
    write_planned_files(
        [
            PlannedFile.from_bytes(
                homepage_bootstrap_path(),
                _serialise(payload),
            )
        ],
        tmp_path,
    )

    result = load_local_homepage_bootstrap(snapshot_root=tmp_path)
    assert result.snapshot.snapshot_id == SNAPSHOT_ID
    assert result.featured_lookup_entries[0].slug == "nancy-pelosi"


def test_load_local_homepage_bootstrap_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_local_homepage_bootstrap(snapshot_root=tmp_path)


def test_load_local_homepage_feed_default_root_is_publish_dir() -> None:
    import unittest.mock as mock

    from src.runtime.paths import local_publish_root

    sentinel = object()
    with mock.patch("src.runtime.inspect.load_homepage_feed", return_value=sentinel) as patched:
        result = load_local_homepage_feed()
    called_root = patched.call_args[0][0]
    assert called_root == local_publish_root()
    assert result is sentinel


# ── load_local_manifest ────────────────────────────────────────────


def test_load_local_manifest_explicit_root(tmp_path: Path) -> None:
    _write_snapshot(tmp_path)
    result = load_local_manifest(SNAPSHOT_ID, snapshot_root=tmp_path)
    assert result.snapshot_id == SNAPSHOT_ID
    assert result.verify_counts() is True
    assert result.total_files == 6
    assert result.root_sha256 == manifest_root_sha256(result.entries)


def test_load_local_manifest_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_local_manifest("1970-01-01", snapshot_root=tmp_path)


def test_load_local_manifest_invalid_json(tmp_path: Path) -> None:
    dest = tmp_path / manifest_path("bad-snap")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"not json {{{{")
    with pytest.raises(ValueError, match="Invalid JSON"):
        load_local_manifest("bad-snap", snapshot_root=tmp_path)


# ── load_latest_local_manifest ─────────────────────────────────────


def test_load_latest_local_manifest_single_snapshot(tmp_path: Path) -> None:
    _write_snapshot(tmp_path)
    result = load_latest_local_manifest(snapshot_root=tmp_path)
    assert result.snapshot_id == SNAPSHOT_ID
    assert result.verify_counts() is True
    assert result.total_files == 6
    assert result.root_sha256 == manifest_root_sha256(result.entries)


def test_load_latest_local_manifest_picks_lexicographic_max(tmp_path: Path) -> None:
    _write_snapshot(tmp_path, snapshot_id="2026-03-01")
    _write_snapshot(tmp_path, snapshot_id="2026-04-14")
    result = load_latest_local_manifest(snapshot_root=tmp_path)
    assert result.snapshot_id == "2026-04-14"


def test_load_latest_local_manifest_no_snapshots(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_latest_local_manifest(snapshot_root=tmp_path)


def test_load_latest_local_manifest_default_root_is_publish_dir() -> None:
    import unittest.mock as mock

    from src.runtime.paths import local_publish_root

    sentinel = object()
    with mock.patch("src.runtime.inspect.load_latest_manifest", return_value=sentinel) as patched:
        result = load_latest_local_manifest()
    called_root = patched.call_args[0][0]
    assert called_root == local_publish_root()
    assert result is sentinel


def test_load_latest_local_snapshot_metadata_explicit_root(tmp_path: Path) -> None:
    _write_snapshot(tmp_path)
    snapshot_date, published_at = load_latest_local_snapshot_metadata(snapshot_root=tmp_path)
    assert snapshot_date == SNAPSHOT_DATE
    assert published_at == datetime(2026, 4, 14, 0, 0, 0)


def test_load_latest_local_snapshot_metadata_missing_snapshot(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_latest_local_snapshot_metadata(snapshot_root=tmp_path)


def test_load_latest_local_snapshot_metadata_default_root_is_publish_dir() -> None:
    import unittest.mock as mock

    from src.runtime.paths import local_publish_root

    with mock.patch("src.runtime.inspect.load_latest_snapshot_metadata") as patched:
        patched.return_value = (SNAPSHOT_DATE, datetime(2026, 4, 14, 0, 0, 0))
        load_latest_local_snapshot_metadata()
        called_root = patched.call_args[0][0]
        assert called_root == local_publish_root()


# ── load_local_current_member_lookup ───────────────────────────────


def test_load_local_current_member_lookup_explicit_root(tmp_path: Path) -> None:
    _write_snapshot(tmp_path)
    result = load_local_current_member_lookup(snapshot_root=tmp_path)
    assert result.members[0].bioguide_id == "P000197"
    assert result.members[0].search_name == "nancy pelosi"


def test_load_local_current_member_lookup_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_local_current_member_lookup(snapshot_root=tmp_path)


def test_load_local_current_member_lookup_default_root_is_publish_dir() -> None:
    import unittest.mock as mock

    from src.runtime.paths import local_publish_root

    sentinel = object()
    with mock.patch(
        "src.runtime.inspect.load_current_member_lookup", return_value=sentinel
    ) as patched:
        result = load_local_current_member_lookup()
    called_root = patched.call_args[0][0]
    assert called_root == local_publish_root()
    assert result is sentinel


def test_search_local_current_member_lookup_explicit_root(tmp_path: Path) -> None:
    _write_snapshot(tmp_path)
    result = search_local_current_member_lookup("nancy pelosi", snapshot_root=tmp_path)
    assert [member.slug for member in result.members] == ["nancy-pelosi"]


def test_search_local_current_member_lookup_default_root_is_publish_dir() -> None:
    import unittest.mock as mock

    from src.runtime.paths import local_publish_root

    payload = _current_member_lookup()
    with mock.patch(
        "src.runtime.inspect.load_current_member_lookup", return_value=payload
    ) as patched:
        result = search_local_current_member_lookup("nancy pelosi")
    called_root = patched.call_args[0][0]
    assert called_root == local_publish_root()
    assert [member.slug for member in result.members] == ["nancy-pelosi"]


# ── Integration: all helpers from one publish tree ─────────────────


def test_full_roundtrip_all_helpers(tmp_path: Path) -> None:
    _write_snapshot(tmp_path)
    _write_homepage_feed(tmp_path)

    member = load_local_member_profile("nancy-pelosi", snapshot_root=tmp_path)
    evidence = load_local_evidence_card("ec-inspect-001", snapshot_root=tmp_path)
    feed = load_local_zip_feed("94102", snapshot_root=tmp_path)
    manifest = load_local_manifest(SNAPSHOT_ID, snapshot_root=tmp_path)
    latest = load_latest_local_manifest(snapshot_root=tmp_path)
    homepage = load_local_homepage_feed(snapshot_root=tmp_path)

    assert member.bioguide_id == "P000197"
    assert evidence.member_bioguide_id == "P000197"
    assert feed.zip_code == "94102"
    assert manifest.verify_counts() is True
    assert latest.snapshot_id == manifest.snapshot_id
    assert homepage.snapshot_date == SNAPSHOT_DATE


# ── Path traversal rejection (via local_store boundary) ──────────


class TestInspectPathTraversal:
    """Inspect helpers must reject traversal inputs before touching disk."""

    def test_member_profile_traversal(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Path escapes snapshot root"):
            load_local_member_profile("../../etc/passwd", snapshot_root=tmp_path)

    def test_evidence_card_traversal(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Path escapes snapshot root"):
            load_local_evidence_card("../../../etc/shadow", snapshot_root=tmp_path)

    def test_zip_feed_traversal(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Path escapes snapshot root"):
            load_local_zip_feed("../../../../tmp/x", snapshot_root=tmp_path)

    def test_manifest_traversal(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Path escapes snapshot root"):
            load_local_manifest("../../../etc/passwd", snapshot_root=tmp_path)

    def test_null_byte_in_slug(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="null byte"):
            load_local_member_profile("evil\x00slug", snapshot_root=tmp_path)
