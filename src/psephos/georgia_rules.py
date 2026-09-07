"""Publisher-listed Georgia department PDFs; page-faithful text and explicit image access."""

from __future__ import annotations

import html as html_std
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, quote, urljoin, urlsplit

from lxml import html
from pypdf import PageObject, PdfReader

from .store import Provision, Reference, digest, json_text

INDEX = "https://rules.sos.ga.gov/Download_pdf.aspx"
COLLECTION = "ga-administrative-rules"
PARSER = "ga-department-pdf-pages/3"
CLOCK_NOTE = (
    "The cover filing-through statement dates incorporation, not publication or legal effect. "
    "PDF creation/modification and HTTP/acquisition dates are separate. Rule histories remain "
    "in source text; no consolidated effective date or unobserved updates inferred."
)
MEDIA_NOTE = (
    "Original PDF contains authoritative source layout, tables and graphics. Layout extraction "
    "and machine OCR are navigation aids, not verified table-cell transcriptions. Consult the "
    "linked physical page before relying on numbers, diagrams, signatures or column alignment."
)


def inventory(data: bytes) -> list[dict[str, str]]:
    """Only native download links; never synthesize department names or filenames."""
    result = []
    for anchor in html.fromstring(data).xpath("//a[@href]"):
        label = " ".join(anchor.text_content().split())
        match = re.match(r"Department (\d+)\.\s", label)
        if not match:
            continue
        url = quote(urljoin(INDEX, anchor.get("href")), safe=":/?=&%")
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        if (
            parsed.scheme + "://" + parsed.netloc + parsed.path != INDEX
            or parsed.fragment
            or query.get("st") != ["GASOS"]
            or query.get("dept") != ["Departments"]
            or not query.get("pdf", [""])[0].startswith("Department " + match[1] + " ")
        ):
            raise ValueError("Department label/download URL disagreement")
        result.append({"department": match[1], "title": label, "url": url})
    if not result or len({r["department"] for r in result}) != len(result):
        raise ValueError("Empty/duplicate Georgia department inventory")
    return result


def layout_pages(path: Path) -> list[str]:
    output = subprocess.run(
        ["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True
    ).stdout.decode("utf-8")
    pages = output.split("\f")
    if not pages[-1].strip():
        pages.pop()
    return pages


def outlines(reader: PdfReader) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    def walk(nodes: list[Any], parents: tuple[str, ...] = ()) -> None:
        previous = ""
        for node in nodes:
            if isinstance(node, list):
                walk(node, (*parents, previous) if previous else parents)
            else:
                previous = str(node.title)
                page = reader.get_destination_page_number(node)
                if page is None or not 0 <= page < len(reader.pages):
                    raise ValueError("Publisher bookmark has no valid physical page")
                result.append(
                    {
                        "title": previous,
                        "parents": list(parents),
                        "page": page + 1,
                        "left": str(node.get("/Left", "")),
                        "top": str(node.get("/Top", "")),
                    }
                )

    walk(reader.outline)
    return result


def pdf_value(value: Any, reader: PdfReader, depth: int = 0) -> Any:
    """Bounded annotation values, without process-specific pypdf reader addresses."""
    if depth > 6:
        return {"projection": "nested PDF value retained in original bytes"}
    if hasattr(value, "idnum"):
        resolved = value.get_object()
        if isinstance(resolved, dict) and resolved.get("/Type") == "/Page":
            number = reader.get_page_number(cast(PageObject, resolved))
            if number is None:
                raise ValueError("Annotation page destination is absent from page tree")
            return {"physical_page": number + 1}
        return pdf_value(resolved, reader, depth + 1)
    if isinstance(value, dict):
        return {str(k): pdf_value(v, reader, depth + 1) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [pdf_value(v, reader, depth + 1) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def page_details(page: Any, reader: PdfReader) -> tuple[list[str], list[dict[str, Any]]]:
    resources = page.get("/Resources", {})
    resources = resources.get_object() if hasattr(resources, "get_object") else resources
    objects = resources.get("/XObject", {})
    objects = objects.get_object() if hasattr(objects, "get_object") else objects
    images = [
        str(name) for name, obj in objects.items() if obj.get_object().get("/Subtype") == "/Image"
    ]
    annotations = []
    for ref in page.get("/Annots", []):
        obj = ref.get_object()
        annotations.append(
            {
                str(k): pdf_value(obj[k], reader)
                for k in ("/Subtype", "/Rect", "/A", "/Dest", "/Contents")
                if k in obj
            }
        )
    return images, annotations


def ocr_page(path: Path, page: int, cache: Path) -> tuple[str, str, dict[str, Any]]:
    """Retain reproducible derived OCR, never pretend it is publisher-supplied text."""
    directory = cache / path.name
    directory.mkdir(parents=True, exist_ok=True)
    stem = directory / str(page)
    receipt_path = stem.with_suffix(".json")
    commands = [
        ["pdftoppm", "-f", str(page), "-l", str(page), "-r", "200", "-singlefile", "-png"],
        ["tesseract", "-l", "eng", "--psm", "3", "txt", "hocr"],
    ]
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_bytes())
        for suffix, expected in receipt["derived_sha256"].items():
            if digest(stem.with_suffix(suffix).read_bytes()) != expected:
                raise ValueError("Derived OCR cache digest mismatch")
    else:
        subprocess.run([*commands[0], str(path), str(stem)], check=True, capture_output=True)
        subprocess.run(
            ["tesseract", str(stem.with_suffix(".png")), str(stem), *commands[1][1:]],
            check=True,
            capture_output=True,
        )
        receipt = {
            "source_sha256": path.name,
            "physical_page": page,
            "projection": "machine OCR, unverified; source image controls",
            "commands": commands,
            "tesseract": subprocess.run(
                ["tesseract", "--version"], check=True, capture_output=True, text=True
            ).stdout.splitlines()[0],
            "poppler": subprocess.run(
                ["pdftoppm", "-v"], check=True, capture_output=True, text=True
            ).stderr.splitlines()[0],
            "derived_sha256": {
                suffix: digest(stem.with_suffix(suffix).read_bytes())
                for suffix in (".png", ".txt", ".hocr")
            },
        }
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    # HOCR's generated image filename is local machinery, not publisher evidence.
    root = html.fromstring(stem.with_suffix(".hocr").read_bytes())
    for node in root.xpath("//*[@title]"):
        node.set(
            "title",
            re.sub(r'image "[^"]*"', f'image "sha256:{path.name}#page={page}"', node.get("title")),
        )
    body = root.xpath("//body")
    markup = (
        html.tostring(body[0], encoding="unicode")
        if body
        else html.tostring(root, encoding="unicode")
    )
    projection_receipt = {
        k: receipt[k]
        for k in (
            "source_sha256",
            "physical_page",
            "projection",
            "commands",
            "tesseract",
            "poppler",
        )
    }
    projection_receipt["text_sha256"] = receipt["derived_sha256"][".txt"]
    projection_receipt["normalized_hocr_sha256"] = digest(markup.encode())
    projection_receipt["image_sha256"] = receipt["derived_sha256"][".png"]
    return stem.with_suffix(".txt").read_text(), markup, projection_receipt


def pdf_projection(
    path: Path,
    item: dict[str, str],
    ocr_cache: Path,
    reviewed_media: dict[str, dict[str, str]] | None = None,
) -> tuple[list[Provision], str, dict[str, Any]]:
    """All physical pages, including image-only pages; no blank legal-text substitutes."""
    with path.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise ValueError("Expected actual department PDF bytes")
    reader = PdfReader(path)
    pages = layout_pages(path)
    if len(pages) != len(reader.pages):
        raise ValueError("Physical page tree/layout extraction count differs")
    first = " ".join(pages[0].split())
    current = re.search(
        r"Current through Rules and Regulations filed through (\w+ \d{1,2}, \d{4})", first
    )
    if not current or not re.search(r"Department\s+" + item["department"] + r"(?:\.\s|\s)", first):
        raise ValueError("Cover lacks expected department identity/filing-through statement")
    snapshot = datetime.strptime(current[1], "%B %d, %Y").date().isoformat()
    navigation = outlines(reader)
    units = []
    image_pages, ocr_pages, media_only_pages = [], [], []
    for number, (native, page) in enumerate(zip(pages, reader.pages, strict=True), 1):
        images, annotations = page_details(page, reader)
        markers = [n for n in navigation if n["page"] == number]
        meta: dict[str, Any] = {
            "physical_page": number,
            "page_count": len(pages),
            "publisher_outlines": markers,
            "pdf_annotations": annotations,
            "image_xobjects": images,
            "media_treatment": MEDIA_NOTE,
            "source_text_characters": len(native),
            "text_quality": "publisher_layout_text",
            "projection": "pdftotext -layout; full physical page",
        }
        text = native
        kind = "pdf_page"
        body = "<pre>" + html_std.escape(native) + "</pre>"
        if not native.strip():
            if not images:
                raise ValueError(f"Unresolved blank/vector-only page {number}; review required")
            text, body, receipt = ocr_page(path, number, ocr_cache)
            meta.update(
                text_quality="machine_ocr_unverified",
                projection="Machine OCR for navigation; never publisher text",
                ocr_receipt=receipt,
            )
            if len(text.strip()) < 20:
                review = (reviewed_media or {}).get(f"{path.name}:{number}")
                if not review or not review.get("caption") or not review.get("limitation"):
                    raise ValueError(f"Image-only page {number} needs manual visual treatment")
                # Navigation to actual reviewed source media, explicitly not legal text.
                text = review["caption"] + "\n[Source image only: " + review["limitation"] + "]"
                kind = "pdf_media_page"
                meta.update(text_quality="source_media_only", visual_review=review)
                media_only_pages.append(number)
            else:
                text = "[Machine OCR: unverified navigation text, not publisher text.]\n" + text
                ocr_pages.append(number)
        if images:
            image_pages.append(number)
            meta["media"] = [
                {
                    "source_locator": f"#page={number}",
                    "url": item["url"],
                    "alt": f"Original department {item['department']} PDF physical page {number}",
                    "kind": "embedded_pdf_page",
                    "physical_page": number,
                    "source_sha256": path.name,
                }
            ]
            text += "\n[Source media: consult the original PDF page; images are not verified text.]"
        meta["rule_identifiers_in_text"] = sorted(
            set(re.findall(r"\b\d+(?:-\d+)+-\.\d+\b", native))
        )
        refs = tuple(
            Reference(a["/A"]["/URI"], "publisher_pdf_link", a["/A"]["/URI"], json_text(a))
            for a in annotations
            if isinstance(a.get("/A"), dict) and a["/A"].get("/URI")
        )
        units.append(
            Provision(
                key=f"ga-rules:department-{item['department']}:page-{number}",
                citation=f"Ga. Comp. R. & Regs., Department {item['department']}, PDF page {number}",
                heading=" | ".join(n["title"] for n in markers)
                or f"{item['title']} — page {number}",
                text=text,
                markup=f'<section data-pdf-page="{number}" data-projection="{meta["text_quality"]}">{body}</section>',
                url=item["url"] + f"#page={number}",
                parent_key="ga-rules:department-" + item["department"],
                unit_kind=kind,
                metadata=meta,
                references=refs,
            )
        )
    return (
        units,
        snapshot,
        {
            "publisher_current_through": snapshot,
            "publisher_current_through_statement": current[0],
            "page_count": len(pages),
            "indexed_page_count": len(units),
            "unextractable_or_blank_pages": [],
            "machine_ocr_pages": ocr_pages,
            "media_only_pages": media_only_pages,
            "image_pages": image_pages,
            "publisher_outlines": navigation,
            "rule_outline_count": sum(n["title"].startswith("Rule ") for n in navigation),
            "text_characters": sum(len(t) for t in pages),
            "pdf_metadata": {str(k): str(v) for k, v in (reader.metadata or {}).items()},
            "clock_treatment": CLOCK_NOTE,
            "fidelity": MEDIA_NOTE,
        },
    )
