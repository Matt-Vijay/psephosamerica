"""NYC's published zoning HTML and Portland's complete Title 33 PDF."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Iterator
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from lxml import etree, html
from pypdf import PdfReader

from .acquire import Acquirer, AcquisitionError
from .parse import markup, readable, source_links
from .sources import progress
from .store import Provision, Store

NYC = "https://zoningresolution.planning.nyc.gov"
PORTLAND = "https://www.portland.gov/code/33"
PORTLAND_GUIDES = "https://www.portland.gov/ppd/zoning-land-use/zoning-code-overview/"


def portland_guide(data: bytes, slug: str) -> Provision:
    root = html.fromstring(data)
    articles, headings = root.xpath("//main//article"), root.xpath("//main//h1")
    if len(articles) != 1 or len(headings) != 1:
        raise ValueError("Portland guide content region missing/ambiguous")
    title = readable(headings[0])
    if title.casefold() != slug.replace("-", " "):
        raise ValueError("Portland guide title does not match requested source")
    url = PORTLAND_GUIDES + slug
    return Provision(
        key="portland:guide/" + slug,
        citation="Portland publisher guide: " + title,
        heading=title,
        text=readable(articles[0]),
        markup=markup(articles[0]),
        url=url,
        unit_kind="publisher_guide",
        references=source_links(articles[0], url),
        metadata={
            "warning": "Explanatory navigation guide, not a codified provision or an applicability finding."
        },
    )


def sync_portland_guides(s: Store, a: Acquirer, limit: int | None, as_of: str | None) -> None:
    if as_of:
        raise ValueError("Portland guide pages have no supported historical snapshot")
    s.collection(
        "portland-zoning-guides",
        ("us-or-portland", "Portland, Oregon", "municipality", "us-or"),
        parents=(("us-or", "Oregon", "state", "us"),),
        name="Portland zoning reader guides",
        authority="City of Portland",
        kind="publisher_guidance",
        homepage=PORTLAND_GUIDES,
        source_status="City explanatory pages, distinct from codified Title 33 text",
        access="Public city pages; no independent redistribution license asserted",
        metadata={
            "warning": "Guides point to code; summaries are not a complete set of restrictions. Exact snapshot date unknown."
        },
    )
    slugs = ("base-zones", "overlay-zones")
    for slug in slugs:
        s.inventory("portland-zoning-guides", slug, PORTLAND_GUIDES + slug, "pending")
    for slug in slugs[:limit]:
        url = PORTLAND_GUIDES + slug
        raw = a.fetch(url)
        unit = portland_guide(s.artifact(raw.sha256), slug)
        _, count, new = s.ingest(
            collection="portland-zoning-guides",
            document=unit.key,
            title=unit.heading,
            url=url,
            acquisition=raw.id,
            snapshot_basis="Undated explanatory publisher page",
            parser="portland-guide-1",
            provisions=[unit],
        )
        s.inventory("portland-zoning-guides", slug, url, "indexed")
        progress("portland-zoning-guides", slug, count, new)


def nyc_units(data: bytes, url: str) -> Iterator[Provision]:
    root = html.fromstring(data)
    main = root.xpath("//main")
    if len(main) != 1:
        raise ValueError("NYC publisher main-content region changed")
    sections = main[0].xpath(
        './/article[@data-section][contains(concat(" ",@class," ")," node--type-section ")]'
    )
    if not sections:
        articles = main[0].xpath('.//article[contains(@class,"node--view-mode-full")]')
        if len(articles) != 1:
            raise ValueError("NYC appendix content region missing/ambiguous")
        node = articles[0]
        title = " ".join(node.xpath(".//h1//text() | .//h2//text()")).strip()
        yield Provision(
            key="nyc-zr:" + url.removeprefix(NYC),
            citation="NYC Zoning Resolution " + title,
            heading=title,
            text=readable(node),
            markup=markup(node),
            url=url,
            unit_kind="appendix",
            metadata={
                "tables": len(node.xpath(".//table")),
                "list_numbering": "Ordered item markers are source DOM positions, not publisher paragraph labels. External CSS numbering is not reconstructed; inspect source markup for explicit attributes.",
                "warning": "Diagrams/maps require the original image/PDF; they are not converted to legal text.",
            },
            references=source_links(node, url),
        )
        return
    for node in sections:
        number = node.get("data-section", "")
        bodies = node.xpath('./div[contains(concat(" ",@class," ")," sec-body ")]')
        if len(bodies) != 1:
            raise ValueError("NYC section body missing/ambiguous: " + number)
        heading = " ".join(node.xpath('./div[@class="section-header-wrapper"]//h3//text()')).strip()
        dates = node.xpath('./div[@class="section-header-wrapper"]//time/@datetime')
        wrapper = etree.Element("section", source_section=number)
        header = etree.SubElement(wrapper, "h3")
        header.text = number + " — " + heading
        wrapper.append(deepcopy(bodies[0]))
        annotations = node.xpath('./div[@class="section-annotations"]')
        for annotation in annotations:
            wrapper.append(deepcopy(annotation))
        section_url = urljoin(NYC, node.get("about", ""))
        yield Provision(
            key="nyc-zr:" + number,
            citation="NYC Zoning Resolution § " + number,
            heading=heading,
            text=readable(wrapper),
            markup=markup(wrapper),
            url=section_url,
            parent_key="nyc-zr:" + url.removeprefix(NYC),
            unit_kind="section" if readable(bodies[0]) else "section_heading",
            metadata={
                "publisher_last_amended": dates[0][:10] if len(dates) == 1 else None,
                "date_basis": "Publisher label Last Amended; not inferred from Drupal field name",
                "chapter_url": url,
                "tables": len(bodies[0].xpath(".//table")),
                "list_numbering": "Ordered item markers are source DOM positions, not publisher paragraph labels. External CSS numbering is not reconstructed; inspect source markup for explicit attributes.",
            },
            references=source_links(wrapper, section_url),
        )


def sync_nyc(s: Store, a: Acquirer, limit: int | None, as_of: str | None) -> None:
    if as_of:
        raise ValueError("NYC HTML sync is current only; archived PDFs have independent dates")
    index_receipt = a.fetch(NYC + "/")
    page = html.fromstring(s.artifact(index_receipt.sha256))
    text = " ".join(page.xpath("//main")[0].text_content().split())
    match = re.search(r"All text changes approved by the city council as of (\w+ \d+, \d{4})", text)
    if not match:
        raise ValueError("NYC publisher currency statement changed")
    snapshot = datetime.strptime(match[1], "%b %d, %Y").date().isoformat()
    s.collection(
        "nyc-zoning",
        ("us-ny-nyc", "New York City", "municipality", "us-ny"),
        parents=(("us-ny", "New York", "state", "us"),),
        name="New York City Zoning Resolution",
        authority="NYC Department of City Planning",
        kind="zoning_resolution",
        homepage=NYC,
        source_status="City-published zoning text; GIS vintage is separate; not a parcel legal determination",
        access="Public publisher chapter pages and linked full-document download; publisher robots respected",
        metadata={
            "approved_changes_through": snapshot,
            "inventory_artifact": index_receipt.sha256,
            "warning": "Approved-through is publisher currency, not a blanket effective date.",
        },
    )
    article_urls = sorted(
        {
            urljoin(NYC, link)
            for link in page.xpath("//a/@href")
            if re.fullmatch(r"/article-[ivx]+", link)
        }
    )
    documents = {
        urljoin(NYC, link) for link in page.xpath("//a/@href") if link.startswith("/appendix-")
    }
    for url in article_urls:
        receipt = a.fetch(url)
        article = html.fromstring(s.artifact(receipt.sha256))
        documents.update(
            urljoin(NYC, link)
            for link in article.xpath("//a/@href")
            if re.fullmatch(r"/article-[ivx]+/chapter-\d+", link)
        )
    for url in sorted(documents):
        s.inventory("nyc-zoning", url.removeprefix(NYC), url, "pending")
    for url in sorted(documents)[:limit]:
        item = url.removeprefix(NYC)
        try:
            raw = a.fetch(url)
            _, count, new = s.ingest(
                collection="nyc-zoning",
                document="nyc-zr:" + item,
                title="NYC Zoning Resolution " + item,
                url=url,
                acquisition=raw.id,
                snapshot_date=snapshot,
                snapshot_basis="Publisher homepage approved-text-changes-through date",
                parser="nyc-1",
                provisions=nyc_units(s.artifact(raw.sha256), url),
                metadata={
                    "inventory_artifact": index_receipt.sha256,
                    "article_indexes": len(article_urls),
                },
            )
            s.inventory("nyc-zoning", item, url, "indexed")
            progress("nyc-zoning", item, count, new)
        except (ValueError, AcquisitionError) as exc:
            s.inventory("nyc-zoning", item, url, "failed", str(exc))


def reindex_nyc(s: Store) -> dict[str, int]:
    """Reproject retained NYC bytes offline; never overwrite old versions or their offsets."""
    rows = s.db.execute(
        "SELECT v.*,d.title,d.url FROM versions v JOIN documents d ON d.id=v.document_id "
        "WHERE d.collection_id='nyc-zoning' AND v.parser LIKE 'nyc-1/%' ORDER BY v.rowid"
    ).fetchall()
    if not rows:
        raise ValueError("No retained NYC source versions; acquire nyc first")
    seen = set()
    added = 0
    for row in rows:
        identity = (row["document_id"], row["artifact_sha"], row["member"], row["snapshot_date"])
        if identity in seen:
            continue
        seen.add(identity)
        _, _, new = s.ingest(
            collection="nyc-zoning",
            document=row["document_id"],
            title=row["title"],
            url=row["url"],
            acquisition=row["acquisition_id"],
            member=row["member"],
            snapshot_date=row["snapshot_date"],
            snapshot_basis=row["snapshot_basis"],
            published_on=row["published_on"],
            effective_on=row["effective_on"],
            amended_on=row["amended_on"],
            available_at=row["available_at"],
            parser="nyc-1",
            metadata=json.loads(row["metadata"]),
            provisions=nyc_units(s.artifact(row["artifact_sha"]), row["url"]),
        )
        added += new
    return {"source_versions": len(seen), "new_projection_versions": added, "downloaded_bytes": 0}


def pdf_units(path: Path, document_key: str, citation: str, url: str) -> Iterator[Provision]:
    reader = PdfReader(path)
    poppler = shutil.which("pdftotext")
    if poppler:
        result = subprocess.run(
            [poppler, "-layout", "-enc", "UTF-8", str(path), "-"],
            capture_output=True,
            check=True,
            timeout=300,
        )
        pages = result.stdout.decode("utf-8").split("\f")
        if pages[-1].strip() == "":
            pages.pop()
        if len(pages) != len(reader.pages):
            raise ValueError("PDF extraction page count differs from publisher PDF")
        method = "Poppler layout text"
    else:
        pages = [page.extract_text(extraction_mode="layout") for page in reader.pages]
        method = "pypdf layout text"
    for ordinal, text in enumerate(pages, 1):
        letters = sum(char.isalpha() for char in text)
        quality = (
            "layout_text_unverified"
            if letters >= 20 and "\ufffd" not in text
            else "image_or_extraction_warning"
        )
        yield Provision(
            key=f"{document_key}/page-{ordinal}",
            citation=f"{citation}, PDF page {ordinal}",
            heading=next(
                (line.strip() for line in text.splitlines() if line.strip()), "Image/blank page"
            ),
            text=text.strip() or "[No extractable text. Consult the preserved source PDF page.]",
            markup="",
            url=f"{url}#page={ordinal}",
            unit_kind="pdf_page",
            metadata={
                "pdf_page": ordinal,
                "pdf_pages": len(pages),
                "extraction": method,
                "text_quality": quality,
                "letter_characters": letters,
                "warning": "PDF page, not a complete provision. Tables/figures require source visual verification.",
            },
        )


def sync_portland(s: Store, a: Acquirer, limit: int | None, as_of: str | None) -> None:
    if as_of or limit:
        raise ValueError(
            "Portland text sync acquires the publisher's complete current Title 33 PDF"
        )
    index = a.fetch(PORTLAND)
    page = html.fromstring(s.artifact(index.sha256))
    links = [
        link
        for link in page.xpath("//a[@href]")
        if "Full printable version of Title 33" in link.text_content()
    ]
    if len(links) != 1:
        raise ValueError("Portland full-code download link changed")
    label = " ".join(links[0].text_content().split())
    match = re.search(r"effective\s+(\w+\s+\d+,?\s+\d{4})", label, re.I)
    if not match:
        raise ValueError("Portland full-code effective date missing")
    effective = datetime.strptime(match[1].replace(",", ""), "%B %d %Y").date().isoformat()
    record_url = links[0].get("href", "")
    landing = a.fetch(record_url)
    record = html.fromstring(s.artifact(landing.sha256))
    downloads = [
        urljoin(record_url, link.get("href", ""))
        for link in record.xpath("//a[@href]")
        if link.text_content().strip() == "Download"
    ]
    if len(downloads) != 1:
        raise ValueError("Portland record download missing/ambiguous")
    raw = a.fetch(downloads[0])
    s.collection(
        "portland-zoning",
        ("us-or-portland", "Portland, Oregon", "municipality", "us-or"),
        parents=(("us-or", "Oregon", "state", "us"),),
        name="Portland Title 33 — Planning and Zoning",
        authority="City of Portland",
        kind="zoning_code",
        homepage=PORTLAND,
        source_status="City-published complete printable code; later ordinances not silently folded in",
        access="Publisher full printable download via City Archives; preserve source-specific terms",
        metadata={
            "publisher_edition_effective_on": effective,
            "index_artifact": index.sha256,
            "record_artifact": landing.sha256,
            "limitation": "PDF page retrieval; no automatic table/diagram interpretation or parcel opinion.",
        },
    )
    s.inventory("portland-zoning", "title-33", raw.url, "pending")
    _, count, new = s.ingest(
        collection="portland-zoning",
        document="portland:title-33",
        title="Title 33, Planning and Zoning",
        url=PORTLAND,
        acquisition=raw.id,
        snapshot_date=effective,
        effective_on=effective,
        snapshot_basis="Publisher explicitly dated complete code edition",
        parser="pdf-layout-1",
        provisions=pdf_units(
            s.object_path(raw.sha256), "portland:title-33", "Portland Title 33", raw.url
        ),
        metadata={"index_artifact": index.sha256, "record_artifact": landing.sha256},
    )
    s.inventory("portland-zoning", "title-33", raw.url, "indexed")
    progress("portland-zoning", "title-33", count, new)
