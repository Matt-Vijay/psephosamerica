"""D.C. Council's pinned codified XML archive, including native citation/history edges."""

from __future__ import annotations

import posixpath
import re
import sys
import zipfile
from collections.abc import Iterator
from copy import deepcopy
from typing import Any
from urllib.parse import quote

from lxml import etree

from .acquire import Acquirer
from .parse import child_text, local_name, markup, readable, source_links, xml_root
from .sources import checked_zip, progress
from .store import TEXT_PROJECTION, Provision, Reference, Store, digest

REPO = "https://api.github.com/repos/dccouncil/law-xml-codified"
XI = "{http://www.w3.org/2001/XInclude}include"


def expanded(archive: zipfile.ZipFile, member: str, trail: tuple[str, ...] = ()) -> etree._Element:
    if member in trail or len(trail) > 40:
        raise ValueError("Cyclic/excessive source include chain")
    root = xml_root(archive.read(member))
    root.set("psephos-source-member", member)
    for include in list(root.iter(XI)):
        href = include.get("href", "")
        if (
            ":" in href
            or href.startswith("/")
            or include.get("xpointer")
            or include.get("parse", "xml") != "xml"
        ):
            raise ValueError("Unsupported non-local XML include")
        target = posixpath.normpath(posixpath.join(posixpath.dirname(member), href))
        if not target.startswith(member.split("/")[0] + "/"):
            raise ValueError("Include escapes pinned archive")
        child = expanded(archive, target, (*trail, member))
        child.tail = include.tail
        parent = include.getparent()
        assert parent is not None
        parent.replace(include, child)
    return root


def dc_references(node: etree._Element) -> tuple[Reference, ...]:
    refs = list(source_links(node, "https://code.dccouncil.gov"))
    for element in node.iter():
        tag = local_name(element)
        if tag not in {"annotation", "cite"}:
            continue
        doc, path = element.get("doc"), element.get("path", "")
        if doc:
            target = "dc-law:" + doc
        elif path.startswith("§"):
            target = "dc-code:" + path.split("|")[0]
        elif path:
            target = "dc-source-path:" + path
        else:
            continue
        refs.append(
            Reference(
                target,
                "publisher_" + element.get("type", "citation").lower().replace(" ", "_"),
                readable(element) or doc or path,
                markup(element),
            )
        )
    return tuple(refs)


def code_units(root: etree._Element, archive: zipfile.ZipFile) -> Iterator[Provision]:
    for node in root.iter():
        if local_name(node) != "section" or any(
            local_name(a) == "section" for a in node.iterancestors()
        ):
            continue
        number = child_text(node, "num")
        member = node.get("psephos-source-member")
        ancestors: list[dict[str, Any]] = [
            {
                "kind": child_text(a, "prefix"),
                "number": child_text(a, "num"),
                "heading": child_text(a, "heading"),
                "annotations": [dict(c.attrib) for c in a if local_name(c) == "annotation"],
            }
            for a in reversed(list(node.iterancestors()))
            if local_name(a) == "container"
        ]
        yield Provision(
            key="dc-code:§" + number,
            citation="D.C. Code § " + number,
            heading=child_text(node, "heading"),
            text=readable(node),
            markup=markup(node),
            url="https://code.dccouncil.gov/us/dc/council/code/sections/" + number,
            parent_key="dc-code:" + "|".join(a["number"] for a in ancestors),
            metadata={
                "hierarchy": ancestors,
                "source_member": member,
                "source_member_sha256": digest(archive.read(member)) if member else None,
                "annotations": [
                    dict(c.attrib) for c in node.iter() if local_name(c) == "annotation"
                ],
                "date_warning": "Annotation eff/app attributes apply to the named change, not all section text.",
            },
            references=dc_references(node),
        )


def law_unit(root: etree._Element) -> Provision:
    identity = root.get("id", "")
    meta = root.find("{*}meta")
    stub = meta is not None and meta.find("{*}stub") is not None
    projection = deepcopy(root)
    # Publisher OCR search strings are not verified full law text. Retain raw bytes, exclude them
    # from retrieved legal text, and label metadata-only instruments explicitly.
    for node in projection.xpath('.//*[local-name()="search-text"]'):
        parent = node.getparent()
        assert parent is not None
        parent.remove(node)
    number = child_text(root, "num")
    return Provision(
        key="dc-law:" + identity,
        citation=identity,
        heading=child_text(root, "heading"),
        text=readable(projection),
        markup=markup(root),
        url="https://code.dccouncil.gov/us/dc/council/laws/" + number,
        unit_kind="law_metadata" if stub else "amending_instrument",
        metadata={
            "publisher_stub": stub,
            "text_quality": "metadata_only" if stub else "publisher_structured_xml",
            "effective_on": child_text(meta, "effective") if meta is not None else None,
            "full_text_warning": "A stub or OCR search field is not verified full law text.",
        },
        references=dc_references(projection),
    )


def sync_dc(
    s: Store, a: Acquirer, limit: int | None, as_of: str | None
) -> dict[str, dict[str, list[str]]]:
    """Return current archive membership without retiring historical inventory rows."""
    a.fetch("https://code.dccouncil.gov/")  # Publisher public-domain and bulk-preference notice.
    _, repo = a.json(REPO)
    branch = repo["default_branch"]
    commit_url = REPO + "/commits/" + quote(branch, safe="")
    if as_of:
        commit_url = (
            REPO
            + "/commits?sha="
            + quote(branch, safe="")
            + "&until="
            + as_of
            + "T23:59:59Z&per_page=1"
        )
    commit_receipt, commit = a.json(commit_url)
    if isinstance(commit, list):
        if not commit:
            raise ValueError("No publisher commit before requested date")
        commit = commit[0]
    sha = commit["sha"]
    raw = a.fetch(
        f"https://codeload.github.com/dccouncil/law-xml-codified/zip/{sha}", immutable=True
    )
    with checked_zip(s.object_path(raw.sha256)) as archive:
        prefix = archive.namelist()[0].split("/")[0] + "/"
        library = xml_root(archive.read(prefix + "index.xml"))
        index_member = prefix + "us/dc/council/code/index.xml"
        index = xml_root(archive.read(index_member))
        dates = library.xpath('.//*[local-name()="codified-date"]/text()')
        if len(dates) != 1:
            raise ValueError("Publisher codification date missing or ambiguous")
        snapshot = dates[0]
        if as_of and snapshot > as_of:
            raise ValueError("Publisher commit contains a codification after the requested cutoff")
        currency = index.xpath('.//*[local-name()="recency"]/@through')
        info: dict[str, Any] = {
            "commit": sha,
            "publication_branch": branch,
            "codified_date": snapshot,
            "current_through": currency[0] if currency else None,
            "commit_observation_receipt": commit_receipt.sha256,
            "warning": "Codified date and recency-through differ; neither is a section's blanket effective date.",
        }
        for collection, name, kind in (
            ("dc-code", "Code of the District of Columbia", "statutory_code"),
            ("dc-laws", "D.C. Council law instruments in code publication", "session_law"),
        ):
            s.collection(
                collection,
                ("us-dc", "District of Columbia", "district", "us"),
                name=name,
                authority="Council of the District of Columbia",
                kind=kind,
                homepage="https://code.dccouncil.gov",
                source_status="Council-published codified XML snapshot",
                access="Public domain per publisher; pinned bulk archive, no site scraping",
                metadata=info,
            )
        titles = [
            posixpath.normpath(posixpath.join(posixpath.dirname(index_member), href))
            for href in index.xpath(
                "./xi:include/@href", namespaces={"xi": "http://www.w3.org/2001/XInclude"}
            )
        ]
        laws = [
            member
            for member in archive.namelist()
            if re.search(r"/us/dc/council/periods/\d+/laws/\d+-\d+\.xml$", member)
        ]
        inventory_items = {
            collection: [member.removeprefix(prefix) for member in members]
            for collection, members in (("dc-code", titles), ("dc-laws", laws))
        }
        for collection, members in (("dc-code", titles), ("dc-laws", laws)):
            # The archive hash covers every included member. Reuse only projections
            # accepted with the same snapshot and parser, never inventory status alone.
            accepted = {
                row["member"]
                for row in s.db.execute(
                    "SELECT v.member FROM versions v JOIN documents d ON d.id=v.document_id "
                    "WHERE d.collection_id=? AND v.artifact_sha=? AND v.snapshot_date=? "
                    "AND v.parser=?",
                    (collection, raw.sha256, snapshot, "dc-1/" + TEXT_PROJECTION),
                )
            }
            for member in members:
                s.inventory(collection, member.removeprefix(prefix), raw.url, "pending")
            for member in members[:limit]:
                item = member.removeprefix(prefix)
                if member in accepted:
                    s.inventory(collection, item, raw.url, "indexed")
                    continue
                try:
                    root = (
                        expanded(archive, member)
                        if collection == "dc-code"
                        else xml_root(archive.read(member))
                    )
                    number = child_text(root, "num")
                    identity = (
                        "dc-code:title-" + number
                        if collection == "dc-code"
                        else "dc-law:" + root.get("id", "")
                    )
                    meta = root.find("{*}meta")
                    effective = child_text(meta, "effective") if meta is not None else None
                    if effective and (
                        not re.fullmatch(r"\d{4}-\d{2}-\d{2}", effective)
                        or effective.startswith("0001")
                    ):
                        effective = None
                    _, count, new = s.ingest(
                        collection=collection,
                        document=identity,
                        title=child_text(root, "heading"),
                        url="https://code.dccouncil.gov",
                        acquisition=raw.id,
                        member=member,
                        snapshot_date=snapshot,
                        snapshot_basis="Publisher codified-date embedded in pinned archive",
                        parser="dc-1",
                        provisions=code_units(root, archive)
                        if collection == "dc-code"
                        else [law_unit(root)],
                        effective_on=effective,
                        metadata=info,
                    )
                    s.inventory(collection, item, raw.url, "indexed")
                    if collection == "dc-code":
                        progress(collection, number, count, new)
                except (ValueError, KeyError) as exc:
                    s.inventory(collection, item, raw.url, "failed", str(exc))
                    print(f"{collection}: {item}: FAILED: {exc}", file=sys.stderr)
    return {"inventory_items": inventory_items}
