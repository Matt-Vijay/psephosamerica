"""Minnesota's publisher statute HTML, with native chapter and section inventories."""

from __future__ import annotations

import re
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

BASE = "https://www.revisor.mn.gov"
INDEX = BASE + "/statutes/"
COLLECTION = "mn-statutes"


class MinnesotaAcquirer(CampaignAcquirer):
    automatic_retries = False
    consumed_field = "decoded_bytes"

    @classmethod
    def budget_file(cls, store: Store, directory: Path) -> Path:
        original = store.root / "collectors/mn/budget.json"
        return original if original.exists() else super().budget_file(store, directory)

    def __init__(self, store: Store, directory: Path):
        super().__init__(store, directory, cap=200 * 1024**2, file_cap=25 * 1024**2, delay=1.1)

    def fetch(self, url: str, **options: Any) -> Receipt:
        if urlsplit(url).hostname != "www.revisor.mn.gov":
            raise AcquisitionError(
                "Minnesota continuation only accepts the reviewed publisher host"
            )
        previous = self.store.db.execute(
            "SELECT status,error FROM acquisitions WHERE url=? OR final_url=? ORDER BY id DESC LIMIT 1",
            (url, url),
        ).fetchone()
        if previous and (
            previous[0] in (401, 403, 407, 451)
            or (previous[0] == 0 and re.match(r"^(?:HTTP )?(401|403|407|451)\b", previous[1] or ""))
        ):
            raise AcquisitionError("Retained access denial; review publisher/runtime permissions")
        options["max_file_bytes"] = min(options.get("max_file_bytes", self.file_cap), self.file_cap)
        return super().fetch(url, **options)


def publisher_page(data: bytes) -> tuple[etree._Element, str]:
    root = html.fromstring(data)
    if len(root.xpath("//main")) != 1:
        raise ValueError("Missing unique publisher main container")
    labels = {
        readable(h).strip()
        for h in root.xpath("//main//h1")
        if re.fullmatch(r"\d{4} Minnesota Statutes", readable(h).strip())
    }
    if len(labels) != 1:
        raise ValueError("Missing unique publisher edition label")
    return root, labels.pop()


def chapter_units(data: bytes, chapter: str, url: str) -> list[Provision]:
    root, _ = publisher_page(data)
    containers = root.xpath('//*[@id="xtend"]')
    if len(containers) != 1:
        raise ValueError("No unique legal text container")
    heads = containers[0].xpath('.//h2[@class="chapter_title" or @class="chapter_no"]')
    if len(heads) != 1 or not re.search(
        r"CHAPTER\s+" + re.escape(chapter) + r"[.,\s]", readable(heads[0])
    ):
        raise ValueError("Chapter identity mismatch")
    container = heads[0].getparent()
    groups = []
    key, kind, node = "_context", "scope_notes", etree.Element("div")
    node.text = container.text
    for child in container:
        if child.get("id") == "chapter_analysis":
            if child.tail:
                etree.SubElement(node, "p").text = child.tail
            continue
        if child.get("class") in {"section", "sr", "sr_by_subd"}:
            if readable(node):
                groups.append((key, kind, node))
            key = child.get("id", "").strip()
            kind = "disposition" if child.get("class") in {"sr", "sr_by_subd"} else "section"
            if not key or not key.startswith(f"stat.{chapter}."):
                raise ValueError("Missing or wrong-chapter native section identifier")
            node = etree.Element("div")
        node.append(deepcopy(child))
    if readable(node):
        groups.append((key, kind, node))
    expected = set(container.xpath('.//*[@id="chapter_analysis"]//a[starts-with(@href,"#")]/@href'))
    actual = {"#" + key for key, _, _ in groups if key != "_context"}
    if not actual or actual != expected:
        raise ValueError(
            f"Section inventory mismatch: missing={expected - actual}; extra={actual - expected}"
        )
    totals = Counter(g[0] for g in groups)
    seen: Counter[str] = Counter()
    result = []
    for key, kind, node in groups:
        seen[key] += 1
        suffix = f"/occurrence/{seen[key]}" if totals[key] > 1 else ""
        heading = node.xpath("./div/h1 | ./h1 | ./h2")
        result.append(
            Provision(
                key=f"mn:statutes/{chapter}/{key}" + suffix,
                citation="Minn. Stat. \u00a7 " + key.removeprefix("stat.")
                if key != "_context"
                else f"Minnesota statutes chapter {chapter} \u2014 context",
                heading=readable(heading[0]) if heading else readable(node).splitlines()[0],
                text=readable(node),
                markup=markup(node),
                url=url + "#" + key if key != "_context" else url,
                parent_key=f"mn:statutes/{chapter}",
                unit_kind=kind,
                metadata={
                    "native_id": key,
                    "native_id_occurrences": totals[key],
                    "occurrence": seen[key],
                    "chapter": chapter,
                    "tables": len(node.xpath(".//table")),
                    "media": media_links(node, url),
                    "date_warning": "History, amendment and future-effect notes preserved; no blanket effective date inferred.",
                },
                references=source_links(node, url),
            )
        )
    return result


def sync_minnesota(s: Store, a: Acquirer, limit: int | None, as_of: str | None) -> dict[str, Any]:
    if as_of is not None:
        raise ValueError("Live Minnesota exports do not establish historical legal effectiveness")
    if limit is not None and limit < 0:
        raise ValueError("limit must be nonnegative")
    index = a.fetch(INDEX)
    about = a.fetch(BASE + "/statutes/info")
    root, edition = publisher_page(s.artifact(index.sha256))
    notices = html.fromstring(s.artifact(about.sha256)).xpath("//main")
    if len(notices) != 1 or "Official Versions of Minnesota Statutes" not in readable(notices[0]):
        raise ValueError("Publisher official-version notice changed; review before acquisition")
    parts = []
    for row in root.xpath('//*[@id="toc_table"]/tbody/tr'):
        cells, links = row.xpath("./td"), row.xpath(".//a[@href]")
        if len(cells) == 2 and len(links) == 1:
            url = urljoin(BASE, links[0].get("href"))
            if not url.startswith(BASE + "/statutes/part/"):
                raise ValueError("Unexpected publisher part URL")
            parts.append({"name": readable(cells[1]), "range": readable(cells[0]), "url": url})
    if not parts or len({p["name"] for p in parts}) != len(parts):
        raise ValueError("Empty or duplicate statute part inventory")
    s.collection(
        COLLECTION,
        ("us-mn", "Minnesota", "state", "us"),
        name="Minnesota Statutes",
        authority="Minnesota Office of the Revisor of Statutes",
        kind="statutory_code",
        homepage=INDEX,
        source_status="Government-published HTML; publisher designates authenticated PDFs and printed volumes as official records, not this HTML projection",
        access="Public chapter HTML; robots honored. No blanket redistribution permission or PDF cryptographic verification asserted.",
        metadata={
            "inventory_sha256": index.sha256,
            "about_sha256": about.sha256,
            "edition_label": edition,
            "scope": "All publisher-listed statute subject parts. Consult inventory statuses for actual acquired bodies. Constitution and rules are separate retained families; session laws, special/local laws, later consolidation and external incorporated material are excluded.",
            "companion_policy": "Original PDF companions retained; new HTML collection records publisher PDF links without downloading or authenticating them.",
        },
    )
    chapters = {}
    membership = {}
    for part in parts:
        receipt = a.fetch(part["url"])
        page, label = publisher_page(s.artifact(receipt.sha256))
        if label != edition:
            raise ValueError("Part inventory edition differs from retained master")
        members = {}
        for link in page.xpath('//*[@id="chapters_table"]//a[@href]'):
            chapter = readable(link).strip()
            url = urljoin(BASE, link.get("href"))
            if not re.fullmatch(r"\d+[A-Z]?", chapter) or url != BASE + "/statutes/cite/" + chapter:
                raise ValueError("Unexpected native chapter inventory link")
            members[chapter] = url + "/full"
        if not members:
            raise ValueError("Empty chapter inventory")
        membership[part["name"]] = members
        for chapter, url in members.items():
            chapters[chapter] = (url, part, receipt.id)
            if not s.db.execute(
                "SELECT 1 FROM inventories WHERE collection_id=? AND item=?",
                (COLLECTION, "chapter:" + chapter),
            ).fetchone():
                s.inventory(COLLECTION, "chapter:" + chapter, url, "pending")
        if not s.db.execute(
            "SELECT 1 FROM inventories WHERE collection_id=? AND item=?",
            (COLLECTION, "part:" + part["name"]),
        ).fetchone():
            s.inventory(COLLECTION, "part:" + part["name"], part["url"], "pending")
    attempted = accepted = 0
    failure: AcquisitionError | None = None
    for chapter, (url, part, inventory_id) in chapters.items():
        if (
            not a.refresh
            and s.db.execute(
                "SELECT 1 FROM versions WHERE document_id=?", (f"mn:statutes/{chapter}",)
            ).fetchone()
        ):
            continue
        if limit is not None and attempted >= limit:
            break
        attempted += 1
        try:
            receipt = a.fetch(url)
            raw = s.artifact(receipt.sha256)
            page, label = publisher_page(raw)
            if label != edition:
                raise ValueError(
                    "Chapter edition differs from retained master; no mixed-edition completion"
                )
            units = chapter_units(raw, chapter, url)
            pdfs = [
                urljoin(url, x.get("href"))
                for x in page.xpath("//main//a[@href]")
                if readable(x).strip() == "PDF"
            ]
            s.ingest(
                collection=COLLECTION,
                document=f"mn:statutes/{chapter}",
                title=readable(
                    page.xpath(
                        '//*[@id="xtend"]//h2[@class="chapter_title" or @class="chapter_no"]'
                    )[0]
                ),
                url=url,
                acquisition=receipt.id,
                snapshot_basis="Publisher edition label: "
                + edition
                + "; exact snapshot/effect unknown",
                parser="mn-statutes-native-1",
                provisions=units,
                metadata={
                    "scope": part,
                    "edition_label": edition,
                    "chapter_inventory_acquisition": inventory_id,
                    "pdf_companion_links": pdfs,
                    "pdf_companion_status": "not acquired by this HTML continuation; no signature verification",
                },
            )
            s.inventory(COLLECTION, "chapter:" + chapter, url, "indexed")
            accepted += 1
            print(f"Minnesota {chapter}: {len(units)} units", file=sys.stderr, flush=True)
        except (ValueError, AcquisitionError) as exc:
            s.inventory(COLLECTION, "chapter:" + chapter, url, "failed", str(exc))
            print(f"Minnesota {chapter}: {exc}", file=sys.stderr, flush=True)
            if isinstance(exc, AcquisitionError):
                failure = exc
                break
    for part in parts:
        closed = all(
            s.db.execute(
                "SELECT status FROM inventories WHERE collection_id=? AND item=?",
                (COLLECTION, "chapter:" + chapter),
            ).fetchone()[0]
            == "indexed"
            for chapter in membership[part["name"]]
        )
        s.inventory(
            COLLECTION, "part:" + part["name"], part["url"], "indexed" if closed else "partial"
        )
    if failure is not None:
        raise failure
    return {
        "parts": len(parts),
        "chapters": len(chapters),
        "accepted_this_run": accepted,
        "attempted": attempted,
    }
