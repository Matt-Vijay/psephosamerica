"""Pure replay of the accepted Florida and New Jersey source projections.

The caller must load the pinned historical ``psephos.parse`` / ``psephos.store``
profile. These functions never acquire sources, open a Store, or infer clocks.
Archive integrity is checked by the runner before it extracts ZIP members.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass, replace

from lxml import html

from psephos.parse import markup, media_links, readable, source_links
from psephos.store import Provision

NJ_BASE = "https://pub.njleg.gov/statutes/"
NJ_STATUTES_ARCHIVE_SHA256 = "96d15d26940dff6b96c2c34e8050c99a0687e143d395fc7ccb622e354505cf5a"
NJ_STATUTES_TXT_SHA256 = "f08f83cba812ee3389faada1649e4204f88c91242a141dd14e1e79ad6d4a1e6c"
NJ_STATUTES_RTF_SHA256 = "5fbc18d5b9e41b5144bd4aebf023abd4c472c638d6a950af362d8450103c9ccf"
NJ_THE_TEXT_SHA256 = "1c64b67aeeaa12b6360784debef2decf8c4ab4ca0431991c4bb63b4fa60dde26"


def florida(
    data: bytes, document: str, url: str, constitution: bool = False
) -> Iterator[Provision]:
    """Accepted ``florida_units`` with its text-2 helper dependencies unchanged."""
    page = html.fromstring(data)
    containers = page.xpath(
        '//div[@class="Constitution"]' if constitution else '//div[@class="Chapter"]'
    )
    if len(containers) != 1:
        raise ValueError("Expected one complete publisher legal container")
    container = containers[0]
    sections = container.xpath('.//div[@class="Section"]')
    if not sections:
        raise ValueError("No legal sections; refuse catalog/blocked stub")
    native_keys = []
    for node in sections:
        number = "".join(node.xpath('./span[@class="SectionNumber"]//text()')).strip()
        if not number:
            raise ValueError("Section missing native number")
        article_nodes = node.xpath('ancestor::div[@class="Article"]/div[@class="ArticleNumber"]')
        article = readable(article_nodes[0]) if article_nodes else ""
        native_keys.append((article, number))
    totals = Counter(native_keys)
    seen: Counter[tuple[str, str]] = Counter()
    for node, (article, number) in zip(sections, native_keys, strict=True):
        seen[(article, number)] += 1
        anchor = next(iter(node.xpath('./span[@class="SectionNumber"]//a/@name')), None)
        clean_number = re.sub(r"^SECTION\s+|\.$", "", number)
        key = f"fl:const/{article}/{clean_number}" if constitution else f"fl:stat/{clean_number}"
        if totals[(article, number)] > 1:
            key += f"/occurrence/{seen[(article, number)]}"
        heading = "".join(
            node.xpath('./span[@class="Catchline"]//span[@class="CatchlineText"]//text()')
        ).strip()
        hierarchy = [
            readable(n)
            for a in reversed(list(node.iterancestors()))
            for n in a.xpath(
                './div[@class="Title" or @class="ChapterTitle" or @class="PartTitle" or @class="ArticleNumber" or @class="ArticleName"]'
            )
        ]
        yield Provision(
            key=key,
            citation=f"Fla. Const. {article.title()}, § {clean_number}"
            if constitution
            else f"Fla. Stat. § {clean_number} (2026)",
            heading=heading,
            text=readable(node),
            markup=markup(node),
            url=url + ("#" + anchor if anchor else ""),
            parent_key=document,
            metadata={
                "source_number": number,
                "source_anchor": anchor,
                "article": article,
                "hierarchy": hierarchy,
                "duplicate_number_count": totals[(article, number)],
                "tables": len(node.xpath(".//table")),
                "media": media_links(node, url),
                "clock_note": "History and future/conditional effective notes retained verbatim; no inferred legal effect date.",
            },
            references=source_links(node, url),
        )
    context = deepcopy(container)
    for node in context.xpath('.//div[@class="Section"]'):
        node.drop_tree()
    if readable(context):
        yield Provision(
            key=document + "/_context",
            citation=("Florida Constitution" if constitution else document)
            + " — publisher context",
            heading="Publisher headings, indexes, preamble, and scope notes",
            text=readable(context),
            markup=markup(context),
            url=url,
            parent_key=document,
            unit_kind="scope_notes",
            metadata={
                "includes_preamble": constitution,
                "source_sections": len(sections),
                "media": media_links(context, url),
            },
            references=source_links(context, url),
        )


@dataclass(frozen=True)
class NJVolume:
    key: str
    title: str
    start: int
    end: int
    kind: str


@dataclass(frozen=True)
class PreparedNJ:
    """One verified member pair, decoded once for all 71 accepted volumes."""

    text: str
    volumes: tuple[NJVolume, ...]
    headnotes: tuple[tuple[int, str], ...]
    blank_headnotes: int
    txt_sha256: str
    rtf_sha256: str
    artifact_sha: str | None


def rtf_headnote_text(raw: str) -> str:
    """Preserve the accepted minimal RTF-style headnote decoder exactly."""
    raw = raw.replace("\r", "").replace("\n", "")
    raw = re.sub(r"\\'([a-fA-F0-9]{2})", lambda m: bytes.fromhex(m[1]).decode("cp1252"), raw)
    raw = raw.replace("\\line ", "\r\n").replace("\\tab ", "\t")
    raw = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", raw)
    return raw.replace("{", "").replace("}", "").strip()


def prepare_nj(txt: bytes, rtf: bytes, *, artifact_sha: str | None = None) -> PreparedNJ:
    """Verify accepted members and locate headnotes once, without any I/O.

    ``artifact_sha`` must be the runner's independently verified enclosing ZIP
    hash. Extracted members cannot establish archive integrity by themselves.
    Its absence is allowed for ordinary volumes, but the acceptance overlay
    fails closed unless the accepted archive identity is supplied.
    """
    txt_sha = hashlib.sha256(txt).hexdigest()
    rtf_sha = hashlib.sha256(rtf).hexdigest()
    if txt_sha != NJ_STATUTES_TXT_SHA256 or rtf_sha != NJ_STATUTES_RTF_SHA256:
        raise ValueError(
            "NJ replay requires the exact accepted STATUTES.TXT / STATUTES.RTF members"
        )
    if artifact_sha is not None and artifact_sha != NJ_STATUTES_ARCHIVE_SHA256:
        raise ValueError("NJ replay archive identity does not match the accepted artifact")
    text = txt.decode("cp1252")
    rtf_text = rtf.decode("cp1252")
    heads = []
    cursor = 0
    blank = 0
    for raw in re.findall(r"\\s3 (.*?)\\par", rtf_text, re.S):
        heading = rtf_headnote_text(raw)
        if not heading:
            blank += 1
            continue
        at = text.find(heading, cursor)
        if at < 0:
            raise ValueError("RTF headnote absent from companion TXT: " + repr(heading))
        heads.append((at, heading))
        cursor = at + len(heading)
    if len(heads) < 10000:
        raise ValueError("Statute export lacks expected substantive headnotes")
    titles = list(
        re.finditer(
            r"^(?:TITLE\s+(\d+[A-Z]?)\s+([^\r\n]+)|APPENDIX ([A-Z])\s+([^\r\n]+))",
            text,
            re.M,
        )
    )
    volumes = [
        NJVolume("preface", "Statute export preface", 0, titles[0].start(), "publisher_preface")
    ]
    for i, match in enumerate(titles):
        key = match[1] or "App." + match[3]
        volumes.append(
            NJVolume(
                key,
                match[0].strip(),
                match.start(),
                titles[i + 1].start() if i + 1 < len(titles) else len(text),
                "statutory_title" if match[1] else "statutory_appendix",
            )
        )
    return PreparedNJ(text, tuple(volumes), tuple(heads), blank, txt_sha, rtf_sha, artifact_sha)


def _accepted_nj_classification(unit: Provision, prepared: PreparedNJ, document: str) -> Provision:
    """Apply the sole evidenced acceptance overlay, never a general parser fix.

    The accepted adapter emits nine disposition notes and nine source blocks.
    The accepted source/canonical rows and acceptance accounting preserve eight
    and ten respectively: this exact malformed-identifier entry is source_block.
    No source text, identifier, metadata, or neighboring body is modified.
    """
    if unit.key != "nj-statutes:THE":
        return unit
    metadata = unit.metadata
    if not (
        prepared.artifact_sha == NJ_STATUTES_ARCHIVE_SHA256
        and prepared.txt_sha256 == NJ_STATUTES_TXT_SHA256
        and prepared.rtf_sha256 == NJ_STATUTES_RTF_SHA256
        and document == "nj-statutes:54A"
        and metadata.get("source_member") == "STATUTES.TXT"
        and metadata.get("source_rtf_member") == "STATUTES.RTF"
        and metadata.get("source_start_byte") == 79052965
        and metadata.get("source_end_byte") == 79053047
        and hashlib.sha256(unit.text.encode("utf-8")).hexdigest() == NJ_THE_TEXT_SHA256
        and unit.unit_kind == "disposition_note"
    ):
        raise ValueError("NJ accepted classification overlay guard failed for nj-statutes:THE")
    return replace(unit, unit_kind="source_block")


def nj_statute_units(prepared: PreparedNJ, document: str) -> Iterator[Provision]:
    """Generate one accepted volume from a shared, verified member preparation."""
    matches = [v for v in prepared.volumes if "nj-statutes:" + v.key == document]
    if len(matches) != 1:
        raise ValueError("Expected one accepted NJ statute volume: " + document)
    volume = matches[0]
    relevant = [(at, h) for at, h in prepared.headnotes if volume.start <= at < volume.end]
    points: list[tuple[int, str, str | None]] = [(volume.start, volume.title, None)]
    for at, heading in relevant:
        head_token = heading.split()[0].rstrip(".,")
        if re.match(r"^52:9H [0-9]+\b", heading):
            head_token = " ".join(heading.split()[:2])
        points.append((at, heading, head_token))
    counts = Counter(x[2] for x in points if x[2])
    seen: Counter[str | None] = Counter()
    for i, (start, heading, token) in enumerate(points):
        end = points[i + 1][0] if i + 1 < len(points) else volume.end
        if start == end:
            continue
        text = prepared.text[start:end]
        if not text.strip():
            raise ValueError("Unexpected whitespace-only source span")
        kind = (
            "section"
            if token and re.fullmatch(r"(?:\d+[A-Z]?|App\.A):[\w.():-]+", token)
            else "source_block"
            if token
            else "scope_notes"
        )
        if token and text.strip() == heading.strip():
            kind = (
                "disposition_note"
                if re.search(r"reallocat|relocat|repeal", text, re.I)
                else "section_heading"
            )
        seen[token] += 1
        key = "nj-statutes:" + token if token else document + "/_intro"
        if token and counts[token] > 1:
            key += "/occurrence-" + str(seen[token])
        unit = Provision(
            key=key,
            citation="N.J. Stat. § " + token if token else volume.title,
            heading=heading,
            text=text,
            markup="",
            url=NJ_BASE + "STATUTES-TEXT.zip",
            parent_key=document,
            unit_kind=kind,
            metadata={
                "source_member": "STATUTES.TXT",
                "source_start_byte": start,
                "source_end_byte": end,
                "encoding": "windows-1252",
                "boundary_basis": "publisher RTF Headnotes style s3, exact sequential mapping to TXT",
                "source_identifier": token,
                "duplicate_identifier_count": counts[token] if token else 0,
                "source_rtf_member": "STATUTES.RTF",
                "warning": "Plain-text projection; companion original RTF retained. Source identifier typos are not silently corrected. Legal effectiveness is not inferred.",
            },
        )
        yield _accepted_nj_classification(unit, prepared, document)


def nj_statutes(
    txt: bytes, rtf: bytes, document: str, *, artifact_sha: str | None = None
) -> Iterator[Provision]:
    """Convenience wrapper; multi-document runners should share ``prepare_nj``."""
    return nj_statute_units(prepare_nj(txt, rtf, artifact_sha=artifact_sha), document)


def nj_constitution(data: bytes, document: str, url: str) -> Iterator[Provision]:
    """Preserve the accepted complete preamble/article CP1252 source partition."""
    if document != "nj-constitution:1947":
        raise ValueError("Unexpected accepted NJ Constitution document")
    text = data.decode("cp1252")
    articles = list(re.finditer(r"^[\t ]*ARTICLE ([IVX]+)[\t ]*\r?$", text, re.M))
    if len(articles) != 11:
        raise ValueError("Constitution expected 11 source articles")
    starts = [(0, "preamble")] + [(m.start(), m[1]) for m in articles]
    for i, (start, name) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        yield Provision(
            key="nj-constitution:" + name,
            citation="N.J. Const. " + ("preamble" if name == "preamble" else "art. " + name),
            heading="New Jersey Constitution " + name,
            text=text[start:end],
            markup="",
            url=url,
            unit_kind="preamble" if name == "preamble" else "article",
            metadata={
                "source_member": "NJCONST.TXT",
                "source_start_byte": start,
                "source_end_byte": end,
                "encoding": "windows-1252",
                "warning": "Article-level units retain all sections, paragraphs, amendment/effective-date annotations; companion RTF preserved.",
            },
        )
