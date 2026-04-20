from __future__ import annotations

from datetime import UTC, date, datetime

from src.api.contracts import BatchMeta, LastUpdatedPayload, NotFoundBody
from src.api.read_api import (
    make_batch_meta,
    make_headers,
    not_found,
    wrap_current_member_lookup,
    wrap_evidence,
    wrap_homepage,
    wrap_last_updated,
    wrap_member,
    wrap_zip,
)
from src.export.contracts import (
    ConfidenceLabel,
    EvidenceBlock,
    EvidenceCardPayload,
    EvidenceSection,
    MemberProfilePayload,
    ScoreSummary,
    SourceAnchor,
    ZipFeedPayload,
    ZipMemberSummary,
)
from src.homepage.contracts import HomepageFeedPayload, MemberMovementSummary, RecentEventSummary
from src.identity.current_member_lookup import CurrentMemberLookupEntry, CurrentMemberLookupPayload

SNAPSHOT_DATE = date(2026, 4, 13)
PUBLISHED_AT = datetime(2026, 4, 13, 0, 0, 0, tzinfo=UTC)


# ── Payload fixtures ───────────────────────────────────────────────


def _zip_payload() -> ZipFeedPayload:
    return ZipFeedPayload(
        zip_code="10001",
        congressional_district="NY-12",
        ambiguity_note=None,
        members=[
            ZipMemberSummary(
                bioguide_id="S000148",
                name="Charles Schumer",
                slug="charles-schumer",
                chamber="senate",
                party="Democrat",
                scores=[
                    ScoreSummary(
                        dimension="conflict_of_interest_risk",
                        current_score=72.0,
                        rule_fire_count=3,
                    )
                ],
                top_evidence_card_ids=["ec-001"],
            )
        ],
        snapshot_date=SNAPSHOT_DATE,
    )


def _member_payload() -> MemberProfilePayload:
    return MemberProfilePayload(
        bioguide_id="S000148",
        name="Charles Schumer",
        slug="charles-schumer",
        state="NY",
        district=None,
        chamber="senate",
        party="Democrat",
        scores=[
            ScoreSummary(
                dimension="conflict_of_interest_risk",
                current_score=72.0,
                rule_fire_count=3,
            )
        ],
        recent_rule_fires=[],
        top_evidence_card_ids=["ec-001", "ec-002"],
        committees=[],
        total_evidence_cards=5,
        snapshot_date=SNAPSHOT_DATE,
    )


def _evidence_payload() -> EvidenceCardPayload:
    return EvidenceCardPayload(
        evidence_card_id="ec-001",
        member_bioguide_id="S000148",
        member_name="Charles Schumer",
        member_slug="charles-schumer",
        dimension="conflict_of_interest_risk",
        rule_id="committee_sector_trade",
        rule_version=1,
        score_delta=-5.0,
        short_explanation="Trade in sector overlapping committee.",
        blocks=[EvidenceBlock(section=EvidenceSection.FACT, text="Purchased AAPL.")],
        source_anchors=[
            SourceAnchor(
                source_type="financial_disclosure",
                source_id="fd-99",
                label="2025 Annual Disclosure",
            )
        ],
        confidence=ConfidenceLabel.HIGH,
        snapshot_date=SNAPSHOT_DATE,
        created_at=PUBLISHED_AT,
    )


def _homepage_payload() -> HomepageFeedPayload:
    return HomepageFeedPayload(
        snapshot_date=SNAPSHOT_DATE,
        top_changes=[
            MemberMovementSummary(
                bioguide_id="S000148",
                name="Charles Schumer",
                slug="charles-schumer",
                chamber="senate",
                party="Democrat",
                state="NY",
                dimension="conflict_of_interest_risk",
                score_delta=-5.0,
                abs_delta=5.0,
                event_count=2,
                top_evidence_card_ids=["ec-001", "ec-002"],
            )
        ],
        recent_events=[
            RecentEventSummary(
                feed_event_id="event-002",
                member_bioguide_id="S000148",
                member_name="Charles Schumer",
                member_slug="charles-schumer",
                dimension="conflict_of_interest_risk",
                score_delta=-2.0,
                short_explanation="Sold sector ETF while on committee.",
                evidence_card_id="ec-002",
                occurred_at=SNAPSHOT_DATE,
            ),
            RecentEventSummary(
                feed_event_id="event-001",
                member_bioguide_id="S000148",
                member_name="Charles Schumer",
                member_slug="charles-schumer",
                dimension="conflict_of_interest_risk",
                score_delta=-3.0,
                short_explanation="Purchased AAPL.",
                evidence_card_id="ec-001",
                occurred_at=SNAPSHOT_DATE,
            ),
        ],
        recent_evidence_card_ids=["ec-002", "ec-001"],
    )


def _current_member_lookup_payload() -> CurrentMemberLookupPayload:
    return CurrentMemberLookupPayload(
        snapshot_date=SNAPSHOT_DATE,
        members=[
            CurrentMemberLookupEntry(
                bioguide_id="S000148",
                slug="charles-schumer",
                name="Charles Schumer",
                search_name="charles schumer",
                state="NY",
                district=None,
                chamber="senate",
            )
        ],
    )


# ── make_batch_meta ────────────────────────────────────────────────


def test_make_batch_meta_explicit():
    meta = make_batch_meta(SNAPSHOT_DATE, PUBLISHED_AT)
    assert meta.snapshot_date == SNAPSHOT_DATE
    assert meta.published_at == PUBLISHED_AT
    assert meta.schema_version == "v1"


def test_make_batch_meta_defaults_published_at():
    before = datetime.now(UTC)
    meta = make_batch_meta(SNAPSHOT_DATE)
    after = datetime.now(UTC)
    assert before <= meta.published_at <= after


def test_make_batch_meta_returns_batch_meta_instance():
    meta = make_batch_meta(SNAPSHOT_DATE, PUBLISHED_AT)
    assert isinstance(meta, BatchMeta)


# ── wrap_zip ───────────────────────────────────────────────────────


def test_wrap_zip_ok_true():
    meta = make_batch_meta(SNAPSHOT_DATE, PUBLISHED_AT)
    resp = wrap_zip(_zip_payload(), meta)
    assert resp.ok is True


def test_wrap_zip_data_preserved():
    meta = make_batch_meta(SNAPSHOT_DATE, PUBLISHED_AT)
    resp = wrap_zip(_zip_payload(), meta)
    assert resp.data.zip_code == "10001"
    assert resp.data.congressional_district == "NY-12"
    assert len(resp.data.members) == 1
    assert resp.data.members[0].bioguide_id == "S000148"


def test_wrap_zip_meta_attached():
    meta = make_batch_meta(SNAPSHOT_DATE, PUBLISHED_AT)
    resp = wrap_zip(_zip_payload(), meta)
    assert resp.meta.snapshot_date == SNAPSHOT_DATE
    assert resp.meta.schema_version == "v1"


def test_wrap_zip_roundtrip_json():
    meta = make_batch_meta(SNAPSHOT_DATE, PUBLISHED_AT)
    resp = wrap_zip(_zip_payload(), meta)
    d = resp.model_dump(mode="json")
    assert d["ok"] is True
    assert d["data"]["zip_code"] == "10001"
    assert d["meta"]["schema_version"] == "v1"
    assert d["meta"]["snapshot_date"] == "2026-04-13"


# ── wrap_member ────────────────────────────────────────────────────


def test_wrap_member_ok_true():
    meta = make_batch_meta(SNAPSHOT_DATE, PUBLISHED_AT)
    resp = wrap_member(_member_payload(), meta)
    assert resp.ok is True


def test_wrap_member_data_preserved():
    meta = make_batch_meta(SNAPSHOT_DATE, PUBLISHED_AT)
    resp = wrap_member(_member_payload(), meta)
    assert resp.data.bioguide_id == "S000148"
    assert resp.data.chamber == "senate"
    assert resp.data.total_evidence_cards == 5
    assert resp.data.top_evidence_card_ids == ["ec-001", "ec-002"]


def test_wrap_member_roundtrip_json():
    meta = make_batch_meta(SNAPSHOT_DATE, PUBLISHED_AT)
    resp = wrap_member(_member_payload(), meta)
    d = resp.model_dump(mode="json")
    assert d["ok"] is True
    assert d["data"]["slug"] == "charles-schumer"
    assert d["data"]["district"] is None


# ── wrap_evidence ──────────────────────────────────────────────────


def test_wrap_evidence_ok_true():
    meta = make_batch_meta(SNAPSHOT_DATE, PUBLISHED_AT)
    resp = wrap_evidence(_evidence_payload(), meta)
    assert resp.ok is True


def test_wrap_evidence_data_preserved():
    meta = make_batch_meta(SNAPSHOT_DATE, PUBLISHED_AT)
    resp = wrap_evidence(_evidence_payload(), meta)
    assert resp.data.evidence_card_id == "ec-001"
    assert resp.data.score_delta == -5.0
    assert resp.data.confidence == ConfidenceLabel.HIGH


def test_wrap_evidence_roundtrip_json():
    meta = make_batch_meta(SNAPSHOT_DATE, PUBLISHED_AT)
    resp = wrap_evidence(_evidence_payload(), meta)
    d = resp.model_dump(mode="json")
    assert d["ok"] is True
    assert d["data"]["rule_id"] == "committee_sector_trade"
    assert d["meta"]["snapshot_date"] == "2026-04-13"


# ── wrap_homepage ──────────────────────────────────────────────────


def test_wrap_homepage_ok_true():
    meta = make_batch_meta(SNAPSHOT_DATE, PUBLISHED_AT)
    resp = wrap_homepage(_homepage_payload(), meta)
    assert resp.ok is True


def test_wrap_homepage_preserves_ranked_lists():
    meta = make_batch_meta(SNAPSHOT_DATE, PUBLISHED_AT)
    resp = wrap_homepage(_homepage_payload(), meta)
    assert resp.data.top_changes[0].top_evidence_card_ids == ["ec-001", "ec-002"]
    assert [event.feed_event_id for event in resp.data.recent_events] == ["event-002", "event-001"]
    assert resp.data.recent_evidence_card_ids == ["ec-002", "ec-001"]


def test_wrap_homepage_roundtrip_json():
    meta = make_batch_meta(SNAPSHOT_DATE, PUBLISHED_AT)
    resp = wrap_homepage(_homepage_payload(), meta)
    d = resp.model_dump(mode="json")
    assert d["ok"] is True
    assert d["data"]["top_changes"][0]["event_count"] == 2
    assert d["data"]["recent_events"][0]["evidence_card_id"] == "ec-002"
    assert d["meta"]["published_at"] == "2026-04-13T00:00:00Z"


# ── wrap_current_member_lookup ─────────────────────────────────────


def test_wrap_current_member_lookup_ok_true():
    meta = make_batch_meta(SNAPSHOT_DATE, PUBLISHED_AT)
    resp = wrap_current_member_lookup(_current_member_lookup_payload(), meta)
    assert resp.ok is True


def test_wrap_current_member_lookup_data_preserved():
    meta = make_batch_meta(SNAPSHOT_DATE, PUBLISHED_AT)
    resp = wrap_current_member_lookup(_current_member_lookup_payload(), meta)
    assert resp.data.members[0].bioguide_id == "S000148"
    assert resp.data.members[0].search_name == "charles schumer"


def test_wrap_current_member_lookup_roundtrip_json():
    meta = make_batch_meta(SNAPSHOT_DATE, PUBLISHED_AT)
    resp = wrap_current_member_lookup(_current_member_lookup_payload(), meta)
    d = resp.model_dump(mode="json", by_alias=True)
    assert d["ok"] is True
    assert d["data"]["sd"] == "2026-04-13"
    assert d["data"]["m"][0]["b"] == "S000148"


# ── wrap_last_updated ──────────────────────────────────────────────


def test_wrap_last_updated_ok_true():
    resp = wrap_last_updated(SNAPSHOT_DATE, PUBLISHED_AT)
    assert resp.ok is True


def test_wrap_last_updated_data():
    resp = wrap_last_updated(SNAPSHOT_DATE, PUBLISHED_AT)
    assert isinstance(resp.data, LastUpdatedPayload)
    assert resp.data.snapshot_date == SNAPSHOT_DATE
    assert resp.data.published_at == PUBLISHED_AT


def test_wrap_last_updated_meta_matches_data():
    resp = wrap_last_updated(SNAPSHOT_DATE, PUBLISHED_AT)
    assert resp.meta.snapshot_date == resp.data.snapshot_date
    assert resp.meta.published_at == resp.data.published_at


def test_wrap_last_updated_roundtrip_json():
    resp = wrap_last_updated(SNAPSHOT_DATE, PUBLISHED_AT)
    d = resp.model_dump(mode="json")
    assert d["ok"] is True
    assert d["data"]["snapshot_date"] == "2026-04-13"
    assert d["meta"]["schema_version"] == "v1"


# ── make_headers ───────────────────────────────────────────────────


def test_make_headers_content_type():
    h = make_headers(SNAPSHOT_DATE)
    assert h["Content-Type"] == "application/json; charset=utf-8"


def test_make_headers_cache_control():
    h = make_headers(SNAPSHOT_DATE)
    assert "public" in h["Cache-Control"]
    assert "max-age=" in h["Cache-Control"]


def test_make_headers_snapshot_date():
    h = make_headers(SNAPSHOT_DATE)
    assert h["X-Snapshot-Date"] == "2026-04-13"


def test_make_headers_no_etag_by_default():
    h = make_headers(SNAPSHOT_DATE)
    assert "ETag" not in h


def test_make_headers_etag_unquoted_is_quoted():
    h = make_headers(SNAPSHOT_DATE, etag="abc123")
    assert h["ETag"] == '"abc123"'


def test_make_headers_etag_already_quoted_not_doubled():
    h = make_headers(SNAPSHOT_DATE, etag='"already-quoted"')
    assert h["ETag"] == '"already-quoted"'
    assert h["ETag"].count('"') == 2


def test_make_headers_weak_etag_preserved():
    h = make_headers(SNAPSHOT_DATE, etag='W/"weak-value"')
    assert h["ETag"] == 'W/"weak-value"'


def test_make_headers_returns_fresh_copy():
    h1 = make_headers(SNAPSHOT_DATE)
    h2 = make_headers(SNAPSHOT_DATE)
    h1["X-Custom"] = "mutated"
    assert "X-Custom" not in h2


# ── not_found ──────────────────────────────────────────────────────


def test_not_found_ok_false():
    nf = not_found("zip", "99999")
    assert nf.ok is False


def test_not_found_error_field():
    nf = not_found("zip", "99999")
    assert nf.error == "not_found"


def test_not_found_resource_type_and_identifier():
    nf = not_found("zip", "99999")
    assert nf.resource_type == "zip"
    assert nf.identifier == "99999"


def test_not_found_detail_contains_identifier():
    nf = not_found("member", "unknown-slug")
    assert "unknown-slug" in nf.detail


def test_not_found_evidence():
    nf = not_found("evidence", "ec-999")
    assert nf.resource_type == "evidence"
    assert "ec-999" in nf.detail


def test_not_found_roundtrip_json():
    nf = not_found("zip", "00000")
    d = nf.model_dump(mode="json")
    assert d["ok"] is False
    assert d["error"] == "not_found"
    assert d["resource_type"] == "zip"
    assert d["identifier"] == "00000"


def test_not_found_returns_not_found_body():
    nf = not_found("zip", "10001")
    assert isinstance(nf, NotFoundBody)
