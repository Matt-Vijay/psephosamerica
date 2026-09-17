"""Illinois publisher chapter inventories and complete, source-linked act exports."""

from __future__ import annotations

import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

from lxml import html

from .acquire import Acquirer, AcquisitionError, Receipt
from .campaign import CampaignAcquirer, save
from .parse import markup, media_links, readable, source_links
from .store import Provision, Store

BASE = "https://ilga.gov"
INDEX = BASE + "/Legislation/ILCS/Chapters"
TERMS = BASE + "/Disclaimers"
COLLECTION = "il-statutes"
NOTICE = (
    "Government publisher drafting database; publisher says this is not official/authoritative "
    "ILCS text. Recent acts may be absent; future-effective changes may already replace effective text."
)


class IllinoisAcquirer(CampaignAcquirer):
    automatic_retries = False
    consumed_field = "decoded_bytes"

    @classmethod
    def budget_file(cls, store: Store, directory: Path) -> Path:
        original = store.root / "collectors/il/budget.json"
        return original if original.exists() else super().budget_file(store, directory)

    def __init__(self, store: Store, directory: Path):
        path = self.budget_file(store, directory)
        if path.exists():
            old = json.loads(path.read_bytes())
            # The original collector stored its fixed 200 MiB cap in code, not JSON.
            if set(old) == {"decoded_bytes"}:
                save(path, {**old, "cap_bytes": 200 * 1024**2})
        super().__init__(store, directory, cap=200 * 1024**2, file_cap=30 * 1024**2, delay=10.1)

    def _pause(self, url: str, delay: float | None = None) -> None:
        if urlsplit(url).hostname != "ilga.gov":
            raise AcquisitionError("Illinois continuation only accepts the reviewed publisher host")
        if shutil.disk_usage(self.store.root).free <= 100 * 1024**3:
            raise AcquisitionError("Original 100 GiB disk stop threshold reached")
        super()._pause(url, delay)

    def fetch(self, url: str, **options: Any) -> Receipt:
        if urlsplit(url).hostname != "ilga.gov":
            raise AcquisitionError("Illinois continuation only accepts the reviewed publisher host")
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


def link_query(url: str, path: str) -> dict[str, list[str]]:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.netloc != "ilga.gov"
        or parts.fragment
        or parts.path.lower() != path.lower()
    ):
        raise ValueError("Unexpected Illinois publisher link")
    return parse_qs(parts.query, keep_blank_values=True)


def inventory(data: bytes, chapter_url: str | None = None) -> list[tuple[str, str, str]]:
    kind = "Articles" if chapter_url else "Acts"
    parent = link_query(chapter_url, "/Legislation/ILCS/Acts") if chapter_url else None
    root = html.fromstring(data)
    result = []
    for node in root.xpath(f'//a[contains(@href,"/ILCS/{kind}?")]'):
        label, url = readable(node).strip(), urljoin(BASE, node.get("href"))
        query = link_query(url, "/Legislation/ILCS/" + kind)
        field = "ActID" if parent else "ChapterNumber"
        ids = query.get(field, [])
        if len(ids) != 1 or not ids[0].isdigit() or len(query.get("ChapterID", [])) != 1:
            raise ValueError("Missing unique native Illinois identifier")
        if parent:
            if query["ChapterID"] != parent["ChapterID"] or not re.match(
                re.escape(parent["ChapterNumber"][0]) + r"\s+ILCS\s+[^/]+/", label
            ):
                raise ValueError("Act inventory belongs to a different chapter")
        elif not label.startswith("CHAPTER " + ids[0] + " "):
            raise ValueError("Chapter label/URL disagreement")
        result.append((ids[0], label, url))
    if not result or len({r[0] for r in result}) != len(result):
        raise ValueError("Empty or duplicate Illinois inventory")
    return result


def whole_act_link(data: bytes, inventory_url: str) -> str:
    expected = link_query(inventory_url, "/Legislation/ILCS/Articles")
    links: set[str] = {
        urljoin(BASE, str(node.get("href")))
        for node in html.fromstring(data).xpath("//a[@href]")
        if readable(node).strip() == "View Entire Act"
    }
    if len(links) != 1:
        raise ValueError("Missing unique native View Entire Act link")
    url = links.pop()
    query = link_query(url, "/legislation/ILCS/details")
    if (
        query.get("ActID") != expected.get("ActID")
        or query.get("ChapterID") != expected.get("ChapterID")
        or query.get("ChapAct") != ["FullText"]
    ):
        raise ValueError("Whole-act link identity mismatch")
    return url


def act_units(data: bytes, url: str, actid: str, citation_prefix: str) -> list[Provision]:
    roots = html.fromstring(data).xpath('//*[@id="billtextanchor"]')
    if len(roots) != 1:
        raise ValueError("No unique legal text container")
    root = roots[0]
    tables = root.xpath(".//table[not(ancestor::table)]")
    seen: Counter[str] = Counter()
    units = []
    for ordinal, node in enumerate(tables):
        text = readable(node)
        if not text.strip():
            continue
        match = re.match(r"\((\d+\s+ILCS\s+[^)]+)\)", text)
        if not match:
            raise ValueError(f"Unrecognized legal unit table {ordinal}: {text[:150]}")
        citation = " ".join(match[1].split())
        if not re.match(re.escape(citation_prefix) + r"(?:/|\s|$)", citation):
            raise ValueError("Act body citation differs from the native inventory")
        seen[citation] += 1
        kind = "heading" if "heading" in citation else "section"
        heading = text.splitlines()[1] if len(text.splitlines()) > 1 else citation
        units.append(
            Provision(
                key="ilcs:"
                + citation
                + (f"/occurrence/{seen[citation]}" if seen[citation] > 1 else ""),
                citation=citation,
                heading=heading[:250],
                text=text,
                markup=markup(node),
                url=url,
                unit_kind=kind,
                metadata={
                    "source_identifier": citation,
                    "act_id": actid,
                    "occurrence": seen[citation],
                    "source_table_ordinal": ordinal,
                    "tables": len(node.xpath(".//table")),
                    "media": media_links(node, url),
                },
                references=source_links(node, url),
            )
        )
    if not any(u.unit_kind == "section" for u in units):
        raise ValueError("Heading-only or empty act; no legal sections")
    if re.sub(r"\s+", "", readable(root)) != re.sub(r"\s+", "", "".join(u.text for u in units)):
        raise ValueError("Source text outside unit tables would be lost")
    return units


def sync_illinois(s: Store, a: Acquirer, limit: int | None, as_of: str | None) -> dict[str, Any]:
    if as_of is not None or (limit is not None and limit < 0):
        raise ValueError("Illinois requires a nonnegative limit and has no historical acquisition")
    index, terms = a.fetch(INDEX), a.fetch(TERMS)
    chapters = inventory(s.artifact(index.sha256))
    s.collection(
        COLLECTION,
        ("us-il", "Illinois", "state", "us"),
        name="Illinois Compiled Statutes",
        authority="Illinois General Assembly / Legislative Reference Bureau",
        kind="statutes",
        homepage=INDEX,
        source_status="Government publisher; unofficial drafting compilation",
        access="Public HTTPS. Robots honored; 10.1-second minimum. Publisher terms retained; no blanket redistribution license asserted.",
        metadata={
            "warning": NOTICE,
            "scope": "Native chapter/act inventories; only acquired bodies are indexed. Repeal labels are inventory notices, not historical law. Constitution, regulations and session laws excluded from this command.",
            "snapshot_warning": "Acquisition time is not snapshot/effective date; current exports undated.",
            "inventory_sha256": index.sha256,
            "terms_sha256": terms.sha256,
        },
    )
    tasks: dict[str, tuple[str, str, str]] = {}
    memberships: dict[str, list[str]] = {}
    listed: set[str] = set()
    for number, _, url in chapters:
        try:
            receipt = a.fetch(url)
            rows = inventory(s.artifact(receipt.sha256), url)
        except (ValueError, AcquisitionError) as exc:
            s.inventory(COLLECTION, "chapter:" + number, url, "failed", str(exc))
            raise
        memberships[number] = []
        for actid, label, act_url in rows:
            item = "act:" + actid
            if item in listed:
                raise ValueError("Act is listed under multiple chapters; review native identity")
            listed.add(item)
            memberships[number].append(item)
            if "Repealed by" in label:
                s.inventory(COLLECTION, item, act_url, "metadata_only", label)
                continue
            if not s.db.execute(
                "SELECT 1 FROM inventories WHERE collection_id=? AND item=?", (COLLECTION, item)
            ).fetchone():
                s.inventory(COLLECTION, item, act_url, "pending", label)
            tasks[item] = (label, act_url, receipt.sha256)
        s.inventory(
            COLLECTION,
            "chapter:" + number,
            url,
            "partial",
            "Act inventory expanded; body closure not yet reconciled",
        )
        print(
            f"Illinois chapter {number}: {len(rows)} act inventory entries",
            file=sys.stderr,
            flush=True,
        )
    attempted = accepted = 0
    failure = None
    for item, (label, act_url, inventory_sha) in tasks.items():
        actid = item.split(":", 1)[1]
        if (
            not a.refresh
            and s.db.execute(
                "SELECT 1 FROM versions WHERE document_id=?", ("ilcs:" + item,)
            ).fetchone()
        ):
            continue
        if limit is not None and attempted >= limit:
            break
        attempted += 1
        try:
            listing = a.fetch(act_url)
            url = whole_act_link(s.artifact(listing.sha256), act_url)
            receipt = a.fetch(url)
            prefix = label.split("/", 1)[0].strip()
            units = act_units(s.artifact(receipt.sha256), url, actid, prefix)
            s.ingest(
                collection=COLLECTION,
                document="ilcs:" + item,
                title=label,
                url=url,
                acquisition=receipt.id,
                snapshot_basis="Publisher live drafting database; exact snapshot and legal effective date unknown",
                parser="ilcs-native-act-1",
                provisions=units,
                metadata={
                    "source_act_id": actid,
                    "inventory_url": act_url,
                    "inventory_sha256": inventory_sha,
                    "act_listing_sha256": listing.sha256,
                    "warning": NOTICE,
                },
            )
            s.inventory(COLLECTION, item, url, "acquired")
            accepted += 1
            print(f"Illinois {item}: {len(units)} source units", file=sys.stderr, flush=True)
        except (ValueError, AcquisitionError) as exc:
            s.inventory(COLLECTION, item, act_url, "failed", str(exc))
            print(f"Illinois {item}: {exc}", file=sys.stderr, flush=True)
            if isinstance(exc, AcquisitionError):
                failure = exc
                break
    for number, _, url in chapters:
        statuses = [
            s.db.execute(
                "SELECT status FROM inventories WHERE collection_id=? AND item=?",
                (COLLECTION, item),
            ).fetchone()[0]
            for item in memberships[number]
        ]
        s.inventory(
            COLLECTION,
            "chapter:" + number,
            url,
            "indexed" if all(x in {"acquired", "metadata_only"} for x in statuses) else "partial",
        )
    if failure:
        raise failure
    return {
        "chapters": len(chapters),
        "act_exports": len(tasks),
        "attempted": attempted,
        "accepted_this_run": accepted,
    }
