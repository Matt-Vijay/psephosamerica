"""Tests for the pure RSS/URL helpers in public_statement_rss_materialize."""

from __future__ import annotations

from defusedxml.ElementTree import fromstring

from src.runtime.public_statement_rss_materialize import (
    _child_text,
    _clean_string,
    _is_official_member_host,
    _item_date,
    _item_link,
    _member_name,
    _normalized_statement_url,
)

_ATOM = 'xmlns:atom="http://www.w3.org/2005/Atom"'


def test_item_link_prefers_rss_link() -> None:
    item = fromstring("<item><link>https://a.house.gov/news</link></item>")
    assert _item_link(item) == "https://a.house.gov/news"


def test_item_link_falls_back_to_atom_href() -> None:
    item = fromstring(f'<item {_ATOM}><atom:link href="https://a.house.gov/atom"/></item>')
    assert _item_link(item) == "https://a.house.gov/atom"


def test_item_link_none_when_absent() -> None:
    assert _item_link(fromstring("<item><title>t</title></item>")) is None


def test_item_date_parses_rfc822_and_iso_and_rejects_garbage() -> None:
    assert (
        _item_date(fromstring("<item><pubDate>Wed, 15 May 2024 12:00:00 GMT</pubDate></item>"))
        == "2024-05-15"
    )
    assert (
        _item_date(fromstring("<item><published>2024-05-15T09:00:00Z</published></item>"))
        == "2024-05-15"
    )
    assert _item_date(fromstring("<item><title>no date</title></item>")) is None
    assert _item_date(fromstring("<item><pubDate>not-a-date</pubDate></item>")) is None


def test_child_text_unescapes_strips_and_handles_missing() -> None:
    assert _child_text(fromstring("<item><title>  A&amp;B  </title></item>"), "title") == "A&B"
    assert _child_text(fromstring("<item><title></title></item>"), "title") is None
    assert _child_text(fromstring("<item/>"), "title") is None


def test_is_official_member_host() -> None:
    assert _is_official_member_host("https://house.gov/x") is True
    assert _is_official_member_host("https://jdoe.house.gov/x") is True
    assert _is_official_member_host("https://senate.gov/x") is True
    assert _is_official_member_host("https://jane.senate.gov/x") is True
    assert _is_official_member_host("https://evil.test/x") is False
    assert _is_official_member_host("ftp://jdoe.house.gov/x") is False
    assert _is_official_member_host("not a url") is False


def test_normalized_statement_url_rejects_unofficial_and_empty() -> None:
    assert _normalized_statement_url(None) is None
    assert _normalized_statement_url("") is None
    assert _normalized_statement_url("https://evil.test/news") is None


def test_member_name_from_official_or_first_last() -> None:
    assert _member_name({"official_full": "Jane Q. Doe"}) == "Jane Q. Doe"
    assert _member_name({"first": "Jane", "last": "Doe"}) == "Jane Doe"
    assert _member_name({"first": "  ", "last": "Doe"}) == "Doe"
    assert _member_name("not a dict") == ""


def test_clean_string() -> None:
    assert _clean_string("  hi  ") == "hi"
    assert _clean_string("   ") is None
    assert _clean_string(123) is None
