"""Council Clerk's Portland Charter, separately observed from the City Code."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from .acquire import Acquirer, AcquisitionError
from .campaign import CampaignAcquirer, save
from .parse import markup, readable, source_links
from .portland_code import code_media, content_signature, page, view
from .store import Provision, Store

BASE = "https://www.portland.gov"
INDEX = BASE + "/charter"
COLLECTION = "portland-city-charter"
CAP = 128 * 1024**2
PREFLIGHT_CAP = 16 * 1024**2
FILE_CAP = 8 * 1024**2
CLOCK_NOTE = (
    "Current Council Clerk charter display observed at acquisition, not a certified legal "
    "effective-date snapshot. Source history, amendments and upcoming changes retain their "
    "own scope; no changes applied and no acquisition/render date assigned as legal effect."
)


def official_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != "www.portland.gov" or parts.fragment:
        raise AcquisitionError("Charter acquisition requires exact official Portland HTTPS URLs")
    return url


class CharterAcquirer(CampaignAcquirer):
    """A non-resetting allowance; caller owns the publisher's single-writer lock."""

    def __init__(
        self,
        store: Store,
        directory: Path | None = None,
        *,
        refresh: bool = False,
        preflight: bool = False,
    ):
        directory = directory or store.root / "campaigns" / "portland-charter"
        if (
            not (directory / "budget.json").exists()
            and store.db.execute(
                "SELECT 1 FROM acquisitions WHERE url LIKE ? LIMIT 1", (INDEX + "%",)
            ).fetchone()
        ):
            raise AcquisitionError("Restore original Portland Charter campaign budget before HTTP")
        directory.mkdir(parents=True, exist_ok=True)
        super().__init__(
            store,
            directory,
            cap=CAP,
            file_cap=FILE_CAP,
            ceiling=PREFLIGHT_CAP if preflight else CAP,
            delay=2.1,
        )
        self.refresh = refresh

    def _pause(self, url: str, delay: float | None = None) -> None:
        official_url(url)
        prior = self.store.db.execute(
            "SELECT status FROM acquisitions WHERE url=? ORDER BY id DESC LIMIT 1", (url,)
        ).fetchone()
        if prior and prior[0] in (401, 403, 407, 451):
            raise AcquisitionError("Retained publisher/proxy denial; no automatic retry")
        if self.downloaded + 65536 > self.max_bytes:
            raise AcquisitionError("Portland Charter campaign cap exhausted before request")
        super()._pause(url, delay)
        self.budget["request_overhead_bytes"] = self.budget.get("request_overhead_bytes", 0) + 65536
        self.budget["requests_started"] = self.budget.get("requests_started", 0) + 1
        self.downloaded += 65536

    def _bounded_response(self, response: httpx.Response) -> None:
        length = response.headers.get("content-length", "")
        if response.status_code != 200 and length.isdigit() and int(length) > 65536:
            self.downloaded += int(length) - 65536
        super()._bounded_response(response)

    def close(self) -> None:
        pending = self.budget.pop("reserved_bytes", 0)
        self.budget["consumed_bytes"] += pending
        self.budget["uncertain_reserved_bytes"] = (
            self.budget.get("uncertain_reserved_bytes", 0) + pending
        )
        save(self.budget_path, self.budget)
        super().close()


def source_key(url: str) -> str:
    official_url(url)
    parts = urlsplit(url)
    if parts.query or not re.fullmatch(r"/charter/\d+(?:/[a-zA-Z0-9-]+)*", parts.path):
        raise ValueError("Not a native Portland Charter node URL: " + url)
    return "pdx-charter:" + parts.path.removeprefix("/charter/")


def chapter_list(data: bytes) -> list[dict[str, Any]]:
    result = []
    for row in view(page(data), "page_1").xpath(
        './div[@class="view-content"]/div[@class="views-row"]'
    ):
        links = row.xpath(".//a[@href]")
        if len(links) != 1:
            raise ValueError("Ambiguous Portland Charter chapter row")
        target = urljoin(BASE, links[0].get("href"))
        number = source_key(target).removeprefix("pdx-charter:")
        label = " ".join(readable(links[0]).split())
        if not number.isdigit() or not label.startswith("Chapter " + number + " "):
            raise ValueError("Portland Charter chapter label/URL disagreement")
        result.append({"number": number, "url": target, "label": label})
    if not result or len({item["number"] for item in result}) != len(result):
        raise ValueError("Empty/duplicate Portland Charter chapter inventory")
    return result


def members(data: bytes, url: str) -> list[dict[str, str]]:
    root = page(data)
    result = []
    for name, kind in (("entity_view_1", "article"), ("entity_view_2", "section")):
        for row in view(root, name).xpath('./div[@class="views-row"]'):
            links = row.xpath("./article/h2/a" if kind == "section" else "./div/span/a")
            if len(links) != 1:
                raise ValueError("Native Charter member has no unique heading link")
            target = urljoin(BASE, links[0].get("href", ""))
            if source_key(target).split("/")[0] != source_key(url).split("/")[0]:
                raise ValueError("Charter member outside its chapter")
            result.append(
                {
                    "url": target,
                    "label": " ".join(readable(links[0]).split()),
                    "kind": kind,
                    **({"content_sha256": content_signature(row)} if kind == "section" else {}),
                }
            )
    if len({item["url"] for item in result}) != len(result):
        raise ValueError("Duplicate Portland Charter member URL")
    return result


def _native_own(data: bytes) -> Any:
    root = deepcopy(page(data))
    for node in root.xpath('.//div[@class="views-element-container"]'):
        if node.getparent() is not None:
            node.drop_tree()
    return root


def own_content(data: bytes) -> str:
    """Independent article display, excluding its descendant lists/change-set views."""
    return content_signature(_native_own(data))


def _context_signature(nodes: list[Any]) -> str:
    # The native chapter wraps history in a prefix-note field; print puts the
    # same content directly in view-header. Compare content, not that wrapper.
    values = {
        "text": " ".join(" ".join(readable(node).split()) for node in nodes),
        "tables": [
            [
                [
                    (" ".join(readable(cell).split()), cell.get("rowspan"), cell.get("colspan"))
                    for cell in row.xpath("./td|./th")
                ]
                for row in table.xpath(".//tr")
            ]
            for node in nodes
            for table in node.xpath(".//table")
        ],
        "images": [
            (image.get("src"), image.get("alt")) for node in nodes for image in node.xpath(".//img")
        ],
    }
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def chapter_context_signature(data: bytes) -> str:
    fields = _native_own(data).xpath(
        './/div[contains(concat(" ",@class," ")," field--name-field-prefix-note ") or contains(concat(" ",@class," ")," field--name-field-section-body ")]'
    )
    return _context_signature(fields)


def _own(node: Any) -> Any:
    result = deepcopy(node)
    for child in result.xpath('./div[@class="views-element-container"]'):
        child.drop_tree()
    return result


def chapter_units(
    data: bytes,
    chapter: dict[str, Any],
    membership: dict[str, list[dict[str, str]]],
    article_signatures: dict[str, str],
) -> list[Provision]:
    root = page(data)
    headings = root.xpath('.//h1[@class="page-title"]')
    if len(headings) != 1 or " ".join(readable(headings[0]).split()) != chapter["label"]:
        raise ValueError("Charter printable chapter differs from inventory heading")
    container = view(root, "page_chapter_all")
    expected = {item["url"]: item for group in membership.values() for item in group}
    if len(expected) != sum(len(group) for group in membership.values()):
        raise ValueError("Charter member occurs under multiple parents")
    parents = {item["url"]: parent for parent, group in membership.items() for item in group}
    ordered: list[str] = []

    def visit(parent: str) -> None:
        for child in membership.get(parent, []):
            if child["url"] in ordered:
                raise ValueError("Cyclic Charter membership")
            ordered.append(child["url"])
            if child["kind"] == "article":
                if child["url"] not in membership:
                    raise ValueError("Unexpanded native Charter article")
                visit(child["url"])

    visit(chapter["url"])
    if set(ordered) != set(expected):
        raise ValueError("Disconnected Charter membership")
    context = deepcopy(container)
    for node in context.xpath('.//div[@class="node--embedded"]'):
        if node.getparent() is not None:
            node.drop_tree()
    if _context_signature([context]) != chapter.get("context_signature"):
        raise ValueError("Printable Charter chapter context differs from native display")
    result = [
        Provision(
            key=source_key(chapter["url"]),
            citation="Portland Charter Chapter " + chapter["number"],
            heading=chapter["label"],
            text=(chapter["label"] + "\n" + readable(context)).strip(),
            markup=markup(context),
            url=chapter["url"],
            unit_kind="chapter_context",
            metadata={"clock_note": CLOCK_NOTE},
            references=source_links(context, chapter["url"]),
        )
    ]
    obtained = []
    for node in container.xpath('.//div[@class="node--embedded"]'):
        links = node.xpath("./h2/a | ./h3/a | ./h4/a | ./h5/a | ./h6/a")
        if len(links) != 1:
            raise ValueError("Embedded Charter node has no unique native heading")
        url = urljoin(BASE, links[0].get("href", ""))
        label = " ".join(readable(links[0]).split())
        if url not in expected or label != expected[url]["label"]:
            raise ValueError("Printable Charter member/label mismatch: " + url)
        obtained.append(url)
        ancestor = next(
            (a for a in node.iterancestors() if a.get("class") == "node--embedded"), None
        )
        parent_url = (
            chapter["url"]
            if ancestor is None
            else urljoin(
                BASE, ancestor.xpath("./h2/a/@href | ./h3/a/@href | ./h4/a/@href | ./h5/a/@href")[0]
            )
        )
        if parents[url] != parent_url:
            raise ValueError("Printable/native Charter parent mismatch: " + url)
        own = _own(node)
        kind = expected[url]["kind"]
        signature = (
            expected[url].get("content_sha256")
            if kind == "section"
            else article_signatures.get(url)
        )
        if signature is None or content_signature(own) != signature:
            raise ValueError("Printable Charter content differs from native display: " + url)
        if kind == "section":
            match = re.match(r"Section (\d+-\d+[A-Za-z]?(?:\.\d+)?)(?:\b|\s)", label)
            if not match:
                raise ValueError("Charter section lacks a native printed identifier: " + label)
            number = match[1]
            citation = "Portland Charter Section " + number
        else:
            number = None
            citation = "Portland Charter Chapter " + chapter["number"] + " " + label
        result.append(
            Provision(
                key=source_key(url),
                citation=citation,
                heading=label,
                text=readable(own),
                markup=markup(own),
                url=url,
                parent_key=source_key(parent_url),
                unit_kind="section" if kind == "section" else "article_context",
                metadata={
                    "source_number": number,
                    "source_kind": kind,
                    "clock_note": CLOCK_NOTE,
                    "tables": len(own.xpath(".//table")),
                    "media": code_media(own, url),
                    "list_numbering": "Printed labels and list markup preserved; generated ordered-item markers are not legal paragraph identifiers.",
                },
                references=source_links(own, url),
            )
        )
    if obtained != ordered:
        raise ValueError("Printable Charter differs from ordered native member inventory")
    return result


def preflight(store: Store, acquirer: Acquirer) -> dict[str, Any]:
    receipt = acquirer.fetch(INDEX, max_file_bytes=FILE_CAP)
    root = page(store.artifact(receipt.sha256))
    terms_url = BASE + "/help/about/privacy"
    # The full retained root includes the publisher policy navigation in its footer.
    from lxml import html

    if "/help/about/privacy" not in html.fromstring(store.artifact(receipt.sha256)).xpath(
        "//a/@href"
    ):
        raise ValueError("Charter publisher policy link missing")
    terms = acquirer.fetch(terms_url, max_file_bytes=FILE_CAP)
    chapters = chapter_list(store.artifact(receipt.sha256))
    for chapter in chapters:
        raw = acquirer.fetch(chapter["url"], max_file_bytes=FILE_CAP)
        links = page(store.artifact(raw.sha256)).xpath(
            './/a[contains(@href,"/all-articles")]/@href'
        )
        if len(links) != 1 or urljoin(BASE, links[0]) != chapter["url"] + "/all-articles":
            raise ValueError("Missing unique literal Charter printable export: " + chapter["url"])
        chapter.update(
            export_url=official_url(urljoin(BASE, links[0])), index_acquisition_id=raw.id
        )
        chapter["context_signature"] = chapter_context_signature(store.artifact(raw.sha256))
    return {
        "root_acquisition_id": receipt.id,
        "root_sha256": receipt.sha256,
        "terms_acquisition_id": terms.id,
        "chapters": chapters,
        "clock_note": CLOCK_NOTE,
        "native_root_context": readable(
            view(root, "page_1").xpath('./div[@class="view-header"]')[0]
        ),
        "changes_links": root.xpath('.//a[contains(@href,"/charter-code-policies/changes")]/@href'),
    }


def sync_portland_charter(
    store: Store,
    acquirer: Acquirer,
    limit: int | None,
    as_of: str | None,
) -> None:
    if as_of is not None:
        raise ValueError("Portland Charter display has no certified historical snapshot date")
    if limit is not None and limit < 1:
        raise ValueError("Charter limit must be positive")
    acquirer.delay = max(acquirer.delay, 2.1)
    plan = preflight(store, acquirer)
    store.collection(
        COLLECTION,
        ("us-or-portland", "Portland, Oregon", "municipality", "us-or"),
        parents=(("us-or", "Oregon", "state", "us"),),
        name="Portland City Charter",
        authority="City of Portland Council Clerk",
        kind="municipal_charter",
        homepage=INDEX,
        source_status="Official city publisher display; legal compilation/effect date unknown",
        access="Public native chapter and printable exports; publisher accuracy/privacy terms retained; no blanket redistribution license inferred",
        metadata={
            "inventory_acquisition_id": plan["root_acquisition_id"],
            "terms_acquisition_id": plan["terms_acquisition_id"],
            "inventory_sha256": plan["root_sha256"],
            "clock_note": CLOCK_NOTE,
            "native_root_context": plan["native_root_context"],
            "changes_navigation_not_applied": plan["changes_links"],
            "excluded": "Prior charter editions, ballot measures, ordinances and upcoming/recent amendments are not independently acquired or applied; City Code is a separate collection.",
        },
    )
    current = {chapter["url"] for chapter in plan["chapters"]}
    for row in store.db.execute(
        "SELECT item,url FROM inventories WHERE collection_id=?", (COLLECTION,)
    ).fetchall():
        if row["url"] not in current:
            store.inventory(COLLECTION, row["item"], row["url"], "not_in_current_inventory")
    for chapter in plan["chapters"]:
        present = store.db.execute(
            "SELECT 1 FROM versions WHERE document_id=? AND artifact_sha="
            "(SELECT sha256 FROM acquisitions WHERE url=? AND status=200 AND sha256 IS NOT NULL ORDER BY id DESC LIMIT 1)",
            (source_key(chapter["url"]), chapter["export_url"]),
        ).fetchone()
        store.inventory(
            COLLECTION, chapter["number"], chapter["url"], "indexed" if present else "pending"
        )
    processed = 0
    for chapter in plan["chapters"]:
        state = store.db.execute(
            "SELECT status FROM inventories WHERE collection_id=? AND item=?",
            (COLLECTION, chapter["number"]),
        ).fetchone()[0]
        if state == "indexed" and not acquirer.refresh:
            continue
        if limit is not None and processed >= limit:
            break
        try:
            membership = {}
            receipts = {}
            signatures = {}
            queue = [chapter["url"]]
            while queue:
                url = queue.pop(0)
                if url in membership:
                    raise ValueError("Repeated/cyclic Charter container membership")
                raw = acquirer.fetch(url, max_file_bytes=FILE_CAP)
                data = store.artifact(raw.sha256)
                membership[url] = members(data, url)
                receipts[url] = raw.id
                signatures[url] = own_content(data)
                queue.extend(c["url"] for c in membership[url] if c["kind"] == "article")
            raw = acquirer.fetch(chapter["export_url"], max_file_bytes=FILE_CAP)
            units = chapter_units(store.artifact(raw.sha256), chapter, membership, signatures)
            for url, acquisition_id in receipts.items():
                sha = store.db.execute(
                    "SELECT sha256 FROM acquisitions WHERE id=?", (acquisition_id,)
                ).fetchone()[0]
                blocks = page(store.artifact(sha)).xpath(
                    './/div[@data-block-plugin-id="views_block:change_sets-effected_on_codecharterpolicy"]'
                )
                if len(blocks) > 1:
                    raise ValueError("Ambiguous Charter change-set context")
                if blocks and readable(blocks[0]).strip():
                    units.append(
                        Provision(
                            key=source_key(url) + "/changes-context",
                            citation="Portland Charter changes context: " + url,
                            heading="Publisher recent/upcoming changes (not applied)",
                            text=readable(blocks[0]),
                            markup=markup(blocks[0]),
                            url=url,
                            parent_key=source_key(url),
                            unit_kind="changes_context",
                            metadata={
                                "clock_note": CLOCK_NOTE,
                                "source_acquisition_id": acquisition_id,
                            },
                            references=source_links(blocks[0], url),
                        )
                    )
            media = []
            for url in sorted(
                {
                    m["url"]
                    for unit in units
                    for m in unit.metadata.get("media", [])
                    if m["url"] and not m.get("original_url")
                }
            ):
                parts = urlsplit(url)
                if (
                    parts.scheme != "https"
                    or parts.netloc != "www.portland.gov"
                    or not parts.path.startswith("/sites/default/files/")
                ):
                    media.append({"url": url, "status": "not_fetched_external_or_embedded_media"})
                    continue
                asset = acquirer.fetch(url, max_file_bytes=FILE_CAP)
                media.append(
                    {
                        "url": url,
                        "status": "retained_not_transcribed",
                        "sha256": asset.sha256,
                        "acquisition_id": asset.id,
                    }
                )
            dependencies = [
                plan["root_acquisition_id"],
                plan["terms_acquisition_id"],
                raw.id,
                *receipts.values(),
                *(m["acquisition_id"] for m in media if "acquisition_id" in m),
            ]
            available = max(
                store.db.execute(
                    "SELECT observed_at FROM acquisitions WHERE id=?", (identifier,)
                ).fetchone()[0]
                for identifier in dependencies
            )
            _, count, new = store.ingest(
                collection=COLLECTION,
                document=source_key(chapter["url"]),
                title=chapter["label"],
                url=chapter["export_url"],
                acquisition=raw.id,
                snapshot_basis=CLOCK_NOTE,
                parser="portland-charter-1",
                provisions=units,
                available_at=available,
                metadata={
                    "native_chapter_url": chapter["url"],
                    "membership": membership,
                    "membership_acquisitions": receipts,
                    "article_content_signatures": signatures,
                    "clock_note": CLOCK_NOTE,
                    "media_outcomes": media,
                },
            )
            store.inventory(COLLECTION, chapter["number"], chapter["url"], "indexed")
            processed += 1
            print(
                f"{COLLECTION}: {chapter['number']}: {count} units ({'indexed' if new else 'unchanged'})",
                file=sys.stderr,
                flush=True,
            )
        except (ValueError, AcquisitionError) as error:
            store.inventory(COLLECTION, chapter["number"], chapter["url"], "failed", str(error))
            raise
    directory = store.root / "portland-charter"
    directory.mkdir(exist_ok=True)
    save(directory / "plan.json", plan)
