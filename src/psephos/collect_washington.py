"""Washington's official live RCW: inventoried titles, whole-chapter HTML, native cites.

No section-by-section crawling, legislative-status inference, or inferred effective dates.
The whole discovered title/chapter inventory is retained even when a tranche is selected.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from copy import deepcopy
from datetime import date
from typing import Any
from urllib.parse import parse_qs, urlsplit

from lxml import etree, html

from .acquire import Acquirer, AcquisitionError
from .parse import markup, media_links, readable, source_links
from .store import Provision, Store

HOME = "https://app.leg.wa.gov/RCW/default.aspx"
AUTHORITY = "https://leg.wa.gov/state-laws-and-rules/state-laws-rcw/"
ARCHIVES = AUTHORITY + "past-versions-of-state-laws/"
DISCLAIMER = "https://leg.wa.gov/disclaimer/"
COLLECTION = "wa-rcw"
DEFAULT_TITLES = ("1", "29A", "29B", "34", "35", "35A", "36", "42", "44")
CITE = re.compile(r"\d+[A-Z]?(?:\.\d+[A-Z]?){0,2}")


def page(data: bytes) -> etree._Element:
    # The live publisher sends UTF-8, sometimes without an in-document charset.
    return html.fromstring(data.decode("utf-8"))


def inventory_links(data: bytes, depth: int) -> list[tuple[str, str, str]]:
    root = page(data)
    items: dict[str, tuple[str, str, str]] = {}
    for link in root.xpath(
        '//*[@id="contentWrapper" or @id="ContentPlaceHolder1_dgSections"]//a[@href]'
    ):
        query = parse_qs(urlsplit(link.get("href", "")).query)
        cite = next((v[0] for k, v in query.items() if k.lower() == "cite"), "")
        if not CITE.fullmatch(cite) or cite.count(".") != depth:
            continue
        rows = link.xpath("ancestor::tr[1]")
        if not rows:
            continue  # Chapter/title notes also contain cross-references, not inventory rows.
        row = rows[0]
        cells = row.xpath("./td")
        heading = readable(cells[-1]) if len(cells) > 1 else readable(link)
        items[cite] = (cite, HOME + "?Cite=" + cite, heading)
    return list(items.values())


def chapter_units(data: bytes, chapter: str, url: str) -> list[Provision]:
    root = page(data)
    units = root.xpath('//*[@id="ContentPlaceHolder1_dlSectionContent"]/span[a[@name]]')
    if not units:
        if chapter == "36.42":
            contents = root.xpath('//*[@id="contentWrapper"]')
            if len(contents) == 1 and "County and city sales and use taxes:" in readable(
                contents[0]
            ):
                node = contents[0]
                return [
                    Provision(
                        key="wa-rcw:36.42/contents-notes",
                        citation="RCW Chapter 36.42 contents and notes",
                        heading="Retail sales and use taxes — publisher cross-reference chapter",
                        text=readable(node),
                        markup=markup(node),
                        url=url,
                        unit_kind="chapter_notes",
                        parent_key="wa-rcw:36.42",
                        metadata={
                            "source_identifier": chapter,
                            "publisher_note_only_chapter": True,
                        },
                        references=source_links(node, url),
                    )
                ]
        # The publisher embeds redistricting plans as unnumbered chapter material,
        # not section containers. Keep their legal descriptions as one native unit.
        contents = root.xpath('//*[@id="contentWrapper"]')
        if chapter in {"29A.76C", "44.07F"} and len(contents) == 1:
            node = contents[0]
            text = readable(node)
            if "RESOLUTION OF REDISTRICTING" in text and "Reviser's note:" in text:
                return [
                    Provision(
                        key="wa-rcw:" + chapter,
                        citation="RCW Chapter " + chapter,
                        heading="Redistricting plan — unnumbered publisher chapter",
                        text=text,
                        markup=markup(node),
                        url=url,
                        unit_kind="redistricting_plan",
                        parent_key="wa-rcw:" + chapter.split(".")[0],
                        metadata={
                            "source_identifier": chapter,
                            "unnumbered_source": True,
                            "tables": len(node.xpath(".//table")),
                            "media": media_links(node, url),
                            "geography_status": "Publisher legal descriptions retained; no inferred GIS geometry or current-boundary claim",
                        },
                        references=source_links(node, url),
                    )
                ]
        raise ValueError("No publisher section containers; refusing digest-only import")
    anchors = [n.xpath("./a[@name]")[0].get("name") for n in units]
    if any(not CITE.fullmatch(c or "") or not c.startswith(chapter + ".") for c in anchors):
        raise ValueError("Section anchor outside requested chapter")
    listed = set(root.xpath('//*[@id="contentWrapper"]//a[starts-with(@href,"#")]/@href'))
    if listed and {"#" + c for c in anchors} != listed:
        raise ValueError("Full chapter sections do not match publisher chapter contents")
    totals = Counter(anchors)
    seen: Counter[str] = Counter()
    result = []
    for node, cite in zip(units, anchors, strict=True):
        seen[cite] += 1
        suffix = "/occurrence/" + str(seen[cite]) if totals[cite] > 1 else ""
        headings = node.xpath("./div/h3")
        heading = readable(headings[1]) if len(headings) > 1 else ""
        clean = deepcopy(node)
        for control in clean.xpath('.//a[contains(@class,"hidden-print")]'):
            control.drop_tree()
        source_notes = [
            readable(n) for n in node.xpath('./div[starts-with(normalize-space(.),"[")]')
        ]
        result.append(
            Provision(
                key="wa-rcw:" + cite + suffix,
                citation="RCW " + cite,
                heading=heading,
                text=readable(clean),
                markup=markup(node),
                url=HOME + "?cite=" + cite,
                parent_key="wa-rcw:" + chapter,
                metadata={
                    "source_identifier": cite,
                    "source_anchor": cite,
                    "source_notes": source_notes,
                    "occurrence": seen[cite],
                    "duplicate_identifier_count": totals[cite],
                    "tables": len(node.xpath(".//table")),
                    "media": media_links(node, url),
                    "clock_policy": "Dates and conditional amendments retained as source wording; no effective date inference",
                },
                references=source_links(node, url),
            )
        )
    # Keep chapter-level cross-references, notes, subchapter headings and section list.
    for node in root.xpath('//*[@id="contentWrapper"]'):
        result.append(
            Provision(
                key="wa-rcw:" + chapter + "/contents-notes",
                citation="RCW Chapter " + chapter + " contents and notes",
                heading="Chapter contents and notes",
                text=readable(node),
                markup=markup(node),
                url=url,
                parent_key="wa-rcw:" + chapter,
                unit_kind="chapter_notes",
                metadata={
                    "source_identifier": chapter,
                    "tables": len(node.xpath(".//table")),
                    "media": media_links(node, url),
                },
                references=source_links(node, url),
            )
        )
    return result


def sync_washington(
    s: Store,
    a: Acquirer,
    limit: int | None = None,
    as_of: str | None = None,
    *,
    titles: tuple[str, ...] | None = DEFAULT_TITLES,
) -> dict[str, Any]:
    """Inventory the entire live RCW, acquire selected complete titles (None = all).

    ``limit`` caps chapter documents, not inventory discovery. ``as_of`` is accepted
    only for today's observation; a mutable live view cannot reconstruct history.
    Caller owns Acquirer lifetime and its cumulative byte budget.
    """
    if as_of and str(as_of) != date.today().isoformat():
        raise ValueError("Live RCW does not support historical as-of acquisition")
    if limit is not None and limit < 0:
        raise ValueError("limit must be nonnegative")
    initial_bytes = a.downloaded
    a.delay = max(a.delay, 1.1)
    authority = a.fetch(AUTHORITY)
    disclaimer = a.fetch(DISCLAIMER)
    archive = a.fetch(ARCHIVES)
    # Native XML services expose legislation/status links, not codified legal text.
    native_receipts = []
    for service in ("RcwCiteAffectedService", "SessionLawService", "LegislativeDocumentService"):
        receipt = a.fetch("https://wslwebservices.leg.wa.gov/" + service + ".asmx?WSDL")
        native_receipts.append(receipt.id)
    home = a.fetch(HOME)
    data = s.artifact(home.sha256)
    root = page(data)
    last_update = " ".join(
        root.xpath('//*[@id="ContentPlaceHolder1_pnlLastUpdate"]//text()')
    ).strip()
    s.collection(
        COLLECTION,
        ("us-wa", "Washington", "state", "us"),
        name="Revised Code of Washington — official live chapters",
        authority="Washington State Legislature / Code Reviser",
        kind="statute",
        homepage=HOME,
        source_status="official publisher; live compilation, not certified archive",
        access="Public HTTPS; publisher robots checked; >=1.1 seconds per host; no selling or redistribution authorization inferred",
        metadata={
            "authority_acquisition": authority.id,
            "disclaimer_acquisition": disclaimer.id,
            "archive_inventory_acquisition": archive.id,
            "native_service_receipts": native_receipts,
            "native_service_limitation": "bill-affects links are not proof of enactment; services not codified text",
            "publisher_last_update_statement": last_update,
            "reuse_notice": "Publisher claims copyright for RCW/WAC and asks users intending to sell copies to contact Statute Law Committee.",
            "selected_titles": list(titles) if titles is not None else "all",
            "inventory_scope": "all titles and their discovered chapters",
        },
    )
    discovered_titles = inventory_links(data, 0)
    if not discovered_titles:
        raise ValueError("No publisher title inventory")
    for title, url, _ in discovered_titles:
        old = s.db.execute(
            "SELECT status FROM inventories WHERE collection_id=? AND item=?",
            (COLLECTION, "title:" + title),
        ).fetchone()
        if not old or old[0] != "inventoried":
            s.inventory(COLLECTION, "title:" + title, url, "pending")
    chapters = []
    failures = []
    for title, url, _heading in discovered_titles:
        try:
            r = a.fetch(url)
            children = inventory_links(s.artifact(r.sha256), 1)
            children = [child for child in children if child[0].startswith(title + ".")]
            if not children:
                raise ValueError("Title has no chapter inventory")
            s.inventory(COLLECTION, "title:" + title, url, "inventoried")
            for cite, chapter_url, chapter_heading in children:
                full_url = chapter_url.replace("?Cite=", "?cite=") + "&full=true"
                old = s.db.execute(
                    "SELECT status FROM inventories WHERE collection_id=? AND item=?",
                    (COLLECTION, "chapter:" + cite),
                ).fetchone()
                if not old:
                    s.inventory(COLLECTION, "chapter:" + cite, full_url, "pending")
                chapters.append((title, cite, full_url, chapter_heading))
        except (AcquisitionError, ValueError) as exc:
            s.inventory(COLLECTION, "title:" + title, url, "failed", str(exc))
            failures.append({"item": "title:" + title, "error": str(exc)})
            if isinstance(exc, AcquisitionError):
                # A budget, robots, access or retry deferral must stop this host.
                raise
    selected = [c for c in chapters if titles is None or c[0] in titles]
    if not a.refresh:
        selected = [
            chapter
            for chapter in selected
            if not s.db.execute(
                "SELECT 1 FROM inventories i JOIN documents d ON d.id=? "
                "WHERE i.collection_id=? AND i.item=? AND i.status IN ('ingested','indexed') "
                "AND EXISTS (SELECT 1 FROM versions v WHERE v.document_id=d.id)",
                ("wa-rcw:chapter:" + chapter[1], COLLECTION, "chapter:" + chapter[1]),
            ).fetchone()
        ]
    if limit is not None:
        selected = selected[:limit]
    documents = sections = 0
    for title, cite, url, heading in selected:
        try:
            r = a.fetch(url)
            provisions = chapter_units(s.artifact(r.sha256), cite, url)
            s.ingest(
                collection=COLLECTION,
                document="wa-rcw:chapter:" + cite,
                title="RCW " + cite + " — " + heading,
                url=url,
                acquisition=r.id,
                snapshot_basis="Live publisher retrieval; individual legal clocks unknown; collection update statement retained in metadata",
                parser="washington-full-chapter-html-v1",
                provisions=provisions,
                metadata={
                    "publisher_last_update_statement": last_update,
                    "title": title,
                    "chapter": cite,
                    "inventory_acquisition": home.id,
                    "section_count": sum(p.unit_kind == "section" for p in provisions),
                },
            )
            s.inventory(COLLECTION, "chapter:" + cite, url, "ingested")
            documents += 1
            sections += sum(p.unit_kind == "section" for p in provisions)
            section_count = sum(p.unit_kind == "section" for p in provisions)
            print(
                f"Washington {cite}: {section_count} sections, {len(provisions)} units",
                file=sys.stderr,
                flush=True,
            )
        except (AcquisitionError, ValueError) as exc:
            s.inventory(COLLECTION, "chapter:" + cite, url, "failed", str(exc))
            failures.append({"item": "chapter:" + cite, "error": str(exc)})
            if isinstance(exc, AcquisitionError):
                raise
    return {
        "discovered_titles": len(discovered_titles),
        "discovered_chapters": len(chapters),
        "selected_chapters": len(selected),
        "documents": documents,
        "sections": sections,
        "downloaded_bytes_this_run": a.downloaded - initial_bytes,
        "failures": failures,
    }
