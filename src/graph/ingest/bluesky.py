"""Adapt official public Bluesky posts into social-post edges.

Bluesky exposes a fully public AppView API (no login) for public posts. A post
by an official is part of their public communications. :func:`parse_bluesky_post`
parses one post (or feed item); :func:`social_post_edge` builds a ``social_post``
edge from the official to the platform, keyed by the post AT-URI, with the text
carried for downstream NLP stance extraction; :func:`social_post_provenance`
stamps ``known_at`` at the post time (public when posted).

Only the official's own public posts are ingested — within public-record /
public-conduct bounds, no login.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from src.graph.edges import GraphEdge
from src.graph.provenance import ProvenanceEnvelope


@dataclass(frozen=True)
class BlueskyPost:
    """One parsed public Bluesky post."""

    uri: str
    cid: str
    text: str
    author_handle: str
    author_did: str
    created_at: datetime


def _parse_created_at(raw: Any) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("Bluesky post is missing createdAt")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"unparseable Bluesky createdAt: {raw!r}") from exc


def parse_bluesky_post(record: dict[str, Any]) -> BlueskyPost:
    """Parse a Bluesky post (accepts a raw post or a ``{"post": ...}`` feed item)."""
    post = record.get("post") if "post" in record else record
    if not isinstance(post, dict):
        raise ValueError("Bluesky feed item has no post")
    uri = (post.get("uri") or "").strip()
    if not uri:
        raise ValueError("Bluesky post is missing a uri")
    author = post.get("author") or {}
    record_data = post.get("record") or {}
    return BlueskyPost(
        uri=uri,
        cid=(post.get("cid") or "").strip(),
        text=(record_data.get("text") or "").strip(),
        author_handle=(author.get("handle") or "").strip(),
        author_did=(author.get("did") or "").strip(),
        created_at=_parse_created_at(record_data.get("createdAt")),
    )


def social_post_provenance(
    post: BlueskyPost,
    *,
    content_sha256: str,
    first_observed_at: datetime,
    known_at: datetime | None = None,
) -> ProvenanceEnvelope:
    """Provenance for a social post; ``known_at`` defaults to the post time."""
    return ProvenanceEnvelope(
        source_url=f"https://bsky.app/profile/{post.author_did}/post/{post.uri.rsplit('/', 1)[-1]}",
        content_sha256=content_sha256,
        first_observed_at=first_observed_at,
        valid_from=date(post.created_at.year, post.created_at.month, post.created_at.day),
        known_at=known_at if known_at is not None else post.created_at,
    )


def social_post_edge(
    *,
    official_canonical_id: str,
    post: BlueskyPost,
    provenance: ProvenanceEnvelope,
) -> GraphEdge:
    """Build the official -> platform social-post edge for one Bluesky post."""
    attributes = {"handle": post.author_handle}
    if post.text:
        attributes["text"] = post.text
    return GraphEdge(
        edge_type="social_post",
        src_id=official_canonical_id,
        dst_id="platform:bluesky",
        attributes=attributes,
        external_key=post.uri,
        provenance=provenance,
    )
