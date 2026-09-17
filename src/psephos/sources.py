"""Explicit publisher adapters. No discovery crawler and no guessed legal relationships."""

from __future__ import annotations

import re
import sys
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from lxml import html

from .acquire import Acquirer, AcquisitionError
from .campaign import CampaignAcquirer
from .parse import child_text, ecfr_units, uscode_units, xml_root
from .retrieve import Reader, cutoff_date
from .store import TEXT_PROJECTION, Store, writer_lock

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


def sync_uscode(s: Store, a: Acquirer, limit: int | None, as_of: str | None) -> dict[str, Any]:
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
    expected = []
    inventory = []
    for link in individual:
        item_match = re.search(r"xml_(usc[^@]+)", link)
        if item_match is None:
            raise ValueError("OLRC title URL format changed")
        inventory.append(item_match[1].lower())
        if link not in reserved:
            expected.append(item_match[1].lower())
        s.inventory(
            "uscode", item_match[1].lower(), link, "reserved" if link in reserved else "pending"
        )
    individual = [link for link in individual if link not in reserved]
    if not expected or len(set(expected)) != len(expected):
        raise ValueError("Empty or duplicated OLRC title inventory")
    downloads = (
        individual[:limit] if limit else [next(link for link in links if "xml_uscAll" in link)]
    )
    for link in downloads:
        raw = a.fetch(link, immutable=not a.refresh)
        retained = {
            row[0].removesuffix(".xml").lower()
            for row in s.db.execute(
                "SELECT v.member FROM versions v JOIN documents d ON d.id=v.document_id "
                "WHERE d.collection_id='uscode' AND v.snapshot_date=? AND v.parser=? "
                "AND json_extract(v.metadata,'$.release_point')=? AND v.artifact_sha=?",
                (snapshot, "uslm-3/" + TEXT_PROJECTION, release, raw.sha256),
            )
            if row[0]
        }
        if set(expected) <= retained:
            for title_url, item in zip(individual, expected, strict=True):
                s.inventory("uscode", item, title_url, "indexed")
            continue
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
    return {"inventory_items": {"uscode": inventory}}


def sync_ecfr(s: Store, a: Acquirer, limit: int | None, as_of: str | None) -> dict[str, Any]:
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
    inventory = []
    for title in titles:
        snapshot = as_of or title["up_to_date_as_of"] or "reserved"
        inventory.append(str(title["number"]) + "@" + snapshot)
        url = f"https://www.ecfr.gov/api/versioner/v1/full/{snapshot}/title-{title['number']}.xml"
        s.inventory(
            "ecfr",
            str(title["number"]) + "@" + snapshot,
            url,
            "reserved" if title.get("reserved") else "pending",
        )
    selected = [title for title in titles if not title.get("reserved")]
    expected = [
        str(title["number"]) + "@" + (as_of or title["up_to_date_as_of"]) for title in selected
    ]
    if not expected or len(set(expected)) != len(expected):
        raise ValueError("Empty or duplicated eCFR title inventory")
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
            # eCFR can correct prior snapshots: revalidate the body, not just its date.
            raw = a.fetch(url, accept="application/xml", max_age_seconds=a.resume_window())
            existing = s.db.execute(
                "SELECT 1 FROM versions WHERE document_id=? AND snapshot_date=? AND parser=? "
                "AND artifact_sha=?",
                ("ecfr:title-" + number, snapshot, "ecfr-1/" + TEXT_PROJECTION, raw.sha256),
            ).fetchone()
            if existing:
                s.inventory("ecfr", item, url, "indexed")
                continue
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
    return {"inventory_items": {"ecfr": inventory}}


@dataclass(frozen=True)
class _Source:
    sync: Callable[[Store, Acquirer, int | None, str | None], object]
    collection_ids: tuple[str, ...]
    limitations: str
    historical_sync: bool = False
    campaign: type[CampaignAcquirer] | None = None
    receipt_prefixes: tuple[str, ...] = ()


def _sources() -> dict[str, _Source]:
    """The installed acquisition catalog; importing it performs no I/O."""
    from .census import sync_census
    from .collect_georgia import sync_georgia
    from .collect_oregon import sync_oregon
    from .collect_virginia import sync_virginia
    from .collect_washington import WashingtonAcquirer, sync_washington
    from .dc import sync_dc
    from .florida import sync_florida
    from .geography import sync_nyc_geo, sync_portland_geo
    from .municipal import sync_nyc, sync_portland, sync_portland_guides
    from .nebraska import COLLECTIONS, NebraskaAcquirer, sync_nebraska
    from .portland_charter import CharterAcquirer, sync_portland_charter
    from .portland_code import sync_portland_code
    from .texas import sync_texas
    from .washington_rules import sync_washington_rules

    return {
        "census-geography": _Source(
            sync_census,
            ("census-geography-2025",),
            "Fixed bounded 2025 geography; no limit, refresh or historical acquisition.",
        ),
        "uscode": _Source(
            sync_uscode, ("uscode",), "Current OLRC release point; not blanket legal effectiveness."
        ),
        "ecfr": _Source(
            sync_ecfr,
            ("ecfr",),
            "Versioner incorporation snapshots since 2017; not official legal editions or effective-date reconstruction.",
            True,
        ),
        "dc": _Source(
            sync_dc,
            ("dc-code", "dc-laws"),
            "Pinned Council XML revision selected by repository date; not inferred legal effectiveness.",
            True,
        ),
        "texas": _Source(
            sync_texas,
            ("texas",),
            "Current bulk compilation; precise snapshot/effective dates unknown.",
        ),
        "florida": _Source(
            sync_florida,
            ("florida-statutes-2026",),
            "2026 publisher chapter inventory; no historical acquisition.",
        ),
        "nyc": _Source(
            sync_nyc, ("nyc-zoning",), "Publisher zoning pages, not the entire municipal code."
        ),
        "portland": _Source(
            sync_portland,
            ("portland-zoning",),
            "Reviewed zoning PDF edition; no partial or historical acquisition.",
        ),
        "portland-code": _Source(
            sync_portland_code,
            ("portland-city-code",),
            "Clerk-listed code exports; no limit or refresh; Title 33 is a separate zoning collection.",
        ),
        "portland-charter": _Source(
            sync_portland_charter,
            ("portland-city-charter",),
            "Council Clerk chapter inventory; current display, not inferred effectiveness. "
            "128 MiB persistent allowance; existing stores require original campaign budget.",
            campaign=CharterAcquirer,
        ),
        "portland-guides": _Source(
            sync_portland_guides,
            ("portland-zoning-guides",),
            "Supporting guidance only; not additional code coverage.",
        ),
        "nyc-gis": _Source(
            sync_nyc_geo,
            ("nyc-zoning-gis",),
            "Publisher GIS archive; no historical or partial acquisition.",
        ),
        "portland-gis": _Source(
            sync_portland_geo,
            ("portland-zoning-gis",),
            "Live GIS membership; no historical or partial acquisition.",
        ),
        "washington-land-use": _Source(
            sync_washington_rules,
            ("wa-wac", "wa-rcw", "wa-wsr", "wa-rulemaking-notices"),
            "Bounded SEPA/GMA source scope, not statewide administrative-law completeness.",
        ),
        "georgia": _Source(
            sync_georgia,
            ("ga-administrative-rules",),
            "Native department PDFs; unverified image text stays explicit. Existing stores require original campaign evidence.",
        ),
        "virginia": _Source(
            sync_virginia,
            ("va-administrative-code",),
            "Native live chapters and agency summaries; some external media unavailable. Existing stores require original campaign evidence.",
        ),
        "oregon": _Source(
            sync_oregon,
            ("oregon-ors",),
            "Fixed 2025-edition public chapter inventory; later laws not consolidated. Existing stores require original campaign budget.",
            campaign=CampaignAcquirer,
            receipt_prefixes=("https://www.oregonlegislature.gov/",),
        ),
        "washington": _Source(
            partial(sync_washington, titles=None),
            ("wa-rcw",),
            "All native live RCW chapters; no historical reconstruction. 250 MiB persistent allowance; existing stores require original campaign budget.",
            campaign=WashingtonAcquirer,
            receipt_prefixes=(
                "https://app.leg.wa.gov/",
                "https://apps.leg.wa.gov/",
                "https://leg.wa.gov/",
                "https://wslwebservices.leg.wa.gov/",
                "https://lawfilesext.leg.wa.gov/",
            ),
        ),
        "nebraska": _Source(
            sync_nebraska,
            COLLECTIONS,
            "Native statutes, UCC, constitution and appendix; legal clocks unknown. Existing stores require original campaign budget.",
            campaign=NebraskaAcquirer,
        ),
    }


def available_sources() -> dict[str, Any]:
    return {
        "sources": [
            {
                "alias": alias,
                "collection_ids": list(source.collection_ids),
                "historical_sync": source.historical_sync,
                "limitations": source.limitations,
            }
            for alias, source in _sources().items()
        ],
        "meaning": "Supported acquisition adapters, not acquired coverage.",
    }


def _sync_source(
    name: str,
    source: _Source,
    store: Store,
    outer: Acquirer,
    limit: int | None,
    as_of: str | None,
) -> None:
    if source.campaign is None:
        source.sync(store, outer, limit, as_of)
        return
    if as_of is not None:
        raise ValueError(f"{name} has no historical acquisition; use retained read cutoffs instead")
    import fcntl

    directory = store.root / "campaigns" / name
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "writer.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not (directory / "budget.json").exists() and (
            any(
                store.db.execute("SELECT 1 FROM collections WHERE id=?", (cid,)).fetchone()
                for cid in source.collection_ids
            )
            or any(
                store.db.execute(
                    "SELECT 1 FROM acquisitions WHERE url LIKE ? LIMIT 1", (prefix + "%",)
                ).fetchone()
                for prefix in source.receipt_prefixes
            )
        ):
            raise AcquisitionError(
                f"Restore the original {name} campaign budget at {directory / 'budget.json'} "
                "before acquiring into this existing store; portable snapshots omit operator budgets."
            )
        campaign = source.campaign(store, directory)
        campaign.refresh = outer.refresh
        initial = campaign.downloaded
        campaign.max_bytes = min(campaign.max_bytes, initial + outer.max_bytes - outer.downloaded)
        try:
            source.sync(store, campaign, limit, as_of)
        finally:
            outer.downloaded += campaign.downloaded - initial
            campaign.close()


def _sync_collections(
    root: Path,
    collections: list[str],
    *,
    refresh: bool = False,
    limit: int | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    adapters = _sources()
    if any(name not in adapters for name in collections):
        raise ValueError("Supported collections: " + ", ".join(adapters))
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    cutoff_date(as_of)
    store = Store(root)
    acquirer = Acquirer(store, refresh=refresh)
    try:
        for collection in collections:
            _sync_source(collection, adapters[collection], store, acquirer, limit, as_of)
        # Source aliases can emit several catalog collections. Summarize only those
        # exact IDs; the unfiltered discovery directory is a paginated UI response.
        selected = dict.fromkeys(
            collection_id for name in collections for collection_id in adapters[name].collection_ids
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


def sync_collections(
    root: Path,
    collections: list[str],
    *,
    refresh: bool = False,
    limit: int | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    with writer_lock(root):
        return _sync_collections(root, collections, refresh=refresh, limit=limit, as_of=as_of)
