"""Refresh contracts with in-memory source/store doubles; no HTTP or database access."""

import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from psephos import dc, municipal
from psephos.store import TEXT_PROJECTION, digest

PREFIX = "law-xml-codified-fixture/"
TITLES = ["us/dc/council/code/titles/7A/index.xml", "us/dc/council/code/titles/3/index.xml"]
LAWS = ["us/dc/council/periods/25/laws/25-42.xml", "us/dc/council/periods/24/laws/24-9.xml"]
DC_ITEMS = {"dc-code": TITLES, "dc-laws": LAWS}
GUIDE_ITEMS = {"portland-zoning-guides": ["base-zones", "overlay-zones"]}


class MemoryStore:
    def __init__(self):
        self.db = self
        self.artifacts = {}
        self.receipts = {}
        self.collections = {}
        self.inventories = {}
        self.versions = []

    def collection(self, collection, *args, **metadata):
        self.collections[collection] = metadata

    def inventory(self, collection, item, url, status, error=None):
        self.inventories[collection, item] = {"status": status, "error": error, "url": url}

    def artifact(self, sha):
        return self.artifacts[sha]

    def object_path(self, sha):
        return io.BytesIO(self.artifact(sha))

    def execute(self, query, parameters):
        assert query == (
            "SELECT v.member FROM versions v JOIN documents d ON d.id=v.document_id "
            "WHERE d.collection_id=? AND v.artifact_sha=? AND v.snapshot_date=? "
            "AND v.parser=?"
        )
        collection, sha, snapshot, parser = parameters
        return [
            row
            for row in self.versions
            if (row["collection"], row["artifact_sha"], row["snapshot_date"], row["parser"])
            == (collection, sha, snapshot, parser)
        ]

    def ingest(self, **values):
        row = {
            "member": None,
            "snapshot_date": None,
            **values,
            "artifact_sha": self.receipts[values["acquisition"]].sha256,
            "parser": values["parser"] + "/" + TEXT_PROJECTION,
        }
        identity = ("document", "artifact_sha", "member", "snapshot_date", "parser")
        for old in self.versions:
            if all(old[key] == row[key] for key in identity):
                return "retained", len(old["provisions"]), False
        # Match atomic ingestion: a generator failure cannot accept a partial projection.
        row["provisions"] = list(row["provisions"])
        self.versions.append(row)
        return "accepted", len(row["provisions"]), True


class OfflineAcquirer:
    refresh = True

    def __init__(self, store, payloads):
        self.store = store
        self.payloads = payloads
        self.calls = []

    def fetch(self, url, **options):
        self.calls.append((url, options))
        data = self.payloads[url]
        receipt = SimpleNamespace(
            id=len(self.store.receipts) + 1,
            url=url,
            sha256=digest(data),
            observed_at="2026-09-16T00:00:00Z",
            size=len(data),
        )
        self.store.artifacts[receipt.sha256] = data
        self.store.receipts[receipt.id] = receipt
        return receipt

    def json(self, url):
        receipt = self.fetch(url)
        return receipt, json.loads(self.store.artifact(receipt.sha256))


def archive_bytes(members):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        for member, content in members.items():
            archive.writestr(PREFIX + member, content)
    return data.getvalue()


@pytest.fixture
def dc_source(monkeypatch):
    members = {
        "index.xml": b"<library><codified-date>2026-09-15</codified-date></library>",
        "us/dc/council/code/index.xml": (
            b'<code xmlns:xi="http://www.w3.org/2001/XInclude">'
            b'<recency through="2026-09-01"/>'
            b'<xi:include href="titles/7A/index.xml"/>'
            b'<xi:include href="titles/3/index.xml"/></code>'
        ),
        "us/dc/council/code/titles/99/index.xml": b"<container><num>99</num></container>",
        "us/dc/council/periods/25/laws/notes.xml": b"<notes/>",
    }
    for number, member in zip(("7A", "3"), TITLES, strict=True):
        members[member] = (
            '<container xmlns:xi="http://www.w3.org/2001/XInclude">'
            f"<num>{number}</num><heading>Title {number}</heading>"
            '<xi:include href="section.xml"/></container>'
        ).encode()
        members[member.replace("index.xml", "section.xml")] = (
            f"<section><num>{number}-1</num><heading>Rule</heading><p>Source body.</p></section>"
        ).encode()
    for number, member in zip(("25-42", "24-9"), LAWS, strict=True):
        members[member] = (
            f'<law id="D.C. Law {number}"><meta><stub/></meta>'
            f"<num>{number}</num><heading>Act</heading>"
            "<search-text>UNVERIFIED OCR</search-text></law>"
        ).encode()
    store = MemoryStore()
    url = "https://codeload.github.com/dccouncil/law-xml-codified/zip/fixture"
    acquirer = OfflineAcquirer(
        store,
        {
            "https://code.dccouncil.gov/": b"Publisher notice",
            dc.REPO: b'{"default_branch":"published/code"}',
            dc.REPO + "/commits/published%2Fcode": b'{"sha":"fixture"}',
            url: archive_bytes(members),
        },
    )
    monkeypatch.setattr(dc, "checked_zip", zipfile.ZipFile)
    return store, acquirer, members, url


@pytest.fixture
def guide_source():
    store = MemoryStore()
    acquirer = OfflineAcquirer(
        store,
        {
            municipal.PORTLAND_GUIDES + slug: (
                f"<html><main><h1>{slug.replace('-', ' ').title()}</h1>"
                "<article><p>Publisher guidance.</p></article></main></html>"
            ).encode()
            for slug in GUIDE_ITEMS["portland-zoning-guides"]
        },
    )
    return store, acquirer


def test_dc_returns_only_native_current_members_and_preserves_stub_labels(dc_source):
    store, acquirer, _, url = dc_source
    store.inventory("dc-code", "old-title", "old-url", "failed", "Historical failure")
    store.inventory("dc-laws", "old-law", "old-url", "indexed")

    assert dc.sync_dc(store, acquirer, None, None) == {"inventory_items": DC_ITEMS}
    assert set(store.collections) == set(DC_ITEMS)
    assert (url, {"immutable": True}) in acquirer.calls
    for collection, items in DC_ITEMS.items():
        assert all(store.inventories[collection, item]["status"] == "indexed" for item in items)
    units = [
        p for row in store.versions if row["collection"] == "dc-laws" for p in row["provisions"]
    ]
    assert len(units) == len(LAWS)
    for unit in units:
        assert unit.unit_kind == "law_metadata"
        assert unit.metadata["text_quality"] == "metadata_only"
        assert "UNVERIFIED OCR" not in unit.text
        assert "UNVERIFIED OCR" in unit.markup


def test_dc_limit_returns_unprocessed_current_members_as_pending(dc_source):
    store, acquirer, _, _ = dc_source
    assert dc.sync_dc(store, acquirer, 1, None) == {"inventory_items": DC_ITEMS}
    for collection, items in DC_ITEMS.items():
        assert store.inventories[collection, items[0]]["status"] == "indexed"
        assert store.inventories[collection, items[1]]["status"] == "pending"


def test_dc_partial_parse_failure_stays_in_returned_inventory(dc_source, monkeypatch, capsys):
    store, acquirer, _, _ = dc_source
    original = dc.code_units

    def fail_after_one_unit(root, archive):
        yield from original(root, archive)
        if dc.child_text(root, "num") == "7A":
            raise ValueError("Fixture partial parse failure")

    monkeypatch.setattr(dc, "code_units", fail_after_one_unit)
    assert dc.sync_dc(store, acquirer, None, None) == {"inventory_items": DC_ITEMS}
    failed = store.inventories["dc-code", TITLES[0]]
    assert failed["status"] == "failed"
    assert failed["error"] == "Fixture partial parse failure"
    assert "Fixture partial parse failure" in capsys.readouterr().err
    assert store.inventories["dc-code", TITLES[1]]["status"] == "indexed"
    assert all(store.inventories["dc-laws", item]["status"] == "indexed" for item in LAWS)
    assert not any(row["document"] == "dc-code:title-7A" for row in store.versions)
    monkeypatch.setattr(dc, "code_units", original)
    dc.sync_dc(store, acquirer, None, None)
    assert store.inventories["dc-code", TITLES[0]]["status"] == "indexed"


def test_dc_unchanged_archive_reuses_only_accepted_members(dc_source, monkeypatch):
    store, acquirer, members, _ = dc_source
    dc.sync_dc(store, acquirer, None, None)
    versions = list(store.versions)
    original = dc.xml_root

    def inventory_xml_only(data):
        assert data in (members["index.xml"], members["us/dc/council/code/index.xml"])
        return original(data)

    def must_not_parse(*args):
        pytest.fail("An identical accepted member must not be reprojected")

    monkeypatch.setattr(dc, "expanded", must_not_parse)
    monkeypatch.setattr(dc, "law_unit", must_not_parse)
    monkeypatch.setattr(dc, "xml_root", inventory_xml_only)
    assert dc.sync_dc(store, acquirer, None, None) == {"inventory_items": DC_ITEMS}
    assert store.versions == versions


@pytest.mark.parametrize(
    ("field", "value"),
    [("parser", "dc-1/text-2"), ("snapshot_date", "2026-09-14"), ("artifact_sha", "old")],
)
def test_dc_reuse_requires_matching_projection_identity(dc_source, monkeypatch, field, value):
    store, acquirer, _, _ = dc_source
    dc.sync_dc(store, acquirer, None, None)
    store.versions[0][field] = value
    original = dc.expanded
    parsed = []

    def track(archive, member, trail=()):
        parsed.append(member)
        return original(archive, member, trail)

    monkeypatch.setattr(dc, "expanded", track)
    dc.sync_dc(store, acquirer, None, None)
    assert PREFIX + TITLES[0] in parsed
    assert PREFIX + TITLES[1] not in parsed


def test_dc_changed_include_reparses_and_removed_members_are_not_current(dc_source):
    store, acquirer, members, url = dc_source
    dc.sync_dc(store, acquirer, None, None)
    members["us/dc/council/code/titles/7A/section.xml"] = (
        b"<section><num>7A-1</num><heading>Rule</heading><p>Changed body.</p></section>"
    )
    members["us/dc/council/code/index.xml"] = members["us/dc/council/code/index.xml"].replace(
        b'<xi:include href="titles/3/index.xml"/>', b""
    )
    del members[LAWS[1]]
    acquirer.payloads[url] = archive_bytes(members)

    assert dc.sync_dc(store, acquirer, None, None) == {
        "inventory_items": {"dc-code": TITLES[:1], "dc-laws": LAWS[:1]}
    }
    current = [row for row in store.versions if row["document"] == "dc-code:title-7A"][-1]
    assert "Changed body." in current["provisions"][0].text
    assert ("dc-code", TITLES[1]) in store.inventories
    assert ("dc-laws", LAWS[1]) in store.inventories


def test_dc_empty_law_inventory_still_returns_its_collection(dc_source):
    store, acquirer, members, url = dc_source
    for member in LAWS:
        del members[member]
    acquirer.payloads[url] = archive_bytes(members)
    assert dc.sync_dc(store, acquirer, None, None) == {
        "inventory_items": {"dc-code": TITLES, "dc-laws": []}
    }
    assert "dc-laws" in store.collections


@pytest.mark.parametrize("limit", [None, 1])
def test_guides_return_exact_selected_inventory_including_pending(guide_source, limit):
    store, acquirer = guide_source
    store.inventory("portland-zoning-guides", "old-guide", "old-url", "failed")
    assert municipal.sync_portland_guides(store, acquirer, limit, None) == {
        "inventory_items": GUIDE_ITEMS
    }
    assert set(store.collections) == set(GUIDE_ITEMS)
    assert store.inventories["portland-zoning-guides", "base-zones"]["status"] == "indexed"
    assert store.inventories["portland-zoning-guides", "overlay-zones"]["status"] == (
        "pending" if limit else "indexed"
    )


def test_guide_partial_failure_is_recorded_and_propagated(guide_source):
    store, acquirer = guide_source
    acquirer.payloads[municipal.PORTLAND_GUIDES + "overlay-zones"] = (
        b"<html><main><h1>Different page</h1><article>Not the guide.</article></main></html>"
    )
    with pytest.raises(ValueError, match="title does not match"):
        municipal.sync_portland_guides(store, acquirer, None, None)
    assert store.inventories["portland-zoning-guides", "base-zones"]["status"] == "indexed"
    failure = store.inventories["portland-zoning-guides", "overlay-zones"]
    assert failure["status"] == "failed"
    assert "title does not match" in failure["error"]
