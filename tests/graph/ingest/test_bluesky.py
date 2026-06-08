from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.ingest.bluesky import (
    BlueskyPost,
    parse_bluesky_post,
    social_post_edge,
    social_post_provenance,
)

_OFFICIAL = "ce-person1"

_POST = {
    "uri": "at://did:plc:abc/app.bsky.feed.post/xyz",
    "cid": "bafyreiaqmw3nwouxqlynvoe2i7z3rnawhcfbiornicfer4b5qdr",
    "author": {"handle": "senwarren.bsky.social", "did": "did:plc:abc"},
    "record": {
        "text": "We must hold corporations accountable.",
        "createdAt": "2024-03-15T18:21:26.811Z",
    },
}

_FEED_ITEM = {"post": _POST}


def test_parse_post() -> None:
    post = parse_bluesky_post(_POST)
    assert isinstance(post, BlueskyPost)
    assert post.uri == "at://did:plc:abc/app.bsky.feed.post/xyz"
    assert post.text == "We must hold corporations accountable."
    assert post.author_handle == "senwarren.bsky.social"
    assert post.created_at == datetime(2024, 3, 15, 18, 21, 26, 811000, tzinfo=UTC)


def test_parse_feed_item_unwraps_post() -> None:
    post = parse_bluesky_post(_FEED_ITEM)
    assert post.uri == "at://did:plc:abc/app.bsky.feed.post/xyz"


def test_missing_uri_rejected() -> None:
    with pytest.raises(ValueError, match="uri"):
        parse_bluesky_post({"record": {"text": "x", "createdAt": "2024-03-15T00:00:00Z"}})


def test_missing_created_at_rejected() -> None:
    with pytest.raises(ValueError, match="createdAt"):
        parse_bluesky_post({"uri": "at://x", "author": {"handle": "h"}, "record": {"text": "x"}})


def test_bad_created_at_rejected() -> None:
    with pytest.raises(ValueError, match="createdAt"):
        parse_bluesky_post(
            {
                "uri": "at://x",
                "author": {"handle": "h"},
                "record": {"text": "x", "createdAt": "nope"},
            }
        )


def test_social_post_edge() -> None:
    post = parse_bluesky_post(_POST)
    prov = social_post_provenance(
        post, content_sha256="a" * 64, first_observed_at=datetime(2024, 6, 1, tzinfo=UTC)
    )
    edge = social_post_edge(official_canonical_id=_OFFICIAL, post=post, provenance=prov)
    assert edge.edge_type == "social_post"
    assert edge.src_id == _OFFICIAL
    assert edge.dst_id == "platform:bluesky"
    assert edge.attributes["handle"] == "senwarren.bsky.social"
    assert edge.attributes["text"] == "We must hold corporations accountable."
    assert edge.external_key == "at://did:plc:abc/app.bsky.feed.post/xyz"


def test_provenance_known_at_is_post_time() -> None:
    post = parse_bluesky_post(_POST)
    prov = social_post_provenance(
        post, content_sha256="a" * 64, first_observed_at=datetime(2024, 6, 1, tzinfo=UTC)
    )
    assert prov.valid_from == date(2024, 3, 15)
    assert prov.known_at == datetime(2024, 3, 15, 18, 21, 26, 811000, tzinfo=UTC)


def test_edge_leakage_gate() -> None:
    post = parse_bluesky_post(_POST)
    prov = social_post_provenance(
        post, content_sha256="a" * 64, first_observed_at=datetime(2024, 6, 1, tzinfo=UTC)
    )
    edge = social_post_edge(official_canonical_id=_OFFICIAL, post=post, provenance=prov)
    assert edge.known_as_of(datetime(2024, 3, 16, tzinfo=UTC)) is True
    assert edge.known_as_of(datetime(2024, 3, 14, tzinfo=UTC)) is False


def test_distinct_posts_separate() -> None:
    a = parse_bluesky_post(_POST)
    b = parse_bluesky_post({**_POST, "uri": "at://did:plc:abc/app.bsky.feed.post/different"})
    pa = social_post_provenance(
        a, content_sha256="a" * 64, first_observed_at=datetime(2024, 6, 1, tzinfo=UTC)
    )
    pb = social_post_provenance(
        b, content_sha256="b" * 64, first_observed_at=datetime(2024, 6, 1, tzinfo=UTC)
    )
    ea = social_post_edge(official_canonical_id=_OFFICIAL, post=a, provenance=pa)
    eb = social_post_edge(official_canonical_id=_OFFICIAL, post=b, provenance=pb)
    assert ea.edge_id != eb.edge_id


def test_empty_text_post() -> None:
    post = parse_bluesky_post(
        {
            "uri": "at://x",
            "author": {"handle": "h"},
            "record": {"createdAt": "2024-03-15T00:00:00Z"},
        }
    )
    assert post.text == ""
    prov = social_post_provenance(
        post, content_sha256="a" * 64, first_observed_at=datetime(2024, 6, 1, tzinfo=UTC)
    )
    edge = social_post_edge(official_canonical_id=_OFFICIAL, post=post, provenance=prov)
    assert "text" not in edge.attributes  # empty text not carried


def test_feed_item_with_non_dict_post_rejected() -> None:
    with pytest.raises(ValueError, match="no post"):
        parse_bluesky_post({"post": None})
