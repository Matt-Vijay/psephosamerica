"""One publisher-listed Florida statutes edition; no inferred effective-law timeline."""

from __future__ import annotations

import json
import re
import sys
import tempfile
import time
from collections import Counter
from copy import deepcopy
from typing import Any
from urllib.parse import urldefrag, urljoin

from lxml import html

from .acquire import Acquirer, AcquisitionError, Receipt
from .parse import markup, media_links, readable, source_links
from .store import Provision, Store, utc_now

BASE = "https://www.flsenate.gov"
INDEX = BASE + "/Laws/Statutes"
COLLECTION = "florida-statutes-2026"
EDITION = 2026


def edition_page(data: bytes) -> Any:
    # Retained Senate pages end with these tags; lxml otherwise repairs truncation.
    if not re.search(rb"</body>\s*</html>\s*$", data, re.I):
        raise ValueError("Incomplete publisher HTML document")
    page = html.fromstring(data)
    editions = [
        readable(n).strip() for n in page.xpath("//h2") if "Florida Statutes" in readable(n)
    ]
    if editions != [f"{EDITION} Florida Statutes"]:
        raise ValueError(
            f"Expected explicit {EDITION} statute body/index edition; observed {editions}"
        )
    return page


def title_inventory(data: bytes) -> list[dict[str, Any]]:
    page = edition_page(data)
    containers = page.xpath('//div[@class="statutesTOC"]/ol')
    if len(containers) != 1:
        raise ValueError("Missing/ambiguous publisher title inventory")
    entries = []
    for row in containers[0].xpath("./li"):
        links = row.xpath("./a")
        if len(links) != 1:
            raise ValueError("Missing/ambiguous native title link")
        link = links[0]
        target = link.get("href", "")
        match = re.fullmatch(r"/Laws/Statutes/2026/Title(\d+)/#Title(\d+)", target)
        if not match or match[1] != match[2] or link.xpath("./span/@id") != ["Title" + match[1]]:
            raise ValueError("Title link/anchor identity mismatch")
        entries.append(
            {
                "number": int(match[1]),
                "url": urldefrag(urljoin(BASE, target))[0],
                "label": readable(link),
            }
        )
    if not entries or len({e["number"] for e in entries}) != len(entries):
        raise ValueError("Empty or duplicate publisher title membership")
    return entries


def chapter_inventory(data: bytes, title: int) -> list[dict[str, str]]:
    page = edition_page(data)
    groups = page.xpath(f'//li[a/span[@id="Title{title}"]]/ol[@class="chapter"]')
    if len(groups) != 1:
        raise ValueError("Missing/ambiguous expanded title chapter list")
    entries = []
    for row in groups[0].xpath("./li"):
        links = row.xpath("./a")
        if len(links) != 1:
            raise ValueError("Missing/ambiguous native chapter link")
        link = links[0]
        match = re.fullmatch(r"/Laws/Statutes/2026/Chapter(\d+[A-Z]?)", link.get("href", ""))
        if not match:
            raise ValueError(
                "Unsupported chapter entry; do not expand numeric ranges or part links"
            )
        entries.append(
            {
                "number": match[1],
                "url": urljoin(BASE, link.get("href")) + "/All",
                "label": readable(link),
            }
        )
    if not entries or len({e["number"] for e in entries}) != len(entries):
        raise ValueError("Empty or duplicate publisher chapter membership")
    return entries


def chapter_units(data: bytes, chapter: str, url: str) -> list[Provision]:
    """Preserve whole Section nodes, including notes/reversions; indexes stay context."""
    page = edition_page(data)
    containers = page.xpath('//div[@class="Chapter"]')
    if len(containers) != 1:
        raise ValueError("Expected one complete publisher chapter container")
    container = containers[0]
    identity = container.xpath('./div[@class="ChapterTitle"]/div[@class="ChapterNumber"]')
    if len(identity) != 1 or readable(identity[0]).strip() != "CHAPTER " + chapter:
        raise ValueError("Chapter body identity differs from inventory")
    sections = container.xpath('.//div[@class="Section"]')
    numbers = [
        readable(n.xpath('./span[@class="SectionNumber"]')[0]).strip()
        if n.xpath('./span[@class="SectionNumber"]')
        else ""
        for n in sections
    ]
    if not numbers or any(
        not re.fullmatch(re.escape(chapter) + r"\.\d+[A-Z]?", n) for n in numbers
    ):
        raise ValueError("Missing or out-of-chapter native section identity")
    index = [
        readable(n).strip()
        for n in container.xpath(
            './/div[@class="CatchlineIndex"]//div[@class="IndexItem"]/span[@class="SectionNumber"]'
        )
    ]
    if Counter(index) != Counter(numbers):
        raise ValueError("Chapter catchline index/body membership differs")
    totals = Counter(numbers)
    seen: Counter[str] = Counter()
    document = "fl:chapter/" + chapter
    result = []
    for node, number in zip(sections, numbers, strict=True):
        bodies = node.xpath('./span[@class="SectionBody"]')
        if len(bodies) != 1 or not readable(bodies[0]).strip():
            raise ValueError("Empty statutory section body; review disposition/index separately")
        seen[number] += 1
        key = "fl:stat/" + number
        if totals[number] > 1:
            key += "/occurrence/" + str(seen[number])
        anchors = node.xpath('./span[@class="SectionNumber"]//a/@name')
        if len(anchors) > 1:
            raise ValueError("Ambiguous native section anchor")
        anchor = anchors[0] if anchors else None
        hierarchy = [
            readable(n)
            for a in reversed(list(node.iterancestors()))
            for n in a.xpath('./div[@class="Title" or @class="ChapterTitle" or @class="PartTitle"]')
        ]
        result.append(
            Provision(
                key=key,
                citation=f"Fla. Stat. § {number} ({EDITION})",
                heading="".join(
                    node.xpath('./span[@class="Catchline"]/span[@class="CatchlineText"]//text()')
                ).strip(),
                text=readable(node),
                markup=markup(node),
                url=url + ("#" + anchor if anchor else ""),
                parent_key=document,
                metadata={
                    "source_number": number,
                    "source_anchor": anchor,
                    "edition_year": EDITION,
                    "hierarchy": hierarchy,
                    "duplicate_number_count": totals[number],
                    "tables": len(node.xpath(".//table")),
                    "media": media_links(node, url),
                    "clock_note": "History, footnotes and future/conditional replacement text retained verbatim. Edition year is not a snapshot or legal-effect date.",
                },
                references=source_links(node, url),
            )
        )
    context = deepcopy(container)
    for node in context.xpath('.//div[@class="Section"]'):
        node.drop_tree()
    if readable(context):
        result.append(
            Provision(
                key=document + "/_context",
                citation=document + " — publisher context",
                heading="Publisher hierarchy, catchline indexes and scope notes",
                text=readable(context),
                markup=markup(context),
                url=url,
                parent_key=document,
                unit_kind="scope_notes",
                metadata={"source_sections": len(sections), "media": media_links(context, url)},
                references=source_links(context, url),
            )
        )
    return result


def inventory_edition(s: Store, a: Acquirer) -> dict[str, Any]:
    """Receipt every actual title index before choosing a chapter acquisition budget."""
    receipts: list[Receipt] = []

    def fetch(url: str) -> bytes:
        receipt = a.fetch(url, max_file_bytes=2 * 1024**2)
        receipts.append(receipt)
        return s.artifact(receipt.sha256)

    root = fetch(INDEX)
    titles = title_inventory(root)
    seen: set[str] = set()
    for title in titles:
        title["chapters"] = chapter_inventory(fetch(title["url"]), title["number"])
        title["inventory_acquisition"] = receipts[-1].id
        for chapter in title["chapters"]:
            if chapter["number"] in seen:
                raise ValueError("Chapter appears in multiple native titles")
            seen.add(chapter["number"])
        print(
            f"Florida title {title['number']}: {len(title['chapters'])} native chapters",
            file=sys.stderr,
            flush=True,
        )
    return {
        "edition_year": EDITION,
        "titles": titles,
        "chapter_count": len(seen),
        "receipts": [vars(r) for r in receipts],
        "downloaded_bytes": a.downloaded,
    }


def _scope(s: Store, plan: dict[str, Any]) -> dict[str, Any]:
    states = {
        r["item"]: r["status"]
        for r in s.db.execute(
            "SELECT item,status FROM inventories WHERE collection_id=?", (COLLECTION,)
        )
    }
    acquired = {
        r[0]: r[1]
        for r in s.db.execute(
            "SELECT d.id,d.url FROM documents d WHERE d.collection_id=? AND EXISTS "
            "(SELECT 1 FROM versions v WHERE v.document_id=d.id)",
            (COLLECTION,),
        )
    }
    for title in plan["titles"]:
        for chapter in title["chapters"]:
            item = "chapter-" + chapter["number"]
            if (
                states.get(item) == "indexed"
                and acquired.get("fl:chapter/" + chapter["number"]) != chapter["url"]
            ):
                states[item] = "failed"
                s.inventory(
                    COLLECTION,
                    item,
                    chapter["url"],
                    "failed",
                    "Indexed inventory lacks its exact acquired chapter document",
                )
    accepted_titles = []
    for title in plan["titles"]:
        complete = all(states.get("chapter-" + c["number"]) == "indexed" for c in title["chapters"])
        s.inventory(
            COLLECTION,
            "title-" + str(title["number"]),
            title["url"],
            "indexed" if complete else "pending",
        )
        if complete:
            accepted_titles.append(title["number"])
    missing = [
        {"chapter": c["number"], "status": states.get("chapter-" + c["number"], "pending")}
        for t in plan["titles"]
        for c in t["chapters"]
        if states.get("chapter-" + c["number"]) != "indexed"
    ]
    review = {
        "scope": "All chapters actually listed by the retained 2026 Florida Senate title indexes; not administrative/local law or a complete legal history.",
        "edition_year": EDITION,
        "titles_total": len(plan["titles"]),
        "titles_complete": len(accepted_titles),
        "chapters_total": plan["chapter_count"],
        "chapters_indexed": plan["chapter_count"] - len(missing),
        "missing_chapter_count": len(missing),
        "missing_chapter_sample": missing[:5],
        "missing_inventory": "Use legal_coverage(collection='florida-statutes-2026', view='inventory', status='pending' or 'failed'); paginate for all entries.",
        "reviewed_at": utc_now(),
        "clock_policy": "2026 is an annual edition label. Snapshot/publication/legal-effective dates unknown; future-effective and conditional alternatives remain in source notes.",
        "gaps": [
            "Administrative regulations, session laws, local ordinances and case law are outside this edition import.",
            "Publisher auxiliary PDF indexes/tracing tables and external media are unacquired dependencies, not silently incorporated text.",
        ],
    }
    row = s.db.execute("SELECT metadata FROM collections WHERE id=?", (COLLECTION,)).fetchone()
    meta = json.loads(row[0])
    if "discovery_review" in meta and "historical_discovery_review" not in meta:
        meta["historical_discovery_review"] = meta.pop("discovery_review")
    meta["discovery_review"] = review
    with s.db:
        s.db.execute(
            "UPDATE collections SET metadata=?,name=? WHERE id=?",
            (
                json.dumps(meta, sort_keys=True, separators=(",", ":")),
                "2026 Florida Statutes — publisher-listed edition"
                + (" (partial)" if missing else ""),
                COLLECTION,
            ),
        )
    return review


def sync_florida(s: Store, a: Acquirer, limit: int | None, as_of: str | None) -> dict[str, Any]:
    """Extend the retained edition chapter by chapter; old accepted projections/IDs survive."""
    if as_of is not None or (limit is not None and limit < 1):
        raise ValueError(
            "Florida supports the pinned 2026 edition, no inferred as-of acquisition; limit must be positive"
        )
    a.delay = max(a.delay, 10.1)
    # A resumed process must not immediately follow the previous writer's request.
    a.last_request.setdefault("www.flsenate.gov", time.monotonic())
    a.max_bytes = min(a.max_bytes, 256 * 1024**2)
    started = utc_now()
    floor = s.db.execute("SELECT coalesce(max(id),0) FROM acquisitions").fetchone()[0]
    privacy = a.fetch(BASE + "/About/Privacy", max_file_bytes=2 * 1024**2)
    plan = inventory_edition(s, a)
    if not s.db.execute("SELECT 1 FROM collections WHERE id=?", (COLLECTION,)).fetchone():
        s.collection(
            COLLECTION,
            ("us-fl", "Florida", "state", "us"),
            name="2026 Florida Statutes — publisher-listed edition (partial)",
            authority="Florida Senate",
            kind="statutory_code",
            homepage=INDEX,
            source_status="Official publisher HTML; exact incorporation date unknown",
            access="Public native whole-chapter HTML; >=10.1 seconds per host; policy receipts retained, no blanket redistribution license",
            metadata={"edition_year": EDITION, "exact_date_unknown": True},
        )
    for title in plan["titles"]:
        for chapter in title["chapters"]:
            old = s.db.execute(
                "SELECT 1 FROM inventories WHERE collection_id=? AND item=?",
                (COLLECTION, "chapter-" + chapter["number"]),
            ).fetchone()
            if not old:
                s.inventory(COLLECTION, "chapter-" + chapter["number"], chapter["url"], "pending")
    _scope(s, plan)
    new, reused, attempted, failed, chapter_receipts = 0, 0, 0, [], []
    output = s.root / "florida-edition"
    output.mkdir(exist_ok=True)
    (output / "inventory-plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    try:
        for title in plan["titles"]:
            for chapter in title["chapters"]:
                number, url = chapter["number"], chapter["url"]
                document = "fl:chapter/" + number
                if limit is not None and attempted >= limit:
                    break
                counted_attempt = False
                try:
                    receipt = a.fetch(url, max_file_bytes=8 * 1024**2)
                    chapter_receipts.append(vars(receipt))
                    raw = s.artifact(receipt.sha256)
                    edition_page(raw)
                    existing = s.db.execute(
                        "SELECT id FROM versions WHERE document_id=? AND artifact_sha=? AND parser IN ('fl-html-1/text-2','florida-edition-1/text-3')",
                        (document, receipt.sha256),
                    ).fetchone()
                    if existing:
                        reused += 1
                    else:
                        attempted += 1
                        counted_attempt = True
                        units = chapter_units(raw, number, url)
                        _, _, added = s.ingest(
                            collection=COLLECTION,
                            document=document,
                            title=chapter["label"],
                            url=url,
                            acquisition=receipt.id,
                            snapshot_basis="Publisher 2026 annual edition; exact incorporation/publication/effectiveness unknown",
                            parser="florida-edition-1",
                            provisions=units,
                            metadata={
                                "edition_year": EDITION,
                                "title_number": title["number"],
                                "title_inventory_acquisition": title["inventory_acquisition"],
                                "native_section_count": len(units) - 1,
                                "scope": "Complete retained chapter with separately labeled index/context. Notes and future/conditional alternatives not applied.",
                            },
                        )
                        new += added
                    s.inventory(COLLECTION, "chapter-" + number, url, "indexed")
                    print(
                        f"Florida chapter {number}: {'reused accepted projection' if existing else str(len(units) - 1) + ' section nodes'}",
                        file=sys.stderr,
                        flush=True,
                    )
                except AcquisitionError as exc:
                    s.inventory(COLLECTION, "chapter-" + number, url, "failed", str(exc))
                    failed.append({"chapter": number, "reason": str(exc)})
                    raise
                except ValueError as exc:
                    if not counted_attempt:
                        attempted += 1
                    s.inventory(COLLECTION, "chapter-" + number, url, "failed", str(exc))
                    failed.append({"chapter": number, "reason": str(exc)})
                    print(f"Florida chapter {number}: REJECTED {exc}", file=sys.stderr, flush=True)
            _scope(s, plan)
            if limit is not None and attempted >= limit:
                break
    finally:
        report = {
            "started_at": started,
            "finished_at": utc_now(),
            "pre_acquisition_id": floor,
            "cap_bytes": a.max_bytes,
            "max_chapter_bytes": 8 * 1024**2,
            "minimum_host_seconds": a.delay,
            "new_versions": new,
            "reused_versions": reused,
            "rejections": failed,
            "downloaded_bytes": a.downloaded,
            "scope": _scope(s, plan),
            "privacy_receipt": vars(privacy),
            "index_receipts": plan["receipts"],
            "chapter_receipts": chapter_receipts,
        }
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=output,
            prefix="run-" + started.replace(":", "-") + "-",
            suffix=".json",
            delete=False,
        ) as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
        (output / "latest-run.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
