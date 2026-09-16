"""Nebraska's four native law inventories and complete publisher HTML exports.

Legal dates are unknown. Existing parser IDs are retained because the direct
parsers reproduce accepted projections; sync never edits an existing version.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
from lxml import etree, html

from .acquire import Acquirer, AcquisitionError
from .campaign import CampaignAcquirer, save
from .parse import markup, media_links, readable, source_links
from .store import Provision, Store

BASE = "https://nebraskalegislature.gov"
LANDING = BASE + "/laws/laws.php"
CAP = 512 * 1024**2
PRINT = "div[contains(concat(' ',normalize-space(@class),' '),' printwidth ')]"
STATUTE = "div[contains(concat(' ',normalize-space(@class),' '),' statute ')]"
NOTICE = re.compile(r"^(Repealed|Transferred|Renumbered|Omitted|Reserved|Unconstitutional)\b", re.I)


@dataclass(frozen=True)
class Family:
    collection: str
    title: str
    kind: str
    document_kind: str
    browse: str
    entry: str
    parameter: str
    full_export: str | None = None


FAMILIES = {
    "chapter": Family(
        "ne-revised-statutes",
        "Nebraska Revised Statutes",
        "statutes",
        "numbered_statutory_chapter",
        "browse-statutes.php",
        "browse-chapters.php",
        "chapter",
    ),
    "ucc": Family(
        "ne-uniform-commercial-code",
        "Nebraska Uniform Commercial Code",
        "statutes",
        "uniform_commercial_code",
        "browse-ucc.php",
        "ucc.php",
        "code",
        "display-fullucc.php",
    ),
    "constitution": Family(
        "ne-constitution",
        "Nebraska Constitution",
        "constitution",
        "constitution",
        "browse-constitution.php",
        "articles.php",
        "article",
        "display-fullconst.php",
    ),
    "appendix": Family(
        "ne-revised-statutes-appendix",
        "Nebraska Revised Statutes Appendix",
        "statutory_appendix",
        "statutory_appendix",
        "browse-appendix.php",
        "appendix.php",
        "section",
    ),
}
COLLECTIONS = tuple(family.collection for family in FAMILIES.values())


def official_url(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.netloc != "nebraskalegislature.gov"
        or parts.username
        or parts.password
        or parts.fragment
    ):
        raise AcquisitionError("Nebraska acquisition requires an exact official HTTPS URL: " + url)
    return url


class NebraskaAcquirer(CampaignAcquirer):
    """One persistent allowance, including redirects, failures and interruptions.

    Import a prior campaign's budget.json into this directory before resuming.
    An existing source store without its original budget fails before HTTP:
    payload receipts alone cannot reconstruct uncertainty charges.
    """

    def __init__(
        self,
        store: Store,
        directory: Path | None = None,
        *,
        refresh: bool = False,
    ):
        directory = directory or store.root / "campaigns" / "nebraska"
        if not (directory / "budget.json").exists():
            prior = store.db.execute(
                "SELECT 1 FROM acquisitions WHERE url LIKE ? LIMIT 1", (BASE + "/%",)
            ).fetchone()
            if prior:
                raise AcquisitionError(
                    "Restore the original Nebraska campaign budget.json into "
                    + str(directory)
                    + " before resuming; receipt bytes cannot reconstruct prior charges"
                )
        directory.mkdir(parents=True, exist_ok=True)
        super().__init__(
            store,
            directory,
            cap=CAP,
            file_cap=32 * 1024**2,
            delay=3.2,
        )
        self.refresh = refresh

    def _pause(self, url: str, delay: float | None = None) -> None:
        official_url(url)  # Also runs before every redirect or robots request.
        denied = self.store.db.execute(
            "SELECT status FROM acquisitions WHERE url=? ORDER BY id DESC LIMIT 1", (url,)
        ).fetchone()
        if denied and denied[0] in (401, 403, 407, 451):
            raise AcquisitionError("Retained publisher/proxy denial; do not retry: " + url)
        if self.downloaded + 65536 > self.max_bytes:
            raise AcquisitionError("Nebraska campaign cap exhausted before request")
        super()._pause(url, delay)
        self.budget["request_overhead_bytes"] = self.budget.get("request_overhead_bytes", 0) + 65536
        self.budget["requests_started"] = self.budget.get("requests_started", 0) + 1
        self.downloaded += 65536  # Nonrefundable connection/error/interruption allowance.

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


@dataclass(frozen=True)
class NativeEntry:
    identifier: str
    url: str
    print_url: str


@dataclass(frozen=True)
class NativeInventory:
    entries: tuple[NativeEntry, ...]
    full_export: str | None = None

    def documents(self) -> tuple[tuple[str, str], ...]:
        if self.full_export:
            return (("full", self.full_export),)
        return tuple((entry.identifier, entry.print_url) for entry in self.entries)


def _entries(
    root: html.HtmlElement,
    url: str,
    path: str,
    parameter: str,
    *,
    chapter: bool = False,
) -> tuple[NativeEntry, ...]:
    entries = []
    for anchor in root.xpath("//tr/td/span[1]/a[@href]"):
        target = urljoin(url, anchor.get("href"))
        parsed = urlsplit(target)
        query = parse_qs(parsed.query)
        if parsed.path != "/laws/" + path or parameter not in query or "print" in query:
            continue
        official_url(target)
        if len(query[parameter]) != 1 or not query[parameter][0]:
            raise ValueError("Ambiguous native Nebraska identifier")
        identifier = query[parameter][0]
        row = anchor.xpath("ancestor::tr[1]")[0]
        print_links = set()
        for sibling in row.xpath(".//a[@href]"):
            link = urljoin(url, sibling.get("href"))
            p, q = urlsplit(link), parse_qs(urlsplit(link).query)
            if p.path == "/laws/" + ("display-chapters.php" if chapter else path) and (
                chapter or q.get("print") == ["true"]
            ):
                official_url(link)
                if q.get(parameter) != [identifier]:
                    raise ValueError("Native row pairs different Nebraska identifiers")
                print_links.add(link)
        if len(print_links) != 1:
            raise ValueError("Missing or ambiguous literal Nebraska print link")
        entries.append(NativeEntry(identifier, target, print_links.pop()))
    if not entries:
        raise ValueError("Empty Nebraska native inventory")
    return tuple(entries)


def native_inventory(data: bytes, family: str, url: str) -> NativeInventory:
    spec = FAMILIES[family]
    root = html.fromstring(data)
    entries = _entries(root, url, spec.entry, spec.parameter, chapter=family == "chapter")
    if len({entry.identifier for entry in entries}) != len(entries):
        raise ValueError("Duplicate Nebraska inventory identifier")
    exports = {
        official_url(urljoin(url, anchor.get("href")))
        for anchor in root.xpath("//a[@href]")
        if spec.full_export
        and urlsplit(urljoin(url, anchor.get("href"))).path == "/laws/" + spec.full_export
    }
    if spec.full_export and len(exports) != 1:
        raise ValueError("Missing or ambiguous Nebraska full export")
    return NativeInventory(entries, exports.pop() if exports else None)


def chapter_index(data: bytes, chapter: str, url: str) -> tuple[NativeEntry, ...]:
    entries = _entries(html.fromstring(data), url, "statutes.php", "statute")
    if any(not entry.identifier.startswith(chapter + "-") for entry in entries):
        raise ValueError("Nebraska chapter index contains another chapter")
    return entries  # Duplicate occurrences are evidence, not deduplicated rows.


def statutory_media(node: etree._Element, url: str) -> list[dict[str, str]]:
    result = media_links(node, url)
    for link in node.xpath(".//a[@href]"):
        href = link.get("href")
        if re.search(r"\.(?:png|jpe?g|gif|svg|webp)(?:$|\?)", href, re.I):
            result.append(
                {
                    "source_locator": href,
                    "url": urljoin(url, href),
                    "alt": readable(link),
                    "status": "linked statutory image; original asset retained separately",
                }
            )
    return result


def chapter_units(
    data: bytes,
    chapter: str,
    url: str,
    entries: tuple[NativeEntry, ...],
) -> list[Provision]:
    root = html.fromstring(data)
    nodes = root.xpath("//" + PRINT + "[not(ancestor::" + PRINT + ")]")
    identifiers = []
    for node in nodes:
        heads = node.xpath("./strong[1]")
        head = " ".join(readable(heads[0]).split()) if heads else ""
        match = re.match(r"([0-9]+-[0-9][0-9,.]*?)\s*\.\s+(.*)", head)
        if not match or not match[1].startswith(chapter + "-"):
            raise ValueError("Unrecognized Nebraska section heading: " + head[:160])
        identifiers.append((match[1], match[2]))
    if [number for number, _ in identifiers] != [entry.identifier for entry in entries]:
        raise ValueError("Nebraska chapter export differs from native identifier order/occurrences")
    counts = Counter(number for number, _ in identifiers)
    seen: Counter[str] = Counter()
    units = []
    for node, (number, heading), entry in zip(nodes, identifiers, entries, strict=True):
        seen[number] += 1
        suffix = f"/_occurrence/{seen[number]}" if counts[number] > 1 else ""
        # Chapter export /1 classified absence of <p>, unlike constitution's empty <p> notices.
        notice = bool(NOTICE.match(heading)) and not node.xpath("./p")
        units.append(
            Provision(
                key=f"ne:statute:{number}{suffix}",
                citation=f"Neb. Rev. Stat. § {number}",
                heading=heading,
                text=readable(node),
                markup=markup(node),
                url=entry.url,
                parent_key=f"ne:chapter:{chapter}",
                unit_kind="editorial_notice" if notice else "section",
                metadata={
                    "source_identifier": number,
                    "chapter": chapter,
                    "duplicate_identifier_count": counts[number],
                    "occurrence": seen[number],
                    "tables": len(node.xpath(".//table")),
                    "media": statutory_media(node, url),
                    "date_semantics": "Dates in headings, text and source credits retained verbatim; no inferred effective or snapshot day.",
                    "content_role": "supporting_notice" if notice else "actual_legal_text",
                },
                references=source_links(node, url),
            )
        )
    outside = deepcopy(root)
    for node in outside.xpath("//" + PRINT + "|//head|//script|//style"):
        if node.getparent() is not None:
            node.drop_tree()
    if readable(outside).strip():
        units.append(
            Provision(
                key=f"ne:chapter:{chapter}/_context",
                citation=f"Nebraska Chapter {chapter} context",
                heading="Publisher chapter context",
                text=readable(outside),
                markup=markup(outside),
                url=url,
                unit_kind="scope_notes",
                metadata={
                    "media": statutory_media(outside, url),
                    "context_order": "Collected outside-section text; original placement remains in full raw chapter receipt",
                },
                references=source_links(outside, url),
            )
        )
    return units


def _ancillary_media(node: etree._Element, url: str) -> list[dict[str, str]]:
    result = statutory_media(node, url)
    for element in node.xpath(".//object[@data]|.//embed[@src]"):
        locator = element.get("data") or element.get("src")
        result.append(
            {
                "url": urljoin(url, locator),
                "source_locator": locator,
                "status": "native embedded medium; separate asset required",
            }
        )
    return result


def ancillary_units(
    data: bytes,
    family: str,
    url: str,
    inventory: NativeInventory,
    item: str = "full",
) -> list[Provision]:
    if family not in {"ucc", "constitution", "appendix"}:
        raise ValueError("Expected a Nebraska ancillary family")
    root = html.fromstring(data)
    native = {entry.identifier: entry for entry in inventory.entries}
    selector = PRINT if family == "ucc" else STATUTE if family == "constitution" else "body"
    nodes = root.xpath("//" + selector)
    if not nodes:
        raise ValueError("Missing Nebraska legal containers")
    units, found = [], []
    for node in nodes:
        if family == "constitution":
            heads, titles = node.xpath("./h2"), node.xpath("./h3")
            if len(heads) != 1 or len(titles) != 1:
                raise ValueError("Missing Nebraska constitutional heading")
            number, title = (
                readable(heads[0]).strip().removesuffix("."),
                readable(titles[0]).strip(),
            )
        else:
            heads = node.xpath("./strong")
            if not heads:
                raise ValueError("Missing Nebraska ancillary heading")
            heading = " ".join(readable(heads[0]).split()).split(" ", 1)
            if len(heading) != 2:
                raise ValueError("Unrecognized Nebraska ancillary identifier")
            number, title = heading[0].removesuffix("."), heading[1]
        if number not in native or (family == "appendix" and number != item):
            raise ValueError("Nebraska export has an identifier absent from its native index")
        found.append(number)
        notice = bool(NOTICE.match(title)) and not any(
            readable(p).strip() for p in node.xpath("./p")
        )
        prefix = {
            "ucc": "Neb. U.C.C. §",
            "constitution": "Neb. Const.",
            "appendix": "Neb. Rev. Stat. Appendix §",
        }[family]
        units.append(
            Provision(
                key=f"ne:{family}:{number}",
                citation=f"{prefix} {number}",
                heading=title,
                text=readable(node),
                markup=markup(node),
                url=native[number].url,
                parent_key=f"ne:{family}:full" if family != "appendix" else None,
                unit_kind="editorial_notice"
                if notice
                else "preamble"
                if number == "Preamble"
                else "section",
                metadata={
                    "source_identifier": number,
                    "document_kind": FAMILIES[family].document_kind,
                    "tables": len(node.xpath(".//table")),
                    "media": _ancillary_media(node, url),
                    "content_role": "supporting_notice" if notice else "publisher_legal_text",
                    "date_semantics": "Native date wording retained; no inferred blanket legal or snapshot date",
                },
                references=source_links(node, url),
            )
        )
    if found != (list(native) if family != "appendix" else [item]):
        raise ValueError("Nebraska ancillary export differs from native identifier order")
    if family != "appendix":
        content = root.xpath(
            "//div[contains(concat(' ',normalize-space(@class),' '),' main-content ')]"
        )
        if len(content) != 1:
            raise ValueError("Ambiguous Nebraska legal content region")
        context = deepcopy(content[0])
        for node in context.xpath(".//" + selector + "|.//script|.//style"):
            if node.getparent() is not None:
                node.drop_tree()
        if readable(context).strip():
            units.append(
                Provision(
                    key=f"ne:{family}:full/_context",
                    citation=FAMILIES[family].title + " publisher context",
                    heading="Publisher export context",
                    text=readable(context),
                    markup=markup(context),
                    url=url,
                    unit_kind="scope_notes",
                    metadata={
                        "media": _ancillary_media(context, url),
                        "content_role": "supporting_context",
                        "original_placement": "Retained full raw artifact",
                    },
                    references=source_links(context, url),
                )
            )
    return units


def sync_nebraska(store: Store, acquirer: Acquirer, limit: int | None, as_of: str | None) -> None:
    """Discover all native memberships, then acquire at most limit pending body documents.

    Default resume skips accepted documents; --refresh revalidates their source
    bytes. Publisher inventories are revalidated each run. The caller owns the
    single-writer lock and NebraskaAcquirer's migrated lifetime budget.
    """
    if as_of is not None:
        raise ValueError(
            "Nebraska live exports have no known snapshot date; historical sync unsupported"
        )
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    acquirer.delay = max(acquirer.delay, 3.2)
    for policy in ("disclaimer", "privacy"):
        acquirer.fetch(
            BASE + f"/contact/{policy}.php", max_age_seconds=86400, max_file_bytes=4 * 1024**2
        )
    landing = acquirer.fetch(LANDING, max_age_seconds=0, max_file_bytes=4 * 1024**2)
    links = {
        urljoin(LANDING, href)
        for href in html.fromstring(store.artifact(landing.sha256)).xpath("//a/@href")
    }
    inventories = {}
    for family, spec in FAMILIES.items():
        url = BASE + "/laws/" + spec.browse
        if url not in links:
            raise ValueError("Nebraska landing page no longer advertises " + url)
        receipt = acquirer.fetch(url, max_age_seconds=0, max_file_bytes=8 * 1024**2)
        inventory = native_inventory(store.artifact(receipt.sha256), family, url)
        inventories[family] = (inventory, receipt)
        store.collection(
            spec.collection,
            ("us-ne", "Nebraska", "state", "us"),
            name=spec.title,
            authority="Nebraska Unicameral Legislature / Revisor of Statutes",
            kind=spec.kind,
            homepage=url,
            source_status="Official publisher live compilation; exact legal currency unknown",
            access="Public; publisher robots policy enforced; no redistribution license inferred",
            metadata={
                "snapshot_date": None,
                "inventory_sha256": receipt.sha256,
                "inventory_observed_at": receipt.observed_at,
                "native_identifiers": [entry.identifier for entry in inventory.entries],
            },
        )
        current = dict(inventory.documents())
        for old in store.db.execute(
            "SELECT item,url FROM inventories WHERE collection_id=?", (spec.collection,)
        ).fetchall():
            if old["item"] not in current:
                store.inventory(
                    spec.collection, old["item"], old["url"], "not_in_current_inventory"
                )
        for item, body_url in current.items():
            accepted = store.db.execute(
                "SELECT 1 FROM documents d JOIN versions v ON v.document_id=d.id "
                "WHERE d.id=? AND d.url=? AND v.artifact_sha="
                "(SELECT sha256 FROM acquisitions WHERE url=? AND status=200 AND sha256 IS NOT NULL ORDER BY id DESC LIMIT 1)",
                (f"ne:{family}:{item}", body_url, body_url),
            ).fetchone()
            store.inventory(spec.collection, item, body_url, "acquired" if accepted else "pending")
    processed = 0
    for family, (inventory, receipt) in inventories.items():
        spec = FAMILIES[family]
        for item, url in inventory.documents():
            state = store.db.execute(
                "SELECT status FROM inventories WHERE collection_id=? AND item=?",
                (spec.collection, item),
            ).fetchone()[0]
            if state == "acquired" and not acquirer.refresh:
                continue
            if limit is not None and processed >= limit:
                return
            try:
                raw = acquirer.fetch(official_url(url), max_file_bytes=32 * 1024**2)
                observed = [raw.observed_at, receipt.observed_at]
                if family == "chapter":
                    entry = next(entry for entry in inventory.entries if entry.identifier == item)
                    index = acquirer.fetch(entry.url, max_age_seconds=0, max_file_bytes=8 * 1024**2)
                    roster = chapter_index(store.artifact(index.sha256), item, entry.url)
                    units = chapter_units(store.artifact(raw.sha256), item, url, roster)
                    observed.append(index.observed_at)
                else:
                    units = ancillary_units(
                        store.artifact(raw.sha256), family, url, inventory, item
                    )
                assets: list[dict[str, Any]] = []
                for media_url in sorted(
                    {m["url"] for unit in units for m in unit.metadata.get("media", []) if m["url"]}
                ):
                    try:
                        official_url(media_url)
                    except AcquisitionError:
                        assets.append({"url": media_url, "status": "not_acquired_out_of_scope"})
                        continue
                    media = acquirer.fetch(media_url, max_file_bytes=16 * 1024**2)
                    assets.append(
                        {
                            "url": media_url,
                            "sha256": media.sha256,
                            "acquisition_id": media.id,
                            "status": "retained",
                        }
                    )
                    observed.append(media.observed_at)
                metadata: dict[str, Any] = {
                    "document_kind": spec.document_kind,
                    "inventory_sha256": receipt.sha256,
                    "media_assets": assets,
                    "full_export": True,
                }
                if family == "chapter":
                    metadata["chapter"] = item
                else:
                    metadata["exact_native_identifiers"] = [
                        u.metadata["source_identifier"]
                        for u in units
                        if "source_identifier" in u.metadata
                    ]
                _, count, new = store.ingest(
                    collection=spec.collection,
                    document=f"ne:{family}:{item}",
                    title=spec.title
                    + (
                        f" Chapter {item}"
                        if family == "chapter"
                        else ""
                        if item == "full"
                        else " " + item
                    ),
                    url=url,
                    acquisition=raw.id,
                    snapshot_date=None,
                    snapshot_basis="Live full chapter export; publisher compilation day unknown; observation is not legal effect"
                    if family == "chapter"
                    else "Live publisher export; exact legal compilation date unknown; acquisition clock is not legal effect",
                    parser="ne-full-chapter/1" if family == "chapter" else f"ne-{family}-native/1",
                    provisions=units,
                    metadata=metadata,
                    available_at=max(observed),
                )
                store.inventory(spec.collection, item, url, "acquired")
                processed += 1
                print(
                    f"{spec.collection}: {item}: {count} units ({'indexed' if new else 'unchanged'})",
                    file=sys.stderr,
                )
            except (ValueError, AcquisitionError) as error:
                store.inventory(spec.collection, item, url, "error", str(error))
                raise  # Do not silently continue after a policy, source or fidelity failure.
