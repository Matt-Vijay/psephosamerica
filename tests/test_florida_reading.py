"""Printed statutory identities are not invented links or effective-law dates."""

from conftest import retain

from psephos.retrieve import Reader
from psephos.store import Provision, Reference


def test_exact_printed_florida_citation_edition_cutoff_and_footnotes(store):
    source = retain(store, b"official fixture with notes")
    url = "https://www.flsenate.gov/Laws/Statutes/2026/Chapter83/All"
    store.ingest(
        collection="test",
        document="fl:chapter/83",
        title="Chapter 83",
        url=url,
        acquisition=source.id,
        snapshot_basis="Annual edition only",
        parser="fixture",
        provisions=[
            Provision(
                "fl:stat/83.43",
                "Fla. Stat. § 83.43 (2026)",
                "Definitions",
                "Definitions; future-effective alternatives remain in the source note.",
                "",
                url,
                metadata={"edition_year": 2026},
                references=(Reference(url + "#1", "source_link", "Note 1", "footnote marker"),),
            )
        ],
    )
    reader = Reader(store)
    found = reader.read("Fla. Stat. §83.43 (2026)")
    assert found["found"] and found["key"] == "fl:stat/83.43" and found["url"] == url
    assert reader.read("Fla. Stat. 83.43")["id"] == found["id"]
    assert not reader.read("Fla. Stat. §83.43 (2025)")["found"]
    assert not reader.read("Fla. Stat. §83.43(1)")["found"]
    assert not reader.read("Fla. Stat. §83.43", as_of="2026-01-01")["found"]
    assert not reader.read("Fla. Stat. §83.43", observation_cutoff="2025-12-31T00:00:00Z")["found"]
    assert reader.references(found["id"])["references"][0]["acquired_targets"] == []


def test_duplicate_florida_occurrences_require_explicit_identity(store):
    source = retain(store, b"two publisher occurrences")
    store.ingest(
        collection="test",
        document="fl:chapter/83",
        title="Chapter 83",
        url=source.url,
        acquisition=source.id,
        snapshot_basis="Unknown exact snapshot",
        parser="fixture",
        provisions=[
            Provision(
                "fl:stat/83.43/occurrence/" + str(n),
                "Fla. Stat. § 83.43 (2026)",
                "Definitions",
                str(n),
                "",
                source.url,
            )
            for n in (1, 2)
        ],
    )
    reader = Reader(store)
    for query in ("fl:stat/83.43", "Fla. Stat. §83.43", "Fla. Stat. §83.43 (2026)"):
        result = reader.read(query)
        assert not result["found"] and len(result["matches"]) == 2
    assert reader.read("fl:stat/83.43/occurrence/2")["text"] == "2"
