"""Native membership, source fidelity and narrow citation resolution."""

from copy import deepcopy

import pytest
from conftest import retain

from psephos.parse import source_medium
from psephos.portland_code import BASE, COLLECTION, members, title_list, title_units
from psephos.retrieve import Reader
from psephos.store import Provision, Reference


def document(body):
    return f"<html><body><main>{body}</main></body></html>".encode()


def fixture():
    fields = '<div class="field--name-field-prefix-note">Amended by <a href="/ordinance">ordinance</a>.</div><div class="field--name-field-section-body"><p>Except as provided, retain this text.</p><table><tr><td>A</td><td>B</td></tr></table></div>'
    label = "1.05.010 A section."
    chapter = BASE + "/code/1/05"
    native = document(
        '<div class="view-display-id-eva_code_chapters"></div>'
        '<div class="view-display-id-eva_code_sections"><div class="views-row">'
        f'<article><h2><a href="/code/1/5/010">{label}</a></h2>{fields}</article></div></div>'
    )
    title = {"url": BASE + "/code/1", "number": "1", "label": "Title 1 General"}
    membership = {
        title["url"]: [{"url": chapter, "label": "Chapter 1.05 General", "kind": "container"}],
        chapter: members(native, chapter),
    }
    export = document(
        '<h1 class="page-title">Title 1 General</h1><div class="view-display-id-page_title_all">'
        '<div class="node--embedded"><h2><a href="/code/1/05">Chapter 1.05 General</a></h2>'
        '<div class="views-element-container"><div class="node--embedded">'
        f'<h3><a href="/code/1/5/010">{label}</a></h3>{fields}</div></div></div></div>'
    )
    return title, membership, export


def test_native_membership_body_links_irregular_slug_and_fidelity():
    title, membership, export = fixture()
    units = title_units(export, title, membership)
    assert [p.unit_kind for p in units] == ["title_context", "chapter_context", "section"]
    section = units[-1]
    assert section.key == "pdx-code:1/5/010" and section.parent_key == "pdx-code:1/05"
    assert section.metadata["tables"] == 1 and "Except as provided" in section.text
    assert "Except as provided" not in units[1].text
    assert any(r.target == BASE + "/ordinance" for r in section.references)
    with pytest.raises(ValueError, match="body differs"):
        title_units(export.replace(b"retain this text", b"omit this text"), title, membership)
    missing = deepcopy(membership)
    missing[BASE + "/code/1/05"] = []
    with pytest.raises(ValueError, match="member label mismatch"):
        title_units(export, title, missing)
    duplicate = deepcopy(membership)
    duplicate[title["url"]] *= 2
    with pytest.raises(ValueError, match="multiple parents"):
        title_units(export, title, duplicate)


def test_title_inventory_never_invents_missing_title_numbers():
    row = '<div class="views-row"><a href="/code/9">Title 9 Test</a></div>'
    assert (
        title_list(
            document(
                '<div class="view-display-id-page_city_code"><div class="view-content">'
                + row
                + "</div></div>"
            )
        )[0]["number"]
        == "9"
    )
    with pytest.raises(ValueError, match="duplicate"):
        title_list(
            document(
                '<div class="view-display-id-page_city_code"><div class="view-content">'
                + row * 2
                + "</div></div>"
            )
        )


def test_portland_references_use_retained_identity_not_url_arithmetic(store):
    store.collection(
        COLLECTION,
        ("us-or-portland", "Portland", "municipality", "us"),
        name="Code",
        authority="Fixture",
        kind="municipal_code",
        homepage=BASE + "/code",
        source_status="Fixture",
        access="Fixture",
    )
    title, membership, export = fixture()
    units = title_units(export, title, membership)
    source = retain(store, export)
    refs = tuple(
        Reference(target, "publisher_link", "Section", "source")
        for target in (
            "PCC 1.05.010",
            BASE + "/code/1/5/010",
            BASE + "/code/33/100s/110",
        )
    )
    units.append(Provision("links", "Links", "Links", "Links", "", source.url, references=refs))
    store.ingest(
        collection=COLLECTION,
        document="title",
        title="Title",
        url=source.url,
        acquisition=source.id,
        snapshot_basis="Unknown",
        parser="fixture",
        provisions=units,
    )
    reader = Reader(store)
    assert reader.read("PCC 1.05.010")["key"] == "pdx-code:1/5/010"
    resolved = reader.references(reader.read("links")["id"])["references"]
    assert sum(bool(r["acquired_targets"]) for r in resolved) == 2
    zoning = next(r for r in resolved if "/33/" in r["target"])
    assert (
        not zoning["acquired_targets"] and zoning["navigation"]["collection"] == "portland-zoning"
    )
    assert not reader.read("PCC 1.05.010", as_of="2099-01-01")["found"]
    assert not reader.read("PCC 1.05.010", observation_cutoff="2025-01-01T00:00:00Z")["found"]


@pytest.mark.parametrize("field", ["collection", "jurisdiction"])
@pytest.mark.parametrize("value", ["", " ", "\t\n"])
def test_blank_search_scope_cannot_broaden_nationally(store, field, value):
    with pytest.raises(ValueError, match="exact identifier"):
        Reader(store).search("word", **{field: value})
    assert Reader(store).search("word", **{field: None})["matches"] == []


def test_failed_http_200_image_is_not_a_retained_media_receipt(store):
    source = retain(store, b"media fixture")
    image_url = BASE + "/sites/default/files/figure.png"
    store.ingest(
        collection="test",
        document="media",
        title="Media",
        url=source.url,
        acquisition=source.id,
        snapshot_basis="Unknown",
        parser="fixture",
        provisions=[
            Provision(
                "media",
                "Media",
                "Media",
                "Image",
                f'<section><img src="{image_url}"/></section>',
                source.url,
                metadata={
                    "media": [
                        {
                            **source_medium(image_url, source.url),
                            "original_url": image_url + "?full=1",
                        }
                    ]
                },
            )
        ],
    )
    with store.db:
        store.db.execute(
            "INSERT INTO acquisitions(url,final_url,observed_at,status,headers,error) VALUES (?, ?, '2026-01-01T00:00:00Z',200,'{}','incomplete')",
            (image_url, image_url),
        )
    result = Reader(store).read("media")
    assert result["media_count"] == 1
    assert result["media"][0]["acquired_receipt"] is None
