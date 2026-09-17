"""South Carolina's unannotated whole-chapter exports, with source-native sections."""

from __future__ import annotations

import fcntl
import json
import re
import sys
from collections import Counter
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from lxml import etree, html

from .acquire import Acquirer, AcquisitionError, Receipt
from .campaign import CampaignAcquirer, save
from .parse import markup, media_links, readable, source_links
from .store import Provision, Store

BASE = "https://www.scstatehouse.gov"
INDEX = BASE + "/code/statmast.php"
COLLECTION = "sc-code"


class SouthCarolinaAcquirer(CampaignAcquirer):
    automatic_retries = False

    @classmethod
    def budget_file(cls, store: Store, directory: Path) -> Path:
        original = store.root / "collectors/south-carolina/http-budget.json"
        return original if original.exists() else super().budget_file(store, directory)

    def __init__(self, store: Store, directory: Path):
        # The legacy entrypoint and this maintained command must share both ledger and lock.
        path = self.budget_file(store, directory)
        self.legacy_lock = (path.parent / "collector.lock").open("a")
        try:
            fcntl.flock(self.legacy_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            super().__init__(store, directory, cap=200 * 1024**2, file_cap=16 * 1024**2, delay=1.1)
            if "requests" in self.budget:
                history = self.budget["requests"]
                if not isinstance(history, list):
                    raise AcquisitionError("Invalid legacy request history")
                self.budget.setdefault(
                    "last_request_unix",
                    datetime.fromisoformat(
                        history[-1]["observed_at"].replace("Z", "+00:00")
                    ).timestamp()
                    if history
                    else 0.0,
                )
                self.budget["continuation_receipts"] = str(store.root / "legal.sqlite3")
                self.budget["legacy_requests_note"] = (
                    "Historical request list retained unchanged; continued requests are in the canonical acquisitions table."
                )
                save(self.budget_path, self.budget)
        except Exception:
            self.legacy_lock.close()
            raise

    def close(self) -> None:
        super().close()
        self.legacy_lock.close()

    def fetch(self, url: str, **options: Any) -> Receipt:
        if urlsplit(url).hostname not in {"www.scstatehouse.gov", "scstatehouse.gov"}:
            raise AcquisitionError("South Carolina campaign only accepts reviewed publisher hosts")
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


def content(data: bytes) -> etree._Element:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("windows-1252")
    nodes = html.fromstring(text).xpath('//*[@id="contentsection"]')
    if len(nodes) != 1:
        raise ValueError("Missing unique source content container")
    return nodes[0]  # type: ignore[no-any-return]


def chapter_units(
    data: bytes, url: str, title: int, chapter: str, inventory_label: str | None = None
) -> list[Provision]:
    node = content(data)
    heads = [readable(child) for child in node if child.tag == "div"]
    native_chapter = chapter.lstrip("0").upper()
    reserved = native_chapter.endswith("R") and inventory_label == (
        f"CHAPTER {native_chapter[:-1]} - RESERVED"
    )
    if reserved:
        native_chapter = native_chapter[:-1]
    article = (inventory_label or "").startswith(f"ARTICLE {native_chapter} - ")
    division = "ARTICLE" if article else "CHAPTER"
    if (
        not any(re.match(rf"^Title {title}\b", h) for h in heads)
        or f"{division} {native_chapter}" not in heads
    ):
        raise ValueError("Publisher title/chapter identity mismatch")
    groups: list[tuple[str, str, str, etree._Element]] = []
    current = etree.Element("div")
    current.text = node.text
    key = f"sc-code:t{title}c{chapter}:context"
    scope_heading = (
        f"Title {title}, Article {native_chapter}"
        if article
        else f"Title {title}, Chapter {chapter}"
    )
    heading, kind = f"{scope_heading} context", "scope_context"
    for child in node:
        match = re.match(
            r"^SECTION\s+(\d+[A-Z]?-\d+[A-Z]?-\d+(?:\.\d+)?[A-Z]?)\.",
            " ".join(readable(child).split()),
        )
        if match and child.tag in ("span", "b", "strong"):
            if readable(current).strip():
                groups.append((key, heading, kind, current))
            current = etree.Element("div")
            key, heading, kind = "sc-code:" + match[1], (child.tail or "").strip(), "section"
            if not match[1].startswith(f"{title}-{native_chapter}-"):
                # Preserve publisher mistakes without creating a false canonical citation.
                key = f"sc-code:t{title}c{chapter}:source-section:{match[1]}"
                kind = "source_anomaly"
        current.append(deepcopy(child))
    if readable(current).strip():
        groups.append((key, heading, kind, current))
    if not any(g[2] == "section" for g in groups):
        text = readable(node)
        reserved_notice = reserved and len(heads) == 2 and text == "\n".join(heads)
        untagged_section = re.search(
            rf"(?m)^SECTION {title}-{re.escape(native_chapter)}-\d+(?:\.\d+)?[A-Z]?\.", text
        )
        if (
            not reserved_notice
            and not untagged_section
            and not re.search(r"\b(?:repealed|transferred|reserved|omitted)\b", text, re.I)
        ):
            raise ValueError("No native sections or explicit chapter disposition")
        groups = [
            (
                f"sc-code:t{title}c{chapter}:chapter",
                scope_heading,
                "inventory_notice" if reserved_notice else "chapter",
                deepcopy(node),
            )
        ]
    totals = Counter(g[0] for g in groups)
    seen: Counter[str] = Counter()
    result = []
    for key, heading, kind, part in groups:
        seen[key] += 1
        suffix = f":occurrence:{seen[key]}" if totals[key] > 1 else ""
        result.append(
            Provision(
                key=key + suffix,
                citation="S.C. Code \u00a7 " + key.rsplit(":", 1)[-1]
                if kind in ("section", "source_anomaly")
                else heading,
                heading=heading,
                text=readable(part),
                markup=markup(part),
                url=url,
                parent_key=f"sc-code:title:{title}:{division.lower()}:{chapter}",
                unit_kind=kind,
                metadata={
                    "title": title,
                    "chapter": chapter,
                    "source_anchors": part.xpath(".//@id | .//a/@name"),
                    "tables": len(part.xpath(".//table")),
                    "media": media_links(part, url),
                    "context_scope": "Whole source chapter markup retained in immutable receipt; source headings and adjacent notes preserved in source order.",
                    "source_snapshot_date": None,
                    **({"native_division": "article"} if article else {}),
                    **(
                        {
                            "citation_warning": "Publisher section number falls outside this chapter; retained verbatim, not a canonical section identity."
                        }
                        if kind == "source_anomaly"
                        else {}
                    ),
                    **(
                        {
                            "publisher_inventory_label": inventory_label,
                            "inventory_source_url": BASE + f"/code/title{title}.php",
                            "inventory_notice_only": True,
                        }
                        if kind == "inventory_notice"
                        else {}
                    ),
                },
                references=source_links(part, url),
            )
        )
    return result


def sync_south_carolina(
    s: Store, a: Acquirer, limit: int | None, as_of: str | None
) -> dict[str, Any]:
    if as_of is not None:
        raise ValueError("Historical SC compilation cannot be reconstructed from live exports")
    if limit is not None and limit < 0:
        raise ValueError("limit must be nonnegative")
    policies = [a.fetch(BASE + path).id for path in ("/policies.php", "/disclaimer.php")]
    index = a.fetch(INDEX)
    root = content(s.artifact(index.sha256))
    currency = re.search(
        r"now current through the \d{4} Session of the General Assembly\.", readable(root)
    )
    if currency is None:
        raise ValueError("Publisher currency notice changed; review before acquisition")
    old = s.db.execute("SELECT metadata FROM collections WHERE id=?", (COLLECTION,)).fetchone()
    metadata = json.loads(old[0]) if old else {}
    metadata.update(
        policy_receipts=policies,
        inventory_acquisition=index.id,
        publisher_currency_statement=currency[0],
        not_legal_effect_clock=True,
        scope="Chapter and article exports enumerated by the retained title inventories. Consult inventory statuses for missing bodies; reserved notices are not statutes. Later acts and complete legal currency are not established.",
    )
    s.collection(
        COLLECTION,
        ("us-sc", "South Carolina", "state", "us"),
        name="South Carolina Code of Laws",
        authority="South Carolina Legislative Council / General Assembly",
        kind="statutes",
        homepage=INDEX,
        source_status="Government-published unannotated text; publisher states online version is not the official print edition",
        access="Public access; source-specific copying permission for unannotated legal text; general site personal/noncommercial notice retained",
        metadata=metadata,
    )
    titles = {}
    for link in root.xpath(".//a[@href]"):
        match = re.fullmatch(r"/code/title(\d+)\.php", link.get("href", ""))
        if match:
            titles[int(match[1])] = urljoin(BASE, link.get("href"))
    if not titles:
        raise ValueError("No publisher title inventory")
    chapters: dict[str, tuple[str, int, str, str]] = {}
    title_receipts = {}
    for title, url in titles.items():
        receipt = a.fetch(url)
        title_receipts[title] = receipt.id
        parent = content(s.artifact(receipt.sha256))
        count = 0
        for link in parent.xpath(".//a[@href]"):
            match = re.fullmatch(r"/code/t(\d+)c(\d+[a-z]?)\.php", link.get("href", ""), re.I)
            if not match:
                continue
            if int(match[1]) != title:
                raise ValueError("Chapter inventory link belongs to another title")
            chapter = match[2]
            cells = link.xpath("ancestor::tr[1]/td[1]")
            if len(cells) != 1:
                raise ValueError("Chapter inventory label missing")
            item = f"t{title}c{chapter}"
            chapters[item] = (urljoin(BASE, link.get("href")), title, chapter, readable(cells[0]))
            count += 1
        if not count:
            raise ValueError("Empty chapter inventory")
        s.inventory(
            COLLECTION, f"title:{title}", url, "inventoried", f"{count} chapter exports listed"
        )
    for item, (url, _, _, _) in chapters.items():
        if not s.db.execute(
            "SELECT 1 FROM inventories WHERE collection_id=? AND item=?", (COLLECTION, item)
        ).fetchone():
            s.inventory(COLLECTION, item, url, "pending")
    attempted = accepted = 0
    for item, (url, title, chapter, label) in chapters.items():
        document = f"sc-code:{item}"
        if (
            not a.refresh
            and s.db.execute("SELECT 1 FROM versions WHERE document_id=?", (document,)).fetchone()
        ):
            continue
        if limit is not None and attempted >= limit:
            break
        attempted += 1
        try:
            receipt = a.fetch(url)
            units = chapter_units(s.artifact(receipt.sha256), url, title, chapter, label)
            s.ingest(
                collection=COLLECTION,
                document=document,
                title=label,
                url=url,
                acquisition=receipt.id,
                snapshot_basis="Publisher inventory: "
                + currency[0]
                + " Exact incorporation day unknown.",
                parser="sc-native-1",
                provisions=units,
                metadata={
                    "inventory_receipt": index.id,
                    "chapter_inventory_acquisition": title_receipts[title],
                    "legal_effect_date": "unknown; source-specific history retained without normalizing to one date",
                    "acquisition_clock": receipt.observed_at,
                },
            )
            s.inventory(COLLECTION, item, url, "ingested")
            accepted += 1
            print(f"South Carolina {item}: {len(units)} units", file=sys.stderr, flush=True)
        except (ValueError, AcquisitionError) as exc:
            s.inventory(COLLECTION, item, url, "failed", str(exc))
            print(f"South Carolina {item}: {exc}", file=sys.stderr, flush=True)
            if isinstance(exc, AcquisitionError):
                raise
    return {"chapters": len(chapters), "accepted_this_run": accepted, "attempted": attempted}
