from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest

from src.runtime.public_statement_rss_materialize import (
    materialize_public_statement_rss,
)

_MODULE = "src.runtime.public_statement_rss_materialize"


class _Response(io.BytesIO):
    def __init__(self, body: bytes, *, final_url: str | None = None) -> None:
        super().__init__(body)
        self._final_url = final_url

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def geturl(self) -> str:
        return self._final_url or ""


def _legislators(path: Path) -> Path:
    path.write_text(
        """
- id:
    bioguide: A000001
  name:
    official_full: Alice Adams
  terms:
    - type: rep
      url: https://adams.house.gov
      rss_url: http://adams.house.gov/rss.xml
- id:
    bioguide: B000002
  name:
    first: Bob
    last: Baker
  terms:
    - type: sen
      url: https://baker.senate.gov
      rss_url: https://baker.senate.gov/rss
- id:
    bioguide: C000003
  name:
    official_full: Casey Clark
  terms:
    - type: rep
      url: https://example.com
      rss_url: https://example.com/rss
""",
        encoding="utf-8",
    )
    return path


def _rss() -> bytes:
    return b"""<?xml version="1.0"?>
<rss><channel>
  <item>
    <title>Energy permitting update</title>
    <link>http://adams.house.gov/news/energy</link>
    <pubDate>Mon, 06 May 2024 12:00:00 GMT</pubDate>
    <description>Grid and energy permitting statement.</description>
  </item>
  <item>
    <title>Unofficial mirror</title>
    <link>https://example.com/news</link>
    <pubDate>Mon, 06 May 2024 12:00:00 GMT</pubDate>
  </item>
</channel></rss>"""


def _rows(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def test_materialize_public_statement_rss_from_local_legislators(
    tmp_path: Path,
) -> None:
    source = _legislators(tmp_path / "legislators-current.yaml")
    output = tmp_path / "public-statements.jsonl"

    def _urlopen(url: str, *, timeout: float, context: object) -> _Response:
        assert timeout == 12.0
        assert context is not None
        assert url in {"http://adams.house.gov/rss.xml", "https://baker.senate.gov/rss"}
        return _Response(_rss())

    with patch(f"{_MODULE}.urllib.request.urlopen", side_effect=_urlopen):
        result = materialize_public_statement_rss(
            output_path=output,
            source_path=source,
            timeout=12.0,
            max_items_per_feed=1,
        )

    rows = _rows(output)
    assert result.feed_count == 2
    assert result.fetched_feed_count == 2
    assert result.row_count == 2
    assert result.output_sha256 == hashlib.sha256(output.read_bytes()).hexdigest()
    assert result.source_sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert rows[0]["member_bioguide_id"] == "A000001"
    assert rows[0]["member_name"] == "Alice Adams"
    assert rows[0]["statement_date"] == "2024-05-06"
    assert rows[0]["statement_source_url"] == "https://adams.house.gov/news/energy"
    assert rows[0]["statement_id"].startswith("statement-")


def test_materialize_public_statement_rss_dry_run_counts_local_feeds(
    tmp_path: Path,
) -> None:
    source = _legislators(tmp_path / "legislators-current.yaml")
    output = tmp_path / "public-statements.jsonl"

    result = materialize_public_statement_rss(
        output_path=output,
        source_path=source,
        dry_run=True,
    )

    assert result.dry_run is True
    assert result.feed_count == 2
    assert result.row_count == 0
    assert not output.exists()


def test_materialize_public_statement_rss_skips_malformed_feed(
    tmp_path: Path,
) -> None:
    source = _legislators(tmp_path / "legislators-current.yaml")
    output = tmp_path / "public-statements.jsonl"

    def _urlopen(url: str, *, timeout: float, context: object) -> _Response:
        assert url in {"http://adams.house.gov/rss.xml", "https://baker.senate.gov/rss"}
        return _Response(b"<rss><channel><item></channel></rss>")

    with patch(f"{_MODULE}.urllib.request.urlopen", side_effect=_urlopen):
        result = materialize_public_statement_rss(
            output_path=output,
            source_path=source,
        )

    assert result.feed_count == 2
    assert result.fetched_feed_count == 2
    assert result.row_count == 0
    assert result.skipped_reasons == {"feed_parse_failed": 2}
    assert output.read_text(encoding="utf-8") == ""


def test_materialize_public_statement_rss_refuses_existing_without_force(
    tmp_path: Path,
) -> None:
    output = tmp_path / "public-statements.jsonl"
    output.write_text("existing\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        materialize_public_statement_rss(
            output_path=output,
            source_path=_legislators(tmp_path / "legislators-current.yaml"),
        )


def test_materialize_public_statement_rss_uses_unique_temp_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _legislators(tmp_path / "legislators-current.yaml")
    output = tmp_path / "public-statements.jsonl"
    seen_temp_names: list[str] = []
    original_open = Path.open

    def guarded_open(path: Path, *args: object, **kwargs: object):
        mode = str(args[0]) if args else str(kwargs.get("mode", "r"))
        if path == output and any(flag in mode for flag in ("w", "a", "x", "+")):
            raise AssertionError("direct final-path write")
        if path.name == ".public-statements.jsonl.tmp":
            raise AssertionError("fixed temp filename used")
        if path.name.startswith(".public-statements.jsonl.") and path.name.endswith(".tmp"):
            token = path.name.removeprefix(".public-statements.jsonl.").removesuffix(".tmp")
            UUID(token)
            seen_temp_names.append(path.name)
        return original_open(path, *args, **kwargs)

    def _urlopen(url: str, *, timeout: float, context: object) -> _Response:
        assert url in {"http://adams.house.gov/rss.xml", "https://baker.senate.gov/rss"}
        return _Response(_rss())

    monkeypatch.setattr(Path, "open", guarded_open)
    with patch(f"{_MODULE}.urllib.request.urlopen", side_effect=_urlopen):
        materialize_public_statement_rss(
            output_path=output,
            source_path=source,
            max_items_per_feed=1,
        )

    assert len(seen_temp_names) == 1
    assert output.is_file()


def test_materialize_public_statement_rss_cleans_source_temp_when_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "public-statements.jsonl"
    original_write_bytes = Path.write_bytes
    temp_paths: list[Path] = []

    def failing_write_bytes(path: Path, data: bytes) -> int:
        if path.name.startswith(".public-statements-") and path.suffix == ".yaml":
            temp_paths.append(path)
            original_write_bytes(path, b"partial")
            raise OSError("source temp write failed")
        return original_write_bytes(path, data)

    def _urlopen(url: str, *, timeout: float, context: object) -> _Response:
        assert url == "https://example.test/legislators.yaml"
        return _Response(b"source")

    monkeypatch.setattr(Path, "write_bytes", failing_write_bytes)
    with patch(f"{_MODULE}.urllib.request.urlopen", side_effect=_urlopen):
        with pytest.raises(OSError, match="source temp write failed"):
            materialize_public_statement_rss(
                output_path=output,
                source_url="https://example.test/legislators.yaml",
            )

    assert not output.exists()
    assert temp_paths
    assert all(not path.exists() for path in temp_paths)


def test_materialize_public_statement_rss_rejects_non_https_source_url(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="source_url must be HTTPS"):
        materialize_public_statement_rss(
            output_path=tmp_path / "public-statements.jsonl",
            source_url="http://example.test/legislators.yaml",
            dry_run=True,
        )


def test_materialize_public_statement_rss_rejects_non_positive_timeout(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="timeout must be positive"):
        materialize_public_statement_rss(
            output_path=tmp_path / "public-statements.jsonl",
            source_path=_legislators(tmp_path / "legislators-current.yaml"),
            timeout=0,
        )


@pytest.mark.parametrize("max_feeds,max_items", [(-1, None), (None, -1)])
def test_materialize_public_statement_rss_rejects_negative_limits(
    tmp_path: Path,
    max_feeds: int | None,
    max_items: int | None,
) -> None:
    with pytest.raises(ValueError, match="limits must be non-negative"):
        materialize_public_statement_rss(
            output_path=tmp_path / "public-statements.jsonl",
            source_path=_legislators(tmp_path / "legislators-current.yaml"),
            max_feeds=max_feeds,
            max_items_per_feed=max_items,
        )


def test_materialize_public_statement_rss_skips_off_origin_feed_response(
    tmp_path: Path,
) -> None:
    source = _legislators(tmp_path / "legislators-current.yaml")
    output = tmp_path / "public-statements.jsonl"

    def _urlopen(url: str, *, timeout: float, context: object) -> _Response:
        return _Response(_rss(), final_url="https://example.com/rss.xml")

    with patch(f"{_MODULE}.urllib.request.urlopen", side_effect=_urlopen):
        result = materialize_public_statement_rss(
            output_path=output,
            source_path=source,
            max_feeds=1,
        )

    assert result.feed_count == 1
    assert result.fetched_feed_count == 0
    assert result.row_count == 0
    assert result.skipped_reasons == {"feed_fetch_failed": 1}


def test_materialize_public_statement_rss_rejects_oversized_feed_body(
    tmp_path: Path,
) -> None:
    source = _legislators(tmp_path / "legislators-current.yaml")
    output = tmp_path / "public-statements.jsonl"

    def _urlopen(url: str, *, timeout: float, context: object) -> _Response:
        return _Response(b"123456")

    with patch(f"{_MODULE}.urllib.request.urlopen", side_effect=_urlopen):
        result = materialize_public_statement_rss(
            output_path=output,
            source_path=source,
            max_feeds=1,
            max_feed_bytes=5,
        )

    assert result.skipped_reasons == {"feed_fetch_failed": 1}
    assert output.read_text(encoding="utf-8") == ""
