import json
from types import SimpleNamespace

import httpx
import pytest

from psephos import new_york as ny
from psephos.acquire import Acquirer, AcquisitionError
from psephos.store import Provision


def test_original_allowance_and_reserved_chunk_are_not_reset(store, tmp_path):
    ledger = store.root / "collectors/ny-northeast/http-budget.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(json.dumps({"cap": ny.CAP, "newly_decoded_bytes": 60637692, "runs": []}))
    a = ny.NewYorkAcquirer(store, tmp_path)
    try:
        assert a.downloaded == 60637692 and a.max_bytes == ny.CAP - 1024**2
        assert a.budget_path == ledger and not (tmp_path / "budget.json").exists()
        a._record(
            ny.ORIGIN + "/api/3/laws",
            ny.ORIGIN + "/api/3/laws",
            "2026-01-01T00:00:00Z",
            401,
            {},
            None,
            "Authentication required",
        )
        with pytest.raises(AcquisitionError, match="Retained access denial"):
            a.fetch(ny.ORIGIN + "/api/3/laws")
        with pytest.raises(AcquisitionError, match="reviewed PDF publisher"):
            a.fetch("https://www.nysenate.gov/legislation/laws/all")
    finally:
        a.close()


def test_page_projection_keeps_blanks_accounted_and_rejects_contents_only(tmp_path, monkeypatch):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"%PDF-fixture")
    page = (
        "General Construction\nSection 1. Source text\n"
        + "The agency shall preserve this source text. " * 40
        + "\nSection 2. Another requirement\n"
    )

    def run(args, **kwargs):
        return SimpleNamespace(stdout="Pages: 2\n" if args[0] == "pdfinfo" else page + "\f\f")

    monkeypatch.setattr(ny.subprocess, "run", run)
    units, meta = ny.parse_pdf(pdf, "GCN", "General Construction", ny.ORIGIN)
    assert len(units) == 1 and meta["empty_pages_not_indexed"] == [2]
    assert units[0].text == page.strip("\r\n")
    assert units[0].key == "ny:GCN:page:1"
    with pytest.raises(ValueError, match="volume identity"):
        ny.parse_pdf(pdf, "ABC", "Alcoholic Beverage Control", ny.ORIGIN)
    page = "General Construction\nContents\nSection 1. Title\nSection 2. Title"
    with pytest.raises(ValueError, match="substantive statutory text"):
        ny.parse_pdf(pdf, "GCN", "General Construction", ny.ORIGIN)


def test_volume_identity_rejects_incidental_law_mentions():
    source = [
        "CHAPTER 808\nPUBLIC HOUSING LAW\nArticle 1\n"
        "The public buildings law and the public lands law are also mentioned."
    ]
    assert ny.volume_identity(source, "Public Housing")["page"] == 1
    for wrong in ("Public Buildings", "Public Lands", "Public"):
        with pytest.raises(ValueError, match="volume identity"):
            ny.volume_identity(source, wrong)
    declaration = "This chapter shall be known as the vehicle and traffic law."
    assert ny.volume_identity(["Contents"] * 30 + [declaration], "Vehicle and Traffic") == {
        "basis": "statutory self-title",
        "page": 31,
        "text": declaration,
    }
    family = (
        'The title of this act is "the family court act of the state of New York." '
        'It may be cited as "The Family Court Act."'
    )
    assert ny.volume_identity([family], "Family Court")["basis"] == "statutory self-title"


@pytest.mark.parametrize("prior_404", [True, False])
def test_resume_skips_existing_volumes_and_prior_404_without_querying_denied_api(
    store, monkeypatch, prior_404
):
    monkeypatch.setattr(ny, "VOLUMES", [("ONE", "First"), ("BAD", "Missing"), ("TWO", "Second")])
    root = ny.ORIGIN + "/pdf/laws/"
    seen = []

    def respond(request):
        url = str(request.url)
        seen.append(url)
        assert "/api/" not in url
        if "BAD" in url:
            return httpx.Response(404)
        return httpx.Response(
            200, content=b"User-agent: *\nAllow: /" if url.endswith("/robots.txt") else b"fixture"
        )

    a = Acquirer(store, delay=0)
    a.client.close()
    a.client = httpx.Client(transport=httpx.MockTransport(respond))
    a._pause = lambda *args, **kwargs: None
    if prior_404:
        a._record(
            root + "BAD?full=true",
            root + "BAD?full=true",
            "2026-01-01T00:00:00Z",
            404,
            {},
            None,
            "Not Found",
        )
    monkeypatch.setattr(
        ny,
        "parse_pdf",
        lambda p, c, t, u: (
            [Provision("ny:" + c + ":page:1", t, t, "Source law", "", u, unit_kind="page")],
            {"pdf_pages": 1},
        ),
    )
    try:
        assert ny.sync_new_york(store, a, 1, None)["accepted_this_run"] == 1
        assert ny.sync_new_york(store, a, 2, None)["accepted_this_run"] == 1
        assert ny.sync_new_york(store, a, None, None)["attempted"] == 0
        assert seen.count(root + "ONE?full=true") == 1
        assert sum("BAD" in u for u in seen) == (0 if prior_404 else 1)
        assert (
            store.db.execute("SELECT status FROM inventories WHERE item='BAD'").fetchone()[0]
            == "source_unavailable"
        )
        assert store.db.execute("SELECT count(*) FROM versions").fetchone()[0] == 2
    finally:
        a.close()
