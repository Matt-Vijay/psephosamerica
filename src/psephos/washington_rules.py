"""Bounded Washington SEPA/GMA publications; source history is not operative-law inference."""

from __future__ import annotations

import json
import re
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

from .acquire import Acquirer, AcquisitionError, Receipt
from .collect_washington import chapter_units as rcw_chapter_units
from .collect_washington import page
from .parse import markup, media_links, readable, source_links
from .store import Provision, Reference, Store

WAC = "https://app.leg.wa.gov/WAC/default.aspx"
CHAPTERS = (
    "197-11",
    "365-185",
    "365-190",
    "365-191",
    "365-195",
    "365-196",
    "365-197",
    "365-198",
    "365-199",
)
AUTHORITY = "https://leg.wa.gov/state-laws-and-rules/state-rules-wac/"
ARCHIVES = AUTHORITY + "past-versions-of-state-rules/"
DISCLAIMER = "https://leg.wa.gov/disclaimer/"
REGISTER = "https://leg.wa.gov/state-laws-and-rules/washington-state-register/"
TABLE = "https://lawfilesext.leg.wa.gov/law/wsr/2026/table-26.htm"
AGENCY = "https://www.commerce.wa.gov/about/legislative/rulemaking/"
LEGEND = "https://leg.wa.gov/media/vvxb5flh/keytotable.htm"
ISSUES = "https://lawfilesext.leg.wa.gov/law/wsr/WsrByIssue.htm"
WAC_CITE = re.compile(r"\d+[A-Z]?(?:-\d+[A-Z]?){0,2}")
WSR_CITE = re.compile(r"\bWSR\s+(\d{2}-\d{2}-\d{3})\b")
LIMITS = "Live publisher compilation, not certified annual archive or proof of all current legal requirements. Filing history is not an inferred effective-law timeline."


def wac_inventory(data: bytes, title: str | None = None) -> list[tuple[str, str, str]]:
    """Only native inventory table rows; reviser's-note links are not current chapters."""
    root = page(data)
    scope = '//*[@id="contentWrapper"]' if title else '//*[@id="mainContent"]'
    found = {}
    for row in root.xpath(scope + "//table/tr"):
        cells = row.xpath("./td")
        if len(cells) < 2:
            continue
        for link in cells[0].xpath(".//a[@href]"):
            href = urljoin(WAC, link.get("href", ""))
            parsed = urlsplit(href)
            match = re.fullmatch(r"(?:cite|Cite)=([\dA-Z-]+)", parsed.query)
            if (
                parsed.netloc != "app.leg.wa.gov"
                or parsed.scheme not in {"http", "https"}
                or parsed.fragment
                or parsed.path.lower() != "/wac/default.aspx"
                or not match
            ):
                continue
            cite = match[1]
            if WAC_CITE.fullmatch(cite) and cite.count("-") == (1 if title else 0):
                if title is None or cite.startswith(title + "-"):
                    found[cite] = (cite, WAC + "?cite=" + cite, readable(cells[-1]))
    if not found:
        raise ValueError("No native WAC inventory rows")
    return list(found.values())


def wac_chapter(data: bytes, chapter: str, url: str) -> list[Provision]:
    root = page(data)
    contents = root.xpath('//*[@id="contentWrapper"]')
    nodes = root.xpath('//*[@id="ContentPlaceHolder1_dlSectionContent"]/span[a[@name]]')
    if len(contents) != 1 or not nodes:
        raise ValueError("No complete WAC section bodies; refusing index/digest import")
    anchors = [node.xpath("./a[@name]")[0].get("name", "") for node in nodes]
    if any(
        not WAC_CITE.fullmatch(cite) or cite.count("-") != 2 or not cite.startswith(chapter + "-")
        for cite in anchors
    ):
        raise ValueError("WAC section outside requested chapter")
    listed = contents[0].xpath('.//a[starts-with(@href,"#")]/@href')
    if set(listed) != {"#" + cite for cite in anchors}:
        raise ValueError("WAC body anchors differ from complete chapter inventory")
    parts: dict[str, str] = {}
    part = ""
    for row in contents[0].xpath("./table[1]/tr"):
        if row.xpath('./td[@colspan="3"]'):
            part = readable(row)
        for href in row.xpath('.//a[starts-with(@href,"#")]/@href'):
            parts[href[1:]] = part
    totals = Counter(anchors)
    seen: Counter[str] = Counter()
    result = []
    for node, cite in zip(nodes, anchors, strict=True):
        headings = node.xpath("./div/h3")
        if len(headings) != 2 or cite not in readable(headings[0]):
            raise ValueError("Unsupported WAC section heading layout")
        clean = deepcopy(node)
        for control in clean.xpath('./div/h3/a[normalize-space(.)="PDF"]'):
            control.drop_tree()
        notes = node.xpath('./div[starts-with(normalize-space(.),"[")]')
        refs = list(source_links(node, url))
        # The same RCW URL may occur in both a substantive condition and an
        # authority note. Keep the body link; append its distinct note context.
        refs.extend(
            replace(ref, relation="publisher_statutory_authority", evidence=markup(note))
            for note in notes
            if "Statutory Authority:" in readable(note)
            for ref in source_links(note, url)
            if "/RCW/" in ref.target
        )
        for note in notes:
            refs.extend(
                Reference(
                    "wa-wsr:" + number, "publisher_filing_citation", "WSR " + number, markup(note)
                )
                for number in WSR_CITE.findall(readable(note))
            )
        seen[cite] += 1
        suffix = "/occurrence/" + str(seen[cite]) if totals[cite] > 1 else ""
        result.append(
            Provision(
                key="wa-wac:" + cite + suffix,
                citation="WAC " + cite,
                heading=readable(headings[1]),
                text=readable(clean),
                markup=markup(node),
                url=WAC + "?cite=" + cite,
                parent_key="wa-wac:" + chapter,
                metadata={
                    "source_identifier": cite,
                    "source_part_heading": parts.get(cite, ""),
                    "source_notes": [readable(n) for n in notes],
                    "duplicate_identifier_count": totals[cite],
                    "occurrence": seen[cite],
                    "tables": len(node.xpath(".//table")),
                    "media": media_links(node, url),
                    "clock_policy": LIMITS,
                },
                references=tuple(refs),
            )
        )
    result.append(
        Provision(
            key="wa-wac:" + chapter + "/contents-notes",
            citation="WAC " + chapter + " contents and disposition",
            heading="Chapter hierarchy, contents, and disposition of former sections",
            text=readable(contents[0]),
            markup=markup(contents[0]),
            url=url,
            parent_key="wa-wac:" + chapter.split("-")[0],
            unit_kind="chapter_notes",
            metadata={
                "scope": "Index/notes/disposition, not additional operative rule bodies",
                "media": media_links(contents[0], url),
            },
            references=source_links(contents[0], url),
        )
    )
    return result


def official_link(url: str) -> str:
    """Publisher-linked HTTPS upgrade on these two official WA publication hosts only."""
    parsed = urlsplit(url)
    if (
        parsed.netloc not in {"app.leg.wa.gov", "lawfilesext.leg.wa.gov"}
        or parsed.scheme not in {"http", "https"}
        or parsed.fragment
    ):
        raise ValueError("Unsupported Washington publication link")
    return parsed._replace(scheme="https").geturl()


def filing_links(data: bytes, url: str) -> dict[str, str]:
    result = {}
    for link in page(data).xpath("//a[@href]"):
        target = urljoin(url, link.get("href", ""))
        parsed = urlsplit(target)
        match = re.fullmatch(r"/law/wsr/(20\d{2})/(\d{2})/(\d{2}-\d{2}-\d{3})\.htm", parsed.path)
        if parsed.netloc == "lawfilesext.leg.wa.gov" and match:
            if match[3][:2] != match[1][-2:] or match[3][3:5] != match[2] or parsed.query:
                raise ValueError("Inconsistent official filing link")
            result[match[3]] = official_link(target)
    return result


def filing_unit(data: bytes, number: str, url: str) -> Provision:
    if not data.rstrip().lower().endswith(b"</html>"):
        raise ValueError("Incomplete filing HTML")
    root = page(data)
    text = readable(root)
    if not text.startswith("WSR " + number + "\n") or "TextEnd" not in data.decode("utf-8-sig"):
        raise ValueError("Filing identity/body marker mismatch")
    lines = text.splitlines()
    if len(lines) < 4 or lines[1] not in {"PROPOSED RULES", "PERMANENT RULES"}:
        raise ValueError("Unsupported filing type; do not treat all Register material as rules")
    filed = re.search(r"\[Filed ([A-Z][a-z]+ \d{1,2}, \d{4}),", text)
    if not filed:
        raise ValueError("Missing explicit filing clock")
    projected = deepcopy(root)
    deleted = added = 0
    for node in projected.iter():
        if not isinstance(node.tag, str):
            continue
        style = node.get("style", "").replace(" ", "").lower()
        label = (
            "DELETED"
            if "text-decoration:line-through" in style or node.tag in {"del", "s", "strike"}
            else "UNDERLINED"
            if "text-decoration:underline" in style or node.tag in {"ins", "u"}
            else ""
        )
        if label:
            node.text = "[" + label + "]" + (node.text or "")
            node.tail = "[/" + label + "]" + (node.tail or "")
            deleted += label == "DELETED"
            added += label == "UNDERLINED"
    refs = list(source_links(root, url))
    refs.extend(
        Reference("wa-wsr:" + cited, "publisher_filing_citation", "WSR " + cited, text[:6000])
        for cited in WSR_CITE.findall(text[:6000])
        if cited != number
    )
    return Provision(
        key="wa-wsr:" + number,
        citation="WSR " + number,
        heading=lines[1] + " — " + lines[2],
        text=readable(projected),
        markup=markup(root),
        url=url,
        unit_kind="rulemaking_filing",
        metadata={
            "filing_type": lines[1],
            "filed_on": datetime.strptime(filed[1], "%B %d, %Y").date().isoformat(),
            "header": lines[:8],
            "deletion_spans": deleted,
            "underlined_spans": added,
            "media": media_links(root, url),
            "tables": len(root.xpath(".//table")),
            "scope": "Complete retained filing HTML, not codified current law. UNDERLINED preserves source annotation, not a conclusion that text is in force. Deleted text stays explicitly labeled; filing/intended adoption/effectiveness/publication/observation are separate clocks.",
        },
        references=tuple(refs),
    )


def action_legend(data: bytes) -> dict[str, str]:
    result = {}
    for row in page(data).xpath("//table//tr"):
        cells = [readable(cell).strip() for cell in row.xpath("./td")]
        for i, value in enumerate(cells):
            if value == "=" and i > 0 and i + 1 < len(cells):
                result[cells[i - 1]] = cells[i + 1]
    if not {"AMD", "-P", "-C", "-E", "-W", "NEW"} <= result.keys():
        raise ValueError("Unsupported/missing official action legend")
    return result


def table_unit(data: bytes, title: str, url: str, legend: dict[str, str]) -> Provision:
    root = page(data)
    text = readable(root)
    year = re.search(r"(20\d{2}) WAC-to-Register Table", text)
    if not year or "Title " + title + " WAC" not in text:
        raise ValueError("Affected-section table identity mismatch")
    rows = []
    refs: list[Reference] = []
    for row in root.xpath('//table[@id="table"]/tr[td]'):
        cells = row.xpath("./td")
        if len(cells) == 1 and readable(cells[0]).strip() == "NO FILINGS FOR THIS TITLE":
            continue
        if len(cells) != 4:
            raise ValueError("Unsupported affected-section row")
        cite, action, number = [readable(c).strip() for c in cells[:3]]
        if (
            not WAC_CITE.fullmatch(cite)
            or not cite.startswith(title + "-")
            or not re.fullmatch(r"\d{2}-\d{2}-\d{3}", number)
        ):
            raise ValueError("Malformed affected-section identity")
        symbol, _, suffix = action.rpartition("-") if action not in legend else (action, "", "")
        if symbol not in legend or (suffix and "-" + suffix not in legend):
            raise ValueError("Unknown action symbol; retain source but refuse interpretation")
        meaning = legend[symbol] + ("; " + legend["-" + suffix] if suffix else "")
        rows.append({"wac": cite, "action": action, "wsr": number, "meaning": meaning})
        refs.extend(
            replace(
                ref,
                relation="publisher_action_table",
                label=f"{cite}: {action} — {meaning}: {ref.label}",
                evidence=markup(row),
            )
            for ref in source_links(row, url)
        )
    if not rows and "NO FILINGS FOR THIS TITLE" not in text:
        raise ValueError("No affected-section rows or explicit empty notice")
    for node in root.xpath("//style|//script|//head"):
        node.drop_tree()
    return Provision(
        key=f"wa-wsr:table:{year[1]}:{title}",
        citation=f"{year[1]} WAC-to-Register table, Title {title}",
        heading="Publisher affected-section index — action types are not interchangeable",
        text=readable(root),
        markup=markup(root),
        url=url,
        unit_kind="filing_index",
        metadata={
            "printed_year": year[1],
            "rows": rows,
            "scope": "Index only, not full rule text or an operative-law timeline. Follow exact filing evidence.",
        },
        references=tuple(refs),
    )


def sync_washington_rules(
    s: Store, a: Acquirer, limit: int | None, as_of: str | None
) -> dict[str, Any]:
    """One selected current family; resumable cached bytes, no recursive link crawling."""
    if as_of is not None:
        raise ValueError("Live WAC acquisition cannot reconstruct a historical as-of date")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    a.delay = max(a.delay, 1.1)
    a.max_bytes = min(a.max_bytes, 16 * 1024**2)
    receipts: dict[str, Receipt] = {}

    def fetch(url: str) -> bytes:
        receipt = a.fetch(url, max_file_bytes=2 * 1024**2)
        receipts[url] = receipt
        return s.artifact(receipt.sha256)

    def ingest(
        collection: str,
        key: str,
        title: str,
        url: str,
        units: list[Provision],
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        _, _, new = s.ingest(
            collection=collection,
            document=key,
            title=title,
            url=url,
            acquisition=receipts[url].id,
            snapshot_basis="Live retained publisher observation; see source-specific clocks, not inferred legal effectiveness",
            parser="washington-land-use-v1",
            provisions=units,
            metadata=metadata,
        )
        return new

    for url in (AUTHORITY, ARCHIVES, DISCLAIMER):
        fetch(url)
    root_data = fetch(WAC)
    root = page(root_data)
    updated = root.xpath('//*[@id="ContentPlaceHolder1_pnlDefaultUpdated"]')
    if len(updated) != 1:
        raise ValueError("Missing WAC compilation currency notice")
    currency = readable(updated[0])
    titles = wac_inventory(root_data)
    if not {"197", "365"} <= {entry[0] for entry in titles}:
        raise ValueError("Requested titles missing from WAC inventory")
    selected_inventory = [
        child
        for title in ("197", "365")
        for child in wac_inventory(fetch(WAC + "?cite=" + title), title)
    ]
    if not set(CHAPTERS) <= {entry[0] for entry in selected_inventory}:
        raise ValueError("Selected chapter inventory changed; review scope before acquisition")
    s.collection(
        "wa-wac",
        ("us-wa", "Washington", "state", "us"),
        name="Washington Administrative Code — SEPA/GMA chapter selection",
        authority="Washington State Legislature / Code Reviser",
        kind="administrative_rule",
        homepage=WAC,
        source_status="Official publisher's live compilation; certified annual archive is separate",
        access="Public HTTPS; robots checked; >=1.1s per host; no redistribution permission inferred",
        metadata={
            "scope": "Complete retained chapter bodies only for selected SEPA/GMA chapters, not all WAC or all Title 365. Actual accepted scope is in document/inventory listings.",
            "selected_chapters": list(CHAPTERS),
            "inventory_scope": "All root titles, and all current chapter rows in Titles 197/365; note-only former chapters are retained in title artifacts, not counted as current chapters.",
            "publisher_compilation_updated": currency,
            "clock_policy": LIMITS,
            "certified_archive_notice": "Certified PDF annual archive is the official publication; no annual archive downloaded or live HTML mislabeled certified.",
            "publication_receipts": {
                url: receipts[url].id for url in (WAC, AUTHORITY, ARCHIVES, DISCLAIMER)
            },
            "reuse_notice": "Publisher copyright/sale notice retained; local research only, no blanket redistribution license.",
        },
    )
    for title, url, _ in titles:
        s.inventory(
            "wa-wac",
            "title:" + title,
            url,
            "inventoried" if title in {"197", "365"} else "outside_scope",
        )
    selected = CHAPTERS[:limit] if limit else CHAPTERS
    new_documents = 0
    for chapter, url, heading in selected_inventory:
        whole = url + "&full=true"
        old = s.db.execute(
            "SELECT status FROM inventories WHERE collection_id='wa-wac' AND item=?",
            ("chapter:" + chapter,),
        ).fetchone()
        if not old:
            s.inventory(
                "wa-wac",
                "chapter:" + chapter,
                whole,
                "pending" if chapter in CHAPTERS else "outside_scope",
            )
        if chapter not in selected:
            continue
        try:
            data = fetch(whole)
            units = wac_chapter(data, chapter, whole)
        except (AcquisitionError, ValueError) as exc:
            s.inventory("wa-wac", "chapter:" + chapter, whole, "failed", str(exc))
            raise
        new_documents += ingest(
            "wa-wac",
            "wa-wac:chapter:" + chapter,
            "WAC " + chapter + " — " + heading,
            whole,
            units,
            {
                "chapter": chapter,
                "publisher_compilation_updated": currency,
                "section_count": len(units) - 1,
                "inventory_acquisition": receipts[WAC + "?cite=" + chapter.split("-")[0]].id,
            },
        )
        s.inventory("wa-wac", "chapter:" + chapter, whole, "ingested")
        print(f"WAC {chapter}: {len(units) - 1} section bodies + chapter notes", flush=True)

    # Missing SEPA authority only. The accepted RCW collection/history is not refreshed.
    if not s.db.execute("SELECT 1 FROM collections WHERE id='wa-rcw'").fetchone():
        s.collection(
            "wa-rcw",
            ("us-wa", "Washington", "state", "us"),
            name="Revised Code of Washington — selected chapters",
            authority="Washington State Legislature / Code Reviser",
            kind="statute",
            homepage="https://app.leg.wa.gov/RCW/default.aspx",
            source_status="Official publisher's live compilation, not certified archive",
            access="Public HTTPS; publisher robots checked",
            metadata={"scope": "SEPA authority chapter 43.21C only; not all RCW"},
        )
    rcw_url = "https://app.leg.wa.gov/RCW/default.aspx?cite=43.21C&full=true"
    rcw_data = fetch(rcw_url)
    new_documents += ingest(
        "wa-rcw",
        "wa-rcw:chapter:43.21C",
        "RCW 43.21C — State environmental policy",
        rcw_url,
        rcw_chapter_units(rcw_data, "43.21C", rcw_url),
        {
            "chapter": "43.21C",
            "scope": "Supplemental complete chapter required by acquired SEPA rules; not all Title 43",
        },
    )
    s.inventory("wa-rcw", "chapter:43.21C", rcw_url, "ingested")
    current = s.db.execute("SELECT metadata FROM collections WHERE id='wa-rcw'").fetchone()[0]
    meta = json.loads(current)
    meta["supplemental_land_use_chapters"] = ["43.21C"]
    with s.db:
        s.db.execute(
            "UPDATE collections SET metadata=? WHERE id='wa-rcw'",
            (json.dumps(meta, sort_keys=True, separators=(",", ":")),),
        )

    # Tables select filing URLs, not inferred filename joins. Keep their real printed years.
    fetch(REGISTER)
    table_data = fetch(TABLE)
    legend_data = fetch(LEGEND)
    legend = action_legend(legend_data)
    s.collection(
        "wa-wsr",
        ("us-wa", "Washington", "state", "us"),
        name="Washington Register — selected GMA filings and source indexes",
        authority="Washington State Code Reviser",
        kind="rulemaking_evidence",
        homepage=REGISTER,
        source_status="Official filings and separate index/legend evidence; not current codification",
        access="Public retained HTTPS, no amendment application",
        metadata={
            "scope": "One GMA proposal/final pair and one brief-adjudication final filing; limited publisher indexes, not all Register filings.",
            "selected_filings": ["25-13-090", "26-01-181", "26-10-066"],
            "table_root_receipt": receipts[TABLE].id,
            "table_warning": "Followed title-link targets/printed years retained even when different from parent index year. Index does not establish operative law.",
            "legend_receipt": receipts[LEGEND].id,
        },
    )
    available: dict[str, str] = {}
    for title in ("197", "365"):
        links = {
            official_link(urljoin(TABLE, n.get("href", "")))
            for n in page(table_data).xpath("//a[@href]")
            if readable(n).strip() == "Title " + title
        }
        if len(links) != 1:
            raise ValueError("Ambiguous affected-title link")
        url = links.pop()
        match = re.fullmatch(
            r"/law/wsr/(20\d{2})/tbl(\d{2})-" + title + r"\.htm", urlsplit(url).path
        )
        if not match or match[1][-2:] != match[2] or urlsplit(url).query:
            raise ValueError("Unsupported affected-title URL; no guessed source traversal")
        data = fetch(url)
        unit = table_unit(data, title, url, legend)
        new_documents += ingest("wa-wsr", unit.key, unit.citation, url, [unit], unit.metadata)
        available.update(filing_links(data, url))
    for url, key, title in [
        (TABLE, "wa-wsr:table-root:2026", "2026 affected-section index scope"),
        (LEGEND, "wa-wsr:action-legend", "Official WAC-to-Register action legend"),
    ]:
        node = page(fetch(url))
        for hidden in node.xpath("//style|//script|//head"):
            hidden.drop_tree()
        unit = Provision(
            key,
            title,
            title,
            readable(node),
            markup(node),
            url,
            unit_kind="filing_index",
            metadata={"scope": "Publisher navigation/legend, not rule text"},
            references=source_links(node, url),
        )
        new_documents += ingest("wa-wsr", key, title, url, [unit])
    issues = fetch(ISSUES)
    issue_url = "https://lawfilesext.leg.wa.gov/law/wsr/2026/10/26-10.htm"
    if issue_url not in {
        official_link(urljoin(ISSUES, n.get("href", "")))
        for n in page(issues).xpath("//a[@href]")
        if "2026/10/26-10.htm" in n.get("href", "")
    }:
        raise ValueError("Requested issue missing from official index")
    available.update(filing_links(fetch(issue_url), issue_url))
    for number in ("25-13-090", "26-01-181", "26-10-066"):
        if number not in available:
            raise ValueError("Selected filing no longer discoverable in retained official indexes")
        url = available[number]
        unit = filing_unit(fetch(url), number, url)
        new_documents += ingest("wa-wsr", unit.key, unit.citation, url, [unit], unit.metadata)
        s.inventory("wa-wsr", "filing:" + number, url, "ingested")

    # One agency notice, not a guidance crawl or a correction to codified law.
    agency = page(fetch(AGENCY))
    mains = agency.xpath("//main")
    if len(mains) != 1 or "365-199-100" not in readable(mains[0]):
        raise ValueError("Agency notice layout/scope changed")
    node = mains[0]
    s.collection(
        "wa-rulemaking-notices",
        ("us-wa", "Washington", "state", "us"),
        name="Washington Commerce — rulemaking-status notice",
        authority="Washington Department of Commerce",
        kind="agency_guidance",
        homepage=AGENCY,
        source_status="Agency characterization, not codified rules or an official filing",
        access="One public HTTPS observation; linked Box/video materials not acquired",
        metadata={
            "scope": "One agency overview notice only. Pending rulemaking and claims of obsolete rules do not establish a change in legal effect.",
            "currency": "Page observation only; statements and their dates retained verbatim.",
        },
    )
    unit = Provision(
        "wa-commerce:rulemaking-notice",
        "Commerce rulemaking notice",
        "Agency rulemaking status — separate from codification and filings",
        readable(node),
        markup(node),
        AGENCY,
        unit_kind="publisher_notice",
        metadata={
            "scope": "Agency guidance/notice, not operative law; Box and other external dependencies unacquired",
            "media": media_links(node, AGENCY),
        },
        references=source_links(node, AGENCY),
    )
    new_documents += ingest("wa-rulemaking-notices", unit.key, unit.citation, AGENCY, [unit])
    return {
        "new_documents": new_documents,
        "downloaded_this_run": a.downloaded,
        "receipts": [
            {
                "id": r.id,
                "url": r.url,
                "sha256": r.sha256,
                "bytes": r.size,
                "observed_at": r.observed_at,
            }
            for r in receipts.values()
        ],
    }
