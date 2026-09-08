"""Native VAC inventories and whole-chapter HTML; no invented consolidation."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from copy import deepcopy
from typing import Any
from urllib.parse import urljoin, urlsplit

from lxml import etree, html

from .parse import markup, media_links, readable, source_links
from .store import Provision

BASE = "https://law.lis.virginia.gov"
COLLECTION = "va-administrative-code"
PARSER = "virginia-vac-chapter-html/1"
LIMITS = (
    "Publisher permanent-code inventory only. Emergency rules are excluded by the publisher; "
    "some exempt actions lag effectiveness by two business days. No historical reconstruction "
    "or legal-effect inference. External forms/IBR are references, not retained incorporated text. "
    "Commonwealth copyright notices remain; no public redistribution license is asserted."
)


def expanded_inventory(
    data: bytes, title: str, url: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    root = html.fromstring(data)
    agencies, chapters = [], []
    for link in root.xpath("//dt/a[@href]"):
        match = re.fullmatch(
            r"/admincode(?:expand)?/title(\d+)/agency(\d+)/(?:chapter(\d+)/)?", link.get("href", "")
        )
        if match is None:
            if "/chapter" in link.get("href", ""):
                raise ValueError("Unsupported native chapter link; denominator not closed")
            continue
        if match[1] != title:
            raise ValueError("Title inventory includes another title")
        sibling = link.getparent().getnext()
        if sibling is None or sibling.tag != "dd" or not readable(sibling).strip():
            raise ValueError("Native inventory entry lacks its publisher label")
        item = {
            "title": title,
            "agency": match[2],
            "label": sibling.text_content().strip(),
            "url": urljoin(url, link.get("href")),
        }
        if match[3]:
            chapters.append({**item, "chapter": match[3]})
        else:
            agencies.append(item)
    if not agencies or len({c["url"] for c in chapters}) != len(chapters):
        raise ValueError("Empty agency roster or duplicate chapter link")
    return agencies, chapters


def chapter_id(item: dict[str, Any]) -> str:
    return f"{item['title']}VAC{item['agency']}-{item['chapter']}"


def empty_heading_block(node: etree._Element) -> bool:
    """An unnamed heading with actual following text is still a source block."""
    for following in node.itersiblings():
        if following.tag == "p" and following.get("class") in {"vacno", "part", "article"}:
            break
        if readable(following).strip():
            return False
    return True


def preface_units(data: bytes, item: dict[str, Any], url: str) -> list[Provision]:
    record = json.loads(data)
    if (str(record.get("TitleNumber")), str(record.get("AgencyNumber"))) != (
        item["title"],
        item["agency"],
    ):
        raise ValueError("Agency preface API identity disagrees with native inventory")
    if "PrefaceSummary" not in record:
        raise ValueError("Agency preface API lacks the documented body field")
    body = record.get("PrefaceSummary")
    if not body:
        return []  # Retain the API absence as inventory evidence, never placeholder legal text.
    node = html.fragment_fromstring(body, create_parent="div")
    if not readable(node).strip():
        return []
    key = f"va-vac:{item['title']}VAC{item['agency']}/preface"
    return [
        Provision(
            key,
            f"{item['title']}VAC{item['agency']} Agency Summary",
            record.get("AgencyName") or item["label"],
            readable(node),
            markup(node),
            url,
            unit_kind="agency_summary",
            metadata={
                "text_quality": "publisher_html",
                "source_fields": {k: v for k, v in record.items() if k != "PrefaceSummary"},
                "scope": "Publisher agency summary, not a regulation or applicability determination.",
                "tables": len(node.xpath(".//table")),
                "media": media_links(node, url),
            },
            references=source_links(node, url),
        )
    ]


def chapter_index(data: bytes, item: dict[str, Any]) -> dict[str, Any]:
    root = html.fromstring(data)
    scope = f"/title{item['title']}/agency{item['agency']}/chapter{item['chapter']}"
    full_links = {
        urljoin(BASE, a.get("href"))
        for a in root.xpath("//a[@href]")
        if urlsplit(urljoin(BASE, a.get("href"))).netloc == urlsplit(BASE).netloc
        and urlsplit(urljoin(BASE, a.get("href"))).path.rstrip("/") == "/admincodefull" + scope
    }
    if len(full_links) != 1:
        raise ValueError("Native chapter index lacks one exact Read Chapter link")
    sections: list[dict[str, Any]] = []
    native_urls = []
    repeated_entries: dict[str, int] = {}
    entry_ids: dict[str, list[str]] = {}
    scoped_ids: dict[tuple[str, str], list[str]] = {}
    for link in root.xpath("//dt/a[@href]"):
        resolved = urlsplit(urljoin(BASE, link.get("href")))
        path = resolved.path
        match = re.fullmatch(re.escape("/admincode" + scope) + r"/section([^/]+)/", path)
        if match is None:
            continue
        if resolved.scheme != "https" or resolved.netloc != urlsplit(BASE).netloc:
            raise ValueError("Section identity points outside native publisher")
        dd = link.getparent().getnext()
        if dd is None or dd.tag != "dd":
            raise ValueError("Section index label missing")
        entry = {
            "number": match[1],
            "label": readable(dd),
            "link_label": readable(link),
            "url": urljoin(BASE, link.get("href")),
            "native_index_classes": " ".join(link.getparent().xpath("./input/@class")),
        }
        native_urls.append(entry["url"])
        ids = dd.xpath('.//*[@id and starts-with(@id,"sectionID")]/@id')
        entry_ids.setdefault(entry["number"], []).extend(ids)
        scoped_ids.setdefault((entry["number"], entry["native_index_classes"]), []).extend(ids)
        if sections and entry == sections[-1]:
            # Adjacent identical publisher entries share a URL, not necessarily one
            # legal version. Keep their original IDs and all body occurrences; warn.
            repeated_entries[entry["number"]] = repeated_entries.get(entry["number"], 0) + 1
        else:
            sections.append(entry)
    scoped_occurrences = {}
    for number in dict.fromkeys(s["number"] for s in sections):
        group = [s for s in sections if s["number"] == number]
        if len(group) < 2:
            continue
        scopes = [re.findall(r"\bpart([A-Z0-9]+)\b", s["native_index_classes"]) for s in group]
        if (
            number in repeated_entries
            or any(len(scope) != 1 for scope in scopes)
            or len({scope[0] for scope in scopes}) != len(group)
            or len({(s["url"], s["label"], s["link_label"]) for s in group}) != 1
        ):
            raise ValueError("Duplicate section inventory identity")
        for ordinal, (section, part_scope) in enumerate(zip(group, scopes, strict=True), 1):
            section.update(
                occurrence=ordinal,
                native_part=part_scope[0],
                publisher_entry_ids=scoped_ids[number, section["native_index_classes"]],
            )
        scoped_occurrences[number] = [
            {k: s[k] for k in ("occurrence", "native_part", "publisher_entry_ids")} for s in group
        ]
    raw_links = re.findall(
        rb"""\bhref\s*=\s*(["'])("""
        + re.escape(("/admincode" + scope + "/section").encode())
        + rb"""[^/"']+/)\1""",
        data,
    )
    if [BASE + path.decode("ascii") for _, path in raw_links] != native_urls:
        raise ValueError("Parsed section roster differs from literal native source links")
    return {
        "full_url": next(iter(full_links)),
        "sections": sections,
        **({"scoped_occurrences": scoped_occurrences} if scoped_occurrences else {}),
        **(
            {
                "repeated_index_entries": repeated_entries,
                "repeated_index_source_ids": {n: entry_ids[n] for n in repeated_entries},
            }
            if repeated_entries
            else {}
        ),
    }


def section_nodes(node: etree._Element, wrappers: set[etree._Element]) -> Iterator[etree._Element]:
    for child in node:
        if child in wrappers:
            yield from section_nodes(child, wrappers)
        else:
            yield child


def source_fragment(nodes: list[etree._Element], article: etree._Element) -> etree._Element:
    """Split at native headings without discarding attributed ancestor context."""
    root = etree.Element("div")
    copies = {article: root}
    for node in nodes:
        parents = []
        node_parent = node.getparent()
        assert node_parent is not None
        parent: etree._Element | None = node_parent
        while parent is not None and parent not in copies:
            parents.append(parent)
            parent = parent.getparent()
        assert parent is not None
        for ancestor in reversed(parents):
            clone = etree.SubElement(copies[parent], ancestor.tag, dict(ancestor.attrib))
            clone.text = ancestor.text
            copies[ancestor] = clone
            parent = ancestor
        copies[node_parent].append(deepcopy(node))
    return root


def chapter_units(
    data: bytes, item: dict[str, Any], index: dict[str, Any]
) -> tuple[list[Provision], dict[str, Any]]:
    root = html.fromstring(data)
    # The publisher sometimes leaves layout divs open inside a section. libxml's
    # whole-page repair then swallows the site footer into the chapter. The literal
    # source article boundary is the authority, not that repaired page tree.
    fragments = re.findall(
        rb"""<article\s+id=["']admincode["'][^>]*>.*?</article\s*>""", data, re.S | re.I
    )
    if len(fragments) != 1:
        raise ValueError("No unique literal whole-chapter source article")
    fragment, anchor_repairs = re.subn(rb"<ahref=(?=[\"'])", b"<a href=", fragments[0], flags=re.I)
    article = html.fromstring(
        fragment, parser=html.HTMLParser(encoding=root.getroottree().docinfo.encoding)
    )
    if anchor_repairs:
        # Measured source typo: <ahref="literal URL">... </a>. Insert only the
        # missing separator, retaining the exact URL/text and original source bytes.
        original = html.fromstring(
            fragments[0], parser=html.HTMLParser(encoding=root.getroottree().docinfo.encoding)
        )
        if original.text_content() != article.text_content():
            raise ValueError("Malformed anchor repair changed source text")
    before_layout = readable(article)
    wrappers = article.xpath('.//div[.//p[@class="vacno" or @class="part" or @class="article"]]')
    preserved: set[etree._Element] = set()
    for wrapper in wrappers:
        if wrapper.attrib and dict(wrapper.attrib) != {"style": "padding: 0px;"}:
            attrs = dict(wrapper.attrib)
            measured = (
                set(attrs) == {"data-show-promulgator"}
                and attrs["data-show-promulgator"] in {"0", "1"}
            ) or (
                set(attrs) == {"data-qa", "data-qa-meta", "data-ref-promulgator"}
                and attrs["data-qa"] == "referenceStandard"
                and re.fullmatch(r"[0-9a-fA-F-]{36}", attrs["data-qa-meta"])
                and re.fullmatch(r"[A-Za-z0-9_]+", attrs["data-ref-promulgator"])
            )
            if not measured or (wrapper.text or "").strip() or (wrapper.tail or "").strip():
                raise ValueError("Attributed wrapper crosses source section boundaries")
            preserved.add(wrapper)  # Preserve these source-reference attributes in each fragment.
        else:
            wrapper.drop_tag()
    if " ".join(before_layout.split()) != " ".join(readable(article).split()):
        raise ValueError("Layout repair changed source text")
    if article.xpath(".//script|.//iframe"):
        raise ValueError("Unexpected active content within chapter body")
    if article.xpath(".//svg|.//object|.//embed|.//audio|.//video|.//canvas"):
        raise ValueError("Additional embedded media requires an explicit projection review")
    headings = article.xpath("./h2")
    if len(headings) != 1 or not readable(headings[0]).startswith(f"Chapter {item['chapter']}."):
        raise ValueError("Whole-chapter heading disagrees with inventory")
    chapter = chapter_id(item)
    key = "va-vac:" + chapter
    units: list[Provision] = []
    current: list[etree._Element] = []
    current_key, citation, heading, kind, target_url = (
        key,
        chapter,
        readable(headings[0]),
        "chapter_context",
        index["full_url"],
    )
    hierarchy: list[str] = []
    current_hierarchy: list[str] = []
    identity_metadata: dict[str, Any] = {}
    matched_sections = []
    empty_section_headings = 0
    empty_hierarchy_headings = 0

    def flush() -> None:
        if not current:
            return
        node = source_fragment(current, article)
        text = readable(node)
        if not text.strip():
            raise ValueError("Empty source block; no legal-text stub is created")
        repeated = index.get("repeated_index_entries", {}).get(
            current_key.removeprefix(key + "-"), 0
        )
        units.append(
            Provision(
                key=current_key,
                citation=citation,
                heading=heading,
                text=text,
                markup=markup(node),
                url=target_url,
                parent_key=key if current_key != key else None,
                unit_kind=kind,
                metadata={
                    **identity_metadata,
                    **(
                        {
                            "native_index_occurrences": repeated + 1,
                            "identity_note": "Publisher repeats this URL/identifier. All source body occurrences are retained together, not deduplicated or resolved into a chosen legal version.",
                        }
                        if repeated
                        else {}
                    ),
                    "hierarchy": current_hierarchy.copy(),
                    "text_quality": "publisher_html",
                    "tables": len(node.xpath(".//table")),
                    "media": media_links(node, index["full_url"]),
                },
                references=source_links(node, index["full_url"]),
            )
        )

    for child in section_nodes(article, preserved):
        cls = child.get("class", "")
        if (
            child.tag == "p"
            and cls in {"vacno", "part", "article"}
            and not readable(child)
            and (cls == "vacno" or empty_heading_block(child))
        ):
            # Empty publisher headings stay in markup, without inventing a new unit.
            if cls == "vacno":
                empty_section_headings += 1
            else:
                empty_hierarchy_headings += 1
            current.append(child)
            continue
        if (
            cls == "vacno"
            and kind in {"section", "source_reference_list"}
            and readable(child) == heading
        ):
            # Some exports repeat the same source section heading (including repeals).
            # Preserve both headings in the one native identity, not a fabricated section.
            current.append(child)
            continue
        if child.tag == "p" and cls in {"vacno", "part", "article"}:
            flush()
            current = []
            identity_metadata = {}
            heading = readable(child)
            if cls == "vacno":
                matches = [
                    s
                    for s in index["sections"]
                    if heading.startswith(chapter + "-" + s["number"] + ". ")
                    or heading.casefold() == s["label"].casefold()
                    or (
                        s["link_label"].upper() == "FORMS"
                        and heading.casefold() == f"Forms ({chapter})".casefold()
                    )
                    or (
                        s["link_label"].upper() == "DIBR"
                        and heading.casefold()
                        == f"Documents Incorporated by Reference ({chapter})".casefold()
                    )
                ]
                if len(matches) > 1:
                    parts = [re.match(r"Part\s+([A-Z0-9]+)\b", h) for h in hierarchy]
                    part = next((m[1] for m in parts if m is not None), None)
                    matches = [s for s in matches if s.get("native_part") == part]
                if len(matches) != 1:
                    raise ValueError(f"No unique native section identity for {heading!r}")
                section = matches[0]
                matched_sections.append(section["number"])
                citation = chapter + "-" + section["number"]
                current_key, target_url = "va-vac:" + citation, section["url"]
                if "occurrence" in section:
                    current_key += "/_occurrence/" + str(section["occurrence"])
                    identity_metadata = {
                        "native_key": "va-vac:" + citation,
                        "source_occurrence": section["occurrence"],
                        "publisher_entry_ids": section["publisher_entry_ids"],
                        "identity_note": "Publisher repeats this citation in different parts. Occurrences remain separate; native index part and source heading agree. No legal consolidation or applicability choice is inferred.",
                    }
                kind = (
                    "source_reference_list" if section["number"] in {"9998", "9999"} else "section"
                )
            else:
                if cls == "part":
                    hierarchy = [heading]
                else:
                    hierarchy = [h for h in hierarchy if h.startswith("Part ")] + [heading]
                current_key = key + "/context/" + str(len(units))
                citation, kind, target_url = (
                    chapter + " " + heading.split("\n")[0],
                    "hierarchy_context",
                    index["full_url"],
                )
            current_hierarchy = hierarchy.copy()
        current.append(child)
    flush()
    if matched_sections != [s["number"] for s in index["sections"]]:
        raise ValueError("Whole chapter and native section roster/order differ")
    if readable(article) != "\n".join(u.text for u in units):
        raise ValueError("Whole-chapter text/order was not preserved")
    if len({u.key for u in units}) != len(units):
        raise ValueError("Repeated source block identity")
    source_tables = len(article.xpath(".//table"))
    source_images = len(article.xpath(".//img"))
    if source_tables != len(re.findall(rb"<table\b", fragment, re.I)) or source_images != len(
        re.findall(rb"<img\b", fragment, re.I)
    ):
        raise ValueError("Raw table/image tag count differs; source parsing requires review")
    if (
        sum(u.metadata["tables"] for u in units) != source_tables
        or sum(len(u.metadata["media"]) for u in units) != source_images
    ):
        raise ValueError("Table/image projection count differs from source")
    return units, {
        **(
            {"scoped_native_occurrences": index["scoped_occurrences"]}
            if index.get("scoped_occurrences")
            else {}
        ),
        **(
            {
                "preserved_cross_section_wrappers": [
                    dict(w.attrib) for w in wrappers if w in preserved
                ],
                "wrapper_projection": "Source-reference wrapper ancestry is repeated in each independent section fragment; no attributes or body text are discarded. Original HTML controls.",
            }
            if preserved
            else {}
        ),
        **(
            {"empty_source_hierarchy_blocks": empty_hierarchy_headings}
            if empty_hierarchy_headings
            else {}
        ),
        **({"malformed_anchor_whitespace_repairs": anchor_repairs} if anchor_repairs else {}),
        **(
            {"empty_source_section_headings": empty_section_headings}
            if empty_section_headings
            else {}
        ),
        **(
            {
                "repeated_native_index_entries": index["repeated_index_entries"],
                "repeated_native_index_source_ids": index["repeated_index_source_ids"],
            }
            if index.get("repeated_index_entries")
            else {}
        ),
        **(
            {
                "unclosed_layout_wrappers": len(wrappers) - len(preserved),
                "layout_projection": "Literal source article boundary; anonymous or exactly style='padding: 0px;' cross-section div wrappers unwrapped without changing ordered text. Original bytes retained.",
            }
            if len(wrappers) > len(preserved)
            else {}
        ),
        "section_count": len(matched_sections),
        "tables": source_tables,
        "images": source_images,
        "source_title": root.findtext("head/title"),
        "chapter_heading": readable(headings[0]),
        "publisher_page_date": root.xpath('string(//div[@id="printDate"])').strip(),
        "clock_note": "Page print date is presentation, not legal effect; snapshot is the observed acquisition date. History/authority stay in section text without inferred consolidated dates.",
        "source_notices": [readable(n) for n in root.xpath('//p[starts-with(@id,"sidenote")]')],
        "limits": LIMITS,
    }
