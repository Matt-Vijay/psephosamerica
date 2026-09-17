"""Idaho's native chapter PDF inventory, with page-level source projections."""

from __future__ import annotations

import re
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from lxml import html
from pypdf import PdfReader

from .acquire import Acquirer, AcquisitionError, Receipt
from .campaign import CampaignAcquirer
from .municipal import pdf_units
from .parse import readable
from .store import Provision, Reference, Store, json_text

BASE = "https://legislature.idaho.gov"
INDEX = BASE + "/statutesrules/idstat/"
CURRENCY = BASE + "/statutesrules/howcurrentisthislaw"
TERMS = BASE + "/site-disclaimer"
COLLECTION = "id-statutes"


class IdahoAcquirer(CampaignAcquirer):
    automatic_retries = False
    consumed_field = "decoded_bytes"

    @classmethod
    def budget_file(cls, store: Store, directory: Path) -> Path:
        original = store.root / "collectors/idaho/bytes.json"
        return original if original.exists() else super().budget_file(store, directory)

    def __init__(self, store: Store, directory: Path):
        super().__init__(store, directory, cap=200 * 1024**2, file_cap=16 * 1024**2, delay=1.1)
        self.max_bytes -= 1024**2  # Preserve the original collector's safety reserve.

    def _pause(self, url: str, delay: float | None = None) -> None:
        if urlsplit(url).hostname != "legislature.idaho.gov":
            raise AcquisitionError("Idaho continuation only accepts the reviewed publisher host")
        if shutil.disk_usage(self.store.root).free <= 100 * 1024**3:
            raise AcquisitionError("Original 100 GiB disk stop threshold reached")
        super()._pause(url, delay)

    def fetch(self, url: str, **options: Any) -> Receipt:
        if urlsplit(url).hostname != "legislature.idaho.gov":
            raise AcquisitionError("Idaho continuation only accepts the reviewed publisher host")
        denied = self.store.db.execute(
            "SELECT 1 FROM acquisitions WHERE (url=? OR final_url=?) AND "
            "(status IN (401,403,407,451) OR (status=0 AND "
            "(error LIKE 'HTTP 403%' OR error LIKE 'HTTP 401%' OR error LIKE 'HTTP 451%'))) LIMIT 1",
            (url, url),
        ).fetchone()
        if denied:
            raise AcquisitionError("Retained access denial; review publisher/runtime permissions")
        options["max_file_bytes"] = min(options.get("max_file_bytes", self.file_cap), self.file_cap)
        return super().fetch(url, **options)


def inventory(data: bytes, title: str | None = None) -> list[tuple[str, str, str | None]]:
    """Keep every native row, distinguishing PDF exports, notices and missing PDFs."""
    root = html.fromstring(data)
    kind = "TITLE" if title is None else "CHAPTER"
    tables = root.xpath(
        f"//table[.//td[starts-with(translate(normalize-space(.),"
        f'"abcdefghijklmnopqrstuvwxyz","ABCDEFGHIJKLMNOPQRSTUVWXYZ"),"{kind} ")]]'
    )
    if len(tables) != 1:
        raise ValueError("Missing unique Idaho inventory table")
    result: list[tuple[str, str, str | None]] = []
    for row in tables[0].xpath("./tr | ./tbody/tr"):
        cells = row.xpath("./td")
        match = (
            re.fullmatch(
                kind + r" (?:\[(\d+[A-Z]?)\]\s*)?(\d+[A-Z]?)(?:\s+\[(\d+[A-Z]?)\])?",
                readable(cells[0]).strip(),
                re.I,
            )
            if cells
            else None
        )
        if not match or len(cells) < 3:
            raise ValueError(
                f"Unexpected Idaho {kind.lower()} inventory row: {readable(row)[:200]}"
            )
        number, label = match[3] or match[2], readable(cells[2]).strip()
        if match[1] or match[3]:
            label = readable(cells[0]).strip() + " " + label
        expected = (
            INDEX + "Title" + number
            if title is None
            else BASE
            + f"/wp-content/uploads/statutesrules/idstat/Title{title}/T{title}CH{number}.pdf"
        )
        links = {urljoin(BASE, href).rstrip("/") for href in row.xpath(".//a/@href")}
        subchapter = [
            href
            for href in links
            if title is not None
            and re.fullmatch(
                re.escape(BASE + f"/wp-content/uploads/statutesrules/idstat/Title{title}/")
                + rf"T{re.escape(title)}CH\d+SCH{re.escape(number)}\.pdf",
                href,
            )
        ]
        if expected in links:
            result.append((number, label, expected + "/" if title is None else expected))
        elif len(subchapter) == 1:
            result.append((number, label, subchapter[0]))
        elif title is not None and re.search(r"\[(?:REPEALED|RESERVED)\]", label):
            if any(href.lower().endswith(".pdf") for href in links):
                raise ValueError("Nonexport notice has an unexpected PDF link")
            notice_key = (
                "source-label:" + readable(cells[0]).strip()[len(kind) + 1 :].replace(" ", "")
                if match[1] or match[3]
                else number
            )
            result.append((notice_key, label, None))
        elif (
            title is not None
            and INDEX + f"Title{title}/T{title}CH{number}" in links
            and not any(href.lower().endswith(".pdf") for href in links)
        ):
            result.append((number, label, None))
        else:
            raise ValueError(f"Missing exact native export: {kind} {number}")
    if not result or len({r[0] for r in result}) != len(result):
        raise ValueError("Empty or duplicate Idaho inventory")
    return result


def chapter_units(
    s: Store, receipt: Receipt, title: str, chapter: str, url: str
) -> list[Provision]:
    path = s.object_path(receipt.sha256)
    if not s.artifact(receipt.sha256).startswith(b"%PDF-"):
        raise ValueError("Expected publisher PDF, not HTML/error text")
    reader = PdfReader(path)
    units = list(
        pdf_units(
            path,
            f"id-statutes:title-{title}/chapter-{chapter}",
            f"Idaho Code, Title {title}, Chapter {chapter}",
            url,
        )
    )
    checked_short_pages = {
        (
            "adb3d936020e06d1e94218810570f7219b9a3cc62d89c683997e95f135e8cd82",
            2,
        ): "short_history_note_visually_verified",
        (
            "a8e2803b0914d5aa802bfe0b74d0b27f8cf4a2191ba1e2d4861eeb595122bb58",
            4,
        ): "short_history_note_visually_verified",
        (
            "4bd7e27b7c4f38ebe8b63dc15135b16218786106df3d7cf9f1e1de490dbd98c2",
            4,
        ): "short_repeal_notice_visually_verified",
        (
            "021132a258f0aa6f11eaaf73d0987de89d079eff6a8bd6cca2748fb72b6bfda3",
            4,
        ): "short_history_note_visually_verified",
    }
    if (
        not units
        or not re.search(rf"TITLE\s+{re.escape(title)}\b", units[0].text, re.I)
        or not re.search(rf"CHAPTER\s+{re.escape(chapter)}\b", units[0].text, re.I)
    ):
        raise ValueError("PDF title/chapter identity missing")
    result = []
    for page, unit in zip(reader.pages, units, strict=True):
        quality = unit.metadata["text_quality"]
        quality = checked_short_pages.get((receipt.sha256, unit.metadata["pdf_page"]), quality)
        if quality not in {
            "layout_text_unverified",
            "short_history_note_visually_verified",
            "short_repeal_notice_visually_verified",
        }:
            raise ValueError(f"PDF page {unit.metadata['pdf_page']} needs source-media review")
        refs = []
        for annotation in page.get("/Annots", []):
            obj = annotation.get_object()
            action = obj.get("/A")
            if action and action.get_object().get("/URI"):
                target = str(action.get_object()["/URI"])
                refs.append(
                    Reference(
                        target,
                        "publisher_pdf_link",
                        target,
                        json_text(
                            {
                                "pdf_page": unit.metadata["pdf_page"],
                                "annotation_subtype": str(obj.get("/Subtype", "")),
                                "rectangle": [float(x) for x in obj.get("/Rect", [])],
                                "uri": target,
                            }
                        ),
                    )
                )
        result.append(
            replace(
                unit,
                references=tuple(refs),
                metadata={
                    **unit.metadata,
                    "text_quality": quality,
                    "title": int(title),
                    "chapter": chapter,
                    "source_native_anchor": f"#page={unit.metadata['pdf_page']}",
                    "embedded_images": len(page.images),
                    "fidelity": "Complete original PDF retained; layout text is a page projection; tables and media remain in original PDF.",
                    "legal_effective_date": None,
                },
            )
        )
    return result


def sync_idaho(s: Store, a: Acquirer, limit: int | None, as_of: str | None) -> dict[str, Any]:
    if as_of is not None or (limit is not None and limit < 0):
        raise ValueError("Idaho requires a nonnegative limit and has no historical acquisition")
    index, terms, currency = (a.fetch(url) for url in (INDEX, TERMS, CURRENCY))
    root = html.fromstring(s.artifact(currency.sha256))
    for node in root.xpath("//script | //style"):
        node.drop_tree()
    match = re.search(r"current through the (\d{4}) Legislative Session", readable(root))
    if not match or b"LEXIS" not in s.artifact(terms.sha256):
        raise ValueError("Publisher currency/official-copy notice changed; review first")
    session = match[1]
    titles = inventory(s.artifact(index.sha256))
    s.collection(
        COLLECTION,
        ("us-id", "Idaho", "state", "us"),
        name="Idaho Statutes",
        authority="Idaho State Legislature / Legislative Services Office",
        kind="statutes",
        homepage=INDEX,
        source_status="Government website exports; publisher identifies Lexis printed Code as official copies",
        access="Public publisher PDFs; robots honored. Copyright footer retained; no blanket redistribution license asserted.",
        metadata={
            "inventory_sha256": index.sha256,
            "terms_sha256": terms.sha256,
            "currency_sha256": currency.sha256,
            "edition": session + " Legislative Session",
            "unit_scope": "Chapter PDF, indexed by physical page; not individual statutory sections",
            "scope": "All native title inventories; actual bodies depend on inventory status. Constitution, regulations and later session laws excluded; exact snapshot/effect unknown.",
        },
    )
    tasks = {}
    memberships: dict[str, list[str]] = {}
    for title, _, title_url in titles:
        assert title_url is not None
        receipt = a.fetch(title_url)
        rows = inventory(s.artifact(receipt.sha256), title)
        memberships[title] = []
        for chapter, label, url in rows:
            item = f"T{title}CH{chapter}"
            memberships[title].append(item)
            if url is None:
                status = (
                    "nonexport_notice"
                    if re.search(r"\[(?:REPEALED|RESERVED)\]", label)
                    else "missing_pdf_export"
                )
                s.inventory(COLLECTION, item, title_url, status, label)
                continue
            if not s.db.execute(
                "SELECT 1 FROM inventories WHERE collection_id=? AND item=?", (COLLECTION, item)
            ).fetchone():
                s.inventory(COLLECTION, item, url, "pending")
            tasks[item] = (title, chapter, url, receipt.sha256, label)
    attempted = accepted = 0
    failure = None
    for item, (title, chapter, url, inventory_sha, label) in tasks.items():
        if (
            not a.refresh
            and s.db.execute(
                "SELECT 1 FROM versions WHERE document_id=?", (COLLECTION + ":" + item,)
            ).fetchone()
        ):
            continue
        if limit is not None and attempted >= limit:
            break
        attempted += 1
        try:
            receipt = a.fetch(url)
            units = chapter_units(s, receipt, title, chapter, url)
            s.ingest(
                collection=COLLECTION,
                document=COLLECTION + ":" + item,
                title=f"Idaho Code Title {title} Chapter {chapter}",
                url=url,
                acquisition=receipt.id,
                snapshot_basis=f"Publisher states current through {session} Legislative Session; exact snapshot/effect unknown",
                parser="idaho-native-pdf-1",
                provisions=units,
                metadata={
                    "source_edition": session + " Legislative Session",
                    "currency_evidence_sha256": currency.sha256,
                    "inventory_sha256": inventory_sha,
                    "publisher_inventory_label": label,
                    "title": int(title),
                    "chapter": chapter,
                    "source_snapshot_date": None,
                    "effective_date": None,
                },
            )
            s.inventory(COLLECTION, item, url, "indexed")
            accepted += 1
            print(f"Idaho {item}: {len(units)} pages", file=sys.stderr, flush=True)
        except (ValueError, AcquisitionError) as exc:
            s.inventory(COLLECTION, item, url, "failed", str(exc))
            print(f"Idaho {item}: {exc}", file=sys.stderr, flush=True)
            if isinstance(exc, AcquisitionError):
                failure = exc
                break
    for title, _, url in titles:
        statuses = [
            s.db.execute(
                "SELECT status FROM inventories WHERE collection_id=? AND item=?",
                (COLLECTION, item),
            ).fetchone()[0]
            for item in memberships[title]
        ]
        s.inventory(
            COLLECTION,
            "title:" + title,
            url or INDEX,
            "indexed" if all(x in {"indexed", "nonexport_notice"} for x in statuses) else "partial",
        )
    if failure is not None:
        raise failure
    return {
        "titles": len(titles),
        "chapter_exports": len(tasks),
        "attempted": attempted,
        "accepted_this_run": accepted,
    }
