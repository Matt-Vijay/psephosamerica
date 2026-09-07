"""Source structure stays intact; retrieval text is a navigable projection, not a summary."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator
from copy import deepcopy
from urllib.parse import urljoin

from lxml import etree

from .store import Provision, Reference

BLOCKS = {
    "p",
    "paragraph",
    "subsection",
    "subparagraph",
    "clause",
    "subclause",
    "content",
    "heading",
    "head",
    "num",
    "section",
    "article",
    "rule",
    "notes",
    "note",
    "sourcecredit",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "tr",
    "row",
    "br",
    "cita",
    "auth",
}


def local_name(node: etree._Element) -> str:
    return etree.QName(node).localname if isinstance(node.tag, str) else ""


def xml_root(data: bytes) -> etree._Element:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    root = etree.fromstring(data, parser)
    if root.getroottree().docinfo.doctype:
        raise ValueError("DTD-bearing XML is not supported; no external entity expansion")
    return root


def readable(node: etree._Element) -> str:
    """Keep paragraph/row boundaries and inline punctuation; XML is retained for full fidelity."""
    parts: list[str] = []

    def walk(element: etree._Element) -> None:
        tag = local_name(element).lower()
        if not tag:
            return
        if tag in {"img", "graphic", "gph"}:
            locator = (
                element.get("src")
                or element.get("href")
                or child_text(element, "GID")
                or "see source markup"
            )
            if locator.startswith("data:"):
                locator = "embedded source image"
            parts.append(f"\n[Source media: {locator}; wording/figure not transcribed.]\n")
            return
        if tag in BLOCKS:
            parts.append("\n")
        elif tag in {"td", "th", "entry"}:
            parts.append("\t")
        if element.text:
            parts.append(element.text)
        for child in element:
            walk(child)
            if child.tail:
                parts.append(child.tail)
        if tag in BLOCKS:
            parts.append("\n")

    walk(node)
    lines = [re.sub(r"[^\S\n\t]+", " ", line).strip() for line in "".join(parts).splitlines()]
    return "\n".join(line for line in lines if line)


def child_text(node: etree._Element, name: str) -> str:
    return " ".join(readable(c) for c in node if local_name(c).lower() == name.lower()).strip()


def markup(node: etree._Element) -> str:
    return etree.tostring(node, encoding="unicode", with_tail=False)


def media_links(node: etree._Element, base_url: str) -> list[dict[str, str]]:
    result = []
    for element in node.iter():
        tag = local_name(element).lower()
        if tag not in {"img", "graphic", "gph"}:
            continue
        locator = element.get("src") or element.get("href") or child_text(element, "GID")
        result.append(
            {
                "source_locator": locator if not locator.startswith("data:") else "embedded image",
                "url": urljoin(base_url, locator)
                if locator and not locator.startswith("data:")
                else "",
                "alt": element.get("alt", ""),
                "status": "not_transcribed; locator not necessarily fetchable",
            }
        )
    return result


def source_links(node: etree._Element, base_url: str) -> tuple[Reference, ...]:
    refs = []
    for element in node.iter():
        if local_name(element).lower() not in {"a", "ref", "reference"}:
            continue
        target = element.get("href") or element.get("url")
        if not target:
            continue
        # A URI inside the legal source is evidence, not a request to fetch it.
        label = " ".join(readable(element).split())
        if "uscode.house.gov" in base_url and target.startswith("/us/"):
            refs.append(Reference(target, "publisher_citation_identifier", label, markup(element)))
        else:
            refs.append(
                Reference(urljoin(base_url, target), "publisher_link", label, markup(element))
            )
    return tuple(refs)


def uscode_units(root: etree._Element, title: str) -> Iterator[Provision]:
    unit_tags = {"section", "courtRule", "reorganizationPlan"}
    candidates = [
        n
        for n in root.iter()
        if n.get("identifier", "").startswith("/us/usc/")
        and local_name(n) in unit_tags
        and not any(local_name(a) in unit_tags for a in n.iterancestors())
    ]
    totals = Counter(n.get("identifier") for n in candidates)
    occurrences: Counter[str] = Counter()
    for node in candidates:
        identifier = node.get("identifier", "")
        tag = local_name(node)
        if not identifier.startswith("/us/usc/") or tag not in unit_tags:
            continue
        if any(local_name(a) in unit_tags for a in node.iterancestors()):
            continue
        heading, number = child_text(node, "heading"), child_text(node, "num")
        number_element = next((c for c in node if local_name(c) == "num"), None)
        native_num = number_element.get("value", "") if number_element is not None else ""
        citation = f"{title} U.S.C. § {native_num or number}".strip()
        if tag != "section":
            citation = f"{title} U.S.C. Appendix — {number} {heading}".strip()
        occurrences[identifier] += 1
        # OLRC really publishes some duplicate section numbers. Keep every versioned occurrence,
        # and make the unqualified citation ambiguous rather than choosing one silently.
        suffix = f"/_occurrence/{occurrences[identifier]}" if totals[identifier] > 1 else ""
        url = "https://uscode.house.gov/download/download.shtml"
        if tag == "section" and title.isdigit() and native_num:
            url = (
                "https://uscode.house.gov/view.xhtml?edition=prelim&num=0&req=granuleid%3A"
                f"USC-prelim-title{title}-section{native_num}"
            )
        ancestors = [
            {
                "key": "usc:" + a.get("identifier", ""),
                "kind": local_name(a),
                "heading": child_text(a, "heading"),
                "number": child_text(a, "num"),
            }
            for a in reversed(list(node.iterancestors()))
            if a.get("identifier") and local_name(a) not in {"uscDoc", "main"}
        ]
        refs = source_links(node, "https://uscode.house.gov")
        yield Provision(
            key="usc:" + identifier + suffix,
            citation=citation,
            heading=heading,
            text=readable(node),
            markup=markup(node),
            url=url,
            parent_key=ancestors[-1]["key"] if ancestors else None,
            unit_kind=tag,
            metadata={
                "hierarchy": ancestors,
                "source_number": native_num,
                "source_status": node.get("status"),
                "source_identifier": identifier,
                "duplicate_identifier_count": totals[identifier],
                "occurrence": occurrences[identifier],
                "reading_link_scope": "live preliminary edition"
                if "view.xhtml" in url
                else "publisher download index",
                "tables": len(node.xpath('.//*[local-name()="table"]')),
            },
            references=refs,
        )
    # Introductory and chapter-level notes must not disappear merely because they are not sections.
    scopes = [
        n
        for n in root.iter()
        if n.get("identifier", "").startswith("/us/usc/")
        and local_name(n) not in unit_tags | {"uscDoc"}
        and not any(local_name(a) in unit_tags for a in n.iterancestors())
        and any(local_name(c) in {"notes", "note"} for c in n)
    ]
    scope_totals = Counter(n.get("identifier") for n in scopes)
    scope_seen: Counter[str] = Counter()
    for node in scopes:
        identifier = node.get("identifier", "")
        if not identifier.startswith("/us/usc/") or local_name(node) in unit_tags | {"uscDoc"}:
            continue
        if any(local_name(a) in unit_tags for a in node.iterancestors()):
            continue
        notes = [c for c in node if local_name(c) in {"notes", "note"}]
        if not notes:
            continue
        wrapper = etree.Element("scopeNotes", source=identifier)
        for item in notes:
            wrapper.append(deepcopy(item))
        scope_seen[identifier] += 1
        suffix = f"/_occurrence/{scope_seen[identifier]}" if scope_totals[identifier] > 1 else ""
        yield Provision(
            key="usc:" + identifier + "/_notes" + suffix,
            citation=f"{title} U.S.C. {identifier} notes",
            heading=child_text(node, "heading") + " — scope notes",
            text=readable(wrapper),
            markup=markup(wrapper),
            url="https://uscode.house.gov/download/download.shtml",
            parent_key="usc:" + identifier,
            unit_kind="scope_notes",
            references=source_links(wrapper, "https://uscode.house.gov"),
        )
    # Appendix compiled acts often lack section identifiers. Retain each whole publisher-named act,
    # instead of throwing away repealed/transferred notes or manufacturing section-level identities.
    for ordinal, node in enumerate(root.xpath('.//*[local-name()="compiledAct"]')):
        if any(a.get("identifier") and local_name(a) in unit_tags for a in node.iterancestors()):
            continue
        identity = root.get("identifier", "") + f"/_compiled_act/{ordinal + 1}"
        yield Provision(
            key="usc:" + identity,
            citation=f"{title} U.S.C. appendix — {child_text(node, 'heading')}",
            heading=child_text(node, "heading"),
            text=readable(node),
            markup=markup(node),
            url="https://uscode.house.gov/download/download.shtml",
            unit_kind="compiled_act",
            metadata={"identity_basis": "publisher document order; not a section ID"},
            references=source_links(node, "https://uscode.house.gov"),
        )
    if not root.get("identifier") and "[ELIMINATED]" in readable(root):
        appendix = root.find("{*}appendix")
        if appendix is not None:
            yield Provision(
                key=f"usc:/us/usc/t{title}/_notice",
                citation=f"{title} U.S.C. Appendix — eliminated",
                heading=child_text(appendix, "heading"),
                text=readable(appendix),
                markup=markup(appendix),
                url="https://uscode.house.gov/download/download.shtml",
                unit_kind="editorial_notice",
                metadata={"status": "eliminated", "identity_basis": "publisher docNumber"},
            )


def ecfr_units(root: etree._Element, title: str, snapshot: str) -> Iterator[Provision]:
    def key(node: etree._Element) -> str:
        scope = [a for a in reversed(list(node.iterancestors())) if a.get("TYPE")]
        scope.append(node)
        return "ecfr:" + "/".join(
            a.get("TYPE", local_name(a)).lower() + "-" + a.get("N", "") for a in scope
        )

    for node in root.iter():
        kind = node.get("TYPE", "")
        if kind not in {"SECTION", "APPENDIX"}:
            continue
        if any(a.get("TYPE") in {"SECTION", "APPENDIX"} for a in node.iterancestors()):
            continue
        number = node.get("N", "")
        parent = next((a for a in node.iterancestors() if a.get("TYPE")), None)
        ancestors = [
            {
                "key": key(a),
                "kind": a.get("TYPE"),
                "number": a.get("N"),
                "heading": child_text(a, "HEAD"),
            }
            for a in reversed(list(node.iterancestors()))
            if a.get("TYPE")
        ]
        part = next((a.get("N") for a in node.iterancestors() if a.get("TYPE") == "PART"), None)
        url = f"https://www.ecfr.gov/on/{snapshot}/title-{title}"
        if part:
            url += f"/part-{part}"
        if kind == "SECTION":
            url += f"/section-{number}"
        yield Provision(
            key=key(node),
            citation=f"{title} CFR {number}",
            heading=child_text(node, "HEAD"),
            text=readable(node),
            markup=markup(node),
            url=url,
            parent_key=key(parent) if parent is not None else None,
            unit_kind=kind.lower(),
            metadata={
                "hierarchy": ancestors,
                "cfr_part": part,
                "source_attributes": dict(node.attrib),
            },
            references=source_links(node, url),
        )
    # Authority/source notes outside sections are part of the context of every provision below.
    for node in root.iter():
        if not node.get("TYPE") or node.get("TYPE") in {"SECTION", "APPENDIX"}:
            continue
        if any(a.get("TYPE") in {"SECTION", "APPENDIX"} for a in node.iterancestors()):
            continue
        notes = [c for c in node if local_name(c) in {"AUTH", "SOURCE", "CITA"}]
        if not notes:
            continue
        wrapper = etree.Element("scopeNotes")
        for item in notes:
            wrapper.append(deepcopy(item))
        yield Provision(
            key=key(node) + "/_notes",
            citation=f"{title} CFR {node.get('TYPE')} {node.get('N')} notes",
            heading=child_text(node, "HEAD") + " — authority/source",
            text=readable(wrapper),
            markup=markup(wrapper),
            url=f"https://www.ecfr.gov/on/{snapshot}/title-{title}",
            parent_key=key(node),
            unit_kind="scope_notes",
            references=source_links(wrapper, "https://www.ecfr.gov"),
        )
