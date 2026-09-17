import json

import httpx
import pytest

from psephos.acquire import Acquirer, AcquisitionError
from psephos.south_carolina import (
    BASE,
    INDEX,
    SouthCarolinaAcquirer,
    chapter_units,
    sync_south_carolina,
)


def chapter(number, body):
    return (
        f'<div id="contentsection"><div>Title 1 - Administration</div>'
        f"<div>CHAPTER {number}</div>{body}</div>"
    ).encode()


def test_native_sections_keep_history_tables_context_and_repeat_occurrences():
    raw = chapter(
        1,
        "<span>SECTION 1-1-10.</span> Heading.<br/>Exact body."
        "<table><tr><td>Class</td><td>10</td></tr></table><br/>HISTORY: Old act.<br/>"
        "<span>SECTION 1-1-10.</span> Future wording.<br/>Future act.",
    )
    units = chapter_units(raw, BASE + "/code/t01c001.php", 1, "001")
    assert [u.unit_kind for u in units] == ["scope_context", "section", "section"]
    assert units[1].key == "sc-code:1-1-10:occurrence:1"
    assert units[2].key == "sc-code:1-1-10:occurrence:2"
    assert "Class\t10" in units[1].text and "HISTORY: Old act." in units[1].text
    assert units[1].metadata["tables"] == 1
    with pytest.raises(ValueError, match="identity mismatch"):
        chapter_units(raw, BASE, 2, "001")
    with pytest.raises(ValueError, match="outside requested"):
        chapter_units(raw.replace(b"SECTION 1-1-10.", b"SECTION 2-1-10."), BASE, 1, "001")


def test_untagged_native_body_and_disposition_remain_whole_chapters():
    for body in ("<br/>SECTION 1-1-10. Untagged body.<br/>Exact wording.", "<div>Repealed.</div>"):
        units = chapter_units(chapter(1, body), BASE, 1, "001")
        assert len(units) == 1 and units[0].unit_kind == "chapter"
    with pytest.raises(ValueError, match="No native sections"):
        chapter_units(chapter(1, "<div>Access page; no legal body.</div>"), BASE, 1, "001")


def test_original_ledger_is_shared_with_legacy_lock_and_not_reset(store, tmp_path):
    original = store.root / "collectors/south-carolina/http-budget.json"
    original.parent.mkdir(parents=True)
    history = [
        {"url": BASE, "status": 200, "decoded_bytes": 500, "observed_at": "2026-09-07T00:00:00Z"}
    ]
    original.write_text(
        json.dumps(
            {
                "cap_bytes": 200 * 1024**2,
                "consumed_bytes": 500,
                "reserved_bytes": 0,
                "requests": history,
            }
        )
    )
    a = SouthCarolinaAcquirer(store, tmp_path)
    try:
        assert a.budget_path == original and a.downloaded == 500
        assert a.budget["last_request_unix"] > 0
        with pytest.raises(BlockingIOError):
            SouthCarolinaAcquirer(store, tmp_path)
        a.downloaded += 1
    finally:
        a.close()
    state = json.loads(original.read_text())
    assert state["consumed_bytes"] == 501 and state["requests"] == history
    assert not (tmp_path / "budget.json").exists()


def test_http_denial_is_not_retried_or_bypassed_by_new_instance(store, tmp_path):
    requests = []

    def respond(request):
        requests.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, stream=httpx.ByteStream(b"User-agent: *\nAllow: /\n"))
        return httpx.Response(403, stream=httpx.ByteStream(b"Forbidden"))

    for attempt in range(2):
        a = SouthCarolinaAcquirer(store, tmp_path)
        a.client.close()
        a.client = httpx.Client(
            transport=httpx.MockTransport(respond), event_hooks={"response": [a._bounded_response]}
        )
        a._pause = lambda *args, **kwargs: None
        try:
            with pytest.raises(
                AcquisitionError, match="Retained access denial" if attempt else "403"
            ):
                a.fetch(BASE + "/code/t01c001.php")
        finally:
            a.close()
    assert requests.count(BASE + "/code/t01c001.php") == 1
    assert (
        store.db.execute(
            "SELECT status FROM acquisitions WHERE url=? ORDER BY id DESC LIMIT 1",
            (BASE + "/code/t01c001.php",),
        ).fetchone()[0]
        == 403
    )


def test_limited_resume_keeps_accepted_versions_and_does_not_redownload(store):
    bodies = {
        BASE + "/robots.txt": b"User-agent: *\nAllow: /\n",
        BASE + "/policies.php": b"Publisher copying notice",
        BASE + "/disclaimer.php": b"Publisher disclaimer",
        INDEX: b'<div id="contentsection">now current through the 2025 Session of the General Assembly.<a href="/code/title1.php">Title 1</a></div>',
        BASE
        + "/code/title1.php": b'<div id="contentsection"><table><tr><td><a href="/code/t01c001.php">First</a></td></tr><tr><td><a href="/code/t01c002.php">Second</a></td></tr></table></div>',
        BASE + "/code/t01c001.php": chapter(1, "<span>SECTION 1-1-10.</span> First wording."),
        BASE + "/code/t01c002.php": chapter(2, "<span>SECTION 1-2-10.</span> Second wording."),
    }
    requests = []

    def respond(request):
        url = str(request.url)
        requests.append(url)
        return httpx.Response(200, content=bodies[url])

    a = Acquirer(store, delay=0)
    a.client.close()
    a.client = httpx.Client(transport=httpx.MockTransport(respond))
    a._pause = lambda *args, **kwargs: None
    try:
        first = sync_south_carolina(store, a, 1, None)
        saved = [tuple(r) for r in store.db.execute("SELECT * FROM versions")]
        second = sync_south_carolina(store, a, 1, None)
        assert first["accepted_this_run"] == second["accepted_this_run"] == 1
        assert all(
            row in [tuple(r) for r in store.db.execute("SELECT * FROM versions")] for row in saved
        )
        assert len(requests) == len(bodies)
    finally:
        a.close()
