"""Adapt GDELT news articles into official->outlet news-mention edges.

GDELT indexes worldwide news in near-real-time and exposes a public document
API. An article mentioning an official is part of their media footprint.
:func:`parse_gdelt_article` parses one article; :func:`news_mention_edge` builds
a ``news_mention`` edge from the official to the news outlet (the domain),
keyed by the article URL, and :func:`news_provenance` stamps it at the
publication date (public when published) so the leakage gate holds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from src.graph.edges import GraphEdge
from src.graph.provenance import ProvenanceEnvelope


@dataclass(frozen=True)
class GdeltArticle:
    """One parsed GDELT news article."""

    url: str
    title: str
    domain: str
    seen_date: date
    language: str
    source_country: str


def _parse_seendate(raw: str) -> date:
    digits = raw.strip()
    if len(digits) < 8 or not digits[:8].isdigit():
        raise ValueError(f"unparseable GDELT seendate: {raw!r}")
    try:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError as exc:
        raise ValueError(f"unparseable GDELT seendate: {raw!r}") from exc


def parse_gdelt_article(record: dict[str, Any]) -> GdeltArticle:
    """Parse one GDELT article record."""
    url = (record.get("url") or "").strip()
    if not url:
        raise ValueError("GDELT article is missing a url")
    return GdeltArticle(
        url=url,
        title=(record.get("title") or "").strip(),
        domain=(record.get("domain") or "").strip(),
        seen_date=_parse_seendate(record.get("seendate") or ""),
        language=(record.get("language") or "").strip(),
        source_country=(record.get("sourcecountry") or "").strip(),
    )


def news_provenance(
    article: GdeltArticle,
    *,
    content_sha256: str,
    first_observed_at: datetime,
    known_at: datetime | None = None,
) -> ProvenanceEnvelope:
    """Provenance for a news mention; ``known_at`` defaults to the publication day."""
    published = datetime(
        article.seen_date.year, article.seen_date.month, article.seen_date.day, tzinfo=UTC
    )
    return ProvenanceEnvelope(
        source_url=article.url,
        content_sha256=content_sha256,
        first_observed_at=first_observed_at,
        valid_from=article.seen_date,
        known_at=known_at if known_at is not None else published,
    )


def news_mention_edge(
    *,
    official_canonical_id: str,
    article: GdeltArticle,
    provenance: ProvenanceEnvelope,
) -> GraphEdge:
    """Build the official -> outlet news-mention edge for one article."""
    outlet = article.domain or "unknown"
    attributes = {"title": article.title} if article.title else {}
    if article.language:
        attributes["language"] = article.language
    if article.source_country:
        attributes["source_country"] = article.source_country
    return GraphEdge(
        edge_type="news_mention",
        src_id=official_canonical_id,
        dst_id=f"outlet:{outlet}",
        attributes=attributes,
        external_key=article.url,
        provenance=provenance,
    )
