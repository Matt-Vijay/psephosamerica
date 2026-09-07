"""Offline projection of the accepted Mississippi court-rule PDF volumes.

This preserves the reviewed ``ms-pdf-volume-layout-1/text-2`` procedure. Its
historical output depends on pypdf 6.17.0 and Poppler pdftotext 26.02.0; it does
not discover sources, acquire bytes, infer legal dates, or write a Store.
"""

from __future__ import annotations

import hashlib
import html
import io
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from psephos.store import Provision, Reference

_INDIRECT_OBJECT = re.compile(r"IndirectObject\((\d+), (\d+), (\d+)\)")
_OPTIONAL_METADATA_DEFAULTS: dict[str, Any] = {
    "sparse_page_review": {},
    "text_gaps": [],
    "full_legal_text_eligible": True,
    "representation_gaps": [],
}


def _resolved(value: Any) -> Any:
    return value.get_object() if hasattr(value, "get_object") else value


def project_pdf(
    path: Path,
    document: str,
    title: str,
    url: str,
    inventory_receipt: int,
    review: dict[str, Any],
    *,
    snapshot_profile: dict[str, Any] | None = None,
) -> Provision:
    """Project retained bytes, optionally restoring receipted serialization state.

    The artifact-bound profile restores only pypdf's historical process identity
    inside annotation/reference repr strings and historically absent empty
    metadata fields. It cannot replace PDF wording, object IDs, or other metadata.
    """
    path = path.resolve(strict=True)
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("PDF must be a regular file within the 16 MiB source bound")
    executable = shutil.which("pdftotext")
    if executable is None:
        raise ValueError("Replay requires Poppler pdftotext 26.02.0 on PATH")
    raw = path.read_bytes()
    if not raw.startswith(b"%PDF-"):
        raise ValueError("Publisher object is not PDF")
    reader = PdfReader(io.BytesIO(raw))
    result = subprocess.run(
        [executable, "-layout", "-enc", "UTF-8", str(path), "-"],
        capture_output=True,
        check=True,
        timeout=120,
    )
    pages = result.stdout.decode("utf-8").split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    if len(pages) != len(reader.pages):
        raise ValueError("PDF page extraction count mismatch")
    sparse_review = review.get("pages", {})
    for number, page_text in enumerate(pages, 1):
        if len(re.sub(r"\s", "", page_text)) >= 30:
            continue
        expected = sparse_review.get(str(number), {}).get("expected_layout_text_sha256")
        if expected != hashlib.sha256(page_text.encode()).hexdigest():
            raise ValueError(f"Unreviewed sparse PDF page {number}; volume not counted")
    text = "\n\n".join(
        f"[PDF page {number}]\n"
        + page_text
        + (
            "\n[Collector note: " + sparse_review[str(number)]["collector_note"] + "]\n"
            if str(number) in sparse_review
            else ""
        )
        for number, page_text in enumerate(pages, 1)
    )
    if len(text) < 400 or not re.search(r"\b(shall|must|may)\b", text, re.I):
        raise ValueError("No substantive legal text")
    destinations = {}
    for key, value in reader.named_destinations.items():
        page_number = reader.get_destination_page_number(value)
        if page_number is None:
            raise ValueError(f"PDF named destination has no page: {key}")
        destinations[str(key)] = page_number + 1
    references = []
    annotations = []
    images = []
    for number, page in enumerate(reader.pages, 1):
        resources = _resolved(page.get("/Resources", {}))
        objects = _resolved(resources.get("/XObject", {}))
        image_names = [
            str(key)
            for key, value in objects.items()
            if value.get_object().get("/Subtype") == "/Image"
        ]
        if image_names:
            images.append(
                {
                    "pdf_page": number,
                    "image_objects": image_names,
                    "status": "preserved in native PDF; text extraction may not transcribe image",
                }
            )
        for annotation in page.get("/Annots", []):
            obj = annotation.get_object()
            action = _resolved(obj.get("/A", {}))
            target = action.get("/URI")
            annotations.append({"pdf_page": number, "native_annotation": str(obj)})
            if target:
                references.append(
                    Reference(str(target), "publisher_pdf_link", f"PDF page {number}", str(obj))
                )
    metadata = {
        "representation": "complete PDF volume; layout text projection",
        "pdf_pages": len(pages),
        "page_labels": list(reader.page_labels),
        "native_named_destinations": destinations,
        "pdf_annotations": annotations,
        "media": images,
        "inventory_receipt": inventory_receipt,
        "sparse_page_review": sparse_review,
        "text_gaps": review.get("text_gaps", []),
        "edition_or_incorporation_label": title + " | " + url.rsplit("/", 1)[-1],
        "edition_or_incorporation_date": None,
        "publication_date": None,
        "legal_effect_date": None,
        "clock_note": (
            "Filename/index dates retained as publisher labels only; no blanket legal effect or "
            "publication date inferred. Rule-specific history retained in text."
        ),
        "full_legal_text_scope": (
            "complete PDF volume with extracted wording and form media gaps; "
            "excluded from full-legal-text count"
            if images
            else "whole rule volume including notes, forms, tables, citations and front matter; "
            "not a rule count"
        ),
        "full_legal_text_eligible": not bool(images),
        "representation_gaps": [
            {
                "pdf_page": image["pdf_page"],
                "kind": "form_checkbox_graphics",
                "detail": "Blank checkbox graphics are not represented in layout text; "
                "source form and labels remain in native PDF",
                "native_pdf_preserved": True,
            }
            for image in images
        ],
        "pdf_metadata": {str(key): str(value) for key, value in (reader.metadata or {}).items()},
    }
    if snapshot_profile is not None:
        if snapshot_profile.get("artifact_sha256") != hashlib.sha256(raw).hexdigest():
            raise ValueError("PDF serialization profile does not match artifact")
        identity = snapshot_profile.get("accepted_reader_identity")
        if identity is not None and (type(identity) is not int or identity <= 0):
            raise ValueError("PDF reader identity must be a positive integer or null")

        def restore_identity(value: str) -> str:
            if identity is None:
                if _INDIRECT_OBJECT.search(value):
                    raise ValueError("PDF serialization profile lacks reader identity")
                return value
            return _INDIRECT_OBJECT.sub(
                lambda match: f"IndirectObject({match[1]}, {match[2]}, {identity})", value
            )

        for annotation in annotations:
            annotation["native_annotation"] = restore_identity(str(annotation["native_annotation"]))
        references = [
            Reference(ref.target, ref.relation, ref.label, restore_identity(ref.evidence))
            for ref in references
        ]
        omissions = snapshot_profile.get("omitted_metadata_keys", [])
        if not isinstance(omissions, list) or any(not isinstance(key, str) for key in omissions):
            raise ValueError("PDF metadata omissions must be a list of field names")
        for key in omissions:
            if (
                key not in _OPTIONAL_METADATA_DEFAULTS
                or metadata.get(key) != _OPTIONAL_METADATA_DEFAULTS[key]
            ):
                raise ValueError(f"Cannot omit non-default or unreviewed PDF metadata: {key}")
            metadata.pop(key)
    markup = (
        '<pdfVolume source="'
        + html.escape(url, quote=True)
        + '">'
        + "".join(
            f'<page number="{number}"><pre>{html.escape(page_text)}</pre></page>'
            for number, page_text in enumerate(pages, 1)
        )
        + "</pdfVolume>"
    )
    return Provision(
        key=document,
        citation=title,
        heading=title,
        text=text,
        markup=markup,
        url=url + "#page=1",
        unit_kind="rule_volume_with_media_gaps" if images else "rule_volume",
        metadata=metadata,
        references=tuple(references),
    )
