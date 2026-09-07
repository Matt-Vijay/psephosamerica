"""Explicit publisher adapters. No discovery crawler and no guessed legal relationships."""

from __future__ import annotations

import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from lxml import html

from .acquire import Acquirer, AcquisitionError
from .parse import child_text, ecfr_units, uscode_units, xml_root
from .retrieve import Reader, cutoff_date
from .store import Store

USC_INDEX = "https://uscode.house.gov/download/download.shtml"
ECFR_INDEX = "https://www.ecfr.gov/api/versioner/v1/titles.json"


def progress(collection: str, item: str, count: int, new: bool) -> None:
    print(
        f"{collection}: {item}: {count} units ({'indexed' if new else 'unchanged'})",
        file=sys.stderr,
    )


def checked_zip(path: Path) -> zipfile.ZipFile:
    archive = zipfile.ZipFile(path)
    if sum(x.file_size for x in archive.infolist()) > 4 * 1024**3:
        archive.close()
        raise ValueError("Archive exceeds 4 GiB expanded cap")
    if any(x.file_size > 256 * 1024**2 for x in archive.infolist()):
        archive.close()
        raise ValueError("Archive member exceeds 256 MiB cap")
    return archive


def sync_uscode(s: Store, a: Acquirer, limit: int | None, as_of: str | None) -> None:
    if as_of:
        raise ValueError(
            "US Code sync currently supports the current OLRC release; no guessed historic URLs"
        )
    receipt = a.fetch(USC_INDEX)
    page = html.fromstring(s.artifact(receipt.sha256))
    text = " ".join(page.text_content().split())
    match = re.search(r"Current Release Point Public Law (\d+-\d+) \((\d\d/\d\d/\d{4})\)", text)
    if not match:
        raise ValueError("OLRC release-point format changed")
    release, released = match.groups()
    snapshot = datetime.strptime(released, "%m/%d/%Y").date().isoformat()
    links = [urljoin(USC_INDEX, link) for link in page.xpath("//a/@href") if "/xml_usc" in link]
    individual = [link for link in links if "xml_uscAll" not in link]
    reserved = {
        urljoin(USC_INDEX, link)
        for item in page.xpath('//div[@class="uscitem"]')
        if "[Reserved]" in " ".join(item.xpath('./div[@class="usctitle"]//text()'))
        for link in item.xpath(".//a/@href")
        if "/xml_usc" in link
    }
    s.collection(
        "uscode",
        ("us", "United States", "federal", None),
        name="United States Code",
        authority="Office of the Law Revision Counsel, US House of Representatives",
        kind="statutory_code",
        homepage=USC_INDEX,
        source_status="Publisher USLM compilation; positive-law status retained per title",
        access="Publisher-provided all-title/per-title bulk ZIP downloads; US government publication",
        metadata={
            "release_point": release,
            "current_through_public_law_date": snapshot,
            "inventory_artifact": receipt.sha256,
            "warning": "Release-point currency is not a blanket effective date.",
        },
    )
    for link in individual:
        item_match = re.search(r"xml_(usc[^@]+)", link)
        assert item_match is not None
        s.inventory(
            "uscode", item_match[1].lower(), link, "reserved" if link in reserved else "pending"
        )
    individual = [link for link in individual if link not in reserved]
    downloads = (
        individual[:limit] if limit else [next(link for link in links if "xml_uscAll" in link)]
    )
    for link in downloads:
        raw = a.fetch(link, immutable=True)
        with checked_zip(s.object_path(raw.sha256)) as archive:
            for member in archive.namelist():
                if not re.fullmatch(r"usc\d+[a-zA-Z]?\.xml", member):
                    continue
                item = member.removesuffix(".xml").lower()
                try:
                    root = xml_root(archive.read(member))
                    meta = root.find("{*}meta")
                    if meta is None:
                        raise ValueError("USLM title metadata missing")
                    number = child_text(meta, "docNumber")
                    title = child_text(meta, "title")
                    positive = meta.xpath(
                        './*[local-name()="property"][@role="is-positive-law"]/text()'
                    )
                    _, count, new = s.ingest(
                        collection="uscode",
                        document="usc:" + root.get("identifier", f"/us/usc/t{number}"),
                        title=title,
                        url=USC_INDEX,
                        acquisition=raw.id,
                        member=member,
                        snapshot_date=snapshot,
                        snapshot_basis="OLRC release-point current-through date",
                        parser="uslm-3",
                        provisions=uscode_units(root, number),
                        metadata={
                            "release_point": release,
                            "positive_law": positive[0] if positive else None,
                            "source_created_literal": child_text(meta, "created"),
                            "inventory_artifact": receipt.sha256,
                        },
                    )
                    s.inventory("uscode", item, link, "indexed")
                    progress("uscode", item, count, new)
                except (ValueError, zipfile.BadZipFile) as exc:
                    s.inventory("uscode", item, link, "failed", str(exc))
                    print(f"uscode: {item}: FAILED: {exc}", file=sys.stderr)


def sync_ecfr(s: Store, a: Acquirer, limit: int | None, as_of: str | None) -> None:
    receipt, index = a.json(ECFR_INDEX)
    meta = index["meta"]
    s.collection(
        "ecfr",
        ("us", "United States", "federal", None),
        name="Electronic Code of Federal Regulations",
        authority="Office of the Federal Register and Government Publishing Office",
        kind="regulation",
        homepage="https://www.ecfr.gov",
        source_status="Government-maintained eCFR; not an official legal edition",
        access="Keyless publisher REST API; https://www.ecfr.gov/developers/documentation/api/v1",
        metadata={
            "api_meta": meta,
            "title_inventory": index["titles"],
            "inventory_artifact": receipt.sha256,
            "date_semantics": "https://www.ecfr.gov/reader-aids/ecfr-developer-resources/understanding-ecfr-dates",
        },
    )
    if meta.get("import_in_progress") and not as_of:
        raise AcquisitionError("eCFR import in progress; resume after the publisher finishes")
    titles = index["titles"]
    for title in titles:
        snapshot = as_of or title["up_to_date_as_of"] or "reserved"
        url = f"https://www.ecfr.gov/api/versioner/v1/full/{snapshot}/title-{title['number']}.xml"
        s.inventory(
            "ecfr",
            str(title["number"]) + "@" + snapshot,
            url,
            "reserved" if title.get("reserved") else "pending",
        )
    selected = [title for title in titles if not title.get("reserved")]
    for title in selected[:limit]:
        number = str(title["number"])
        snapshot = as_of or title["up_to_date_as_of"]
        url = f"https://www.ecfr.gov/api/versioner/v1/full/{snapshot}/title-{number}.xml"
        item = number + "@" + snapshot
        try:
            if snapshot > title["up_to_date_as_of"] or snapshot < "2017-01-01":
                raise ValueError(
                    "Requested snapshot outside documented/certified eCFR point-in-time coverage"
                )
            raw = a.fetch(url, accept="application/xml")
            root = xml_root(s.artifact(raw.sha256))
            _, count, new = s.ingest(
                collection="ecfr",
                document="ecfr:title-" + number,
                title=f"{number} CFR — {title['name']}",
                url=f"https://www.ecfr.gov/current/title-{number}",
                acquisition=raw.id,
                snapshot_date=snapshot,
                snapshot_basis="eCFR versioner requested date; incorporation state, not blanket legal effect",
                parser="ecfr-1",
                provisions=ecfr_units(root, number, snapshot),
                metadata={
                    "current_title_metadata": title,
                    "api_meta": meta,
                    "inventory_artifact": receipt.sha256,
                    "warning": "Current metadata is not asserted as the history of a prior snapshot.",
                },
            )
            s.inventory("ecfr", item, url, "indexed")
            progress("ecfr", item, count, new)
        except (ValueError, AcquisitionError) as exc:
            s.inventory("ecfr", item, url, "failed", str(exc))
            print(f"ecfr: {item}: FAILED: {exc}", file=sys.stderr)


def sync_collections(
    root: Path,
    collections: list[str],
    *,
    refresh: bool = False,
    limit: int | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    from .dc import sync_dc
    from .geography import sync_nyc_geo, sync_portland_geo
    from .municipal import sync_nyc, sync_portland, sync_portland_guides
    from .texas import sync_texas
    from .washington_rules import sync_washington_rules

    adapters = {
        "uscode": (sync_uscode, ("uscode",)),
        "ecfr": (sync_ecfr, ("ecfr",)),
        "dc": (sync_dc, ("dc-code", "dc-laws")),
        "texas": (sync_texas, ("texas",)),
        "nyc": (sync_nyc, ("nyc-zoning",)),
        "portland": (sync_portland, ("portland-zoning",)),
        "portland-guides": (sync_portland_guides, ("portland-zoning-guides",)),
        "nyc-gis": (sync_nyc_geo, ("nyc-zoning-gis",)),
        "portland-gis": (sync_portland_geo, ("portland-zoning-gis",)),
        "washington-land-use": (
            sync_washington_rules,
            ("wa-wac", "wa-rcw", "wa-wsr", "wa-rulemaking-notices"),
        ),
    }
    if any(name not in adapters for name in collections):
        raise ValueError("Supported collections: " + ", ".join(adapters))
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    cutoff_date(as_of)
    store = Store(root)
    acquirer = Acquirer(store, refresh=refresh)
    try:
        for collection in collections:
            adapters[collection][0](store, acquirer, limit, as_of)
        # Source aliases can emit several catalog collections. Summarize only those
        # exact IDs; the unfiltered discovery directory is a paginated UI response.
        selected = dict.fromkeys(
            collection_id for name in collections for collection_id in adapters[name][1]
        )
        reader = Reader(store)
        summaries = [reader.coverage(collection=collection_id) for collection_id in selected]
        return {
            "requested_sources": list(collections),
            "collections": [entry for summary in summaries for entry in summary["collections"]],
            "collection_summary_status": {
                collection_id: summary["status"]
                for collection_id, summary in zip(selected, summaries, strict=True)
            },
            "downloaded_this_run": acquirer.downloaded,
            "meaning": "Requested-source catalog summaries, not a legal-completeness certification.",
        }
    finally:
        acquirer.close()
        store.close()
