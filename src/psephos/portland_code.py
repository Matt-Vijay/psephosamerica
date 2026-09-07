"""Clerk-listed Portland code exports, native membership and explicit nontext limits."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from typing import Any
from urllib.parse import urldefrag, urljoin, urlsplit

from lxml import html

from .acquire import Acquirer, AcquisitionError
from .parse import markup, media_links, readable, source_links, source_medium
from .sources import progress
from .store import Provision, Reference, Store

BASE = "https://www.portland.gov"
INDEX = BASE + "/code"
COLLECTION = "portland-city-code"
CAP = 128 * 1024**2
FILE_CAP = 8 * 1024**2
CLOCK_NOTE = (
    "Current publisher display at acquisition, not a certified effective-law snapshot. "
    "Prefix/history notes, replacement dates and upcoming changes retain their own scope; "
    "no amendments applied and no render/acquisition date assigned as legal effectiveness."
)


def page(data: bytes) -> Any:
    if not re.search(rb"</body>\s*</html>\s*$", data, re.I):
        raise ValueError("Incomplete Portland HTML document")
    root = html.fromstring(data)
    main = root.xpath("//main")
    if len(main) != 1:
        raise ValueError("Portland main region missing/ambiguous")
    return main[0]


def view(root: Any, name: str) -> Any:
    found = root.xpath(
        f'.//div[contains(concat(" ",normalize-space(@class)," ")," view-display-id-{name} ")]'
    )
    if len(found) != 1:
        raise ValueError("Missing/ambiguous Portland inventory view: " + name)
    return found[0]


def title_list(data: bytes) -> list[dict[str, Any]]:
    root = view(page(data), "page_city_code")
    result = []
    for row in root.xpath('./div[@class="view-content"]/div[@class="views-row"]'):
        links = row.xpath(".//a")
        if len(links) != 1 or not re.fullmatch(r"/code/\d+", links[0].get("href", "")):
            raise ValueError("Unrecognized publisher title inventory row")
        link = links[0]
        number = link.get("href").rsplit("/", 1)[1]
        label = " ".join(readable(link).split())
        if not label.startswith("Title " + number + " "):
            raise ValueError("Title inventory label/URL disagreement")
        result.append({"number": number, "url": urljoin(BASE, link.get("href")), "label": label})
    if not result or len({r["number"] for r in result}) != len(result):
        raise ValueError("Empty/duplicate Portland title inventory")
    return result


def consumed(s: Store, first: int) -> int:
    return int(
        s.db.execute(
            "SELECT coalesce(sum(CAST(coalesce(json_extract(a.headers,'$.psephos_downloaded_bytes'),"
            "CASE WHEN a.status=200 THEN b.bytes ELSE 0 END) AS INTEGER)),0) FROM acquisitions a "
            "LEFT JOIN artifacts b ON b.sha256=a.sha256 WHERE a.id>=? AND a.url LIKE ?",
            (first, BASE + "/%"),
        ).fetchone()[0]
    )


def sync_portland_code(s: Store, a: Acquirer, limit: int | None, as_of: str | None) -> None:
    if limit or as_of or a.refresh:
        raise ValueError(
            "Portland code sync uses the reviewed live inventory/cache, not a guessed historical edition or partial title limit"
        )
    a.delay = max(a.delay, 2.1)
    directory = s.root / "portland-code"
    directory.mkdir(exist_ok=True)
    plan_path = directory / "plan.json"
    if plan_path.exists():
        plan = json.loads(plan_path.read_bytes())
    else:
        first = s.db.execute(
            "SELECT min(id) FROM acquisitions WHERE url=? AND status=200", (INDEX,)
        ).fetchone()[0]
        first = (
            first - 1
            if first
            else s.db.execute("SELECT coalesce(max(id),0)+1 FROM acquisitions").fetchone()[0]
        )
        a.max_bytes = min(a.max_bytes, a.downloaded + CAP - consumed(s, first))
        plan = {**preflight(s, a), "first_acquisition_id": first}
        plan_path.write_text(json.dumps(plan, indent=2) + "\n")
    a.max_bytes = min(a.max_bytes, a.downloaded + CAP - consumed(s, plan["first_acquisition_id"]))
    s.collection(
        COLLECTION,
        ("us-or-portland", "Portland, Oregon", "municipality", "us-or"),
        parents=(("us-or", "Oregon", "state", "us"),),
        name="Portland City Code — Clerk printable titles",
        authority="City of Portland Council Clerk",
        kind="municipal_code",
        homepage=INDEX,
        source_status="Official city display, not reconstructed effective-law history",
        access="Public publisher printable exports and city-hosted figures; no blanket redistribution license. Incorporated third-party/model-code texts are not fetched.",
        metadata={
            "scope": plan["scope"],
            "clock_note": CLOCK_NOTE,
            "inventory_acquisition_id": plan["root_acquisition_id"],
            "terms_acquisition_id": plan["terms_acquisition_id"],
            "changes_navigation": "The retained root index lists recent/upcoming changes separately; these have not been applied to the code display.",
            "title33": "Reused portland-zoning collection, retained July 1 2026 PDF edition; not silently refreshed",
        },
    )
    for title in plan["titles"]:
        s.inventory(COLLECTION, "title-" + title["number"], title["url"], title["status"])
    for title in plan["titles"]:
        if title["status"] not in {"planned", "failed", "indexed"}:
            continue
        try:
            raw = a.fetch(title["export_url"], max_file_bytes=FILE_CAP)
            membership = {title["url"]: title["children"]}
            queue = [c for c in title["children"] if c["kind"] == "container"]
            membership_receipts = {title["url"]: title["index_acquisition_id"]}
            while queue:
                child = queue.pop(0)
                if child["url"] in membership:
                    raise ValueError("Cyclic or duplicate publisher container membership")
                index = a.fetch(child["url"], max_file_bytes=FILE_CAP)
                children = members(s.artifact(index.sha256), child["url"])
                membership[child["url"]] = children
                membership_receipts[child["url"]] = index.id
                queue.extend(c for c in children if c["kind"] == "container")
            units = title_units(s.artifact(raw.sha256), title, membership)
            index_sha = s.db.execute(
                "SELECT sha256 FROM acquisitions WHERE id=?", (title["index_acquisition_id"],)
            ).fetchone()[0]
            changes = page(s.artifact(index_sha)).xpath(
                './/div[@data-block-plugin-id="views_block:change_sets-effected_on_codecharterpolicy"]'
            )
            if len(changes) > 1:
                raise ValueError("Ambiguous title changes context")
            if changes:
                units.append(
                    Provision(
                        source_key(title["url"]) + "/changes-context",
                        "Portland City Code Title " + title["number"] + " — changes context",
                        "Publisher upcoming and recent changes (not applied)",
                        readable(changes[0]),
                        markup(changes[0]),
                        title["url"],
                        parent_key=source_key(title["url"]),
                        unit_kind="changes_context",
                        metadata={
                            "clock_note": CLOCK_NOTE,
                            "source_acquisition_id": title["index_acquisition_id"],
                        },
                        references=code_references(changes[0], title["url"]),
                    )
                )
            media_urls = sorted(
                {
                    m["url"]
                    for p in units
                    for m in p.metadata.get("media", [])
                    if not m.get("original_url")
                }
            )
            media_outcomes = []
            for url in media_urls:
                parts = urlsplit(url)
                if (
                    parts.scheme != "https"
                    or parts.netloc != "www.portland.gov"
                    or not parts.path.startswith("/sites/default/files/")
                ):
                    media_outcomes.append(
                        {"url": url, "status": "not_fetched_external_or_embedded_media"}
                    )
                    continue
                try:
                    image = a.fetch(url, max_file_bytes=FILE_CAP)
                    content = s.artifact(image.sha256)
                    if not content.startswith(
                        (b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"\xff\xd8\xff")
                    ):
                        raise ValueError("Publisher media is not a supported raster image")
                    media_outcomes.append(
                        {
                            "url": url,
                            "status": "retained_not_transcribed",
                            "acquisition_id": image.id,
                        }
                    )
                except (AcquisitionError, ValueError) as exc:
                    media_outcomes.append({"url": url, "status": "unavailable", "error": str(exc)})
            _, count, new = s.ingest(
                collection=COLLECTION,
                document=source_key(title["url"]),
                title=title["label"],
                url=title["export_url"],
                acquisition=raw.id,
                snapshot_basis=CLOCK_NOTE,
                parser="portland-code-1",
                provisions=units,
                metadata={
                    "membership_acquisitions": membership_receipts,
                    "clock_note": CLOCK_NOTE,
                    "media_outcomes": media_outcomes,
                    "native_title_url": title["url"],
                },
            )
            title.update(
                status="indexed",
                export_acquisition_id=raw.id,
                membership=membership,
                membership_acquisitions=membership_receipts,
                counts=dict(Counter(p.unit_kind for p in units)),
                media_outcomes=media_outcomes,
            )
            title.pop("error", None)
            s.inventory(COLLECTION, "title-" + title["number"], title["url"], "indexed")
            progress(COLLECTION, title["number"], count, new)
        except (ValueError, AcquisitionError) as exc:
            title.update(status="failed", error=str(exc))
            s.inventory(COLLECTION, "title-" + title["number"], title["url"], "failed", str(exc))
            raise
        finally:
            plan_path.write_text(json.dumps(plan, indent=2) + "\n")


def members(data: bytes, url: str) -> list[dict[str, str]]:
    root = page(data)
    result = []
    for name in ("eva_code_chapters", "eva_code_sections"):
        for row in view(root, name).xpath('./div[@class="views-row"]'):
            links = row.xpath(
                "./article/h2/a"
                if name.endswith("sections")
                else './div[contains(concat(" ",@class," ")," views-field-title ")]/span/a'
            )
            if len(links) != 1:
                raise ValueError("Native member row missing/duplicate link")
            target = urldefrag(urljoin(BASE, links[0].get("href", "")))[0]
            # Native slugs are not hierarchy: chapter /1/05 lists /1/5/010.
            # Membership and printable nesting must agree; only the title prefix is stable.
            if source_key(target).split("/")[0] != source_key(url).split("/")[0]:
                raise ValueError("Member outside its native title: " + target)
            result.append(
                {
                    "url": target,
                    "label": " ".join(readable(links[0]).split()),
                    "kind": "section" if name.endswith("sections") else "container",
                    **(
                        {"content_sha256": content_signature(row)}
                        if name.endswith("sections")
                        else {}
                    ),
                }
            )
    if len({r["url"] for r in result}) != len(result):
        raise ValueError("Duplicate native member URL")
    return result


def content_signature(node: Any) -> str:
    """Compare the independently displayed prefix/body fields, not Drupal wrappers."""
    fields = node.xpath(
        './/div[contains(concat(" ",@class," ")," field--name-field-prefix-note ") or contains(concat(" ",@class," ")," field--name-field-section-body ")]'
    )
    values = [
        {
            "text": " ".join(readable(f).split()),
            "tables": len(f.xpath(".//table")),
            "images": f.xpath(".//img/@src"),
        }
        for f in fields
    ]
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def preflight(s: Store, a: Acquirer) -> dict[str, Any]:
    root = a.fetch(INDEX, max_file_bytes=FILE_CAP)
    terms = a.fetch(BASE + "/help/about/privacy", max_file_bytes=FILE_CAP)
    titles = title_list(s.artifact(root.sha256))
    for title in titles:
        receipt = a.fetch(title["url"], max_file_bytes=FILE_CAP)
        title["index_acquisition_id"] = receipt.id
        if title["number"] == "33":
            title.update(status="reuse_retained_zoning_collection", children=[])
            continue
        data = s.artifact(receipt.sha256)
        links = page(data).xpath('.//a[contains(@href,"/all")]/@href')
        if links != [urlsplit(title["url"]).path + "/all"]:
            title.update(status="omitted_no_unique_printable_export", children=[])
            continue
        title.update(
            status="planned",
            export_url=urljoin(BASE, links[0]),
            children=members(data, title["url"]),
        )
    return {
        "root_acquisition_id": root.id,
        "terms_acquisition_id": terms.id,
        "titles": titles,
        "scope": "Exact retained Clerk title inventory. Supported printable titles indexed; Title 33 reused separately at its retained edition. Not all city law, charter, policies, emergency rules or incorporated model codes.",
        "clock_note": CLOCK_NOTE,
    }


def source_key(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.netloc != "www.portland.gov"
        or parts.query
        or parts.fragment
        or not re.fullmatch(r"/code/[0-9]+(?:/[a-zA-Z0-9-]+)*", parts.path)
    ):
        raise ValueError("Not a native Portland code node URL: " + url)
    return "pdx-code:" + parts.path.removeprefix("/code/")


def code_media(node: Any, url: str) -> list[dict[str, str]]:
    media = media_links(node, url)
    for link in node.xpath('.//a[contains(concat(" ",@class," ")," view-image ")]'):
        original = source_medium(link.get("href", ""), url, readable(link))
        original["source_relationship"] = (
            "Publisher full-size image link; preview is separate, neither is transcribed"
        )
        for parent in link.iterancestors():
            if "media" in parent.get("class", "").split():
                previews = {urljoin(url, src) for src in parent.xpath(".//img/@src")}
                for descriptor in media:
                    if descriptor["url"] in previews:
                        descriptor["original_url"] = original["url"]
                break
        if original["url"] not in {m["url"] for m in media}:
            media.append(original)
    return media


def code_references(node: Any, url: str) -> tuple[Reference, ...]:
    refs = list(source_links(node, url))
    text = readable(node)
    for match in re.finditer(
        r"\b(?:Section|Sections|Subsection|Chapter|Title)\s+(\d{1,2}[A-C]?\.\d{2,3}(?:\.\d{3})?)\b",
        text,
    ):
        label = match[0]
        refs.append(
            Reference(
                "PCC " + match[1],
                "printed_portland_citation",
                label,
                text[max(0, match.start() - 60) : match.end() + 100],
            )
        )
    for match in re.finditer(r"\bORS\s+(\d{1,3}[A-Z]?\.\d{3})\b", text):
        refs.append(
            Reference(
                "ors:" + match[1],
                "printed_oregon_statute_citation",
                match[0],
                text[max(0, match.start() - 60) : match.end() + 100],
            )
        )
    return tuple(refs)


def title_units(
    data: bytes, title: dict[str, Any], membership: dict[str, list[dict[str, str]]]
) -> list[Provision]:
    root = page(data)
    headings = root.xpath('.//h1[@class="page-title"]')
    if len(headings) != 1 or " ".join(readable(headings[0]).split()) != title["label"]:
        raise ValueError("Printable title heading differs from retained inventory")
    container = view(root, "page_title_all")
    nodes = container.xpath('.//div[@class="node--embedded"]')
    expected = {child["url"]: child for group in membership.values() for child in group}
    if len(expected) != sum(len(g) for g in membership.values()):
        raise ValueError("Member occurs under multiple parents")
    parents = {child["url"]: parent for parent, group in membership.items() for child in group}
    obtained = []
    result = []
    title_key = source_key(title["url"])
    context = deepcopy(container)
    for node in context.xpath('.//div[@class="node--embedded"]'):
        if node.getparent() is not None:
            node.drop_tree()
    result.append(
        Provision(
            title_key,
            "Portland City Code Title " + title["number"],
            title["label"],
            title["label"] + "\n" + readable(context),
            markup(context),
            title["url"],
            unit_kind="title_context",
            metadata={"clock_note": CLOCK_NOTE},
            references=code_references(context, title["url"]),
        )
    )
    for node in nodes:
        links = node.xpath("./h2/a | ./h3/a | ./h4/a | ./h5/a | ./h6/a")
        if len(links) != 1:
            raise ValueError("Embedded code node has no unique native heading link")
        url = urljoin(BASE, links[0].get("href", ""))
        label = " ".join(readable(links[0]).split())
        if url not in expected or label != expected[url]["label"]:
            raise ValueError("Printable body/member label mismatch: " + url)
        obtained.append(url)
        native_parent = next(
            (a for a in node.iterancestors() if a.get("class") == "node--embedded"), None
        )
        parent_url = (
            title["url"]
            if native_parent is None
            else urljoin(
                BASE,
                native_parent.xpath("./h2/a/@href | ./h3/a/@href | ./h4/a/@href | ./h5/a/@href")[0],
            )
        )
        if parents[url] != parent_url:
            raise ValueError("Printable/native parent hierarchy mismatch: " + url)
        own = deepcopy(node)
        for embedded in own.xpath('./div[@class="views-element-container"]'):
            embedded.drop_tree()
        if (
            expected[url]["kind"] == "section"
            and content_signature(own) != expected[url]["content_sha256"]
        ):
            raise ValueError("Printable section body differs from native chapter display: " + url)
        number = re.match(r"(?:Chapter )?(\d{1,2}[A-C]?\.\d{2,3}(?:\.\d{3})?)(?:\s|\.|$)", label)
        kind = (
            "section"
            if expected[url]["kind"] == "section"
            else "chapter_context"
            if number
            else "figures_context"
        )
        if kind == "section" and (not number or number[1].count(".") != 2):
            raise ValueError("Section listing has no native citation: " + url)
        citation = "Portland City Code " + (number[1] if number else label)
        result.append(
            Provision(
                source_key(url),
                citation,
                label,
                readable(own),
                markup(own),
                url,
                parent_key=source_key(parent_url),
                unit_kind=kind,
                metadata={
                    "source_number": number[1] if number else None,
                    "tables": len(own.xpath(".//table")),
                    "media": code_media(own, url),
                    "clock_note": CLOCK_NOTE,
                    "list_numbering": "Source printed labels and list markup preserved; generated ordered-item markers are not legal paragraph identifiers.",
                },
                references=code_references(own, url),
            )
        )
    if Counter(obtained) != Counter(expected.keys()):
        raise ValueError("Printable export differs from complete publisher member inventory")
    # This source explicitly names the drawings/table and the image alt labels name the section.
    figures = next((p for p in result if p.url == BASE + "/code/24/figures"), None)
    if figures:
        section = next((p for p in result if p.url == BASE + "/code/24/70/090"), None)
        if (
            section is None
            or not all(
                marker in section.text for marker in ("24.70-C", "Figure No. 2", "Figure No. 3")
            )
            or not all(
                "24.70.090" in m["alt"]
                for m in figures.metadata["media"]
                if "figure" in m["alt"].lower()
            )
        ):
            raise ValueError("Portland figure/section source relationship changed; review required")
        for index, p in enumerate(result):
            if p.url in {figures.url, BASE + "/code/24/70/090"}:
                meta = {
                    **p.metadata,
                    "media_warning": "Publisher Figure 1/2 headings disagree with Figure 2/3 alt labels; section 24.70.090 calls Figure 2/3 and Table 24.70-C. Preserve source labels; no relabeling or numeric transcription.",
                }
                if p.url != figures.url:
                    meta["media"] = figures.metadata["media"]
                result[index] = replace(
                    p,
                    metadata=meta,
                    references=(
                        *p.references,
                        Reference(
                            source_key(figures.url),
                            "publisher_named_figure_dependency",
                            "Figures/Table for 24.70.090",
                            "Section 24.70.090 names Figure 2/3 and Table 24.70-C; figure alt labels explicitly name this section.",
                        ),
                    ),
                )
    return result
