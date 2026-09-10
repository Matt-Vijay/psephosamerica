import gzip
import socket
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import httpx
import pytest

from psephos.acquire import Acquirer, AcquisitionError, retry_seconds


def client(store, handler, **kwargs):
    a = Acquirer(store, delay=0, **kwargs)
    a.client.close()
    a.client = httpx.Client(transport=httpx.MockTransport(handler))
    return a


@pytest.mark.parametrize("variable", ["HTTPS_PROXY", "https_proxy"])
def test_https_proxy_is_used_without_direct_dns_or_fallback(store, monkeypatch, variable):
    tunnels = []

    class Proxy(BaseHTTPRequestHandler):
        def do_CONNECT(self):
            tunnels.append(self.path)
            self.send_error(403, "Fixture proxy denies destination")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Proxy)
    worker = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    worker.start()
    for key in ("HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(variable, f"http://127.0.0.1:{server.server_port}")
    # The runtime also supplies SOCKS; HTTPS acquisition must not need socksio.
    monkeypatch.setenv("ALL_PROXY", "socks5h://127.0.0.1:1")
    monkeypatch.setenv("SSL_CERT_FILE", "/nonexistent/psephos-test-ca.pem")
    resolve = socket.getaddrinfo

    def only_proxy(host, *args, **kwargs):
        assert host == "127.0.0.1", "Acquisition bypassed the configured HTTPS proxy"
        return resolve(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", only_proxy)
    a = None
    try:
        a = Acquirer(store, delay=0)
        with pytest.raises(AcquisitionError, match="Fixture proxy denies destination"):
            a.fetch("https://publisher.invalid/source", check_robots=False)
        assert tunnels == ["publisher.invalid:443"]
        assert a.downloaded == 0
        assert store.db.execute("SELECT count(*) FROM acquisitions").fetchone()[0] == 1
        assert store.db.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 0
    finally:
        if a is not None:
            a.close()
        server.shutdown()
        worker.join(timeout=1)
        server.server_close()


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


def test_suffix_receipts_never_satisfy_full_downloads_and_resume(store):
    raw = b"official archive bytes"
    calls = []

    def handler(request):
        calls.append(request.headers.get("range"))
        if request.headers.get("range"):
            assert request.headers["accept-encoding"] == "identity"
            return httpx.Response(
                206,
                content=raw[-5:],
                headers={"Content-Range": f"bytes {len(raw) - 5}-{len(raw) - 1}/{len(raw)}"},
            )
        return httpx.Response(200, content=raw)

    a = client(store, handler)
    try:
        tail = a.fetch("https://example.test/zip", check_robots=False, suffix_bytes=5)
        assert a.fetch("https://example.test/zip", check_robots=False, suffix_bytes=5) == tail
        full = a.fetch("https://example.test/zip", check_robots=False)
        assert store.artifact(full.sha256) == raw
        assert calls == ["bytes=-5", None]
        assert full.sha256 != tail.sha256
        assert a.fetch("https://example.test/zip", check_robots=False, suffix_bytes=5) == full
    finally:
        a.close()


@pytest.mark.parametrize("status,headers", [(200, {}), (206, {"Content-Range": "bytes 0-4/20"})])
def test_bad_range_is_receipted_then_full_retry_can_succeed(store, status, headers):
    a = client(store, lambda request: httpx.Response(status, headers=headers, content=b"12345"))
    try:
        with pytest.raises(AcquisitionError, match="range"):
            a.fetch("https://example.test/zip", check_robots=False, suffix_bytes=5)
        assert a._cached("https://example.test/zip") is None
        assert store.db.execute("SELECT error FROM acquisitions ORDER BY id DESC").fetchone()[0]
    finally:
        a.close()


def test_partial_does_not_refresh_stale_full_representation(store):
    calls = []

    def handler(request):
        calls.append(request.headers.get("range"))
        if request.headers.get("range"):
            return httpx.Response(206, content=b"new", headers={"Content-Range": "bytes 3-5/6"})
        return httpx.Response(200, content=b"old" if len(calls) == 1 else b"updated")

    a = client(store, handler)
    try:
        first = a.fetch("https://example.test/cache", check_robots=False)
        with store.db:
            store.db.execute("UPDATE acquisitions SET observed_at='2020-01-01T00:00:00Z'")
        a.refresh = True
        a.fetch("https://example.test/cache", check_robots=False, suffix_bytes=3)
        a.refresh = False
        later = a.fetch("https://example.test/cache", check_robots=False, max_age_seconds=60)
        assert later.sha256 != first.sha256 and calls == [None, "bytes=-3", None]
    finally:
        a.close()


def test_failed_transfer_bytes_remain_in_census_budget_after_restart(store):
    from psephos.census import census_bytes

    a = client(store, lambda request: httpx.Response(200, content=b"1234567890"))
    try:
        with pytest.raises(AcquisitionError, match="cap"):
            a.fetch("https://www2.census.gov/fixture", check_robots=False, max_file_bytes=5)
        assert a.downloaded == 10
    finally:
        a.close()
    assert census_bytes(store) == 10
    restarted = client(
        store, lambda request: pytest.fail("Exhausted budget must not request bytes"), max_bytes=-5
    )
    try:
        with pytest.raises(AcquisitionError, match="no new request"):
            restarted.fetch("https://www2.census.gov/fixture", check_robots=False)
    finally:
        restarted.close()


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
