"""Selected New York Senate whole-law PDFs, not a statewide law inventory."""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .acquire import Acquirer, AcquisitionError, Receipt
from .campaign import CampaignAcquirer, save
from .store import Provision, Store

ORIGIN = "https://legislation.nysenate.gov"
DOCS = ORIGIN + "/static/docs/html/laws.html"
COLLECTION = "ny-state-laws"
CAP = 180 * 1024**2
PARSER = "ny-senate-full-pdf/poppler-layout-v3"


class NewYorkAcquirer(CampaignAcquirer):
    automatic_retries = False
    consumed_field = "newly_decoded_bytes"

    @classmethod
    def budget_file(cls, store: Store, directory: Path) -> Path:
        original = store.root / "collectors/ny-northeast/http-budget.json"
        return original if original.exists() else super().budget_file(store, directory)

    def __init__(self, store: Store, directory: Path):
        path = self.budget_file(store, directory)
        if path.exists():
            old = json.loads(path.read_bytes())
            if "cap_bytes" not in old:
                if old.get("cap") != CAP:
                    raise AcquisitionError("Original New York cap differs from reviewed allowance")
                save(path, {**old, "cap_bytes": old["cap"]})
        super().__init__(store, directory, cap=CAP, file_cap=32 * 1024**2, delay=1.1)
        self.max_bytes -= 1024**2

    def _pause(self, url: str, delay: float | None = None) -> None:
        if urlsplit(url).hostname != "legislation.nysenate.gov":
            raise AcquisitionError(
                "New York continuation only accepts the reviewed PDF publisher host"
            )
        if shutil.disk_usage(self.store.root).free <= 100 * 1024**3:
            raise AcquisitionError("Original 100 GiB disk stop threshold reached")
        super()._pause(url, delay)

    def fetch(self, url: str, **options: Any) -> Receipt:
        if urlsplit(url).hostname != "legislation.nysenate.gov":
            raise AcquisitionError(
                "New York continuation only accepts the reviewed PDF publisher host"
            )
        previous = self.store.db.execute(
            "SELECT 1 FROM acquisitions WHERE (url=? OR final_url=?) AND status IN (401,403,407,451) LIMIT 1",
            (url, url),
        ).fetchone()
        if previous:
            raise AcquisitionError(
                "Retained access denial; do not retry the authenticated API or denied index"
            )
        options["max_file_bytes"] = min(options.get("max_file_bytes", self.file_cap), self.file_cap)
        return super().fetch(url, **options)


# Original selected candidates, with titles corrected from the retained PDFs.
# This is not an authoritative inventory or an identifier search.
VOLUMES = [
    ("CNS", "Constitution of the State of New York"),
    ("GCN", "General Construction"),
    ("CVR", "Civil Rights"),
    ("ELN", "Election"),
    ("PBO", "Public Officers"),
    ("GOB", "General Obligations"),
    ("CVP", "Civil Practice Law and Rules"),
    ("PEN", "Penal"),
    ("CPL", "Criminal Procedure"),
    ("RPP", "Real Property"),
    ("RPA", "Real Property Actions and Proceedings"),
    ("MHR", "Municipal Home Rule"),
    ("GMU", "General Municipal"),
    ("TWN", "Town"),
    ("VIL", "Village"),
    ("CNT", "County"),
    ("EXC", "Executive"),
    ("STL", "State"),
    ("LEG", "Legislative"),
    ("LAB", "Labor"),
    ("ENV", "Environmental Conservation"),
    ("EDN", "Education"),
    ("PBH", "Public Health"),
    ("TAX", "Tax"),
    ("RPT", "Real Property Tax"),
    ("SOS", "Social Services"),
    ("DOM", "Domestic Relations"),
    ("EPT", "Estates, Powers and Trusts"),
    ("SCP", "Surrogate's Court Procedure"),
    ("FCT", "Family Court"),
    ("JUD", "Judiciary"),
    ("COR", "Correction"),
    ("CVS", "Civil Service"),
    ("RSS", "Retirement and Social Security"),
    ("INS", "Insurance"),
    ("BNK", "Banking"),
    ("GBS", "General Business"),
    ("BSC", "Business Corporation"),
    ("NPC", "Not-for-Profit Corporation"),
    ("LLC", "Limited Liability Company"),
    ("PTR", "Partnership"),
    ("DCD", "Debtor and Creditor"),
    ("UCC", "Uniform Commercial Code"),
    ("LIE", "Lien"),
    ("WKC", "Workers' Compensation"),
    ("VAT", "Vehicle and Traffic"),
    ("HAY", "Highway"),
    ("TRA", "Transportation"),
    ("PBA", "Public Authorities"),
    ("PBS", "Public Service"),
    ("ABC", "Alcoholic Beverage Control"),
    ("ABP", "Abandoned Property"),
    ("AGM", "Agriculture and Markets"),
    ("COM", "Economic Development"),
    ("EDP", "Eminent Domain Procedure"),
    ("ENG", "Energy"),
    ("IND", "Indian"),
    ("MIL", "Military"),
    ("NAV", "Navigation"),
    ("PAR", "Parks, Recreation and Historic Preservation"),
    ("PML", "Racing, Pari-Mutuel Wagering and Breeding"),
    ("REL", "Rural Electric Cooperative"),
    ("SCL", "Second Class Cities"),
    ("STF", "State Finance"),
    ("SLG", "Statute of Local Governments"),
    ("SWC", "Soil and Water Conservation Districts"),
    ("TCP", "Transportation Corporations"),
    ("CCO", "Cooperative Corporations"),
    ("CAN", "Cannabis"),
    ("BEN", "Benevolent Orders"),
    ("BSA", "Boxing, Sparring and Wrestling"),
    ("MHY", "Mental Hygiene"),
    ("PBG", "Public Housing"),
    ("PVH", "Private Housing Finance"),
    ("PBL", "Public Lands"),
    ("RRD", "Railroad"),
    ("RPD", "Rapid Transit"),
    ("SAP", "State Administrative Procedure"),
    ("VET", "Veterans' Services"),
    ("VOL", "Volunteer Firefighters' Benefit"),
    ("VAW", "Volunteer Ambulance Workers' Benefit"),
    ("GCT", "General City"),
]


def volume_identity(pages: list[str], title: str) -> dict[str, Any]:
    """Require a title heading or a chapter's own naming clause, not scattered words."""

    def normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

    expected = {normalize(title) + suffix for suffix in ("", " law", " act")}
    expected |= {"the " + value for value in expected}
    for number, page in enumerate(pages[:3], 1):
        lines = [line.strip() for line in page.splitlines() if line.strip()]
        for start in range(len(lines)):
            for size in (1, 2, 3):
                heading = " ".join(lines[start : start + size])
                if normalize(heading) in expected:
                    return {"basis": "standalone opening title", "page": number, "text": heading}
    # Vehicle and Traffic places its short-title section near the end of the PDF.
    pattern = re.compile(
        r"\b(?:This chapter shall be known (?:and may be cited (?:and referred to )?)?as "
        r"|The title of this act is .{1,150}?\.[\"\u201d]? It may be cited as )"
        r"(?:the )?[\"\u201c]?(?P<title>[^.\"\u201d]{1,120})[.\"\u201d]",
        re.I,
    )
    for number, page in enumerate(pages, 1):
        for match in pattern.finditer(" ".join(page.split())):
            if normalize(match["title"]) in expected:
                return {"basis": "statutory self-title", "page": number, "text": match[0]}
    raise ValueError(
        "Expected volume identity not established by a title or naming clause: " + title
    )


def parse_pdf(
    path: Path, law_id: str, title: str, url: str
) -> tuple[list[Provision], dict[str, Any]]:
    if path.read_bytes()[:5] != b"%PDF-":
        raise ValueError("HTTP 200 was not a PDF")
    info = subprocess.run(
        ["pdfinfo", str(path)], capture_output=True, text=True, check=True, timeout=60
    ).stdout
    match = re.search("^Pages:\\s+(\\d+)", info, re.M)
    if not match:
        raise ValueError("PDF page count missing")
    expected = int(match[1])
    text = subprocess.run(
        ["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"],
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    ).stdout
    pages = text.split("\x0c")
    if not pages[-1].strip():
        pages.pop()
    if len(pages) != expected:
        raise ValueError("Extracted PDF page count mismatch")
    combined = "\n".join(pages)
    substantive = re.findall(
        "(?m)^\\s*(?:\u00a7\\s*\\d|Section\\s+\\d[\\dA-Za-z-]*\\.|S\\s+\\d+\\.)", combined
    )
    if (
        len(substantive) < 2
        or len(combined) < 1500
        or len(re.findall("\\b(?:shall|must)\\b", combined, re.I)) < 3
    ):
        raise ValueError("No demonstrated substantive statutory text: not counted")
    identity = volume_identity(pages, title)
    units = []
    empty = []
    for num, page in enumerate(pages, 1):
        body = page.strip("\r\n")
        if not body.strip():
            empty.append(num)
            continue
        units.append(
            Provision(
                key=f"ny:{law_id}:page:{num}",
                citation=f"NY {title} \u2014 PDF page {num}",
                heading=f"{title} \u2014 page {num}",
                text=body,
                markup="<pre>" + html.escape(body) + "</pre>",
                url=f"{url}#page={num}",
                unit_kind="page",
                metadata={
                    "source_law_id": law_id,
                    "pdf_page": num,
                    "pdf_pages": expected,
                    "text_basis": "Poppler pdftotext -layout; no OCR",
                    "page_is_not_a_statutory_section": True,
                    "may_include_contents_or_headings": True,
                    "media_status": "Original PDF authoritative for visual layout and any non-text graphics; not transcribed",
                },
            )
        )
    return (
        units,
        {
            "pdf_pages": expected,
            "indexed_nonempty_pages": len(units),
            "empty_pages_not_indexed": empty,
            "substantive_section_starts_detected": len(substantive),
            "pdf_info": info,
            "text_characters": len(combined),
            "volume_identity": identity,
        },
    )


def sync_new_york(s: Store, a: Acquirer, limit: int | None, as_of: str | None) -> dict[str, Any]:
    if as_of is not None or (limit is not None and limit < 0):
        raise ValueError("New York requires a nonnegative limit and has no historical acquisition")
    documentation = a.fetch(DOCS, max_file_bytes=1024**2)
    s.collection(
        COLLECTION,
        ("us-ny", "New York", "state", "us"),
        name="New York State Laws - selected Senate PDF volumes",
        authority="New York State Senate / Legislative Bill Drafting Commission",
        kind="statutes_and_constitution",
        homepage=ORIGIN,
        source_status="Official publisher source; selected volume scope, PDF page indexing",
        access="Public documented PDF exports; robots honored; 1.1-second pacing. Prior JSON API 401 and main Senate index 403 remain unrequested. No accounts or blanket redistribution permission.",
        metadata={
            "scope": "Explicit selected volumes, not statewide exhaustive. No regulations, session laws or local enactments.",
            "inventory_basis": "Original 82 selected candidate IDs, with PDF-backed title corrections; no authoritative statewide inventory acquired. Legacy alias guesses are not established equivalences.",
            "clock_note": "PDF/HTTP timestamps are not incorporation, snapshot or legal effectiveness dates.",
            "documentation": DOCS,
            "documentation_sha256": documentation.sha256,
        },
    )
    attempted = accepted = 0
    for code, title in VOLUMES:
        url = ORIGIN + f"/pdf/laws/{code}?full=true"
        if not s.db.execute(
            "SELECT 1 FROM inventories WHERE collection_id=? AND item=?", (COLLECTION, code)
        ).fetchone():
            s.inventory(COLLECTION, code, url, "pending")
        if (
            not a.refresh
            and s.db.execute(
                "SELECT 1 FROM versions WHERE document_id=?", ("ny:" + code,)
            ).fetchone()
        ):
            continue
        previous = s.db.execute(
            "SELECT id,status,headers FROM acquisitions WHERE url=? ORDER BY id DESC LIMIT 1",
            (url,),
        ).fetchone()
        if (
            not a.refresh
            and previous
            and previous["status"] == 404
            and "retry-after" not in json.loads(previous["headers"])
        ):
            s.inventory(
                COLLECTION,
                code,
                url,
                "source_unavailable",
                f"Publisher HTTP 404; receipt {previous['id']}",
            )
            continue
        if limit is not None and attempted >= limit:
            continue
        attempted += 1
        try:
            receipt = a.fetch(url)
            units, metadata = parse_pdf(s.object_path(receipt.sha256), code, title, url)
            s.ingest(
                collection=COLLECTION,
                document="ny:" + code,
                title=title,
                url=url,
                acquisition=receipt.id,
                parser=PARSER,
                provisions=units,
                snapshot_basis="Unknown publisher incorporation/snapshot date; public live whole-law PDF export",
                metadata={
                    **metadata,
                    "source_law_id": code,
                    "scope": "Requested publisher volume export, not statewide law coverage",
                    "effective_date_note": "Dates within legal text retained; no blanket effective date inferred",
                    "edition_date": None,
                    "publisher_incorporation_date": None,
                },
            )
            s.inventory(COLLECTION, code, url, "ingested")
            accepted += 1
            print(f"New York {code}: {len(units)} PDF pages", file=sys.stderr, flush=True)
        except (ValueError, subprocess.SubprocessError, AcquisitionError) as exc:
            missing = s.db.execute(
                "SELECT id,status,headers FROM acquisitions WHERE url=? ORDER BY id DESC LIMIT 1",
                (url,),
            ).fetchone()
            if (
                isinstance(exc, AcquisitionError)
                and missing
                and missing["status"] == 404
                and (not previous or missing["id"] != previous["id"])
                and "retry-after" not in json.loads(missing["headers"])
            ):
                s.inventory(
                    COLLECTION,
                    code,
                    url,
                    "source_unavailable",
                    f"Publisher HTTP 404; receipt {missing['id']}",
                )
                print(f"New York {code}: publisher 404, not acquired", file=sys.stderr, flush=True)
                continue
            s.inventory(COLLECTION, code, url, "failed", str(exc))
            print(f"New York {code}: {exc}", file=sys.stderr, flush=True)
            if isinstance(exc, AcquisitionError):
                raise
    return {
        "selected_candidates": len(VOLUMES),
        "attempted": attempted,
        "accepted_this_run": accepted,
        "statewide_inventory_complete": False,
    }
