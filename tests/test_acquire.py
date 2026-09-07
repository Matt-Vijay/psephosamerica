import gzip
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from psephos.acquire import Acquirer, AcquisitionError, retry_seconds


def client(store, handler, **kwargs):
    a = Acquirer(store, delay=0, **kwargs)
    a.client.close()
    a.client = httpx.Client(transport=httpx.MockTransport(handler))
    return a


def test_decoded_hash_caps_and_cached_role_independence(store):
    raw = b"publisher bytes " * 100
    wire = gzip.compress(raw)
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(
            200,
            headers={"Content-Encoding": "gzip", "Content-Length": str(len(wire))},
            stream=httpx.ByteStream(wire),
        )

    a = client(store, handler)
    try:
        one = a.fetch("https://example.test/source", check_robots=False)
        two = a.fetch("https://example.test/source", check_robots=False)
        assert one == two and len(requests) == 1
        assert a.downloaded == len(raw) and store.artifact(one.sha256) == raw
        with pytest.raises(AcquisitionError, match="cap"):
            a.fetch("https://example.test/other", check_robots=False, max_file_bytes=10)
        assert a.downloaded > len(raw)
    finally:
        a.close()


@pytest.mark.parametrize(
    "destination", ["http://example.test/file", "https://user:password@example.test/file"]
)
def test_redirects_never_downgrade_or_use_credentials(store, destination):
    a = client(store, lambda request: httpx.Response(302, headers={"Location": destination}))
    try:
        with pytest.raises(AcquisitionError, match="HTTPS"):
            a.fetch("https://example.test/robots.txt", check_robots=False)
    finally:
        a.close()


def test_robots_rechecked_after_cache_age_and_block_respected(store):
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200, text="User-agent: *\nDisallow: /blocked\n")

    a = client(store, handler)
    try:
        with pytest.raises(AcquisitionError, match="Disallowed"):
            a.fetch("https://example.test/blocked")
        assert requests == ["/robots.txt"]
        with store.db:
            store.db.execute("UPDATE acquisitions SET observed_at='2020-01-01T00:00:00Z'")
        a.robots.clear()
        with pytest.raises(AcquisitionError, match="Disallowed"):
            a.fetch("https://example.test/blocked")
        assert requests == ["/robots.txt", "/robots.txt"]
    finally:
        a.close()


def test_robots_delay_is_one_per_host_pre_request_pause(store, monkeypatch):
    now = 1000.0
    starts = []
    retries = 0

    def sleep(seconds):
        nonlocal now
        now += seconds

    monkeypatch.setattr("psephos.acquire.time.monotonic", lambda: now)
    monkeypatch.setattr("psephos.acquire.time.sleep", sleep)

    def handler(request):
        nonlocal retries
        starts.append((str(request.url), now))
        if request.url.path == "/robots.txt":
            delay = 20 if request.url.host == "other.test" else 10
            return httpx.Response(200, text=f"User-agent: *\nCrawl-delay: {delay}\n")
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"Location": "https://other.test/final"})
        if request.url.path == "/retry":
            retries += 1
            if retries == 1:
                return httpx.Response(429, headers={"Retry-After": "3"})
        return httpx.Response(200, content=b"fixture")

    a = client(store, handler)
    a.delay = 10.1
    try:
        for path in ("first", "second", "redirect"):
            a.fetch("https://example.test/" + path)
        # A robots refresh is still a request to its own host, with its known policy.
        a.fetch("https://other.test/robots.txt", check_robots=False, max_age_seconds=0)
        a.fetch("https://example.test/after")
        a.fetch("https://example.test/retry")
    finally:
        a.close()
    assert [url for url, _ in starts] == [
        "https://example.test/robots.txt",
        "https://example.test/first",
        "https://example.test/second",
        "https://example.test/redirect",
        "https://other.test/robots.txt",
        "https://other.test/final",
        "https://other.test/robots.txt",
        "https://example.test/after",
        "https://example.test/retry",
        "https://example.test/retry",
    ]
    assert [started for _, started in starts] == pytest.approx(
        [1000, 1010.1, 1020.2, 1030.3, 1030.3, 1050.3, 1070.3, 1070.3, 1080.4, 1090.5]
    )


def test_long_publisher_delays_defer_instead_of_truncating(store):
    a = client(store, lambda request: httpx.Response(200, text="User-agent: *\nCrawl-delay: 120\n"))
    try:
        with pytest.raises(AcquisitionError, match="delay"):
            a.fetch("https://example.test/source")
    finally:
        a.close()
    later = format_datetime(datetime.now(UTC) + timedelta(minutes=10), usegmt=True)
    assert 595 < retry_seconds(later, 0) <= 600
    a = client(store, lambda request: httpx.Response(429, headers={"Retry-After": later}))
    try:
        with pytest.raises(AcquisitionError, match="resume later"):
            a.fetch("https://example.test/source", check_robots=False)
    finally:
        a.close()


@pytest.mark.parametrize(
    "policy,reason",
    [
        (b"\xef\xbb\xbfUser-agent: *\nCrawl-delay: 120\n", "delay"),
        (b"User-agent: * Disallow: /", "multiple directives"),
        (b"<html><body>Application</body></html>", "markup"),
    ],
)
def test_publisher_policy_variants_do_not_silently_allow_requests(store, policy, reason):
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200, content=policy)

    a = client(store, handler)
    try:
        with pytest.raises(AcquisitionError, match=reason):
            a.fetch("https://example.test/source")
        assert requests == ["/robots.txt"]
        assert store.db.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 1
    finally:
        a.close()
