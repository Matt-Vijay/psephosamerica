"""Polite, bounded, resumable HTTP acquisition with immutable byte receipts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from .store import Store, json_text, utc_now

USER_AGENT = "PsephosLegal/0.1 (local legal-information research; polite bulk acquisition)"
SAFE_HEADERS = {
    "content-type",
    "content-length",
    "content-encoding",
    "etag",
    "last-modified",
    "date",
    "retry-after",
    "cache-control",
    "location",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    "content-disposition",
}


@dataclass(frozen=True)
class Receipt:
    id: int
    url: str
    sha256: str
    observed_at: str
    size: int


class AcquisitionError(RuntimeError):
    pass


def validate_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise AcquisitionError("Sources must use public HTTPS URLs without credentials")


def retry_seconds(value: str, fallback: float) -> float:
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
        return max(0.0, (when - datetime.now(UTC)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return fallback


def robots_lines(data: bytes) -> list[str]:
    """Accept ordinary robots text, not a silently permissive HTML/error page."""
    text = data.decode("utf-8-sig", "replace")
    if text.lstrip().startswith("<"):
        raise AcquisitionError("Publisher robots endpoint returned markup, not a policy")
    lines = text.splitlines()
    directives = r"\b(?:user-agent|allow|disallow|crawl-delay|request-rate|sitemap)\s*:"
    for line in lines:
        if len(re.findall(directives, line.split("#", 1)[0], re.I)) > 1:
            raise AcquisitionError("Ambiguous robots policy: multiple directives on one line")
    return lines


class Acquirer:
    def __init__(
        self,
        store: Store,
        *,
        refresh: bool = False,
        max_bytes: int = 4 * 1024**3,
        delay: float = 1.0,
    ):
        self.store = store
        self.refresh = refresh
        self.max_bytes = max_bytes
        self.downloaded = 0
        self.delay = delay
        self.last_request: dict[str, float] = {}
        self.robots: dict[str, RobotFileParser] = {}
        self.client = httpx.Client(
            timeout=httpx.Timeout(90, connect=30),
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"},
            trust_env=False,
        )

    def close(self) -> None:
        self.client.close()

    def _pause(self, url: str, delay: float | None = None) -> None:
        parts = urlsplit(url)
        host = parts.netloc
        parser = self.robots.get(f"{parts.scheme}://{host}")
        policy_delay = parser.crawl_delay(USER_AGENT) if parser is not None else None
        minimum = max(self.delay, delay or 0, float(policy_delay or 0))
        remaining = self.last_request.get(host, 0) + minimum - time.monotonic()
        if remaining > 30:
            raise AcquisitionError(
                f"Publisher minimum delay requires {remaining:.0f}s; resume later"
            )
        if remaining > 0:
            time.sleep(remaining)
        self.last_request[host] = time.monotonic()

    def _record(
        self,
        url: str,
        final_url: str,
        observed: str,
        status: int,
        headers: dict[str, str],
        sha: str | None,
        error: str | None,
    ) -> int:
        with self.store.db:
            cursor = self.store.db.execute(
                "INSERT INTO acquisitions(url,final_url,observed_at,status,sha256,headers,error) "
                "VALUES (?,?,?,?,?,?,?)",
                (url, final_url, observed, status, sha, json_text(headers), error),
            )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def _cached(self, url: str) -> Receipt | None:
        row = self.store.db.execute(
            "SELECT a.*,b.bytes FROM acquisitions a JOIN artifacts b ON b.sha256=a.sha256 "
            "WHERE url=? AND status BETWEEN 200 AND 299 ORDER BY a.id DESC LIMIT 1",
            (url,),
        ).fetchone()
        if row:
            self.store.artifact(row["sha256"])
            return Receipt(row["id"], url, row["sha256"], row["observed_at"], row["bytes"])
        return None

    def _allowed(self, url: str) -> None:
        parts = urlsplit(url)
        validate_url(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self.robots:
            robots_url = origin + "/robots.txt"
            parser = RobotFileParser(robots_url)
            try:
                receipt = self.fetch(robots_url, check_robots=False, max_age_seconds=86400)
                parser.parse(robots_lines(self.store.artifact(receipt.sha256)))
            except AcquisitionError as error:
                row = self.store.db.execute(
                    "SELECT status FROM acquisitions WHERE url=? ORDER BY id DESC LIMIT 1",
                    (robots_url,),
                ).fetchone()
                if row and row[0] in (404, 410):
                    parser.parse([])
                else:
                    raise AcquisitionError(
                        f"Cannot verify robots policy: {origin}: {error}"
                    ) from None
            self.robots[origin] = parser
        parser = self.robots[origin]
        if not parser.can_fetch(USER_AGENT, url):
            raise AcquisitionError(f"Disallowed by publisher robots policy: {url}")
        # The fetch loop enforces this host's policy in its single pre-request pause.

    def fetch(
        self,
        url: str,
        *,
        immutable: bool = False,
        check_robots: bool = True,
        max_file_bytes: int = 1024**3,
        accept: str | None = None,
        max_age_seconds: int | None = None,
    ) -> Receipt:
        validate_url(url)
        cached = self._cached(url)
        fresh = not self.refresh
        if cached and max_age_seconds is not None:
            last_check = self.store.db.execute(
                "SELECT max(observed_at) FROM acquisitions WHERE url=? "
                "AND (status BETWEEN 200 AND 299 OR status=304)",
                (url,),
            ).fetchone()[0]
            age = (
                datetime.now(UTC) - datetime.fromisoformat(last_check.replace("Z", "+00:00"))
            ).total_seconds()
            fresh = age < max_age_seconds
        if cached and (immutable or fresh):
            return cached
        if check_robots:
            self._allowed(url)
        conditional: dict[str, str] = {}
        if accept:
            conditional["Accept"] = accept
        if cached:
            row = self.store.db.execute(
                "SELECT headers FROM acquisitions WHERE id=?", (cached.id,)
            ).fetchone()
            headers = json.loads(row[0])
            if headers.get("etag"):
                conditional["If-None-Match"] = headers["etag"]
            elif headers.get("last-modified"):
                conditional["If-Modified-Since"] = headers["last-modified"]
        current = url
        for attempt in range(5):
            self._pause(current)
            observed = utc_now()
            status = 0
            response_headers: dict[str, str] = {}
            temporary: Path | None = None
            try:
                with self.client.stream("GET", current, headers=conditional) as response:
                    status = response.status_code
                    response_headers = {
                        k: v for k, v in response.headers.items() if k in SAFE_HEADERS
                    }
                    if status in (301, 302, 303, 307, 308):
                        destination = str(response.url.join(response.headers["location"]))
                        validate_url(destination)
                        self._record(url, current, observed, status, response_headers, None, None)
                        if "retry-after" in response.headers:
                            wait = retry_seconds(response.headers["retry-after"], 0)
                            if wait > 30:
                                raise AcquisitionError("Publisher redirect deferred; resume later")
                            time.sleep(wait)
                        if check_robots:
                            self._allowed(destination)
                        current = destination
                        continue
                    if status == 304 and cached:
                        self._record(
                            url, current, observed, status, response_headers, cached.sha256, None
                        )
                        return cached
                    if status in (429, 500, 502, 503, 504) and attempt < 4:
                        self._record(
                            url, current, observed, status, response_headers, None, "retry"
                        )
                        retry = response.headers.get("retry-after", "")
                        wait = retry_seconds(retry, 2 ** (attempt + 1))
                        if wait > 30:
                            raise AcquisitionError(
                                f"Publisher requests retry after {retry}; resume later"
                            )
                        time.sleep(wait)
                        continue
                    response.raise_for_status()
                    file_hash = hashlib.sha256()
                    size = 0
                    staging = self.store.root / "staging"
                    staging.mkdir(exist_ok=True)
                    with tempfile.NamedTemporaryFile(dir=staging, delete=False) as output:
                        temporary = Path(output.name)
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            self.downloaded += len(chunk)
                            if size > max_file_bytes or self.downloaded > self.max_bytes:
                                raise AcquisitionError("Acquisition byte cap exceeded")
                            file_hash.update(chunk)
                            output.write(chunk)
                    if not response.headers.get("content-encoding"):
                        declared = response.headers.get("content-length")
                        if declared and int(declared) != size:
                            raise AcquisitionError("Incomplete HTTP representation")
                    sha = file_hash.hexdigest()
                    target = self.store.object_path(sha)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.exists():
                        self.store.artifact(sha)
                        temporary.unlink()
                    else:
                        os.replace(temporary, target)
                    temporary = None
                    with self.store.db:
                        self.store.db.execute(
                            "INSERT OR IGNORE INTO artifacts VALUES (?,?)", (sha, size)
                        )
                    response_headers["psephos_request_started_at"] = observed
                    observed = utc_now()
                    response_headers["psephos_observed_at_basis"] = "complete response retained"
                    receipt_id = self._record(
                        url, current, observed, status, response_headers, sha, None
                    )
                    return Receipt(receipt_id, url, sha, observed, size)
            except (httpx.HTTPError, OSError, AcquisitionError) as exc:
                self._record(url, current, observed, status, response_headers, None, str(exc))
                raise AcquisitionError(f"{url}: {exc}") from exc
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        raise AcquisitionError(f"Too many redirects/retries: {url}")

    def json(self, url: str, *, immutable: bool = False) -> tuple[Receipt, Any]:
        receipt = self.fetch(url, immutable=immutable)
        return receipt, json.loads(self.store.artifact(receipt.sha256))
