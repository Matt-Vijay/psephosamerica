from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.ingest.gdelt import (
    GdeltArticle,
    news_mention_edge,
    news_provenance,
    parse_gdelt_article,
)

_OFFICIAL = "ce-person1"

_RECORD = {
    "url": "https://example-news.com/article/123",
    "title": "Senator Doe backs new energy bill",
    "domain": "example-news.com",
    "seendate": "20240115T120000Z",
    "language": "English",
    "sourcecountry": "United States",
}


def test_parse_article() -> None:
    article = parse_gdelt_article(_RECORD)
    assert isinstance(article, GdeltArticle)
    assert article.url == "https://example-news.com/article/123"
    assert article.title == "Senator Doe backs new energy bill"
    assert article.domain == "example-news.com"
    assert article.seen_date == date(2024, 1, 15)


def test_parse_rejects_missing_url() -> None:
    with pytest.raises(ValueError, match="url"):
        parse_gdelt_article({"title": "x", "seendate": "20240115T120000Z", "domain": "d"})


def test_parse_rejects_bad_seendate() -> None:
    with pytest.raises(ValueError, match="seendate"):
        parse_gdelt_article({"url": "http://x", "title": "x", "domain": "d", "seendate": "nope"})


def test_parse_rejects_invalid_calendar_seendate() -> None:
    # 8 digits but not a real date (month 13, day 45).
    with pytest.raises(ValueError, match="seendate"):
        parse_gdelt_article(
            {"url": "http://x", "title": "x", "domain": "d", "seendate": "20241345T000000Z"}
        )


def test_minimal_article_has_empty_attributes() -> None:
    article = parse_gdelt_article(
        {"url": "http://x/1", "seendate": "20240115T120000Z", "domain": "x.com"}
    )
    assert article.language == ""
    assert article.source_country == ""
    prov = news_provenance(
        article, content_sha256="a" * 64, first_observed_at=datetime(2024, 6, 1, tzinfo=UTC)
    )
    edge = news_mention_edge(official_canonical_id=_OFFICIAL, article=article, provenance=prov)
    assert edge.attributes == {}  # no title, language, or country


def test_news_mention_edge() -> None:
    article = parse_gdelt_article(_RECORD)
    prov = news_provenance(
        article, content_sha256="a" * 64, first_observed_at=datetime(2024, 6, 1, tzinfo=UTC)
    )
    edge = news_mention_edge(official_canonical_id=_OFFICIAL, article=article, provenance=prov)
    assert edge.edge_type == "news_mention"
    assert edge.src_id == _OFFICIAL
    assert edge.dst_id == "outlet:example-news.com"
    assert edge.attributes["title"] == "Senator Doe backs new energy bill"
    assert edge.external_key == "https://example-news.com/article/123"


def test_news_provenance_known_at_is_publication() -> None:
    article = parse_gdelt_article(_RECORD)
    prov = news_provenance(
        article, content_sha256="a" * 64, first_observed_at=datetime(2024, 6, 1, tzinfo=UTC)
    )
    assert prov.source_url == "https://example-news.com/article/123"
    assert prov.valid_from == date(2024, 1, 15)
    assert prov.known_at == datetime(2024, 1, 15, tzinfo=UTC)  # public when published


def test_news_edge_leakage_gate() -> None:
    article = parse_gdelt_article(_RECORD)
    prov = news_provenance(
        article, content_sha256="a" * 64, first_observed_at=datetime(2024, 6, 1, tzinfo=UTC)
    )
    edge = news_mention_edge(official_canonical_id=_OFFICIAL, article=article, provenance=prov)
    assert edge.known_as_of(datetime(2024, 1, 15, tzinfo=UTC)) is True
    assert edge.known_as_of(datetime(2024, 1, 14, tzinfo=UTC)) is False


def test_distinct_articles_separate() -> None:
    a = parse_gdelt_article(_RECORD)
    b = parse_gdelt_article({**_RECORD, "url": "https://example-news.com/article/999"})
    prov_a = news_provenance(
        a, content_sha256="a" * 64, first_observed_at=datetime(2024, 6, 1, tzinfo=UTC)
    )
    prov_b = news_provenance(
        b, content_sha256="b" * 64, first_observed_at=datetime(2024, 6, 1, tzinfo=UTC)
    )
    ea = news_mention_edge(official_canonical_id=_OFFICIAL, article=a, provenance=prov_a)
    eb = news_mention_edge(official_canonical_id=_OFFICIAL, article=b, provenance=prov_b)
    assert ea.edge_id != eb.edge_id


def test_blank_domain_falls_back() -> None:
    article = parse_gdelt_article({**_RECORD, "domain": ""})
    assert article.domain == ""
    prov = news_provenance(
        article, content_sha256="a" * 64, first_observed_at=datetime(2024, 6, 1, tzinfo=UTC)
    )
    edge = news_mention_edge(official_canonical_id=_OFFICIAL, article=article, provenance=prov)
    assert edge.dst_id == "outlet:unknown"
