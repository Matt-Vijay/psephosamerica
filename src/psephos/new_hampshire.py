"""New Hampshire's native RSA chapter exports, without inferred legal dates."""

from __future__ import annotations

import re
import shutil
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from lxml import etree, html

from .acquire import Acquirer, AcquisitionError, Receipt
from .campaign import CampaignAcquirer
from .parse import markup, media_links, readable, source_links
from .store import Provision, Store

BASE = "https://gc.nh.gov"
HOME = BASE + "/rsa/html/indexes/default.aspx"
TOC = BASE + "/rsa/html/nhtoc.htm"
COLLECTION = "nh-rsa"
PARSER = "nh-rsa-mrg-2"
CURRENCY = "These RSAs are current through the 2025 regular legislative session, or December 2025."


class NewHampshireAcquirer(CampaignAcquirer):
    automatic_retries = False
    consumed_field = "newly_decoded_bytes"

    @classmethod
    def budget_file(cls, store: Store, directory: Path) -> Path:
        original = store.root / "collectors/nh-northeast/budget.json"
        return original if original.exists() else super().budget_file(store, directory)

    def __init__(self, store: Store, directory: Path):
        super().__init__(store, directory, cap=180 * 1024**2, file_cap=5_000_000, delay=11.1)

    def _pause(self, url: str, delay: float | None = None) -> None:
        if urlsplit(url).netloc != "gc.nh.gov":
            raise AcquisitionError("New Hampshire continuation accepts only the reviewed host")
        if shutil.disk_usage(self.store.root).free < 100 * 1024**3:
            raise AcquisitionError("Original New Hampshire 100 GiB disk floor reached")
        if self.max_bytes - self.downloaded < 5_000_000:
            raise AcquisitionError("Original New Hampshire 5 MB safety stop reached")
        super()._pause(url, delay)

    def fetch(self, url: str, **options: Any) -> Receipt:
        if urlsplit(url).scheme != "https" or urlsplit(url).netloc != "gc.nh.gov":
            raise AcquisitionError("New Hampshire continuation accepts only the reviewed host")
        if self.store.db.execute(
            "SELECT 1 FROM acquisitions WHERE (url=? OR final_url=?) AND status IN (401,403,407,451) LIMIT 1",
            (url, url),
        ).fetchone():
            raise AcquisitionError("Retained access denial; review publisher/runtime permissions")
        options["max_file_bytes"] = min(options.get("max_file_bytes", self.file_cap), self.file_cap)
        return super().fetch(url, **options)


def inventory(data: bytes, title: str | None = None) -> list[tuple[str, str, str]]:
    root = html.fromstring(data)
    pattern = (
        r"/rsa/html/NHTOC/NHTOC-([IVXLCDM]+(?:-A)?)\.htm"
        if title is None
        else r"/rsa/html/NHTOC/NHTOC-" + re.escape(title) + r"-(\d+[A-Z]?(?:-[A-Z])?)\.htm"
    )
    items = []
    for node in root.xpath("//a[@href]"):
        url = urljoin(TOC if title is None else BASE + "/rsa/html/NHTOC/", node.get("href"))
        parts = urlsplit(url)
        label = " ".join(node.text_content().split())
        match = re.fullmatch(pattern, parts.path, re.I)
        if not match:
            if label.upper().startswith("TITLE " if title is None else "CHAPTER "):
                raise ValueError("Unrecognized New Hampshire inventory link")
            continue
        if parts.scheme != "https" or parts.netloc != "gc.nh.gov" or parts.query or parts.fragment:
            raise ValueError("Unexpected New Hampshire inventory link")
        number = match[1].upper()
        expected = ("TITLE " if title is None else "CHAPTER ") + number + ":"
        if not label.upper().startswith(expected):
            raise ValueError("New Hampshire inventory label differs from linked identity")
        items.append((number, label, url))
    if not items or len({n for n, _, _ in items}) != len(items):
        raise ValueError("Empty or duplicate New Hampshire inventory")
    return items


def parse_chapter(data: bytes, url: str, title: str, chapter: str) -> list[Provision]:
    root = html.fromstring(data)
    body = root.find("body")
    identity = r"Chapter\s+" + re.escape(chapter) + r"(?:\s|:|$)"
    if body is None or not any(
        re.match(identity, readable(node), re.I) for node in root.xpath("//h1 | //h2")
    ):
        raise ValueError("Response is not the requested RSA chapter")
    if not root.xpath("//codesect") and not re.search(r"repealed|reserved", readable(body), re.I):
        raise ValueError("No statute text or explicit repeal/reservation notice")
    # Native section boundaries partition all body nodes, including history and exceptions.
    groups: list[tuple[str | None, Any, list[str]]] = []
    wrapper = etree.Element("div")
    wrapper.text = body.text
    number = None
    context: list[str] = []
    group_context: list[str] = []
    for child in body:
        if not isinstance(child.tag, str):
            if child.tail:
                if len(wrapper):
                    wrapper[-1].tail = (wrapper[-1].tail or "") + child.tail
                else:
                    wrapper.text = (wrapper.text or "") + child.tail
            continue
        section = (
            child.xpath("./h3")
            if child.tag.lower() == "center"
            else ([child] if child.tag.lower() == "h3" else [])
        )
        match = re.fullmatch(r"Section\s+(\S+)", readable(section[0])) if section else None
        scopes = child.xpath("./h1|./h2") if child.tag.lower() == "center" else []
        if match or scopes:
            if readable(wrapper):
                groups.append((number, wrapper, group_context))
            wrapper = etree.Element("div")
            number = match.group(1) if match else None
            if scopes:
                context = (
                    context[:2] + [readable(scopes[0])]
                    if len(context) >= 2
                    else context + [readable(scopes[0])]
                )
            group_context = list(context)
        wrapper.append(deepcopy(child))
    if readable(wrapper):
        groups.append((number, wrapper, group_context))
    totals = Counter(n for n, _, _ in groups if n)
    seen: Counter[str] = Counter()
    units = []
    for ordinal, (num, node, hierarchy) in enumerate(groups):
        if num and not num.startswith(chapter + ":"):
            raise ValueError(f"Unexpected section identity {num} in chapter {chapter}")
        if num:
            seen[num] += 1
            suffix = f"/_occurrence/{seen[num]}" if totals[num] > 1 else ""
            key = f"nh-rsa:{num}{suffix}"
            citation = f"RSA {num}"
            b = node.xpath("./b")
            heading = readable(b[0]) if b else "Section " + num
            kind = "section"
        else:
            key = f"nh-rsa:chapter-{chapter}/_scope/{ordinal}"
            citation = f"RSA chapter {chapter} \u2014 scope {ordinal + 1}"
            heading = readable(node).splitlines()[0]
            kind = "chapter_notice" if not root.xpath("//codesect") else "scope_heading"
        units.append(
            Provision(
                key=key,
                citation=citation,
                heading=heading,
                text=readable(node),
                markup=markup(node),
                url=url,
                parent_key=f"nh-rsa:chapter-{chapter}",
                unit_kind=kind,
                metadata={
                    "title": title,
                    "chapter": chapter,
                    "source_number": num,
                    "hierarchy": hierarchy,
                    "source_notes": [readable(n) for n in node.xpath(".//sourcenote")],
                    "media": media_links(node, url),
                    "tables": len(node.xpath(".//table")),
                    "duplicate_identifier_count": totals[num] if num else 0,
                },
                references=source_links(node, url),
            )
        )
    if re.sub(r"\s+", "", readable(body)) != re.sub(r"\s+", "", "".join(p.text for p in units)):
        raise ValueError("Chapter projection loses or changes source body text")
    return units


def title_notice(data: bytes, url: str, title: str) -> Provision | None:
    root = html.fromstring(data)
    if [readable(n).strip() for n in root.xpath("//codesect")] != ["Entire Title was repealed"]:
        return None
    body = root.find("body")
    headings = [" ".join(readable(n).split()) for n in root.xpath("//h1")]
    if body is None or len(headings) != 1 or not headings[0].startswith("TITLE " + title + " "):
        raise ValueError("Title repeal notice identity differs from native inventory")
    return Provision(
        key="nh-rsa:title-" + title + "/_notice",
        citation="RSA Title " + title + " - publisher repeal notice",
        heading=headings[0],
        text=readable(body),
        markup=markup(body),
        url=url,
        unit_kind="title_notice",
        metadata={
            "title": title,
            "scope": "Entire-title repeal notice, not chapter or section text",
            "date_warning": "No exact repeal date inferred from an undated publisher notice",
        },
        references=source_links(body, url),
    )


def sync_new_hampshire(
    s: Store, a: Acquirer, limit: int | None, as_of: str | None
) -> dict[str, Any]:
    if as_of is not None or (limit is not None and limit < 0):
        raise ValueError("New Hampshire needs a nonnegative limit and has no historical endpoint")
    home = a.fetch(HOME)
    if CURRENCY not in readable(html.fromstring(s.artifact(home.sha256))):
        raise ValueError("Publisher currency notice changed; inspect before proceeding")
    toc = a.fetch(TOC)
    titles = inventory(s.artifact(toc.sha256))
    s.collection(
        COLLECTION,
        ("us-nh", "New Hampshire", "state", "us"),
        name="New Hampshire Revised Statutes Online",
        authority="New Hampshire General Court",
        kind="statutes",
        homepage=HOME,
        source_status="Government-hosted informational RSA compilation; completeness is inventory-specific",
        access="Public HTTPS; robots delay 11 seconds; no blanket license asserted",
        metadata={
            "publisher_currency_statement": CURRENCY,
            "currency_receipt": home.id,
            "inventory_receipt": toc.id,
            "snapshot_date": None,
            "scope": "Native title/chapter inventory; unconsolidated amendments and other law families excluded",
        },
    )
    for title, _, url in titles:
        if not s.db.execute(
            "SELECT 1 FROM inventories WHERE collection_id=? AND item=?",
            (COLLECTION, "title:" + title),
        ).fetchone():
            s.inventory(COLLECTION, "title:" + title, url, "pending")
    attempted = accepted = 0
    for title, name, title_url in titles:
        if limit is not None and attempted >= limit:
            return {"titles": len(titles), "attempted": attempted, "accepted": accepted}
        inv = a.fetch(title_url)
        raw = s.artifact(inv.sha256)
        notice = title_notice(raw, title_url, title)
        if notice is not None:
            document = "nh-rsa:title-" + title
            if (
                a.refresh
                or not s.db.execute(
                    "SELECT 1 FROM versions WHERE document_id=?", (document,)
                ).fetchone()
            ):
                s.ingest(
                    collection=COLLECTION,
                    document=document,
                    title=name,
                    url=title_url,
                    acquisition=inv.id,
                    parser=PARSER,
                    provisions=[notice],
                    snapshot_basis="Undated native repeal notice; no legal repeal date inferred",
                    metadata={
                        "title": title,
                        "scope": "entire_title_repeal_notice",
                        "currency_receipt": home.id,
                        "inventory_receipt": toc.id,
                    },
                )
                attempted += 1
                accepted += 1
            s.inventory(COLLECTION, "title:" + title, title_url, "source_repeal_notice")
            continue
        chapters = inventory(raw, title)
        for chapter, _, _ in chapters:
            if not s.db.execute(
                "SELECT 1 FROM inventories WHERE collection_id=? AND item=?",
                (COLLECTION, "chapter:" + chapter),
            ).fetchone():
                s.inventory(
                    COLLECTION,
                    "chapter:" + chapter,
                    BASE + f"/rsa/html/{title}/{chapter}/{chapter}-mrg.htm",
                    "pending",
                )
        s.inventory(COLLECTION, "title:" + title, title_url, "partial")
        for chapter, name, index_url in chapters:
            # The retained native chapter 672 index links this whole-chapter route.
            url = BASE + f"/rsa/html/{title}/{chapter}/{chapter}-mrg.htm"
            document = "nh-rsa:chapter-" + chapter
            item = "chapter:" + chapter
            if (
                not a.refresh
                and s.db.execute(
                    "SELECT 1 FROM versions WHERE document_id=?", (document,)
                ).fetchone()
            ):
                continue
            if limit is not None and attempted >= limit:
                return {"titles": len(titles), "attempted": attempted, "accepted": accepted}
            attempted += 1
            try:
                receipt = a.fetch(url)
                units = parse_chapter(s.artifact(receipt.sha256), url, title, chapter)
                s.ingest(
                    collection=COLLECTION,
                    document=document,
                    title=name,
                    url=url,
                    acquisition=receipt.id,
                    snapshot_basis="Publisher states December 2025/2025 session only; exact snapshot/effect dates unknown",
                    parser=PARSER,
                    provisions=units,
                    metadata={
                        "title": title,
                        "chapter": chapter,
                        "currency_statement": CURRENCY,
                        "currency_receipt": home.id,
                        "inventory_receipt": inv.id,
                        "chapter_index_url": index_url,
                        "export_url_basis": "Publisher whole-chapter -mrg.htm convention, verified against retained chapter 672 native export link; requested chapter identity checked",
                        "source_text_status": "informational online compilation",
                    },
                )
                s.inventory(COLLECTION, item, url, "acquired")
                accepted += 1
                print(f"NH {chapter}: {len(units)} units", file=sys.stderr, flush=True)
            except (ValueError, AcquisitionError) as exc:
                s.inventory(COLLECTION, item, url, "failed", str(exc))
                print(f"NH {chapter}: {exc}", file=sys.stderr, flush=True)
                if isinstance(exc, AcquisitionError):
                    raise
        closed = all(
            s.db.execute(
                "SELECT status FROM inventories WHERE collection_id=? AND item=?",
                (COLLECTION, "chapter:" + ch),
            ).fetchone()[0]
            == "acquired"
            for ch, _, _ in chapters
        )
        s.inventory(COLLECTION, "title:" + title, title_url, "complete" if closed else "partial")
    return {"titles": len(titles), "attempted": attempted, "accepted": accepted}
