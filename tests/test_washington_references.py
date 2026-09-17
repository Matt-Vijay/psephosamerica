"""Washington citation navigation requires exact retained publisher identities."""

from dataclasses import replace

import pytest
from conftest import retain

from psephos.retrieve import Reader
from psephos.store import Provision, Reference


def add_target(
    store,
    key,
    url,
    *,
    collection=None,
    document=None,
    day="2025-02-01",
    observed="2026-02-01T00:00:00Z",
    wording="Acquired target wording",
    retained_url=None,
):
    collection = collection or key.split(":", 1)[0]
    store.collection(
        collection,
        ("us-wa", "Washington", "state", "us"),
        name="Washington fixture",
        authority="Fixture publisher",
        kind="fixture",
        homepage="https://app.leg.wa.gov/",
        source_status="Generated fixture",
        access="Offline fixture",
    )
    receipt = retain(store, (key + wording + observed).encode(), observed)
    if collection == "wa-wsr":
        with store.db:
            store.db.execute(
                "UPDATE acquisitions SET url=?,final_url=? WHERE id=?",
                (retained_url or url, retained_url or url, receipt.id),
            )
    version, _, _ = store.ingest(
        collection=collection,
        document=document or key + "/document",
        title="Target fixture",
        url=url,
        acquisition=receipt.id,
        snapshot_date=day,
        snapshot_basis="Fixture snapshot, not an inferred legal effective date",
        parser="fixture",
        provisions=[Provision(key, key, "Heading", wording, "", url)],
    )
    return store.db.execute("SELECT id FROM provisions WHERE version_id=?", (version,)).fetchone()[
        0
    ]


def add_source(store, targets, *, refs=None):
    references = refs or tuple(
        Reference(target, "publisher_link", f"Citation {i}", f'<a href="{target}">Citation {i}</a>')
        for i, target in enumerate(targets)
    )
    receipt = retain(store, repr(references).encode(), "2026-01-01T00:00:00Z")
    version, _, _ = store.ingest(
        collection="test",
        document="citation-source",
        title="Citation source fixture",
        url=receipt.url,
        acquisition=receipt.id,
        snapshot_date="2025-01-01",
        snapshot_basis="Fixture date",
        parser="fixture",
        provisions=[
            Provision(
                "fixture:source",
                "Fixture source",
                "",
                "Retained citation evidence",
                "",
                receipt.url,
                references=references,
            )
        ],
    )
    return store.db.execute("SELECT id FROM provisions WHERE version_id=?", (version,)).fetchone()[
        0
    ]


def test_rcw_and_wac_native_links_resolve_without_rewriting_evidence(store):
    rcw = "https://app.leg.wa.gov/RCW/default.aspx?cite=36.70A.040"
    wac = "https://app.leg.wa.gov/WAC/default.aspx?cite=197-11-060"
    expected = {
        rcw: add_target(store, "wa-rcw:36.70A.040", rcw),
        wac: add_target(store, "wa-wac:197-11-060", wac),
    }
    targets = [
        rcw,
        rcw.replace("https:", "http:"),
        wac,
        wac.replace("?cite=", "?Cite="),
        wac.replace("/WAC/", "/wac/"),
    ]
    source = add_source(store, targets)
    rows = Reader(store).references(source)["references"]
    assert len(rows) == 5
    for row in rows:
        canonical = (
            row["target"]
            .replace("http:", "https:")
            .replace("?Cite=", "?cite=")
            .replace("/wac/", "/WAC/")
        )
        assert row["acquired_targets"][0]["id"] == expected[canonical]
        assert row["acquired_targets"][0]["url"] == canonical
        assert row["resolution_status"] == "resolved"
        assert row["resolution_basis"] == "exact_acquired_publisher_url"
        index = targets.index(row["target"])
        assert row["evidence"] == f'<a href="{targets[index]}">Citation {index}</a>'
        saved = store.db.execute(
            "SELECT target,relation,label,evidence FROM legal_references WHERE provision_id=? AND target=?",
            (source, row["target"]),
        ).fetchone()
        assert tuple(saved) == tuple(
            row[field] for field in ("target", "relation", "label", "evidence")
        )


def test_wa_links_reject_lookalikes_escapes_and_ambiguous_query_parameters(store):
    canonical = "https://app.leg.wa.gov/RCW/default.aspx?cite=36.70A.040"
    add_target(store, "wa-rcw:36.70A.040", canonical)
    targets = [
        canonical + "&cite=36.70A.020",
        canonical + "&Cite=36.70A.040",
        canonical + "&pdf=true",
        canonical + "&full=true",
        canonical + "#36.70A.040",
        canonical.replace("app.leg.wa.gov", "app.leg.wa.gov.evil.test"),
        canonical.replace("app.leg.wa.gov", "app.leg.wa.gov@evil.test"),
        canonical.replace("app.leg.wa.gov", "app.leg.wa.gov:443"),
        canonical.replace("36.70A.040", "36%2E70A%2E040"),
        canonical.replace("36.70A.040", "36.70A"),
        canonical.replace("36.70A.040", "36.70a.040"),
        canonical.replace("?cite=", "?other="),
        " " + canonical,
        canonical + "\n",
        canonical.replace("https:", "javascript:"),
        "https://[malformed/?cite=36.70A.040",
    ]
    source = add_source(store, targets)
    rows = Reader(store).references(source)["references"]
    assert len(rows) == len(targets)
    assert all(not row["acquired_targets"] for row in rows)


@pytest.mark.parametrize(
    "family,chapter,other,citation",
    [
        ("WAC", "197-11", "197-10", "197-11-330"),
        ("RCW", "36.70A", "36.70", "36.70A.040"),
    ],
)
def test_wa_full_chapter_fragment_requires_matching_native_chapter(
    store, family, chapter, other, citation
):
    canonical = f"https://app.leg.wa.gov/{family}/default.aspx?cite={citation}"
    target = add_target(store, f"wa-{family.lower()}:{citation}", canonical)
    native = f"https://app.leg.wa.gov/{family}/default.aspx?cite={chapter}&full=true#{citation}"
    targets = [
        native,
        native.replace(f"cite={chapter}&", f"cite={other}&"),
        native.replace("&full=true", ""),
        native.replace("&full=true", "&full=false"),
        native.replace("&full=true", "&full=true&pdf=true"),
        native.replace(f"cite={chapter}&full=true", f"full=true&cite={chapter}"),
        native.replace(f"#{citation}", f"#{citation}%20"),
        native.replace("app.leg.wa.gov", "example.test"),
    ]
    source = add_source(store, targets)
    rows = {row["target"]: row for row in Reader(store).references(source)["references"]}
    assert rows[native]["acquired_targets"][0]["id"] == target
    assert rows[native]["target"] == native
    assert all(not rows[url]["acquired_targets"] for url in targets[1:])


def test_wa_resolution_requires_exact_key_url_collection_and_unambiguous_target(store):
    url = "https://app.leg.wa.gov/RCW/default.aspx?cite=36.70A.040"
    key = "wa-rcw:36.70A.040"
    add_target(store, key, url.replace("36.70A.040", "36.70A.020"), document="wrong-url")
    add_target(store, "wa-rcw:36.70A.020", url, document="wrong-key")
    add_target(store, key, url, collection="unrelated", document="wrong-collection")
    source = add_source(store, [url])
    reader = Reader(store)
    assert not reader.references(source)["references"][0]["acquired_targets"]
    correct = add_target(store, key, url, document="correct")
    assert reader.references(source)["references"][0]["acquired_targets"][0]["id"] == correct
    add_target(store, key, url, document="duplicate")
    ambiguous = reader.references(source)["references"][0]
    assert ambiguous["resolution_status"] == "ambiguous_acquired_target"
    assert ambiguous["acquired_targets"] == []


def test_wa_reference_resolution_applies_source_and_target_cutoffs(store):
    url = "https://app.leg.wa.gov/WAC/default.aspx?cite=197-11-060"
    key = "wa-wac:197-11-060"
    old = add_target(store, key, url)
    latest = add_target(
        store, key, url, day="2025-03-01", observed="2026-03-01T00:00:00Z", wording="Later wording"
    )
    source = add_source(store, [url])
    reader = Reader(store)
    assert reader.references(source)["references"][0]["acquired_targets"][0]["id"] == latest
    for cutoff in ({"as_of": "2025-02-15"}, {"observation_cutoff": "2026-02-15T00:00:00Z"}):
        row = reader.references(source, **cutoff)["references"][0]
        assert row["acquired_targets"][0]["id"] == old
    for cutoff in ({"as_of": "2025-01-15"}, {"observation_cutoff": "2026-01-15T00:00:00Z"}):
        assert not reader.references(source, **cutoff)["references"][0]["acquired_targets"]
    assert not reader.references(source, as_of="2024-12-31")["references"]
    assert not reader.references(source, observation_cutoff="2025-12-31T00:00:00Z")["references"]
    add_target(
        store,
        "wa-wac:197-11-061",
        url.replace("060", "061"),
        document=key + "/document",
        day="2025-04-01",
        observed="2026-04-01T00:00:00Z",
        wording="Old key no longer present",
    )
    assert not reader.references(source)["references"][0]["acquired_targets"]
    assert (
        reader.references(source, as_of="2025-03-15")["references"][0]["acquired_targets"][0]["id"]
        == latest
    )


def test_wsr_native_link_and_bare_filing_citation_use_exact_retained_file(store):
    key = "wa-wsr:25-13-090"
    url = "https://lawfilesext.leg.wa.gov/law/wsr/2025/13/25-13-090.htm"
    target = add_target(store, key, url)
    native = url.replace("https:", "http:")
    note = Reference(
        key,
        "publisher_filing_citation",
        "WSR 25-13-090",
        "<div>WSR 25-13-090, filed 6/17/25.</div>",
    )
    refs = (
        note,
        Reference(native, "publisher_link", "25-13-090", f'<a href="{native}">25-13-090</a>'),
    )
    source = add_source(store, [], refs=refs)
    rows = Reader(store).references(source)["references"]
    assert len(rows) == 2
    for row in rows:
        assert row["acquired_targets"][0]["id"] == target
        assert row["acquired_targets"][0]["url"] == url
        assert row["resolution_basis"] == "exact_acquired_filing_key_and_retained_publisher_url"
        expected = next(ref for ref in refs if ref.target == row["target"])
        assert row["evidence"] == expected.evidence and row["label"] == expected.label
    assert not Reader(store).references(source, as_of="2025-01-15")["references"][0][
        "acquired_targets"
    ]
    assert not Reader(store).references(source, observation_cutoff="2026-01-15T00:00:00Z")[
        "references"
    ][0]["acquired_targets"]


def test_wsr_refuses_unretained_wrong_host_year_issue_and_malformed_citations(store):
    key = "wa-wsr:25-13-090"
    url = "https://lawfilesext.leg.wa.gov/law/wsr/2025/13/25-13-090.htm"
    add_target(store, key, url, retained_url="https://example.test/unrelated.htm")
    note = Reference(key, "publisher_filing_citation", "WSR 25-13-090", "Original note markup")
    source = add_source(store, [], refs=(note,))
    assert not Reader(store).references(source)["references"][0]["acquired_targets"]
    add_target(store, key, url, document="proper-filing")
    links = [
        url.replace("/2025/13/", "/2026/13/"),
        url.replace("/2025/13/", "/2025/14/"),
        url.replace("lawfilesext.leg.wa.gov", "lawfilesext.leg.wa.gov.evil.test"),
        url + "?cite=25-13-090",
        url + "#25-13-090",
    ]
    refs = tuple(
        Reference(link, "publisher_link", "25-13-090", "Unchanged evidence") for link in links
    )
    refs += (
        replace(note, relation="publisher_link"),
        replace(note, target="wa-wsr:25-13-090&other=26-01-181"),
        replace(note, target="wa-wsr:2025-13-090"),
        replace(note, target="wa-wsr:25-13-090 "),
    )
    source = add_source(store, [], refs=refs)
    assert all(
        not row["acquired_targets"] for row in Reader(store).references(source)["references"]
    )


def test_wsr_bare_key_does_not_guess_url_when_multiple_filings_match(store):
    key = "wa-wsr:25-13-090"
    url = "https://lawfilesext.leg.wa.gov/law/wsr/2025/13/25-13-090.htm"
    add_target(store, key, url, document="filing-a")
    add_target(store, key, url, document="filing-b")
    note = Reference(key, "publisher_filing_citation", "WSR 25-13-090", "Original note markup")
    source = add_source(store, [], refs=(note,))
    row = Reader(store).references(source)["references"][0]
    assert row["acquired_targets"] == []
    assert row["resolution_status"] == "ambiguous_acquired_target"
