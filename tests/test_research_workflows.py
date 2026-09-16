from dataclasses import replace

import pytest
from conftest import retain

from psephos.retrieve import Reader
from psephos.store import Provision, Reference


def add_document(store, document, units, *, collection="test", day="2025-01-01"):
    source = retain(store, repr(units).encode())
    if collection != "test":
        store.collection(
            collection,
            ("us", "United States", "federal", None),
            name="Fixture",
            authority="Fixture",
            kind="code",
            homepage=source.url,
            source_status="Fixture",
            access="Offline",
        )
    return store.ingest(
        collection=collection,
        document=document,
        title="Fixture",
        url=source.url,
        acquisition=source.id,
        snapshot_date=day,
        snapshot_basis="Fixture date, not legal effect",
        parser="fixture",
        provisions=units,
    )


@pytest.mark.parametrize(
    "url,key,collection",
    [
        (
            "https://law.lis.virginia.gov/admincode/title18/agency50/chapter22/section61/",
            "va-vac:18VAC50-22-61",
            "va-administrative-code",
        ),
        ("https://law.lis.virginia.gov/vacode/54.1-1100/", "va-code:section:54.1-1100", "va-code"),
    ],
)
def test_virginia_reference_requires_retained_key_url_collection_and_cutoffs(
    store, url, key, collection
):
    urls = [
        url,
        url + "?other=1",
        url + "#unverified",
        url.replace("law.lis.virginia.gov", "example.gov"),
    ]
    refs = tuple(Reference(u, "publisher_link", "18VAC50-22-61", f'<a href="{u}"/>') for u in urls)
    source = Provision("source", "Source", "", "Follow prerequisites", "", url, references=refs)
    add_document(store, "source", [source])
    target = Provision(key, key, "", "Table", "", url)
    add_document(store, "wrong-collection", [target])
    reader = Reader(store)
    source_id = reader.read("source")["id"]
    assert not any(r["acquired_targets"] for r in reader.references(source_id)["references"])
    add_document(store, "url-mismatch", [replace(target, url=url + "wrong")], collection=collection)
    add_document(store, "vac", [target], collection=collection, day="2025-02-01")
    result = {r["target"]: r for r in reader.references(source_id)["references"]}
    assert result[url]["resolution_status"] == "resolved"
    assert result[url]["acquired_targets"][0]["key"] == target.key
    assert result[url]["evidence"] == refs[0].evidence
    assert all(not result[u]["acquired_targets"] for u in urls[1:])
    assert not any(
        r["acquired_targets"]
        for r in reader.references(source_id, as_of="2025-01-31")["references"]
    )
    assert not reader.references(source_id, observation_cutoff="2025-12-31T23:59:59Z")["references"]
    add_document(store, "duplicate-vac", [target], collection=collection)
    ambiguous = next(r for r in reader.references(source_id)["references"] if r["target"] == url)
    assert ambiguous["resolution_status"] == "ambiguous_acquired_target"
    assert ambiguous["acquired_targets"] == []


def test_uslm_subsection_navigation_verifies_identifier_and_preserves_section_scope(store):
    identifier = "/us/usc/t42/s3604/c"
    ref = Reference(identifier, "publisher_citation_identifier", "3604(c)", "<ref/>")
    source = Provision("source", "Source", "", "See advertisement rule", "", "", references=(ref,))
    add_document(store, "source", [source])
    markup = (
        '<section xmlns="http://xml.house.gov/schemas/uslm/1.0" identifier="/us/usc/t42/s3604">'
        f'<subsection identifier="{identifier}"><content>Advertisement rule</content></subsection>'
        "</section>"
    )
    target = Provision("usc:/us/usc/t42/s3604", "42 USC 3604", "", "Advertisement rule", markup, "")
    add_document(store, "usc", [target], collection="uscode", day="2025-02-01")
    reader = Reader(store)
    source_id = reader.read("source")["id"]
    result = reader.references(source_id)["references"][0]
    assert result["target"] == identifier and result["evidence"] == ref.evidence
    assert result["resolution_status"] == "resolved_within_section"
    found = result["acquired_targets"][0]
    assert found["matched_source_identifier"] == identifier
    assert found["navigation_scope"] == "enclosing_section"
    assert reader.read(found["id"])["key"] == target.key
    assert not reader.references(source_id, as_of="2025-01-31")["references"][0]["acquired_targets"]
    add_document(
        store,
        "usc",
        [replace(target, markup=markup.replace(identifier, identifier + "x"))],
        collection="uscode",
        day="2025-03-01",
    )
    assert not reader.references(source_id)["references"][0]["acquired_targets"]
    assert reader.references(source_id, as_of="2025-02-01")["references"][0]["acquired_targets"]


@pytest.mark.parametrize(
    "body",
    [
        '<ref href="/us/usc/t42/s3604/c">3604(c)</ref>',
        '<subsection identifier="/us/usc/t42/s3604/c"/>' * 2,
        "<broken>",
    ],
)
def test_uslm_missing_duplicate_or_malformed_subsection_stays_unresolved(store, body):
    ref = Reference("/us/usc/t42/s3604/c", "publisher_citation_identifier", "3604(c)", "<ref/>")
    add_document(
        store,
        "source",
        [
            Provision("source", "Source", "", "Follow reference", "", "", references=(ref,)),
        ],
    )
    markup = (
        '<section xmlns="http://xml.house.gov/schemas/uslm/1.0" identifier="/us/usc/t42/s3604">'
        + body
        + "</section>"
    )
    add_document(
        store,
        "usc",
        [
            Provision("usc:/us/usc/t42/s3604", "42 USC 3604", "", "Section text", markup, ""),
        ],
        collection="uscode",
    )
    reader = Reader(store)
    result = reader.references(reader.read("source")["id"])["references"][0]
    assert result["resolution_status"] == "subsection_not_verified_at_cutoffs"
    assert result["acquired_targets"] == []
