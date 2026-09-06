"""Materialize raw public-statement records from official member RSS feeds."""

from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import ssl
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import certifi
import yaml  # type: ignore[import-untyped]
from defusedxml.ElementTree import ParseError, fromstring

from src.evidence.source_anchor_policy import is_official_source_url

_DEFAULT_LEGISLATORS_URL = (
    "https://raw.githubusercontent.com/unitedstates/congress-legislators/"
    "main/legislators-current.yaml"
)
_DEFAULT_MAX_SOURCE_BYTES = 5 * 1024 * 1024
_DEFAULT_MAX_FEED_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class PublicStatementRssMaterializeResult:
    source_url: str
    source_path: str | None
    output_path: str
    dry_run: bool
    force: bool
    feed_count: int
    fetched_feed_count: int
    row_count: int
    skipped_count: int
    skipped_reasons: dict[str, int]
    output_sha256: str | None
    source_sha256: str | None


def materialize_public_statement_rss(
    *,
    output_path: Path,
    source_path: Path | None = None,
    source_url: str = _DEFAULT_LEGISLATORS_URL,
    dry_run: bool = False,
    force: bool = False,
    timeout: float = 30.0,
    max_feeds: int | None = None,
    max_items_per_feed: int | None = None,
    max_source_bytes: int = _DEFAULT_MAX_SOURCE_BYTES,
    max_feed_bytes: int = _DEFAULT_MAX_FEED_BYTES,
) -> PublicStatementRssMaterializeResult:
    """Fetch official member RSS feeds into raw statement JSONL."""
    _validate_https_url(source_url)
    _validate_options(
        timeout=timeout,
        max_feeds=max_feeds,
        max_items_per_feed=max_items_per_feed,
        max_source_bytes=max_source_bytes,
        max_feed_bytes=max_feed_bytes,
    )
    if output_path.is_file() and not force and not dry_run:
        raise FileExistsError(output_path)

    materialized_source_path: Path | None = None
    source = source_path
    if source is None and not dry_run:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        materialized_source_path = _download_to_temp_file(
            source_url,
            output_path.parent,
            timeout=timeout,
            max_bytes=max_source_bytes,
        )
        source = materialized_source_path
    if source is None:
        feeds = []
    else:
        if not source.is_file():
            raise FileNotFoundError(source)
        feeds = _rss_feeds_from_legislators(source)
    if max_feeds is not None:
        feeds = feeds[:max_feeds]

    rows: list[dict[str, Any]] = []
    skipped_reasons: dict[str, int] = {}
    fetched_feed_count = 0
    if not dry_run:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_output = output_path.with_name(f".{output_path.name}.{uuid4()}.tmp")
        try:
            for feed in feeds:
                try:
                    body = _fetch_text(
                        feed["rss_url"],
                        timeout=timeout,
                        max_bytes=max_feed_bytes,
                    )
                except Exception:  # noqa: BLE001
                    _increment(skipped_reasons, "feed_fetch_failed")
                    continue
                fetched_feed_count += 1
                try:
                    feed_rows, feed_skips = _rows_from_feed(
                        body,
                        member=feed,
                        max_items=max_items_per_feed,
                    )
                except ParseError:
                    _increment(skipped_reasons, "feed_parse_failed")
                    continue
                rows.extend(feed_rows)
                for reason, count in feed_skips.items():
                    skipped_reasons[reason] = skipped_reasons.get(reason, 0) + count
            _write_jsonl(temp_output, rows)
            temp_output.replace(output_path)
        finally:
            _unlink_if_exists(temp_output)
            if materialized_source_path is not None:
                _unlink_if_exists(materialized_source_path)

    return PublicStatementRssMaterializeResult(
        source_url=source_url,
        source_path=str(source_path) if source_path is not None else None,
        output_path=str(output_path),
        dry_run=dry_run,
        force=force,
        feed_count=len(feeds),
        fetched_feed_count=fetched_feed_count,
        row_count=len(rows),
        skipped_count=sum(skipped_reasons.values()),
        skipped_reasons=dict(sorted(skipped_reasons.items())),
        output_sha256=_sha256_file(output_path) if output_path.is_file() else None,
        source_sha256=_sha256_file(source)
        if source_path is not None and source is not None
        else None,
    )


def _rss_feeds_from_legislators(path: Path) -> list[dict[str, str]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("legislator source must be a YAML list")
    feeds: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        ids = item.get("id")
        terms = item.get("terms")
        if not isinstance(ids, dict) or not isinstance(terms, list) or not terms:
            continue
        bioguide_id = _clean_string(ids.get("bioguide"))
        latest_term = terms[-1]
        if not isinstance(latest_term, dict) or bioguide_id is None:
            continue
        rss_url = _clean_string(latest_term.get("rss_url"))
        member_url = _clean_string(latest_term.get("url"))
        if rss_url is None or member_url is None or not _is_official_member_host(rss_url):
            continue
        name = item.get("name")
        feeds.append(
            {
                "member_bioguide_id": bioguide_id,
                "member_name": _member_name(name),
                "member_url": member_url,
                "rss_url": html.unescape(rss_url),
            }
        )
    feeds.sort(key=lambda row: row["member_bioguide_id"])
    return feeds


def _rows_from_feed(
    body: str,
    *,
    member: dict[str, str],
    max_items: int | None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    root = fromstring(body)
    items = list(root.findall(".//item"))
    if not items:
        items = list(root.findall(".//{http://www.w3.org/2005/Atom}entry"))
    if max_items is not None:
        items = items[:max_items]
    rows: list[dict[str, Any]] = []
    skipped: dict[str, int] = {}
    for item in items:
        title = _child_text(item, "title")
        link = _item_link(item)
        statement_url = _normalized_statement_url(link)
        statement_date = _item_date(item)
        if title is None or statement_url is None or statement_date is None:
            _increment(skipped, "missing_required_item_fields")
            continue
        rows.append(
            {
                "member_bioguide_id": member["member_bioguide_id"],
                "member_name": member["member_name"],
                "statement_id": _statement_id(
                    member["member_bioguide_id"], statement_date, statement_url
                ),
                "statement_date": statement_date,
                "statement_source_url": statement_url,
                "title": title,
                "summary": _child_text(item, "description") or _child_text(item, "summary"),
                "rss_url": member["rss_url"],
            }
        )
    return rows, skipped


def _item_link(item: Any) -> str | None:
    link = _child_text(item, "link")
    if link is not None:
        return link
    atom_link = item.find("{http://www.w3.org/2005/Atom}link")
    if atom_link is not None:
        href = atom_link.attrib.get("href")
        if isinstance(href, str) and href:
            return href
    return None


def _item_date(item: Any) -> str | None:
    raw = (
        _child_text(item, "pubDate")
        or _child_text(item, "published")
        or _child_text(item, "updated")
    )
    if raw is None:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
        return parsed.date().isoformat()
    except (TypeError, ValueError):
        try:
            return dt.date.fromisoformat(raw[:10]).isoformat()
        except ValueError:
            return None


def _child_text(item: Any, tag: str) -> str | None:
    child = item.find(tag)
    if child is None:
        child = item.find(f"{{http://www.w3.org/2005/Atom}}{tag}")
    if child is None or child.text is None:
        return None
    value = html.unescape(child.text).strip()
    return value or None


def _normalized_statement_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(html.unescape(value.strip()))
    if parsed.scheme == "http" and parsed.hostname and _is_official_member_host(value):
        parsed = parsed._replace(scheme="https")
    normalized = urlunparse(parsed)
    return normalized if is_official_source_url("public_statement", normalized) else None


def _is_official_member_host(value: str) -> bool:
    parsed = urlparse(html.unescape(value.strip()))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    hostname = parsed.hostname.lower()
    return (
        hostname == "house.gov"
        or hostname.endswith(".house.gov")
        or hostname == "senate.gov"
        or hostname.endswith(".senate.gov")
    )


def _statement_id(member_id: str, statement_date: str, url: str) -> str:
    digest = hashlib.sha256(f"{member_id}|{statement_date}|{url}".encode()).hexdigest()[:16]
    return f"statement-{digest}"


def _member_name(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    official = _clean_string(value.get("official_full"))
    if official is not None:
        return official
    first = _clean_string(value.get("first")) or ""
    last = _clean_string(value.get("last")) or ""
    return " ".join(part for part in (first, last) if part)


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _fetch_text(url: str, *, timeout: float, max_bytes: int) -> str:
    _validate_public_statement_feed_url(url)
    context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(url, timeout=timeout, context=context) as response:  # nosec B310
        _validate_response_url(url, response)
        body = _read_bounded_response(response, max_bytes=max_bytes)
    if not isinstance(body, bytes):
        raise TypeError("RSS response body must be bytes")
    return body.decode("utf-8", errors="replace")


def _download_to_temp_file(
    url: str,
    output_dir: Path,
    *,
    timeout: float,
    max_bytes: int,
) -> Path:
    _validate_https_url(url)
    output_dir.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".public-statements-", suffix=".yaml", dir=output_dir)
    os.close(fd)
    temp_path = Path(temp_name)
    temp_path.unlink(missing_ok=True)
    try:
        with urllib.request.urlopen(  # nosec B310
            url,
            timeout=timeout,
            context=ssl.create_default_context(cafile=certifi.where()),
        ) as response:
            _validate_response_url(url, response)
            temp_path.write_bytes(_read_bounded_response(response, max_bytes=max_bytes))
    except Exception:
        _unlink_if_exists(temp_path)
        raise
    return temp_path


def _validate_https_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("source_url must be HTTPS")


def _validate_public_statement_feed_url(url: str) -> None:
    if not _is_official_member_host(url):
        raise ValueError("RSS feed URL must be an official House or Senate HTTP(S) URL")


def _validate_options(
    *,
    timeout: float,
    max_feeds: int | None,
    max_items_per_feed: int | None,
    max_source_bytes: int,
    max_feed_bytes: int,
) -> None:
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if max_feeds is not None and max_feeds < 0:
        raise ValueError("limits must be non-negative")
    if max_items_per_feed is not None and max_items_per_feed < 0:
        raise ValueError("limits must be non-negative")
    if max_source_bytes <= 0 or max_feed_bytes <= 0:
        raise ValueError("max byte limits must be positive")


def _validate_response_url(requested_url: str, response: Any) -> None:
    final_url = getattr(response, "geturl", lambda: None)()
    if not isinstance(final_url, str) or not final_url:
        return
    requested_host = urlparse(requested_url).hostname
    final = urlparse(final_url)
    final_host = final.hostname
    if final.scheme not in {"http", "https"}:
        raise ValueError("public statement response URL must be HTTP(S)")
    if requested_host is None or final_host is None:
        raise ValueError("response URL must include a host")
    if final_host.lower() != requested_host.lower():
        raise ValueError("off-origin public statement response URL")


def _read_bounded_response(response: Any, *, max_bytes: int) -> bytes:
    body = response.read(max_bytes + 1)
    if not isinstance(body, bytes):
        raise TypeError("public statement response body must be bytes")
    if len(body) > max_bytes:
        raise ValueError("public statement response exceeds maximum size")
    return body


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True))
            fh.write("\n")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _increment(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


def _unlink_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
