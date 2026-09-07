"""California's supported PUBINFO tab/LOB export, without executing publisher SQL.

Run with ``python -m psephos.collect_california --store data/collectors/california``.
The session filename is NOT a legal effective date. Weekday updates are not applied.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from copy import deepcopy
from email.utils import parsedate_to_datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlencode

from .acquire import Acquirer, AcquisitionError, Receipt
from .parse import local_name, media_links, readable, source_links, xml_root
from .store import Provision, Store

BASE = "https://downloads.leginfo.legislature.ca.gov/"
COLLECTION = "us-ca-codes"
PARSER = "california-pubinfo/1"
FIELDS = {
    "LAW_SECTION_TBL": "ID LAW_CODE SECTION_NUM OP_STATUES OP_CHAPTER OP_SECTION "
    "EFFECTIVE_DATE LAW_SECTION_VERSION_ID DIVISION TITLE PART CHAPTER ARTICLE HISTORY "
    "CONTENT_XML ACTIVE_FLG TRANS_UID TRANS_UPDATE".split(),
    "LAW_TOC_SECTIONS_TBL": "ID LAW_CODE NODE_TREEPATH SECTION_NUM SECTION_ORDER TITLE "
    "OP_STATUES OP_CHAPTER OP_SECTION TRANS_UID TRANS_UPDATE LAW_SECTION_VERSION_ID "
    "SEQ_NUM".split(),
    "LAW_TOC_TBL": "LAW_CODE DIVISION TITLE PART CHAPTER ARTICLE HEADING ACTIVE_FLG "
    "TRANS_UID TRANS_UPDATE NODE_SEQUENCE NODE_LEVEL NODE_POSITION NODE_TREEPATH "
    "CONTAINS_LAW_SECTIONS HISTORY_NOTE OP_STATUES OP_CHAPTER OP_SECTION".split(),
    "CODES_TBL": ["CODE", "TITLE"],
}
ESCAPES = {"0": "\0", "b": "\b", "n": "\n", "r": "\r", "t": "\t", "Z": "\x1a"}


def mysql_rows(lines: Iterable[str]) -> Iterator[list[str | None]]:
    """Read supported MySQL LOAD DATA optional-backtick/tab format.

    Preserve embedded delimiters/newlines, decode MySQL escapes (not Python's),
    and distinguish quoted literal NULL from unquoted NULL / \\N.
    """
    row: list[str | None] = []
    field: list[str] = []
    quoted = False
    enclosed = False
    escaped = False

    def value() -> str | None:
        raw = "".join(field)
        if raw == "\\N" or (raw == "NULL" and not enclosed):
            return None
        return re.sub(r"\\(.)", lambda m: ESCAPES.get(m[1], m[1]), raw, flags=re.S)

    for line in lines:
        i = 0
        while i < len(line):
            char = line[i]
            if escaped:
                field.append(char)
                escaped = False
            elif char == "\\":
                field.append(char)
                escaped = True
            elif char == "`" and not field and not enclosed:
                quoted = enclosed = True
            elif char == "`" and quoted:
                if i + 1 < len(line) and line[i + 1] == "`":
                    field.append("`")
                    i += 1
                elif i + 1 == len(line) or line[i + 1] in "\t\r\n":
                    quoted = False
                else:
                    field.append(char)
            elif char in "\t\n" and not quoted:
                row.append(value())
                field = []
                enclosed = False
                if char == "\n":
                    yield row
                    row = []
            elif char == "\r" and not quoted and line[i : i + 2] == "\r\n":
                pass
            else:
                field.append(char)
            i += 1
    if quoted or escaped:
        raise ValueError("Truncated enclosed/escaped PUBINFO field")
    if field or row or enclosed:
        row.append(value())
        yield row


def table_rows(archive: zipfile.ZipFile, table: str) -> Iterator[dict[str, Any]]:
    member = table + ".dat"
    with archive.open(member) as raw:
        with io.TextIOWrapper(raw, encoding="utf-8", errors="strict", newline="") as stream:
            for index, row in enumerate(mysql_rows(stream), 1):
                if len(row) != len(FIELDS[table]):
                    raise ValueError(f"{member} row {index}: unexpected field count {len(row)}")
                yield dict(zip(FIELDS[table], row, strict=True))


def verify_loader(path: Path) -> None:
    """Treat SQL exclusively as documentation, never as executable input."""
    with zipfile.ZipFile(path) as archive:
        for table, expected in FIELDS.items():
            sql = archive.read(table.lower() + ".sql").decode("utf-8")
            match = re.search(r"LINES TERMINATED BY[^\n]*\n\s*\((.*?)\)", sql, re.S)
            if not match:
                raise ValueError(f"Unsupported loader syntax for {table}")
            fields = [
                f.strip().upper().replace("@VAR1", "CONTENT_XML") for f in match[1].split(",")
            ]
            if fields != expected:
                raise ValueError(f"Publisher field order changed for {table}")


def lob_bytes(archive: zipfile.ZipFile, locator: str) -> bytes:
    """Never extract files or follow a source path outside the retained archive."""
    path = PurePosixPath(locator.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or ":" in locator:
        raise ValueError(f"Unsafe LOB locator: {locator}")
    info = archive.getinfo(str(path))
    if info.file_size > 32 * 1024**2:
        raise ValueError("LOB exceeds supported 32 MiB maximum")
    return archive.read(info)


def _number(value: Any) -> int:
    return int(value or 0)


def section_citation(row: dict[str, Any]) -> str:
    code, number = row["LAW_CODE"], row["SECTION_NUM"]
    if code == "CONS":
        number = re.sub(r"^SEC(?:TION)?\.?(?: )?", "", number).rstrip(".")
        return f"Cal. Const. art. {row['ARTICLE']}, § {number}"
    return f"Cal. {code} § {number.rstrip('.')}"


def caml_text(root: Any) -> tuple[str, list[dict[str, str]]]:
    """Project CAML semantic whitespace/fractions and expose absent tip-in pages."""
    projection = deepcopy(root)
    missing_media = []
    for element in list(projection.iter()):
        tag = local_name(element)
        style = element.get("class", "")
        if tag == "span" and style in {"EnSpace", "EmSpace", "ThinSpace"}:
            element.text = " " + (element.text or "")
        elif tag == "span" and style.endswith("Leaders"):
            element.text = f" [source {style}] " + (element.text or "")
        elif tag == "Fraction":
            children = {local_name(c): readable(c) for c in element}
            if not children.get("Numerator") or not children.get("Denominator"):
                raise ValueError("Incomplete CAML fraction")
            tail = element.tail
            element.clear()
            element.text = " " + children["Numerator"] + "/" + children["Denominator"]
            element.tail = tail
        elif tag == "TipIn":
            locator = f"CAML TipIn ({element.get('numPages', 'unspecified')} pages)"
            missing_media.append(
                {
                    "source_locator": locator,
                    "url": "",
                    "alt": "",
                    "status": "not_transcribed; publisher XML contains no media locator",
                }
            )
            element.tag = "img"
            element.set("src", locator)
    return readable(projection), missing_media


def section_unit(
    archive: zipfile.ZipFile,
    row: dict[str, Any],
    toc: list[dict[str, Any]],
    hierarchy: list[dict[str, Any]],
) -> Provision:
    code, number = row["LAW_CODE"], row["SECTION_NUM"]
    native_version = row["LAW_SECTION_VERSION_ID"] or row["ID"]
    locator = row["CONTENT_XML"]
    if not locator:
        raise ValueError(f"Missing law text LOB for {row['ID']}")
    data = lob_bytes(archive, locator)
    root = xml_root(data)
    lookup = {"lawCode": code, "sectionNum": number}
    if code == "CONS":
        lookup["article"] = row["ARTICLE"]
    url = "https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?" + urlencode(lookup)
    heading = next((item["TITLE"] for item in toc if item["TITLE"]), "")
    text, missing_media = caml_text(root)
    history = row["HISTORY"]
    if history and history not in text:
        text += "\n" + history
    return Provision(
        key=f"ca:{code}:{number}:version:{native_version}:{row['ID']}",
        citation=section_citation(row),
        heading=heading,
        text=text,
        markup=data.decode("utf-8"),
        url=url,
        parent_key=f"ca:{code}:node:{toc[0]['NODE_TREEPATH']}" if toc else None,
        metadata={
            "source_fields": row,
            "source_native_version_id": native_version,
            "effective_on": row["EFFECTIVE_DATE"],
            "active_flag": row["ACTIVE_FLG"],
            "toc_rows": toc,
            "hierarchy": hierarchy,
            "media": media_links(root, url) + missing_media,
            "text_quality": "incomplete_without_source_media"
            if missing_media or media_links(root, url)
            else "publisher_text_projection",
            "source_markup_bytes": len(data),
            "temporal_warning": "Publisher snapshot; active flag is not an effective-law judgment.",
            "url_basis": "Derived publisher lookup; not a version-pinned representation. Use artifact/member for evidence.",
        },
        references=source_links(root, url),
    )


def register(store: Store) -> None:
    store.collection(
        COLLECTION,
        ("us-ca", "California", "state", "us"),
        name="California Codes and Constitution (official PUBINFO snapshot)",
        authority="California Legislature, Office of Legislative Counsel",
        kind="statutes",
        homepage=BASE,
        source_status="official publisher bulk export; authentication/certification not established",
        access="Official public bulk download; publisher README expressly supports local reuse",
        metadata={
            "documentation": BASE + "pubinfo_Readme.pdf",
            "updates": "Sunday session baseline; weekday incremental exports; deleted records omitted",
            "interactive_site": "robots disallows all automated access; not fetched",
            "coverage": "Codes and Constitution tables only; session bills not imported",
            "update_gap": "Only retained session baseline imported; weekday updates not applied. Not current-law certification.",
            "reuse_basis": "GOV 10248.5 places information made public under 10248 in the public domain, expressly overriding 10248(g). Codes and Constitution are listed in 10248(a)(9)-(10). This scoped rule does not authorize access bypass or license unrelated third-party material or repository software.",
            "reuse_evidence": "Retained Cal. GOV §§ 10248 and 10248.5; native version/LOB identities in source_fields. Enactment: https://www.leginfo.ca.gov/pub/15-16/bill/asm/ab_0851-0900/ab_884_bill_20160922_chaptered.pdf (2016 Ch. 441, Sec. 2).",
        },
    )


def import_archive(store: Store, receipt: Receipt) -> dict[str, Any]:
    """Import the complete retained Code tables, keeping alternate native versions."""
    register(store)
    headers = json.loads(
        store.db.execute("SELECT headers FROM acquisitions WHERE id=?", (receipt.id,)).fetchone()[0]
    )
    modified = headers.get("last-modified")
    snapshot = parsedate_to_datetime(modified).date().isoformat() if modified else None
    report: dict[str, Any] = {
        "acquisition": receipt.__dict__,
        "snapshot_date": snapshot,
        "snapshot_basis": "HTTP Last-Modified of session dump; not a legal effective date",
        "last_modified": modified,
        "source_session": "2025-2026",
        "delta_gap": "No weekday exports applied after baseline. No current-law claim.",
    }
    with zipfile.ZipFile(store.object_path(receipt.sha256)) as archive:
        names = set(archive.namelist())
        missing = [name + ".dat" for name in FIELDS if name + ".dat" not in names]
        if missing:
            raise ValueError(f"Not a complete Code-table baseline; missing {missing}")
        report["archive_members"] = len(names)
        report["archive_uncompressed_bytes"] = sum(i.file_size for i in archive.infolist())
        report["source_table_zip_timestamps"] = {
            table: archive.getinfo(table + ".dat").date_time for table in FIELDS
        }
        codes = list(table_rows(archive, "CODES_TBL"))
        sections = list(table_rows(archive, "LAW_SECTION_TBL"))
        toc = list(table_rows(archive, "LAW_TOC_SECTIONS_TBL"))
        nodes = list(table_rows(archive, "LAW_TOC_TBL"))
        report["table_rows"] = dict(
            zip(FIELDS, map(len, [sections, toc, nodes, codes]), strict=True)
        )
        by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
        by_version: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        by_node: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in sections:
            by_code[row["LAW_CODE"]].append(row)
        for row in toc:
            by_version[(row["LAW_CODE"], row["LAW_SECTION_VERSION_ID"])].append(row)
        for row in nodes:
            by_node[row["LAW_CODE"]].append(row)
        node_index = {(r["LAW_CODE"], r["NODE_TREEPATH"]): r for r in nodes}
        if len(node_index) != len(nodes):
            raise ValueError("Duplicate source hierarchy paths")
        report["missing_toc_nodes"] = sum(
            (r["LAW_CODE"], r["NODE_TREEPATH"]) not in node_index for r in toc
        )
        report["codes"] = codes
        report["section_distinct_citations"] = len({section_citation(r) for r in sections})
        report["section_distinct_native_versions"] = len(
            {r["LAW_SECTION_VERSION_ID"] for r in sections}
        )
        report["active_flags"] = dict(Counter(r["ACTIVE_FLG"] for r in sections))
        report["effective_date_min"] = min(
            (r["EFFECTIVE_DATE"] for r in sections if r["EFFECTIVE_DATE"]), default=None
        )
        report["effective_date_max"] = max(
            (r["EFFECTIVE_DATE"] for r in sections if r["EFFECTIVE_DATE"]), default=None
        )
        report["source_trans_update_min"] = min(
            (r["TRANS_UPDATE"] for r in sections if r["TRANS_UPDATE"]), default=None
        )
        report["source_trans_update_max"] = max(
            (r["TRANS_UPDATE"] for r in sections if r["TRANS_UPDATE"]), default=None
        )
        report["per_code"] = {}
        titles = {r["CODE"]: r["TITLE"] for r in codes}
        for code, rows in sorted(by_code.items()):
            code_nodes = sorted(by_node[code], key=lambda r: _number(r["NODE_SEQUENCE"]))

            def context(row: dict[str, Any], code: str = code) -> list[dict[str, Any]]:
                # Publisher NODE_LEVEL equals dot-delimited path depth; every
                # baseline parent and section path was verified in the TOC table.
                found = {}
                for mapping in by_version[(code, row["LAW_SECTION_VERSION_ID"])]:
                    parts = mapping["NODE_TREEPATH"].split(".")
                    for depth in range(1, len(parts) + 1):
                        path = ".".join(parts[:depth])
                        node = node_index.get((code, path))
                        if node:
                            found[path] = {
                                **node,
                                "key": f"ca:{code}:node:{path}",
                                "heading": node["HEADING"],
                                "kind": "publisher_toc_node",
                            }
                return sorted(found.values(), key=lambda n: _number(n["NODE_SEQUENCE"]))

            def ordering(row: dict[str, Any], code: str = code) -> tuple[int, int, str]:
                mappings = by_version[(code, row["LAW_SECTION_VERSION_ID"])]
                return min(
                    (
                        (_number(t["SEQ_NUM"]), _number(t["SECTION_ORDER"]), row["ID"])
                        for t in mappings
                    ),
                    default=(2**31, 2**31, row["ID"]),
                )

            _, count, inserted = store.ingest(
                collection=COLLECTION,
                document=f"ca:{code}",
                title=titles.get(code, code),
                url=BASE,
                acquisition=receipt.id,
                member="LAW_SECTION_TBL.dat",
                snapshot_date=snapshot,
                snapshot_basis=report["snapshot_basis"],
                parser=PARSER,
                metadata={
                    "code": code,
                    "source_session": "2025-2026",
                    "snapshot_http_last_modified": modified,
                    "delta_gap": report["delta_gap"],
                    "hierarchy_source_member": "LAW_TOC_TBL.dat",
                    "hierarchy_nodes": len(code_nodes),
                    "section_order_source_member": "LAW_TOC_SECTIONS_TBL.dat",
                    "section_toc_rows": sum(r["LAW_CODE"] == code for r in toc),
                    "context_basis": "Each provision retains exact matching TOC rows and ancestor rows; all source tables remain in the raw ZIP.",
                    "effective_date_basis": "Per-section EFFECTIVE_DATE retained; not inferred from filename.",
                },
                provisions=(
                    section_unit(
                        archive,
                        row,
                        by_version[(code, row["LAW_SECTION_VERSION_ID"])],
                        context(row),
                    )
                    for row in sorted(rows, key=ordering)
                ),
            )
            store.inventory(COLLECTION, code, receipt.url, "imported_baseline_missing_updates")
            report["per_code"][code] = {"sections": count, "inserted": inserted}
            print(f"California {code}: {count} section versions", flush=True)
        report["missing_code_tables"] = sorted(set(titles) - set(by_code))
        report["usable_text_bytes"] = store.db.execute(
            "SELECT coalesce(sum(length(CAST(p.text AS BLOB))),0) FROM provisions p "
            "JOIN versions v ON v.id=p.version_id JOIN documents d ON d.id=v.document_id "
            "WHERE d.collection_id=?",
            (COLLECTION,),
        ).fetchone()[0]
        report["law_lob_bytes"] = sum(
            archive.getinfo(r["CONTENT_XML"]).file_size for r in sections if r["CONTENT_XML"]
        )
        report["raw_retained_bytes"] = store.db.execute(
            "SELECT sum(bytes) FROM artifacts"
        ).fetchone()[0]
        report["untranscribed_media_records"] = store.db.execute(
            "SELECT count(*) FROM provisions WHERE json_extract(metadata,'$.text_quality')=?",
            ("incomplete_without_source_media",),
        ).fetchone()[0]
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=Path("data/collectors/california"))
    parser.add_argument("--metadata-only", action="store_true")
    args = parser.parse_args()
    store = Store(args.store)
    # Separate persistent acquisition receipts enforce an aggregate retained-byte cap
    # across reruns. Cached successes are reused, including the large baseline.
    retained = store.db.execute("SELECT coalesce(sum(bytes),0) FROM artifacts").fetchone()[0]
    remaining = max(0, 1536 * 1024**2 - retained)
    acquirer = Acquirer(store, max_bytes=min(25 * 1024**2, remaining), delay=1.0)
    try:
        register(store)
        for name in ("", "pubinfo_load.zip", "pubinfo_Readme.pdf", "pubinfo_News.pdf"):
            metadata_receipt = acquirer.fetch(BASE + name, max_file_bytes=25 * 1024**2)
            if name == "pubinfo_load.zip":
                verify_loader(store.object_path(metadata_receipt.sha256))
        if args.metadata_only:
            return
        acquirer.max_bytes = remaining
        receipt = acquirer.fetch(BASE + "pubinfo_2025.zip", max_file_bytes=1536 * 1024**2)
        report = import_archive(store, receipt)
        (store.root / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except (AcquisitionError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        store.inventory(
            COLLECTION, "baseline", BASE + "pubinfo_2025.zip", "blocked_or_incomplete", str(exc)
        )
        raise
    finally:
        acquirer.close()
        store.close()


if __name__ == "__main__":
    main()
