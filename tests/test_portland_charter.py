"""Exact native Charter membership, structure, clocks and source boundaries."""

from copy import deepcopy

import httpx
import pytest

from psephos.acquire import Acquirer, AcquisitionError
from psephos.campaign import save
from psephos.portland_charter import (
    BASE,
    CAP,
    COLLECTION,
    PREFLIGHT_CAP,
    CharterAcquirer,
    chapter_context_signature,
    chapter_list,
    chapter_units,
    members,
    own_content,
    source_key,
    sync_portland_charter,
)
from psephos.portland_code import content_signature


def document(content):
    return f"<html><body><main>{content}</main><footer><a href='/help/about/privacy'>Policy</a></footer></body></html>".encode()


def view(name, content):
    return f'<div class="views-element-container"><div class="view-display-id-{name}">{content}</div></div>'


def fixture(number="1"):
    article_url = BASE + f"/charter/{number}/1"
    fields = '<div class="field--name-field-prefix-note"><p>Amended in 2022, effective January 1, 2025.</p></div><div class="field--name-field-section-body"><ol start="4"><li>Source obligation.</li></ol><table><tr><td colspan="2">A</td></tr></table></div>'
    label = f"Section {number}-101 A section."
    section_url = f"/charter/{number}/1/101"
    row = f'<div class="views-row"><article><h2><a href="{section_url}">{label}</a></h2>{fields}</article></div>'
    article_data = document(view("entity_view_1", "") + view("entity_view_2", row))
    chapter = {
        "number": number,
        "url": BASE + f"/charter/{number}",
        "label": f"Chapter {number} Government",
    }
    article = f'<div class="views-row"><div><span><a href="/charter/{number}/1">Article 1 Powers</a></span></div></div>'
    chapter_data = document(
        f'<a href="/charter/{number}/all-articles">Print</a>'
        + '<div class="field--name-field-prefix-note">Chapter history, effective January 1, 2025.</div>'
        + view("entity_view_1", article)
        + view("entity_view_2", "")
    )
    chapter["context_signature"] = chapter_context_signature(chapter_data)
    membership = {
        chapter["url"]: members(chapter_data, chapter["url"]),
        article_url: members(article_data, article_url),
    }
    signatures = {chapter["url"]: own_content(chapter_data), article_url: own_content(article_data)}
    nested = f'<div class="node--embedded"><h2><a href="/charter/{number}/1">Article 1 Powers</a></h2><div class="views-element-container"><div class="node--embedded"><h3><a href="{section_url}">{label}</a></h3>{fields}</div></div></div>'
    export = document(
        f'<h1 class="page-title">{chapter["label"]}</h1>'
        + view(
            "page_chapter_all",
            '<div class="view-header">Chapter history, effective January 1, 2025.</div>' + nested,
        )
    )
    return chapter, membership, signatures, export, chapter_data, article_data


def test_charter_native_identity_hierarchy_and_full_body_fidelity():
    chapter, membership, signatures, export, _, _ = fixture()
    units = chapter_units(export, chapter, membership, signatures)
    assert [u.key for u in units] == ["pdx-charter:1", "pdx-charter:1/1", "pdx-charter:1/1/101"]
    assert [u.unit_kind for u in units] == ["chapter_context", "article_context", "section"]
    assert units[-1].parent_key == units[1].key
    assert units[-1].citation == "Portland Charter Section 1-101"
    assert "effective January 1, 2025" in units[-1].text
    assert 'start="4"' in units[-1].markup and 'colspan="2"' in units[-1].markup
    assert "Source obligation" not in units[1].text
    with pytest.raises(ValueError, match="content differs"):
        chapter_units(
            export.replace(b"Source obligation", b"Invented obligation"),
            chapter,
            membership,
            signatures,
        )
    corrupt = deepcopy(membership)
    corrupt[chapter["url"]].append(corrupt[chapter["url"]][0])
    with pytest.raises(ValueError, match="multiple parents"):
        chapter_units(export, chapter, corrupt, signatures)
    with pytest.raises((ValueError, AcquisitionError), match="native Portland Charter"):
        source_key(BASE + "/code/1/1")


def test_member_order_is_not_replaced_by_a_set_and_container_content_is_checked():
    chapter, membership, signatures, export, _, _ = fixture()
    article = BASE + "/charter/1/1"
    old = membership[article][0]
    membership[article].append(
        {**old, "url": BASE + "/charter/1/1/102", "label": "Section 1-102 Second."}
    )
    from lxml import html

    section = html.fromstring(export).xpath('//div[@class="node--embedded"]')[1]
    second = deepcopy(section)
    second.xpath("./h3/a")[0].set("href", "/charter/1/1/102")
    second.xpath("./h3/a")[0].text = "Section 1-102 Second."
    section.addprevious(second)
    reordered = html.tostring(section.getroottree().getroot())
    with pytest.raises(ValueError, match="ordered native"):
        chapter_units(reordered, chapter, membership, signatures)
    assert content_signature(second) == old["content_sha256"]
    chapter["context_signature"] = "Different chapter-level body"
    with pytest.raises(ValueError, match="chapter context"):
        chapter_units(export, chapter, membership, signatures)


def test_root_inventory_has_no_guessed_chapters_or_external_routes():
    rows = "".join(
        f'<div class="views-row"><a href="/charter/{n}">Chapter {n} Government</a></div>'
        for n in ("1", "15")
    )
    body = document(view("page_1", '<div class="view-content">' + rows + "</div>"))
    assert [c["number"] for c in chapter_list(body)] == ["1", "15"]
    with pytest.raises(ValueError, match="disagreement"):
        chapter_list(body.replace(b"Chapter 15", b"Chapter 14"))
    with pytest.raises(AcquisitionError, match="official Portland"):
        chapter_list(
            body.replace(b'href="/charter/15"', b'href="https://example.invalid/charter/15"')
        )


def test_sync_limit_resume_unknown_dates_and_no_duplicate_versions(store, monkeypatch):
    payloads = {
        "/robots.txt": b"User-agent: *\nAllow: /",
        "/help/about/privacy": b"Publisher terms.",
    }
    rows = []
    for n in ("1", "2"):
        chapter, _, _, export, chapter_data, article_data = fixture(n)
        rows.append(f'<div class="views-row"><a href="/charter/{n}">{chapter["label"]}</a></div>')
        payloads[f"/charter/{n}"] = chapter_data
        payloads[f"/charter/{n}/1"] = article_data
        payloads[f"/charter/{n}/all-articles"] = export
    payloads["/charter"] = document(
        view(
            "page_1",
            '<div class="view-header">Council Clerk description.</div><div class="view-content">'
            + "".join(rows)
            + "</div>",
        )
    )
    requests = []

    def handler(request):
        requests.append(str(request.url))
        route = str(request.url).removeprefix(BASE)
        return httpx.Response(200, stream=httpx.ByteStream(payloads[route]))

    monkeypatch.setattr(Acquirer, "_pause", lambda *args: None)
    a = CharterAcquirer(store)
    a.client.close()
    a.client = httpx.Client(
        transport=httpx.MockTransport(handler), event_hooks={"response": [a._bounded_response]}
    )
    try:
        with pytest.raises(ValueError, match="historical"):
            sync_portland_charter(store, a, None, "2020-01-01")
        assert not requests
        sync_portland_charter(store, a, 1, None)
        assert dict(
            store.db.execute(
                "SELECT status,count(*) FROM inventories WHERE collection_id=? GROUP BY status",
                (COLLECTION,),
            )
        ) == {"indexed": 1, "pending": 1}
        sync_portland_charter(store, a, None, None)
        before = [dict(row) for row in store.db.execute("SELECT * FROM versions ORDER BY id")]
        calls, charged = len(requests), a.downloaded
        sync_portland_charter(store, a, None, None)
        assert before == [
            dict(row) for row in store.db.execute("SELECT * FROM versions ORDER BY id")
        ]
        assert len(before) == 2 and len(requests) == calls and a.downloaded == charged
        assert all(
            all(
                row[k] is None
                for k in (
                    "snapshot_date",
                    "published_on",
                    "effective_on",
                    "amended_on",
                    "repealed_on",
                )
            )
            for row in before
        )
        # A failed refresh may retain new bytes before indexing them. Default
        # resume must consume those bytes, not hide them behind an older version.
        payloads["/charter/1/all-articles"] = payloads["/charter/1/all-articles"].replace(
            b"Source obligation", b"Updated obligation"
        )
        payloads["/charter/1/1"] = payloads["/charter/1/1"].replace(
            b"Source obligation", b"Updated obligation"
        )

        def interrupted(*args):
            raise ValueError("Fixture parser interruption after bytes retained")

        monkeypatch.setattr("psephos.portland_charter.chapter_units", interrupted)
        a.refresh = True
        with pytest.raises(ValueError, match="Fixture parser interruption"):
            sync_portland_charter(store, a, 1, None)
        calls = len(requests)
        monkeypatch.setattr("psephos.portland_charter.chapter_units", chapter_units)
        a.refresh = False
        sync_portland_charter(store, a, None, None)
        assert len(requests) == calls
        assert store.db.execute("SELECT count(*) FROM versions").fetchone()[0] == 3
        for original in before:
            assert (
                dict(
                    store.db.execute(
                        "SELECT * FROM versions WHERE id=?", (original["id"],)
                    ).fetchone()
                )
                == original
            )
    finally:
        a.close()


def test_lifetime_preflight_budget_and_redirect_boundary(store, monkeypatch, tmp_path):
    directory = tmp_path / "campaign"
    directory.mkdir()
    save(
        directory / "budget.json",
        {"cap_bytes": CAP, "consumed_bytes": 2000000, "last_request_unix": 0},
    )
    a = CharterAcquirer(store, directory, preflight=True)
    assert a.max_bytes == PREFLIGHT_CAP and a.downloaded == 2000000
    a.close()
    a = CharterAcquirer(store, directory)
    assert a.max_bytes == CAP and a.downloaded == 2000000
    requests = []
    monkeypatch.setattr(Acquirer, "_pause", lambda *args: None)

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://other.invalid/charter"})

    a.client.close()
    a.client = httpx.Client(
        transport=httpx.MockTransport(handler), event_hooks={"response": [a._bounded_response]}
    )
    try:
        with pytest.raises(AcquisitionError, match="official Portland"):
            a.fetch(BASE + "/charter", check_robots=False)
        assert requests == [BASE + "/charter"]
        assert a.downloaded == 2000000 + 65536
    finally:
        a.close()
