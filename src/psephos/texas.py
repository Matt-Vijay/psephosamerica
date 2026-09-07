"""Texas Legislative Council's supported zipped chapter HTML exports."""

from __future__ import annotations

import json
import sys
from collections import Counter
from collections.abc import Iterator
from copy import deepcopy
from pathlib import PurePosixPath

from lxml import etree, html

from .acquire import Acquirer, AcquisitionError
from .parse import markup, readable, source_links
from .sources import checked_zip, progress
from .store import Provision, Store

INDEX = "https://statutes.capitol.texas.gov/assets/StatuteCodeDownloads.json"
FILES = "https://tcss.legis.texas.gov/resources"


def texas_units(data: bytes, code: str, name: str, member: str) -> Iterator[Provision]:
    root = html.fromstring(data)
    chapter = PurePosixPath(member).stem.split(".", 1)[-1]
    title = " ".join(root.xpath("//title/text()"))
    container = root.xpath("//pre")
    if len(container) != 1:
        raise ValueError("Texas export must have exactly one publisher content block")
    groups: list[tuple[str, etree._Element]] = []
    current = etree.Element("div")
    anchor = "_context"
    for child in container[0]:
        anchors = child.xpath(".//a[@name]/@name")
        if anchors:
            if readable(current):
                groups.append((anchor, current))
            current = etree.Element("div")
            anchor = str(anchors[0])
        current.append(deepcopy(child))
    if readable(current):
        groups.append((anchor, current))
    if not groups:
        raise ValueError("Empty Texas chapter")
    totals = Counter(key for key, _ in groups)
    seen: Counter[str] = Counter()
    for anchor, node in groups:
        seen[anchor] += 1
        heading_links = node.xpath('.//a[@href][contains(@style,"font-weight:bold")]')
        heading = readable(heading_links[0]) if heading_links else title
        url = (
            heading_links[0].get("href", "")
            if heading_links
            else "https://statutes.capitol.texas.gov/download.aspx"
        )
        suffix = f"/_occurrence/{seen[anchor]}" if totals[anchor] > 1 else ""
        prefix = f"Tex. {name} "
        citation = prefix + (
            f"Art. {chapter}, § {anchor.split('.', 1)[-1]}" if code == "CN" else f"§ {anchor}"
        )
        yield Provision(
            key=f"tx:{code}/{anchor}" + suffix
            if anchor != "_context"
            else f"tx:{code}/chapter-{chapter}/_context",
            citation=citation if anchor != "_context" else title + " — context",
            heading=heading,
            text=readable(node),
            markup=markup(node),
            url=url,
            parent_key=f"tx:{code}/chapter-{chapter}",
            unit_kind="scope_notes" if anchor == "_context" else "section",
            metadata={
                "chapter": chapter,
                "chapter_title": title,
                "source_anchor": anchor,
                "duplicate_anchor_count": totals[anchor],
                "occurrence": seen[anchor],
                "date_warning": "Future-effective parallel text and history notes are preserved, not resolved.",
            },
            references=source_links(node, "https://statutes.capitol.texas.gov"),
        )


def sync_texas(s: Store, a: Acquirer, limit: int | None, as_of: str | None) -> None:
    if as_of:
        raise ValueError(
            "Texas bulk exports have no precise snapshot date; historical lookup is not implemented"
        )
    index_receipt, index = a.json(INDEX)
    notice = a.fetch("https://statutes.capitol.texas.gov/information/")
    page = html.fromstring(s.artifact(notice.sha256))
    state = json.loads(page.xpath('//script[@id="ng-state"]/text()')[0])
    messages = [
        row["b"]
        for row in state.values()
        if isinstance(row, dict) and row.get("u", "").endswith("StatutesCurrentMsg")
    ]
    s.collection(
        "texas",
        ("us-tx", "Texas", "state", "us"),
        name="Texas Constitution and Statutes",
        authority="Texas Legislative Council",
        kind="statutory_code_and_constitution",
        homepage="https://statutes.capitol.texas.gov/download.aspx",
        source_status="Council-published bulk compilation; historical/future-effective notes retained",
        access="Publisher-supported per-code chapter HTML ZIP downloads; information/privacy notice retained",
        metadata={
            "currency_notice": messages,
            "inventory_artifact": index_receipt.sha256,
            "notice_artifact": notice.sha256,
            "snapshot_warning": "Session-level currency, not a precisely dated export. As-of excludes undated versions.",
        },
    )
    codes = index["StatuteCode"]
    for code in codes:
        s.inventory("texas", code["code"], FILES + code["Html"], "pending")
    for code in codes[:limit]:
        url = FILES + code["Html"]
        try:
            raw = a.fetch(url)
            total = 0
            with checked_zip(s.object_path(raw.sha256)) as archive:
                members = [
                    member for member in archive.namelist() if member.lower().endswith(".htm")
                ]
                if not members:
                    raise ValueError("No HTML chapters in official ZIP")
                for member in members:
                    document = "tx:" + code["code"] + "/" + PurePosixPath(member).stem.lower()
                    _, count, new = s.ingest(
                        collection="texas",
                        document=document,
                        title=code["CodeName"] + " — " + member,
                        url=url,
                        acquisition=raw.id,
                        member=member,
                        snapshot_date=None,
                        snapshot_basis="Undated bulk export; publisher gives legislative-session currency only",
                        parser="texas-1",
                        provisions=texas_units(
                            archive.read(member), code["code"], code["CodeName"], member
                        ),
                        metadata={
                            "currency_notice": messages,
                            "inventory_artifact": index_receipt.sha256,
                            "chapters_in_archive": len(members),
                        },
                    )
                    total += count
                    s.inventory("texas", code["code"] + "/" + member, url, "indexed")
            s.inventory("texas", code["code"], url, "indexed")
            progress("texas", code["code"], total, True)
        except (ValueError, AcquisitionError) as exc:
            s.inventory("texas", code["code"], url, "failed", str(exc))
            print(f"texas: {code['code']}: FAILED: {exc}", file=sys.stderr)
