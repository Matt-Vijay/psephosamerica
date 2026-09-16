"""Native VAC discovery and acquisition within one persistent 512 MiB campaign."""

from __future__ import annotations

import fcntl
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from lxml import html

from psephos.acquire import Acquirer, AcquisitionError
from psephos.campaign import CampaignAcquirer, save
from psephos.store import Store, digest, json_text, utc_now
from psephos.virginia_rules import (
    BASE,
    COLLECTION,
    LIMITS,
    PARSER,
    chapter_id,
    chapter_index,
    chapter_units,
    expanded_inventory,
    preface_units,
)


class VirginiaAcquirer(CampaignAcquirer):
    def _allowed(self, url: str) -> None:
        p = urlsplit(url)
        native = p.netloc == "law.lis.virginia.gov" and p.path.startswith(
            (
                "/developers",
                "/xmlapi/",
                "/admincode/",
                "/admincodefull/",
                "/admincodeexpand/",
                "/RISImages/",
                "/api/AdministrativeCode",
                "/robots.txt",
            )
        )
        notice = p.netloc == "codecommission.dls.virginia.gov" and p.path in {
            "/robots.txt",
            "/faq_va_admin_code.shtml",
        }
        image = p.netloc == "ris.dls.virginia.gov" and (
            p.path.startswith("/uploads/") or p.path == "/robots.txt"
        )
        if p.scheme != "https" or not (native or notice or image):
            raise AcquisitionError(
                "Only native VAC documents and advertised operations are in scope"
            )
        super()._allowed(url)


def title_number(url: str) -> str:
    match = re.search(r"/title(\d+)(?:/|$)", url)
    if match is None:
        raise ValueError("Invalid native title URL")
    return match[1]


def discover(data: Path, *, acquirer: VirginiaAcquirer | None = None) -> dict[str, Any]:
    """Seal native title→agency→chapter membership, without acquiring chapter bodies."""
    directory = data / "virginia-completion"
    directory.mkdir(parents=True, exist_ok=True)
    s = acquirer.store if acquirer else Store(data)
    a = acquirer or VirginiaAcquirer(s, directory, ceiling=16 * 1024**2, delay=1.1)
    try:
        if (directory / "inventory.json").exists() and not a.refresh:
            plan: dict[str, Any] = json.loads((directory / "inventory.json").read_bytes())
            validate_inventory(s, plan)
            return plan
        if not (directory / "campaign-start.json").exists():
            save(
                directory / "campaign-start.json",
                {
                    "first_acquisition_id": s.db.execute(
                        "SELECT coalesce(max(id),0)+1 FROM acquisitions"
                    ).fetchone()[0],
                    "initial_consumed_bytes": a.downloaded,
                    "basis": "Catalog boundary recorded before this campaign's first request.",
                },
            )
        receipts = {}
        for url in (
            BASE + "/developers",
            BASE + "/xmlapi/",
            "https://codecommission.dls.virginia.gov/faq_va_admin_code.shtml",
            BASE + "/admincode/",
            BASE + "/api/AdministrativeCodeGetTitleListOfXml/",
        ):
            r = a.fetch(url, max_file_bytes=2 * 1024**2)
            receipts[url] = asdict(r)
        docs = html.fromstring(s.artifact(receipts[BASE + "/xmlapi/"]["sha256"]))
        if "/api/AdministrativeCodeGetTitleListOfXml" not in docs.xpath("//a/@href"):
            raise ValueError("Title-list operation not advertised in retained docs")
        native = html.fromstring(s.artifact(receipts[BASE + "/admincode/"]["sha256"]))
        if "/admincodeexpand/" not in native.xpath("//a/@href"):
            raise ValueError("Expanded inventory link not advertised")
        r = a.fetch(BASE + "/admincodeexpand/", max_file_bytes=2 * 1024**2)
        receipts[r.url] = asdict(r)
        tree = html.fromstring(s.artifact(r.sha256))
        links = [
            h for h in tree.xpath("//a/@href") if re.fullmatch(r"/admincodeexpand/title\d+/", h)
        ]
        api = json.loads(
            s.artifact(receipts[BASE + "/api/AdministrativeCodeGetTitleListOfXml/"]["sha256"])
        )
        if {title_number(h) for h in links} != {row["TitleNumber"] for row in api}:
            raise ValueError("HTML title roster disagrees with API")
        plan = {
            "title_receipts": [],
            "agencies": [],
            "chapters": [],
            "basis": "Native expanded title hrefs; no constructed chapter/body URLs",
        }
        for link in links:
            r = a.fetch(urljoin(BASE, link), max_file_bytes=2 * 1024**2)
            title = title_number(link)
            agencies, chapters = expanded_inventory(s.artifact(r.sha256), title, r.url)
            for item in agencies + chapters:
                item["inventory_acquisition_id"] = r.id
            plan["title_receipts"].append(asdict(r))
            plan["agencies"].extend(agencies)
            plan["chapters"].extend(chapters)
        plan["preflight_consumed_bytes"] = a.downloaded
        # Keep the old sealed native plans available when a refresh advances the roster.
        for filename in ("preflight.json", "inventory.json"):
            previous = directory / filename
            if previous.exists():
                value = json.loads(previous.read_bytes())
                save(directory / f"{previous.stem}-{digest(json_text(value).encode())}.json", value)
        save(directory / "preflight.json", receipts)
        save(directory / "inventory.json", plan)
        return plan
    finally:
        if acquirer is None:
            a.close()
            s.close()


def validate_inventory(s: Store, plan: dict[str, Any]) -> None:
    agencies, chapters = [], []
    for receipt in plan["title_receipts"]:
        source = s.db.execute(
            "SELECT url,sha256,observed_at FROM acquisitions WHERE id=? AND status=200 AND error IS NULL",
            (receipt["id"],),
        ).fetchone()
        if source is None or (source["url"], source["sha256"], source["observed_at"]) != (
            receipt["url"],
            receipt["sha256"],
            receipt["observed_at"],
        ):
            raise ValueError("Inventory receipt does not match canonical acquisition")
        title = re.search(r"title(\d+)", receipt["url"])
        assert title is not None
        raw = s.artifact(receipt["sha256"])
        aa, cc = expanded_inventory(raw, title[1], receipt["url"])
        # Independent raw-link accounting catches a forgiving HTML parser dropping a tail.
        links = re.findall(
            rb"""\bhref\s*=\s*(["'])(/admincode/title\d+/agency\d+/chapter\d+/)\1""", raw
        )
        if [BASE + value.decode("ascii") for _, value in links] != [c["url"] for c in cc]:
            raise ValueError("Parsed chapter roster differs from literal native source links")
        for item in [*aa, *cc]:
            item["inventory_acquisition_id"] = receipt["id"]
        agencies.extend(aa)
        chapters.extend(cc)
    if agencies != plan["agencies"] or chapters != plan["chapters"]:
        raise ValueError("Retained native inventory differs from plan")
    if len({chapter_id(c) for c in chapters}) != len(chapters):
        raise ValueError("Repeated chapter identity")


def run(
    data: Path, limit: int | None = None, *, acquirer: VirginiaAcquirer | None = None
) -> dict[str, Any]:
    directory = data / "virginia-completion"
    plan = json.loads((directory / "inventory.json").read_bytes())
    s = acquirer.store if acquirer else Store(data)
    a = acquirer or VirginiaAcquirer(s, directory, delay=1.1)
    errors: list[dict[str, Any]] = []
    done = 0
    processed = 0
    try:
        validate_inventory(s, plan)
        inventory_times = {r["id"]: r["observed_at"] for r in plan["title_receipts"]}
        metadata = {
            "scope": "All chapters in the retained native VAC title inventories; acquisition in progress.",
            "expected_titles": len(plan["title_receipts"]),
            "expected_agencies": len(plan["agencies"]),
            "expected_chapters": len(plan["chapters"]),
            "limits": LIMITS,
            "inventory_sha256": digest(json_text(plan).encode()),
            "inventory_receipts": [r["id"] for r in plan["title_receipts"]],
            "rights": "Commonwealth of Virginia all-rights-reserved notices; raw corpus remains local.",
        }
        s.collection(
            COLLECTION,
            ("us-va", "Virginia", "state", "us"),
            name="Virginia Administrative Code",
            authority="Virginia General Assembly / Division of Legislative Automated Systems",
            kind="regulations",
            homepage=BASE + "/admincode/",
            source_status="official publisher HTML",
            access="public native chapter exports and developer services",
            metadata=metadata,
        )
        for item in plan["chapters"]:
            if (
                s.db.execute(
                    "SELECT 1 FROM inventories WHERE collection_id=? AND item=?",
                    (COLLECTION, chapter_id(item)),
                ).fetchone()
                is None
            ):
                s.inventory(COLLECTION, chapter_id(item), item["url"], "pending")
        active = {chapter_id(item) for item in plan["chapters"]}
        for row in s.db.execute(
            "SELECT item,url FROM inventories WHERE collection_id=?", (COLLECTION,)
        ).fetchall():
            if row["item"] not in active:
                s.inventory(COLLECTION, row["item"], row["url"], "not_in_current_inventory")
        for ordinal, item in enumerate(plan["chapters"], 1):
            cid = chapter_id(item)
            existing = s.db.execute(
                "SELECT metadata,artifact_sha FROM versions WHERE document_id=? AND parser=? "
                "ORDER BY available_at DESC,rowid DESC LIMIT 1",
                ("va-vac:" + cid, PARSER + "/text-3"),
            ).fetchone()
            unchanged = False
            if existing is not None:
                vm = json.loads(existing["metadata"])
                # A refresh may have retained new bytes before parsing failed. Resume
                # those bytes rather than mistaking any older version for completion.
                latest_body = s.db.execute(
                    "SELECT sha256 FROM acquisitions WHERE final_url IN (?,?) AND status=200 "
                    "AND sha256 IS NOT NULL ORDER BY id DESC LIMIT 1",
                    (
                        vm["native_read_chapter_url"],
                        vm["native_read_chapter_url"].rstrip("/") + "/",
                    ),
                ).fetchone()
                latest_index = s.db.execute(
                    "SELECT sha256 FROM acquisitions WHERE url=? AND status=200 "
                    "AND sha256 IS NOT NULL ORDER BY id DESC LIMIT 1",
                    (item["url"],),
                ).fetchone()
                indexed = s.db.execute(
                    "SELECT sha256 FROM acquisitions WHERE id=?",
                    (vm["section_inventory_acquisition_id"],),
                ).fetchone()
                unchanged = bool(
                    latest_body
                    and latest_body["sha256"] == existing["artifact_sha"]
                    and latest_index
                    and indexed
                    and latest_index["sha256"] == indexed["sha256"]
                )
            if existing and unchanged and not a.refresh:
                expected_status = (
                    "ingested_with_source_media"
                    if json.loads(existing["metadata"])["images"]
                    else "ingested"
                )
                saved_status = s.db.execute(
                    "SELECT status FROM inventories WHERE collection_id=? AND item=?",
                    (COLLECTION, cid),
                ).fetchone()
                if saved_status["status"] != expected_status:
                    s.inventory(COLLECTION, cid, item["url"], expected_status)
                continue
            if limit is not None and processed >= limit:
                break
            processed += 1
            try:
                ir = a.fetch(item["url"], max_file_bytes=16 * 1024**2)
                info = chapter_index(s.artifact(ir.sha256), item)
                cached = s.db.execute(
                    "SELECT url FROM acquisitions WHERE final_url IN (?,?) AND status=200 AND sha256 IS NOT NULL ORDER BY id DESC LIMIT 1",
                    (info["full_url"], info["full_url"].rstrip("/") + "/"),
                ).fetchone()
                br = a.fetch(
                    cached[0] if cached else info["full_url"], max_file_bytes=128 * 1024**2
                )
                units, vm = chapter_units(s.artifact(br.sha256), item, info)
                vm.update(
                    title_inventory_acquisition_id=item["inventory_acquisition_id"],
                    section_inventory_acquisition_id=ir.id,
                    native_chapter_url=item["url"],
                    native_read_chapter_url=info["full_url"],
                )
                version, count, new = s.ingest(
                    collection=COLLECTION,
                    document="va-vac:" + cid,
                    title=vm["chapter_heading"],
                    url=item["url"],
                    acquisition=br.id,
                    available_at=max(
                        br.observed_at,
                        ir.observed_at,
                        inventory_times[item["inventory_acquisition_id"]],
                    ),
                    snapshot_date=br.observed_at[:10],
                    snapshot_basis="Live publisher snapshot observed on this acquisition date; not an edition guarantee or legal effectiveness.",
                    parser=PARSER,
                    provisions=units,
                    metadata=vm,
                )
                s.inventory(
                    COLLECTION,
                    cid,
                    item["url"],
                    "ingested_with_source_media" if vm["images"] else "ingested",
                )
                done += int(new)
                print(
                    json.dumps(
                        {
                            "chapter": cid,
                            "progress": ordinal,
                            "total": len(plan["chapters"]),
                            "units": count,
                            "bytes": a.downloaded,
                            "version": version,
                        }
                    ),
                    file=sys.stderr,
                    flush=True,
                )
            except (AcquisitionError, ValueError) as e:
                s.inventory(COLLECTION, cid, item["url"], "error", str(e))
                errors.append({"chapter": cid, "error": str(e)})
                save(directory / "errors.json", errors)
                print(json.dumps(errors[-1]), file=sys.stderr, flush=True)
                if isinstance(e, AcquisitionError):
                    raise
        statuses = {
            r["status"]: r["n"]
            for r in s.db.execute(
                "SELECT status,count(*) n FROM inventories WHERE collection_id=? GROUP BY status",
                (COLLECTION,),
            )
        }
        result = {
            "status_counts": statuses,
            "new_chapters": done,
            "processed_chapters": processed,
            "errors": errors,
            "consumed_bytes": a.downloaded,
            "updated_at": utc_now(),
        }
        metadata["scope"] = (
            "Retained VAC native chapter inventory; inspect status_counts for exact completion, not complete current regulations."
        )
        metadata["status_counts"] = statuses
        with s.db:
            s.db.execute(
                "UPDATE collections SET metadata=? WHERE id=?", (json_text(metadata), COLLECTION)
            )
        save(directory / "progress.json", result)
        save(directory / "errors.json", errors)
        return result
    finally:
        if acquirer is None:
            a.close()
            s.close()


def prefaces(data: Path, *, acquirer: VirginiaAcquirer | None = None) -> dict[str, Any]:
    directory = data / "virginia-completion"
    plan = json.loads((directory / "inventory.json").read_bytes())
    s = acquirer.store if acquirer else Store(data)
    a = acquirer or VirginiaAcquirer(s, directory, delay=1.1)
    try:
        validate_inventory(s, plan)
        inventory_times = {r["id"]: r["observed_at"] for r in plan["title_receipts"]}
        docs = json.loads((directory / "preflight.json").read_bytes())[BASE + "/xmlapi/"]
        if "/api/AdministrativeCodePrefaceXml" not in html.fromstring(
            s.artifact(docs["sha256"])
        ).xpath("//a/@href"):
            raise ValueError("Preface operation not advertised")
        manifest = []
        for item in plan["agencies"]:
            key = f"va-vac:{item['title']}VAC{item['agency']}/preface"
            url = BASE + f"/api/AdministrativeCodePrefaceXml/{item['title']}/{item['agency']}/"
            r = a.fetch(url, max_file_bytes=8 * 1024**2)
            units = preface_units(s.artifact(r.sha256), item, url)
            version = None
            if units:
                version, _, _ = s.ingest(
                    collection=COLLECTION,
                    document=key,
                    title=units[0].heading,
                    url=url,
                    acquisition=r.id,
                    available_at=max(
                        r.observed_at, inventory_times[item["inventory_acquisition_id"]]
                    ),
                    parser="virginia-vac-preface/1",
                    provisions=units,
                    snapshot_date=r.observed_at[:10],
                    snapshot_basis="Agency summary observed on this date; legal-effect dates unknown.",
                    metadata={
                        "inventory_acquisition_id": item["inventory_acquisition_id"],
                        "scope": "Agency summary, not an operative regulation.",
                        "limits": LIMITS,
                    },
                )
            manifest.append(
                {
                    "title": item["title"],
                    "agency": item["agency"],
                    "source": asdict(r),
                    "version_id": version,
                    "status": "retained_body" if units else "no_publisher_body",
                }
            )
            save(directory / "prefaces.json", manifest)
            print(
                json.dumps({"preface": key, "body": bool(units), "bytes": a.downloaded}),
                file=sys.stderr,
                flush=True,
            )
        return {
            "prefaces": len(manifest),
            "with_body": sum(m["status"] == "retained_body" for m in manifest),
            "consumed_bytes": a.downloaded,
        }
    finally:
        if acquirer is None:
            a.close()
            s.close()


def media(data: Path, *, acquirer: VirginiaAcquirer | None = None) -> dict[str, Any]:
    """Acquire only embedded official image URLs, not linked forms or IBR documents."""
    directory = data / "virginia-completion"
    s = acquirer.store if acquirer else Store(data)
    a = acquirer or VirginiaAcquirer(s, directory, delay=1.1)
    try:
        urls = sorted(
            {
                medium["url"]
                for r in s.db.execute(
                    "SELECT metadata FROM provisions WHERE version_id IN (SELECT v.id FROM versions v JOIN documents d ON d.id=v.document_id WHERE d.collection_id=?)",
                    (COLLECTION,),
                )
                for medium in json.loads(r[0]).get("media", [])
                if medium["url"]
            }
        )
        previous = (
            {row["url"]: row for row in json.loads((directory / "media.json").read_bytes())}
            if (directory / "media.json").exists()
            else {}
        )
        manifest = []
        for url in urls:
            row: dict[str, Any] = {"url": url}
            parsed = urlsplit(url)
            allowed = parsed.scheme == "https" and (
                (parsed.netloc == "ris.dls.virginia.gov" and parsed.path.startswith("/uploads/"))
                or (
                    parsed.netloc == "law.lis.virginia.gov"
                    and parsed.path.startswith("/RISImages/")
                )
            )
            old = previous.get(url, {})
            if old.get("http_status") in {404, 410} and not a.refresh:
                # Retain a specific missing-resource observation; a resume is not a refresh.
                failed = s.db.execute(
                    "SELECT url,status FROM acquisitions WHERE id=?", (old["acquisition_id"],)
                ).fetchone()
                if failed is None or (failed["url"], failed["status"]) != (url, old["http_status"]):
                    raise ValueError("Image absence receipt does not match canonical acquisition")
                row = old
            elif not allowed:
                row.update(
                    status="not_acquired_outside_reviewed_image_routes",
                    reason="Source link retained; no inferred HTTPS upgrade or third-party fetch.",
                )
            else:
                try:
                    receipt = a.fetch(url, max_file_bytes=16 * 1024**2)
                    raw = s.artifact(receipt.sha256)
                    headers = json.loads(
                        s.db.execute(
                            "SELECT headers FROM acquisitions WHERE id=?", (receipt.id,)
                        ).fetchone()[0]
                    )
                    content_type = headers.get("content-type", "").split(";")[0].lower()
                    row.update(
                        source=asdict(receipt),
                        content_type=content_type,
                        status="retained_image_response"
                        if content_type.startswith("image/")
                        else "unverified_media_type",
                    )
                    if raw.lstrip().lower().startswith((b"<!doctype html", b"<html")):
                        row["status"] = "html_instead_of_image"
                except AcquisitionError as error:
                    row.update(status="unavailable", reason=str(error))
                    failed = s.db.execute(
                        "SELECT id,status FROM acquisitions WHERE url=? ORDER BY id DESC LIMIT 1",
                        (url,),
                    ).fetchone()
                    if failed is not None:
                        row.update(acquisition_id=failed["id"], http_status=failed["status"])
                    manifest.append(row)
                    save(directory / "media.json", manifest)
                    if row.get("http_status") not in {404, 410}:
                        raise  # Stop on access refusal, transport failure or exhausted allowance.
                    print(
                        json.dumps({"image": url, "status": row["http_status"]}),
                        file=sys.stderr,
                        flush=True,
                    )
                    continue
            manifest.append(row)
            save(directory / "media.json", manifest)
            print(
                json.dumps({"image": url, "status": row["status"], "bytes": a.downloaded}),
                file=sys.stderr,
                flush=True,
            )
        return {
            "image_urls": len(urls),
            "retained_image_responses": sum(
                m["status"] == "retained_image_response" for m in manifest
            ),
            "consumed_bytes": a.downloaded,
        }
    finally:
        if acquirer is None:
            a.close()
            s.close()


def sync_virginia(s: Store, a: Acquirer, limit: int | None, as_of: str | None) -> None:
    """Sync native chapters; an unlimited run also resumes agency summaries and images."""
    if as_of:
        raise ValueError("Virginia provides live VAC exports, not a historical sync API")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    directory = s.root / "virginia-completion"
    directory.mkdir(exist_ok=True)
    with (directory / "writer.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (
            not (directory / "budget.json").exists()
            and s.db.execute(
                "SELECT 1 FROM acquisitions WHERE url LIKE 'https://law.lis.virginia.gov/%' "
                "OR url LIKE 'https://ris.dls.virginia.gov/%' "
                "OR url LIKE 'https://codecommission.dls.virginia.gov/%' LIMIT 1"
            ).fetchone()
        ):
            raise AcquisitionError(
                "Virginia source receipts exist without the campaign budget. Restore the original "
                "virginia-completion operator evidence before acquisition; portable datasets do not "
                "grant a new allowance. Existing legal data remains readable."
            )
        campaign = VirginiaAcquirer(s, directory, delay=1.1)
        start = campaign.downloaded
        campaign.refresh = a.refresh
        ceiling = min(campaign.max_bytes, start + max(0, a.max_bytes - a.downloaded))
        try:
            campaign.max_bytes = min(ceiling, start + 16 * 1024**2)
            discover(s.root, acquirer=campaign)
            campaign.max_bytes = ceiling
            result = run(s.root, limit, acquirer=campaign)
            if limit is None:
                result["prefaces"] = prefaces(s.root, acquirer=campaign)
                result["media"] = media(s.root, acquirer=campaign)
            else:
                result["prefaces_and_media"] = (
                    "Deferred by chapter limit; run unlimited sync to resume."
                )
            save(directory / "sync-result.json", result)
            if result["errors"]:
                raise ValueError("VAC chapters remain unresolved; inspect source inventories")
        finally:
            a.downloaded += campaign.downloaded - start
            campaign.close()
