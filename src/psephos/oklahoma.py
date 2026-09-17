"""Senate-linked Oklahoma PDFs: retained source editions, not current-law certification."""

from __future__ import annotations

import html as stdhtml
import json
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from lxml import html
from pypdf import PdfReader

from .acquire import USER_AGENT, Acquirer, AcquisitionError, Receipt, robots_lines
from .campaign import CampaignAcquirer, save
from .store import Provision, Reference, Store, json_text

BASE = "https://oksenate.gov"
INDEX = BASE + "/search-statutes-constitution"
TERMS = BASE + "/privacy-policy"
COLLECTION = "ok-senate-scoped-code"
PARSER = "ok-senate-pdf-2"
CAP = 200 * 1024**2


class OklahomaAcquirer(CampaignAcquirer):
    automatic_retries = False
    consumed_field = "charged_bytes"

    @classmethod
    def budget_file(cls, store: Store, directory: Path) -> Path:
        original = store.root / "collectors/oklahoma/http_ledger.json"
        return original if original.exists() else super().budget_file(store, directory)

    def __init__(self, store: Store, directory: Path):
        path = self.budget_file(store, directory)
        if path.exists():
            old = json.loads(path.read_bytes())
            if "cap_bytes" not in old:
                if old.get("cap") != CAP:
                    raise AcquisitionError("Original Oklahoma allowance differs from reviewed cap")
                save(
                    path,
                    {
                        **old,
                        "cap_bytes": old["cap"],
                        "last_request_unix": old.get("hosts", {})
                        .get("oksenate.gov", {})
                        .get("last_request", 0),
                    },
                )
        super().__init__(store, directory, cap=CAP, file_cap=16 * 1024**2, delay=1.1)

    @property
    def downloaded(self) -> int:
        return int(self.budget["charged_bytes"])

    @downloaded.setter
    def downloaded(self, value: int) -> None:
        if not self._initializing:
            self.budget["decoded_bytes"] = (
                self.budget.get("decoded_bytes", 0) + value - self.downloaded
            )
            self.budget["charged_bytes"] = value
            self.budget["reserved_bytes"] = 0
            save(self.budget_path, self.budget)

    def _pause(self, url: str, delay: float | None = None) -> None:
        if urlsplit(url).hostname != "oksenate.gov":
            raise AcquisitionError("Oklahoma continuation accepts only the reviewed Senate host")
        hosts = self.budget.get("hosts", {})
        if not isinstance(hosts, dict):
            raise AcquisitionError("Invalid original Oklahoma host ledger")
        previous = hosts.get("oksenate.gov", {})
        if previous.get("barrier") or previous.get("retry_until", 0) > time.time():
            raise AcquisitionError("Original Oklahoma host barrier remains in force")
        if shutil.disk_usage(self.store.root).free < 105 * 1024**3:
            raise AcquisitionError("Original Oklahoma 105 GiB disk floor reached")
        super()._pause(url, delay)

    def _allowed(self, url: str) -> None:
        # urllib drops a terminal '?' in rules, misreading the publisher's /search?
        # as /search. Keep the original collector's literal rule-path interpretation.
        if BASE not in self.robots:
            receipt = self.fetch(BASE + "/robots.txt", check_robots=False, max_age_seconds=86400)
            self.policy = robots_lines(self.store.artifact(receipt.sha256))
            parser = RobotFileParser(BASE + "/robots.txt")
            parser.parse(self.policy)
            self.robots[BASE] = parser
        if not policy_allows(self.policy, url):
            raise AcquisitionError("Disallowed by publisher robots policy: " + url)
        parser = self.robots[BASE]
        rate = parser.request_rate(USER_AGENT)
        if rate:
            self.delay = max(self.delay, rate.seconds / rate.requests)

    def fetch(self, url: str, **options: Any) -> Receipt:
        if urlsplit(url).hostname != "oksenate.gov":
            raise AcquisitionError("Oklahoma continuation accepts only the reviewed Senate host")
        if self.store.db.execute(
            "SELECT 1 FROM acquisitions WHERE (url=? OR final_url=?) AND status IN (401,403,407,451) LIMIT 1",
            (url, url),
        ).fetchone():
            raise AcquisitionError("Retained access denial; review publisher/runtime permissions")
        options["max_file_bytes"] = min(options.get("max_file_bytes", self.file_cap), self.file_cap)
        return super().fetch(url, **options)


def policy_allows(lines: list[str], url: str) -> bool:
    groups: list[tuple[list[str], list[tuple[str, bool]]]] = []
    agents: list[str] = []
    rules: list[tuple[str, bool]] = []
    begun = False
    for line in [*lines, "User-agent: __end__"]:
        line = line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        if key.lower() == "user-agent":
            if begun:
                groups.append((agents, rules))
                agents, rules, begun = [], [], False
            agents.append(value.lower())
        elif agents:
            begun = True
            if key.lower() in ("allow", "disallow") and value:
                rules.append((value, key.lower() == "allow"))
    specific = [g for g in groups if any(a != "*" and a in USER_AGENT.lower() for a in g[0])]
    selected = specific or [g for g in groups if "*" in g[0]]
    parts = urlsplit(url)
    path = parts.path + ("?" + parts.query if parts.query else "")
    matches = []
    for _, group_rules in selected:
        for pattern, allow in group_rules:
            expression = "^" + re.escape(pattern).replace(r"\*", ".*")
            if pattern.endswith("$"):
                expression = expression[:-2] + "$"
            if re.search(expression, path):
                matches.append((len(pattern.replace("*", "").rstrip("$")), allow))
    return not matches or max(matches)[1]


def inventory_page(data: bytes, url: str) -> tuple[list[tuple[str, str]], set[int]]:
    rows, pages = [], {0}
    for node in html.fromstring(data).xpath("//a[@href]"):
        target = urljoin(url, str(node.get("href")))
        parts = urlsplit(target)
        label = node.text_content().strip()
        if parts.path.lower().endswith(".pdf"):
            if (
                parts.scheme != "https"
                or parts.netloc != "oksenate.gov"
                or parts.query
                or parts.fragment
            ):
                raise ValueError("Unexpected Oklahoma PDF link")
            if not re.fullmatch(
                r"(?:Title \d+[A-Z]?(?:\..*)?|Article \d+(?:-[A-Z])?\..*|Download entire Oklahoma Constitution)",
                label,
            ):
                raise ValueError("Unrecognized Oklahoma inventory label: " + label)
            rows.append((label, target))
        elif parts.scheme + "://" + parts.netloc + parts.path == INDEX and parts.query:
            query = parse_qs(parts.query)
            if set(query) != {"page"} or len(query["page"]) != 1 or not query["page"][0].isdigit():
                raise ValueError("Unexpected Oklahoma inventory pagination")
            number = int(query["page"][0])
            if number > 100:
                raise ValueError("Oklahoma inventory pagination exceeds reviewed range")
            pages.add(number)
    if not rows or len({u for _, u in rows}) != len(rows):
        raise ValueError("Empty or duplicate Oklahoma PDF inventory page")
    return rows, pages


def slug(label: str) -> str:
    if label == "Download entire Oklahoma Constitution":
        return "constitution"
    match = re.match(r"(Title|Article) (\d+(?:-?[A-Z])?)\b", label)
    if not match:
        raise ValueError("Unrecognized Oklahoma source identity")
    return match[1].lower() + "-" + match[2]


def roman(number: int) -> str:
    result = ""
    for value, letters in (
        (100, "C"),
        (90, "XC"),
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    ):
        while number >= value:
            result += letters
            number -= value
    return result


def volume_label(label: str) -> str:
    # The catalog's 74E is a locator for Title 74's appendix, not a statutory title.
    if slug(label) == "title-74E":
        return "Title 74, Appendix I, Ethics Commission Rules"
    return " ".join(label.split())


def pdf_units(s: Store, receipt: Receipt, label: str, key: str) -> list[Provision]:
    label = " ".join(label.split())
    path = s.object_path(receipt.sha256)
    if not s.artifact(receipt.sha256).startswith(b"%PDF-") or not shutil.which("pdftotext"):
        raise ValueError("A source PDF and Poppler are required")
    reader = PdfReader(path)
    text = subprocess.run(
        ["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"],
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    ).stdout
    pages = text.split("\f")
    if not pages[-1].strip():
        pages.pop()
    if len(pages) != len(reader.pages) or not pages:
        raise ValueError("Oklahoma PDF page count mismatch")
    identity = slug(label)
    if identity == "title-74E":
        pattern = r"(?mi)^\s*TITLE 74, APPENDIX I, ETHICS COMMISSION RULES\s*$"
    elif identity.startswith("title-"):
        pattern = r"(?mi)^\s*TITLE\s+" + re.escape(identity[6:]) + r"\.(?:\s|$)"
    elif identity.startswith("article-"):
        number = re.fullmatch(r"article-(\d+)(-[A-Z])?", identity)
        assert number
        native = re.escape(roman(int(number[1])) + (number[2] or ""))
        pattern = rf"(?mi)^\s*(?:ARTICLE\s+{native}(?:\s|\.|$)|SECTION\s+{native}-\d+(?:\s|\.|$))"
    else:
        pattern = r"(?mi)^\s*OKLAHOMA CONSTITUTION\s*$"
    if not re.search(pattern, pages[0]):
        raise ValueError("PDF opening identity differs from publisher inventory: " + label)
    if not any(
        re.search(r"\b(?:shall|may|repealed)\b", page, re.I)
        and any(
            re.match(r"\s*(?:\u00a7\d|SECTION\s+[IVXLC]+|Rule\s+\d+\.\d+)", line)
            and not re.search(r"\.{4,}", line)
            for line in page.splitlines()
        )
        for page in pages
    ):
        raise ValueError("No demonstrated legal text or statutory notice: " + label)
    units = []
    for n, (page, pdfpage) in enumerate(zip(pages, reader.pages, strict=True), 1):
        image_count = len(pdfpage.images)
        if len(page.strip()) < 30 or "\ufffd" in page:
            raise ValueError(f"PDF page {n} needs source-media review; not silently omitted")
        refs = []
        for annotation in pdfpage.get("/Annots", []):
            obj = annotation.get_object()
            action = obj.get("/A")
            action = action.get_object() if action else {}
            if action.get("/URI"):
                target = str(action["/URI"])
                refs.append(
                    Reference(
                        target,
                        "publisher_pdf_link",
                        str(obj.get("/Contents", "")),
                        json_text(
                            {
                                "page": n,
                                "uri": target,
                                "rectangle": [float(x) for x in obj.get("/Rect", [])],
                            }
                        ),
                    )
                )
        projected = page
        if image_count:
            projected += f"\n[Source media: {image_count} PDF images on page {n}; retained in original PDF, not transcribed.]\n"
        units.append(
            Provision(
                key=f"ok:{key}:page:{n}",
                citation=f"Oklahoma {volume_label(label)}, PDF page {n}",
                heading=f"{volume_label(label)} - page {n}",
                text=projected,
                markup="<pre>" + stdhtml.escape(projected) + "</pre>",
                url=receipt.url + f"#page={n}",
                unit_kind="source_page",
                metadata={
                    "pdf_page": n,
                    "pdf_pages": len(pages),
                    "image_count": image_count,
                    "source_inventory_label": label,
                    "raw_pdf_sha256": receipt.sha256,
                    "text_quality": "layout_text_unverified",
                    "warning": "Physical page, not a legal section. Contents/notices retained; visual/table geometry not certified.",
                },
                references=tuple(refs),
            )
        )
    return units


def sync_oklahoma(s: Store, a: Acquirer, limit: int | None, as_of: str | None) -> dict[str, Any]:
    if as_of is not None or (limit is not None and limit < 0):
        raise ValueError(
            "Oklahoma needs a nonnegative limit and has no verified historical endpoint"
        )
    terms = a.fetch(TERMS)
    queue, visited = {0}, set()
    entries: dict[str, str] = {}
    inventory_receipts = []
    while queue:
        page = min(queue)
        queue.remove(page)
        url = INDEX + (f"?page={page}" if page else "")
        receipt = a.fetch(url)
        rows, linked = inventory_page(s.artifact(receipt.sha256), url)
        visited.add(page)
        queue.update(linked - visited)
        inventory_receipts.append(receipt.id)
        for label, target in rows:
            if target in entries and entries[target] != label:
                raise ValueError("Conflicting native labels for an Oklahoma PDF")
            entries[target] = label
    if visited != set(range(max(visited) + 1)):
        raise ValueError("Oklahoma publisher pagination is incomplete")
    counts = Counter(slug(label) for label in entries.values())
    s.collection(
        COLLECTION,
        ("us-ok", "Oklahoma", "state", "us"),
        name="Oklahoma Senate Constitution and Statutes - retained PDF editions",
        authority="Oklahoma Senate",
        kind="constitution_and_statutes",
        homepage=INDEX,
        source_status="Government publisher; retained source editions of unknown legal currency",
        access="Public HTTPS; robots and privacy receipts retained; no blanket redistribution license asserted",
        metadata={
            "scope": "Native linked PDF inventory, including whole constitution and separate article companions; duplicate title exports are not assumed equivalent.",
            "current_law_completeness": False,
            "snapshot_warning": "Upload folders, PDF creation and HTTP clocks are not legal currency. Later amendments are not consolidated.",
            "inventory_receipts": inventory_receipts,
            "inventory_entries": len(entries),
            "privacy_sha256": terms.sha256,
        },
    )
    attempted = accepted = 0
    for url, label in entries.items():
        key = slug(label)
        if counts[key] > 1:
            key += "/" + Path(urlsplit(url).path).stem
        document = "ok-senate:" + key
        previous = s.db.execute(
            "SELECT status FROM inventories WHERE collection_id=? AND item=?", (COLLECTION, label)
        ).fetchone()
        if not previous or previous["status"] == "outside_scoped_batch":
            s.inventory(COLLECTION, label, url, "pending")
        if (
            not a.refresh
            and s.db.execute("SELECT 1 FROM versions WHERE document_id=?", (document,)).fetchone()
        ):
            if not previous or previous["status"] in {"outside_scoped_batch", "pending"}:
                s.inventory(COLLECTION, label, url, "complete_publisher_pdf_text")
            continue
        prior = s.db.execute(
            "SELECT id,status,headers FROM acquisitions WHERE url=? ORDER BY id DESC LIMIT 1",
            (url,),
        ).fetchone()
        if (
            not a.refresh
            and prior
            and prior["status"] in (404, 410)
            and "retry-after" not in json.loads(prior["headers"])
        ):
            s.inventory(
                COLLECTION,
                label,
                url,
                "source_unavailable",
                f"Publisher HTTP {prior['status']}; receipt {prior['id']}",
            )
            continue
        if limit is not None and attempted >= limit:
            continue
        attempted += 1
        try:
            receipt = a.fetch(url)
            units = pdf_units(s, receipt, label, key)
            s.ingest(
                collection=COLLECTION,
                document=document,
                title=volume_label(label),
                url=url,
                acquisition=receipt.id,
                parser=PARSER,
                provisions=units,
                snapshot_basis="Unknown publisher incorporation date; upload path, PDF creation and HTTP timestamps are not legal currency",
                metadata={
                    "publisher_inventory_label": label,
                    "inventory_receipts": inventory_receipts,
                    "pages": len(units),
                    "source_identity": slug(label),
                    "parallel_export": counts[slug(label)] > 1,
                    "scope": "Whole linked PDF, not current-law completeness; constitutional articles may overlap the whole constitution",
                },
            )
            s.inventory(COLLECTION, label, url, "complete_publisher_pdf_text")
            accepted += 1
            print(f"Oklahoma {label}: {len(units)} pages", file=sys.stderr, flush=True)
        except (AcquisitionError, ValueError, subprocess.SubprocessError) as exc:
            s.inventory(COLLECTION, label, url, "failed", str(exc))
            print(f"Oklahoma {label}: {exc}", file=sys.stderr, flush=True)
            if isinstance(exc, AcquisitionError):
                raise
    return {
        "native_inventory_entries": len(entries),
        "inventory_pages": len(visited),
        "attempted": attempted,
        "accepted_this_run": accepted,
        "current_law_complete": False,
    }
