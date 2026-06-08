from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.ingest.wayback import (
    WaybackSnapshot,
    archived_url,
    campaign_snapshot_edge,
    campaign_snapshot_provenance,
    parse_cdx_row,
)

_OFFICIAL = "ce-person1"
_ROW = [
    "gov,senate,warren)/",
    "20130201163442",
    "http://www.warren.senate.gov/",
    "text/html",
    "200",
    "LYLGYFMB55Y3WK33XQOVN5L4OC3GT56U",
    "5244",
]


def test_parse_cdx_row() -> None:
    snap = parse_cdx_row(_ROW)
    assert isinstance(snap, WaybackSnapshot)
    assert snap.original_url == "http://www.warren.senate.gov/"
    assert snap.timestamp == datetime(2013, 2, 1, 16, 34, 42, tzinfo=UTC)
    assert snap.status_code == "200"
    assert snap.digest == "LYLGYFMB55Y3WK33XQOVN5L4OC3GT56U"


def test_parse_rejects_short_row() -> None:
    with pytest.raises(ValueError, match="CDX"):
        parse_cdx_row(["a", "b"])


def test_parse_rejects_bad_timestamp() -> None:
    bad = list(_ROW)
    bad[1] = "not-a-timestamp"
    with pytest.raises(ValueError, match="timestamp"):
        parse_cdx_row(bad)


def test_parse_rejects_invalid_calendar_timestamp() -> None:
    bad = list(_ROW)
    bad[1] = "20131345000000"  # month 13, day 45
    with pytest.raises(ValueError, match="timestamp"):
        parse_cdx_row(bad)


def test_archived_url() -> None:
    snap = parse_cdx_row(_ROW)
    assert archived_url(snap) == (
        "http://web.archive.org/web/20130201163442/http://www.warren.senate.gov/"
    )


def test_campaign_snapshot_edge() -> None:
    snap = parse_cdx_row(_ROW)
    prov = campaign_snapshot_provenance(
        snap, content_sha256="a" * 64, first_observed_at=datetime(2024, 1, 1, tzinfo=UTC)
    )
    edge = campaign_snapshot_edge(official_canonical_id=_OFFICIAL, snapshot=snap, provenance=prov)
    assert edge.edge_type == "campaign_snapshot"
    assert edge.src_id == _OFFICIAL
    assert edge.dst_id == "campaign_site:www.warren.senate.gov"
    assert edge.external_key == "20130201163442"
    assert edge.attributes["original_url"] == "http://www.warren.senate.gov/"


def test_provenance_uses_archived_url_and_snapshot_time() -> None:
    snap = parse_cdx_row(_ROW)
    prov = campaign_snapshot_provenance(
        snap, content_sha256="a" * 64, first_observed_at=datetime(2024, 1, 1, tzinfo=UTC)
    )
    assert prov.source_url.startswith("http://web.archive.org/web/20130201163442/")
    assert prov.valid_from == date(2013, 2, 1)
    assert prov.known_at == datetime(2013, 2, 1, 16, 34, 42, tzinfo=UTC)


def test_edge_leakage_gate() -> None:
    snap = parse_cdx_row(_ROW)
    prov = campaign_snapshot_provenance(
        snap, content_sha256="a" * 64, first_observed_at=datetime(2024, 1, 1, tzinfo=UTC)
    )
    edge = campaign_snapshot_edge(official_canonical_id=_OFFICIAL, snapshot=snap, provenance=prov)
    assert edge.known_as_of(datetime(2013, 3, 1, tzinfo=UTC)) is True
    assert edge.known_as_of(datetime(2013, 1, 1, tzinfo=UTC)) is False


def test_snapshot_without_digest_or_host() -> None:
    # Empty digest is not carried; a non-URL original falls back to the urlkey.
    row = ["site)/path", "20130201163442", "/relative/path", "text/html", "200", "", "0"]
    snap = parse_cdx_row(row)
    prov = campaign_snapshot_provenance(
        snap, content_sha256="a" * 64, first_observed_at=datetime(2024, 1, 1, tzinfo=UTC)
    )
    edge = campaign_snapshot_edge(official_canonical_id=_OFFICIAL, snapshot=snap, provenance=prov)
    assert "wayback_digest" not in edge.attributes
    assert edge.dst_id == "campaign_site:site)/path"


def test_distinct_snapshots_separate() -> None:
    a = parse_cdx_row(_ROW)
    b_row = list(_ROW)
    b_row[1] = "20140201163442"
    b = parse_cdx_row(b_row)
    pa = campaign_snapshot_provenance(
        a, content_sha256="a" * 64, first_observed_at=datetime(2024, 1, 1, tzinfo=UTC)
    )
    pb = campaign_snapshot_provenance(
        b, content_sha256="b" * 64, first_observed_at=datetime(2024, 1, 1, tzinfo=UTC)
    )
    ea = campaign_snapshot_edge(official_canonical_id=_OFFICIAL, snapshot=a, provenance=pa)
    eb = campaign_snapshot_edge(official_canonical_id=_OFFICIAL, snapshot=b, provenance=pb)
    assert ea.edge_id != eb.edge_id
