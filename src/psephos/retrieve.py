"""Bounded read-only retrieval. A source snapshot is not an effective-law opinion."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from lxml import etree

from .acquire import SAFE_HEADERS
from .parse import media_links, source_medium, xml_root
from .store import Store

RECEIPT_HEADERS = SAFE_HEADERS | {"psephos_request_started_at", "psephos_observed_at_basis"}

TEMPORAL_LIMIT = (
    "As-of selects the latest acquired publisher snapshot dated on/before the requested date. "
    "It does not reconstruct intervening legal changes or decide legal effectiveness. "
    "Observation cutoff is independent; unknown effective dates remain unknown."
)


def cutoff_date(value: str | None) -> str | None:
    return date.fromisoformat(value).isoformat() if value else None


def cutoff_observation(value: str | None) -> str | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("observation_cutoff requires a timezone, e.g. 2026-09-06T23:00:00Z")
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def literal_pattern(query: str) -> re.Pattern[str]:
    if not query.strip() or len(query) > 500:
        raise ValueError("Use a nonempty literal phrase of at most 500 characters")
    return re.compile(r"\s+".join(re.escape(word) for word in query.split()), re.IGNORECASE)


def _display_metadata(raw: str, key: str) -> dict[str, Any]:
    metadata: dict[str, Any] = json.loads(raw)
    if key.startswith("wa-wsr:table:") and isinstance(metadata.get("rows"), list):
        metadata["rows_count"] = len(metadata.pop("rows"))
        metadata["rows_projection"] = (
            "Bulk rows omitted from metadata. Read this table's paginated text/markup, "
            "legal_find and legal_references for row/action evidence; stored metadata unchanged."
        )
    return metadata


def eligible(
    as_of: str | None, observed: str | None, *, exact: bool = False, target_key: str | None = None
) -> tuple[str, list[Any]]:
    day, clock = cutoff_date(as_of), cutoff_observation(observed)
    clauses, params = [], []
    if target_key is not None:
        # Resolve a citation by ranking only documents that ever contained its key,
        # not every national version once for each link in a long filing.
        clauses.append(
            "v.document_id IN (SELECT kv.document_id FROM provisions kp "
            "JOIN versions kv ON kv.id=kp.version_id WHERE kp.key=?)"
        )
        params.append(target_key)
    if day:
        clauses.append("v.snapshot_date IS NOT NULL AND v.snapshot_date <= ?")
        params.append(day)
    if clock:
        clauses.append("v.available_at <= ?")
        params.append(clock)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    sql = (
        "WITH ranked AS (SELECT v.*,v.available_at AS observed_at,a.url AS artifact_url,"
        "row_number() OVER (PARTITION BY v.document_id ORDER BY coalesce(v.snapshot_date,'') "
        "DESC,v.available_at DESC,v.rowid DESC) AS rank FROM versions v "
        "JOIN acquisitions a ON a.id=v.acquisition_id" + where + "),"
        "chosen AS (SELECT * FROM ranked" + ("" if exact else " WHERE rank=1") + ") "
    )
    return sql, params


def _washington_reference(target: str) -> tuple[str, str, str] | None:
    """Recognize retained native WA section links, not arbitrary URL equivalents.

    RCW source markup uses HTTP links while acquired section records use HTTPS.
    Only the publisher's exact endpoint and single cite/Cite parameter support
    that mapping. WAC full-chapter links additionally retain same-chapter native
    section fragments. Retained WSR table links have a cross-checkable filing
    year/issue/identifier path. Other parameters, escapes, fragments and
    title/chapter links are not silently rewritten into section citations.
    """
    filing = re.fullmatch(
        r"https?://lawfilesext\.leg\.wa\.gov/law/wsr/([0-9]{4})/([0-9]{2})/"
        r"([0-9]{2})-([0-9]{2})-([0-9]{3})\.htm",
        target,
    )
    if filing is not None:
        year, issue, short_year, filing_issue, number = filing.groups()
        if year[-2:] != short_year or issue != filing_issue:
            return None
        citation = f"{short_year}-{filing_issue}-{number}"
        return (
            "wa-wsr:" + citation,
            f"https://lawfilesext.leg.wa.gov/law/wsr/{year}/{issue}/{citation}.htm",
            "wa-wsr",
        )
    fragment = re.fullmatch(
        r"https?://app\.leg\.wa\.gov/(?:WAC|wac)/default\.aspx\?[Cc]ite="
        r"([0-9]+[A-Z]?-[0-9]+[A-Z]?)&full=true#([0-9]+[A-Z]?(?:-[0-9]+[A-Z]?){2})",
        target,
    )
    if fragment is not None:
        chapter, citation = fragment.groups()
        if citation.rsplit("-", 1)[0] != chapter:
            return None
        return (
            "wa-wac:" + citation,
            "https://app.leg.wa.gov/WAC/default.aspx?cite=" + citation,
            "wa-wac",
        )
    match = re.fullmatch(
        r"https?://app\.leg\.wa\.gov/(RCW|WAC|wac)/default\.aspx\?[Cc]ite=([^&?#]+)", target
    )
    if match is None:
        return None
    family, citation = match.groups()
    if family == "wac":
        family = "WAC"
    pattern = (
        r"[0-9]+[A-Z]?(?:\.[0-9]+[A-Z]?){2}"
        if family == "RCW"
        else r"[0-9]+[A-Z]?(?:-[0-9]+[A-Z]?){2}"
    )
    if re.fullmatch(pattern, citation) is None:
        return None
    collection = "wa-" + family.lower()
    return (
        collection + ":" + citation,
        f"https://app.leg.wa.gov/{family}/default.aspx?cite={citation}",
        collection,
    )


class Reader:
    def __init__(self, store: Store):
        self.db = store.db

    def coverage(
        self,
        *,
        view: Literal["jurisdictions", "collections", "documents", "inventory"] | None = None,
        jurisdiction: str | None = None,
        collection: str | None = None,
        status: str | None = None,
        offset: int = 0,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Small source directory; never aggregate the national provisions/features tables."""
        if not 1 <= limit <= 20 or not 0 <= offset <= 100000:
            raise ValueError("Use limit 1..20 and offset 0..100000")
        for name, value in (
            ("jurisdiction", jurisdiction),
            ("collection", collection),
            ("status", status),
        ):
            if value is not None and (not value.strip() or len(value) > 200):
                raise ValueError(f"{name} must be a nonempty exact identifier, or omitted")
        view = (
            view
            if view is not None
            else (
                "collections"
                if jurisdiction is not None or collection is not None
                else "jurisdictions"
            )
        )
        if view not in {"jurisdictions", "collections", "documents", "inventory"}:
            raise ValueError("Unknown source-directory view")
        if view in {"documents", "inventory"} and collection is None:
            raise ValueError(f"{view} requires an exact collection")
        if view == "jurisdictions" and collection is not None:
            raise ValueError("Use collections view with a collection filter")
        if status is not None and view != "inventory":
            raise ValueError("status filters inventory only")
        result: dict[str, Any] = {
            "view": view,
            "status": "ok",
            view: [],
            "offset": offset,
            "limit": limit,
            "next_offset": None,
            "total": 0,
            "filters": {"jurisdiction": jurisdiction, "collection": collection, "status": status},
            "scope_warning": "Exact catalog scope only, not applicable law or completeness. Empty/unknown coverage is not absence of legal restrictions. State/federal/local sources are separate; no automatic jurisdiction inheritance.",
            "navigation": "Choose a jurisdiction or collection; use documents for first_key → legal_read, inventory for retained source items/gaps. Follow next_offset.",
            "temporal_semantics": TEMPORAL_LIMIT,
        }
        jurisdiction_row = (
            self.db.execute("SELECT * FROM jurisdictions WHERE id=?", (jurisdiction,)).fetchone()
            if jurisdiction is not None
            else None
        )
        if jurisdiction is not None and jurisdiction_row is None:
            result["status"] = "unknown_jurisdiction"
            return result
        collection_row = (
            self.db.execute("SELECT * FROM collections WHERE id=?", (collection,)).fetchone()
            if collection is not None
            else None
        )
        if collection is not None and collection_row is None:
            result["status"] = "unknown_collection"
            return result
        if (
            collection_row is not None
            and jurisdiction is not None
            and collection_row["jurisdiction_id"] != jurisdiction
        ):
            result["status"] = "scope_mismatch"
            return result

        if view == "jurisdictions":
            where, params = (" WHERE j.id=?", [jurisdiction]) if jurisdiction else ("", [])
            total = self.db.execute(
                "SELECT count(*) FROM jurisdictions j" + where, params
            ).fetchone()[0]
            rows = self.db.execute(
                "SELECT j.*, (SELECT count(*) FROM collections c WHERE c.jurisdiction_id=j.id) "
                "AS registered_collections FROM jurisdictions j"
                + where
                + " ORDER BY j.id LIMIT ? OFFSET ?",
                (*params, limit, offset),
            )
            entries = [dict(row) for row in rows]
        elif view == "collections":
            predicates, params = [], []
            if collection is not None:
                predicates.append("id=?")
                params.append(collection)
            if jurisdiction is not None:
                predicates.append("jurisdiction_id=?")
                params.append(jurisdiction)
            where = " WHERE " + " AND ".join(predicates) if predicates else ""
            total = self.db.execute("SELECT count(*) FROM collections" + where, params).fetchone()[
                0
            ]
            rows = self.db.execute(
                "SELECT * FROM collections" + where + " ORDER BY id LIMIT ? OFFSET ?",
                (*params, limit, offset),
            )
            entries = [self._source_card(dict(row)) for row in rows]
        else:
            assert collection_row is not None and collection is not None
            result["source"] = self._source_card(dict(collection_row))
            if view == "inventory":
                result["inventory_semantics"] = (
                    "Source-native mixed item kinds/statuses, not legal units or a completeness percentage. Documents are a separate view; no inferred item→document join."
                )
                where = "collection_id=?" + (" AND status=?" if status is not None else "")
                params = [collection, status] if status is not None else [collection]
                total = self.db.execute(
                    "SELECT count(*) FROM inventories WHERE " + where, params
                ).fetchone()[0]
                entries = [
                    dict(row)
                    for row in self.db.execute(
                        "SELECT item,url,status,error,checked_at FROM inventories WHERE "
                        + where
                        + " ORDER BY item LIMIT ? OFFSET ?",
                        (*params, limit, offset),
                    )
                ]
                if status is not None and not total:
                    result["status"] = "no_inventory_items_with_status"
            else:
                total = self.db.execute(
                    "SELECT count(*) FROM documents WHERE collection_id=?", (collection,)
                ).fetchone()[0]
                rows = self.db.execute(
                    "SELECT d.*, v.id AS version_id,v.artifact_sha,v.acquisition_id,v.member,"
                    "v.snapshot_date,v.snapshot_basis,v.published_on,v.effective_on,v.amended_on,"
                    "v.repealed_on,v.available_at,v.parser FROM "
                    "(SELECT * FROM documents WHERE collection_id=? ORDER BY id LIMIT ? OFFSET ?) d "
                    "LEFT JOIN versions v ON v.id=(SELECT id FROM versions WHERE document_id=d.id "
                    "ORDER BY coalesce(snapshot_date,'') DESC,available_at DESC,rowid DESC LIMIT 1) ORDER BY d.id",
                    (collection, limit, offset),
                )
                entries = []
                for row in rows:
                    entry = dict(row)
                    first = self.db.execute(
                        "SELECT id,key,unit_kind FROM provisions WHERE version_id=? ORDER BY ordinal LIMIT 1",
                        (entry["version_id"],),
                    ).fetchone()
                    entry.update(
                        first_key=first["key"] if first else None,
                        first_provision_id=first["id"] if first else None,
                        first_unit_kind=first["unit_kind"] if first else None,
                    )
                    entries.append(entry)
                result["document_semantics"] = (
                    "Latest acquired parser projection per document, not complete legal history. first_key may be a heading/context/page, not a whole law. Use legal_search within this collection for a specific provision."
                )
        result["total"] = total
        if not entries and result["status"] == "ok":
            result["status"] = "empty" if not total else "page_exhausted"
        for entry in entries:
            result[view].append(entry)
            if len(json.dumps(result, ensure_ascii=False).encode()) > 24576:
                result[view].pop()
                if not result[view]:
                    result["status"] = "oversized_source_metadata"
                    result["skipped_entry_offset"] = offset
                    result["reason"] = (
                        "This catalog entry exceeds the bounded directory response and is skipped; no completeness claim is available. Inspect its publisher source/retained metadata separately. Follow next_offset for later entries."
                    )
                break
        returned = len(result[view])
        advanced = returned or int("skipped_entry_offset" in result)
        result["next_offset"] = (
            offset + advanced if advanced and offset + advanced < total else None
        )
        if len(json.dumps(result, ensure_ascii=False).encode()) > 24576:
            result.pop("source", None)
            result[view] = []
            result.update(
                status="oversized_source_metadata",
                next_offset=None,
                reason="Catalog metadata exceeds this directory's byte budget; no scope conclusion is available.",
            )
        return result

    def _source_card(self, row: dict[str, Any]) -> dict[str, Any]:
        metadata = json.loads(row["metadata"])
        # Large machine inventories are drill-down evidence, not scope/currency notices.
        bulk = {"layer_metadata", "portal", "title_inventory"}
        row["metadata"] = {key: value for key, value in metadata.items() if key not in bulk}
        row["omitted_machine_metadata_keys"] = sorted(bulk.intersection(metadata))
        row["documents"] = self.db.execute(
            "SELECT count(*) FROM documents WHERE collection_id=?", (row["id"],)
        ).fetchone()[0]
        row["inventory_status_counts"] = [
            dict(r)
            for r in self.db.execute(
                "SELECT status,count(*) AS items FROM inventories WHERE collection_id=? GROUP BY status ORDER BY status",
                (row["id"],),
            )
        ]
        row["scope_status"] = (
            "as_described_in_source_metadata"
            if "discovery_review" in metadata
            or any("scope" in key or "coverage" in key for key in metadata)
            else "not_explicitly_described"
        )
        row["completeness"] = (
            "Not inferred from document, inventory or unit counts; inspect scope/omission notices and the paginated inventory."
        )
        if len(json.dumps(row, ensure_ascii=False).encode()) > 8192:
            row["metadata"] = {}
            row["scope_status"] = "metadata_exceeds_directory_budget"
            row["metadata_warning"] = (
                "Scope/currency metadata is too large for this directory card; no scope conclusion is available. Inspect the retained publisher evidence separately."
            )
        return row

    def search(
        self,
        query: str,
        *,
        collection: str | None = None,
        jurisdiction: str | None = None,
        as_of: str | None = None,
        observation_cutoff: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 30 or len(query) > 500:
            raise ValueError("limit must be 1..30 and query at most 500 characters")
        terms = re.findall(r"[\w]+", query, re.UNICODE)
        if not terms or len(terms) > 30:
            raise ValueError(
                "Use 1..30 words or citation components; FTS operators are not required"
            )
        literal_query = " AND ".join('"' + term + '"' for term in terms)
        sql, params = eligible(as_of, observation_cutoff)
        where = ["provision_search MATCH ?"]
        params.append(literal_query)
        if collection:
            where.append("c.id=?")
            params.append(collection)
        if jurisdiction:
            where.append("c.jurisdiction_id=?")
            params.append(jurisdiction)
        rows = self.db.execute(
            sql + "SELECT p.id,p.key,p.citation,p.heading,p.unit_kind,p.url,c.id AS collection,"
            "c.jurisdiction_id,c.source_status,v.snapshot_date,v.snapshot_basis,v.observed_at,"
            "v.published_on,v.effective_on,v.amended_on,v.artifact_sha,p.text AS _text,"
            "snippet(provision_search,2,'[',']',' … ',48) AS excerpt "
            "FROM provision_search JOIN provisions p ON p.rowid=provision_search.rowid "
            "JOIN chosen v ON v.id=p.version_id JOIN documents d ON d.id=v.document_id "
            "JOIN collections c ON c.id=d.collection_id WHERE "
            + " AND ".join(where)
            + " ORDER BY CASE WHEN p.unit_kind='pdf_page' AND length(trim(p.text))<300 THEN 4 "
            "WHEN instr(lower(p.heading),?)>0 THEN 0 "
            "WHEN instr(lower(p.text),?)>0 THEN 1 WHEN instr(lower(p.text),?)>0 THEN 2 ELSE 3 END, "
            "bm25(provision_search,10,5,1),p.id LIMIT ?",
            (
                *params,
                query.strip().lower(),
                "\n" + query.strip().lower() + "\n",
                query.strip().lower(),
                limit,
            ),
        )
        matches = []
        phrase = literal_pattern(query)
        standalone = re.compile("^" + phrase.pattern + "$", re.IGNORECASE | re.MULTILINE)
        for row in rows:
            item = dict(row)
            text = item.pop("_text")
            line = standalone.search(text)
            match = line or phrase.search(text)
            item["match_kind"] = (
                "standalone_phrase" if line else "phrase" if match else "dispersed_terms"
            )
            item["match_offset"] = match.start() if match else None
            item["read_offset"] = max(0, match.start() - 150) if match else None
            matches.append(item)
        return {
            "matches": matches,
            "query_mode": "lexical_all_terms_phrase_ranked",
            "ranking_warning": "Textual relevance, not legal precedence or universal applicability. Read scoped modifications too.",
            "as_of": as_of,
            "observation_cutoff": observation_cutoff,
            "temporal_semantics": TEMPORAL_LIMIT,
        }

    def _locate(
        self,
        key_or_id: str,
        *,
        as_of: str | None = None,
        observation_cutoff: str | None = None,
    ) -> dict[str, Any]:
        citation = re.fullmatch(
            r"(\d+)\s*U\.?\s*S\.?\s*C\.?\s*(?:§|section|sec\.?)?\s*([\w–-]+)\.?", key_or_id, re.I
        )
        if citation:
            key_or_id = f"usc:/us/usc/t{citation[1]}/s{citation[2]}"
        florida = re.fullmatch(
            r"Fla\.\s*Stat\.\s*(?:§\s*)?(\d+[A-Z]?\.\d+[A-Z]?)(?:\s*\((20\d{2})\))?",
            key_or_id,
            re.I,
        )
        if florida:
            # A printed citation is an exact identifier, not authority to invent a URL.
            key_or_id = (
                f"Fla. Stat. § {florida[1]} ({florida[2]})"
                if florida[2]
                else "fl:stat/" + florida[1]
            )
        exact = (
            self.db.execute("SELECT 1 FROM provisions WHERE id=?", (key_or_id,)).fetchone()
            is not None
        )
        sql, params = eligible(as_of, observation_cutoff, exact=exact)
        # Exact immutable IDs remain readable even after a newer version is acquired, but never
        # bypass an explicit date/observation cutoff. Unversioned keys select the latest snapshot.
        rows = self.db.execute(
            sql + "SELECT p.*,d.title AS document_title,c.name AS collection_name,"
            "c.jurisdiction_id,c.authority,c.source_status,v.snapshot_date,v.snapshot_basis,"
            "v.published_on,v.effective_on,v.amended_on,v.repealed_on,v.observed_at,"
            "v.artifact_sha,v.acquisition_id,v.artifact_url,v.member,v.parser,v.metadata AS version_metadata "
            "FROM provisions p JOIN chosen v ON v.id=p.version_id "
            "JOIN documents d ON d.id=v.document_id JOIN collections c ON c.id=d.collection_id "
            # Separate indexed lookups: SQLite can turn the mixed OR/GLOB form into a
            # full text-table scan once the catalog grows or gains planner statistics.
            "WHERE p.id IN (SELECT id FROM provisions WHERE key=? "
            "UNION SELECT id FROM provisions WHERE id=? "
            "UNION SELECT id FROM provisions WHERE citation=? COLLATE NOCASE "
            "UNION SELECT id FROM provisions WHERE key GLOB ?) LIMIT 3",
            (
                *params,
                key_or_id,
                key_or_id,
                key_or_id,
                key_or_id
                + (
                    "/occurrence/[0-9]*"
                    if key_or_id.startswith("fl:stat/")
                    else "/_occurrence/[0-9]*"
                ),
            ),
        ).fetchall()
        if not rows:
            return {
                "found": False,
                "reason": "No acquired matching provision at these cutoffs",
                "temporal_semantics": TEMPORAL_LIMIT,
            }
        if len(rows) > 1:
            return {
                "found": False,
                "reason": "Ambiguous citation; use a source key",
                "matches": [{"key": r["key"], "citation": r["citation"]} for r in rows],
            }
        result = dict(rows[0])
        result.pop("rowid")
        result["metadata"] = _display_metadata(result["metadata"], result["key"])
        result["version_metadata"] = _display_metadata(result["version_metadata"], result["key"])
        return {"found": True, **result}

    def find(
        self,
        key_or_id: str,
        query: str,
        *,
        as_of: str | None = None,
        observation_cutoff: str | None = None,
        start: int = 0,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Find exact source text without paging through a long provision or inventing chunks."""
        if start < 0 or not 1 <= limit <= 20:
            raise ValueError("start must be nonnegative; limit must be 1..20")
        pattern = literal_pattern(query)
        source = self._locate(key_or_id, as_of=as_of, observation_cutoff=observation_cutoff)
        if not source["found"]:
            return source
        text = source["text"]
        matches: list[dict[str, Any]] = []
        next_start = None
        for match in pattern.finditer(text, start):
            if len(matches) == limit:
                next_start = match.start()
                break
            offset = max(0, match.start() - 150)
            matches.append(
                {
                    "match_offset": match.start(),
                    "match_end": match.end(),
                    "read_offset": offset,
                    "excerpt": text[offset : min(match.end() + 500, offset + 1000)],
                }
            )
        return {
            "found": True,
            **{
                k: source[k]
                for k in (
                    "id",
                    "key",
                    "version_id",
                    "citation",
                    "url",
                    "artifact_sha",
                    "snapshot_date",
                    "observed_at",
                )
            },
            "matches": matches,
            "next_start": next_start,
            "text_characters": len(text),
            "offset_semantics": "Zero-based Unicode character offsets into this exact version's legal_read text, not its markup.",
            "match_mode": "Case-insensitive literal text; whitespace flexible, no user regex.",
            "warning": "Matches/excerpts are navigation, not complete definitions or applicability findings. Read surrounding qualifications.",
            "temporal_semantics": TEMPORAL_LIMIT,
        }

    def read(
        self,
        key_or_id: str,
        *,
        as_of: str | None = None,
        observation_cutoff: str | None = None,
        offset: int = 0,
        length: int = 10000,
        include_markup: bool = False,
        media_offset: int = 0,
    ) -> dict[str, Any]:
        if offset < 0 or media_offset < 0 or not 1 <= length <= 24000:
            raise ValueError("offsets must be nonnegative; length must be 1..24000")
        result = self._locate(key_or_id, as_of=as_of, observation_cutoff=observation_cutoff)
        if not result["found"]:
            return result
        # Old parsers duplicated raw image attributes (including base64) in metadata.
        # One paginated descriptor list is sufficient; immutable source markup is untouched.
        media = list(result["metadata"].pop("media", []))
        legacy_images = result["metadata"].pop("image_links", [])
        result["text_completeness"] = result["metadata"].get(
            "text_quality", "publisher_text_projection"
        )
        if result["markup"]:
            try:
                for medium in media_links(xml_root(result["markup"].encode()), result["url"]):
                    if medium not in media:
                        media.append(medium)
            except (ValueError, etree.XMLSyntaxError):
                result["text_completeness"] = "markup_inspection_warning"
        for locator in legacy_images:
            medium = source_medium(locator, result["url"])
            if not any(m.get("source_locator") == medium["source_locator"] for m in media):
                media.append(medium)
        if media:
            result["text_completeness"] = "incomplete_without_source_media"
        result["media"] = media[media_offset : media_offset + 20]
        result["media_count"] = len(media)
        result["media_next_offset"] = media_offset + 20 if media_offset + 20 < len(media) else None
        result["media_offset"] = media_offset
        result["media_semantics"] = (
            "Distinct source descriptors, 20 per page. Embedded image bytes and oversized attributes stay in paginated markup/original artifacts; metadata image lists are consolidated here."
        )
        media_cutoff = cutoff_observation(observation_cutoff)
        for medium in result["media"]:
            receipt = self.db.execute(
                "SELECT sha256,id FROM acquisitions WHERE url=? AND status=200 "
                "AND (? IS NULL OR observed_at<=?) ORDER BY id DESC LIMIT 1",
                (medium.get("url", ""), media_cutoff, media_cutoff),
            ).fetchone()
            medium["acquired_receipt"] = dict(receipt) if receipt else None
        full_text = result["text"]
        result["text"] = full_text[offset : offset + length]
        result["text_characters"] = len(full_text)
        result["next_offset"] = offset + length if offset + length < len(full_text) else None
        result["offset"] = offset
        # Markup is separately paginated: it can be much larger than the rendered text.
        if include_markup:
            markup = result["markup"]
            result["markup"] = markup[offset : offset + length]
            result["markup_characters"] = len(markup)
            result["markup_next_offset"] = (
                offset + length if offset + length < len(markup) else None
            )
        else:
            result.pop("markup")
        result["neighbors"] = [
            dict(r)
            for r in self.db.execute(
                "SELECT id,key,citation,heading FROM provisions WHERE version_id=? "
                "AND ordinal BETWEEN ? AND ? AND id<>? ORDER BY ordinal",
                (result["version_id"], result["ordinal"] - 1, result["ordinal"] + 1, result["id"]),
            )
        ]
        references = self.references(
            result["id"], limit=5, as_of=as_of, observation_cutoff=observation_cutoff
        )
        result["references"] = references["references"]
        result["references_next_offset"] = references["next_offset"]
        result["found"] = True
        result["temporal_semantics"] = TEMPORAL_LIMIT
        return result

    def references(
        self,
        provision_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
        as_of: str | None = None,
        observation_cutoff: str | None = None,
    ) -> dict[str, Any]:
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("offset must be nonnegative; limit must be 1..100")
        source_sql, source_params = eligible(as_of, observation_cutoff, exact=True)
        rows = self.db.execute(
            source_sql + "SELECT target,relation,label,evidence FROM legal_references r "
            "JOIN provisions p ON p.id=r.provision_id JOIN chosen v ON v.id=p.version_id "
            "WHERE provision_id=? "
            "ORDER BY target,relation,label LIMIT ? OFFSET ?",
            (*source_params, provision_id, limit + 1, offset),
        ).fetchall()
        references = [dict(r) for r in rows[:limit]]
        for ref in references:
            target = (
                "usc:" + ref["target"] if ref["target"].startswith("/us/usc/") else ref["target"]
            )
            expected_url = None
            expected_collection = None
            # Other publishers' malformed URLs are still evidence, not a reason to fail a read.
            link = urlsplit(
                ref["target"]
                if ref["target"].startswith("https://zoningresolution.planning.nyc.gov/")
                else ""
            )
            if (
                link.scheme == "https"
                and link.netloc == "zoningresolution.planning.nyc.gov"
                and not link.query
            ):
                if (
                    link.fragment
                    and re.fullmatch(r"\d{2,3}-\d{2,3}", link.fragment)
                    and re.fullmatch(r"/article-[ivx]+/chapter-\d+", link.path)
                ):
                    target = "nyc-zr:" + link.fragment
                    expected_url = f"https://{link.netloc}{link.path}/{link.fragment}"
                elif not link.fragment and re.fullmatch(
                    r"/article-[ivx]+/chapter-\d+/\d{2,3}-\d{2,3}", link.path
                ):
                    target = "nyc-zr:" + link.path.rsplit("/", 1)[1]
                    expected_url = ref["target"]
                elif not link.fragment and link.path.startswith("/appendix-"):
                    target = "nyc-zr:" + link.path
                    expected_url = ref["target"]
            washington: tuple[str, str | None, str] | None = _washington_reference(ref["target"])
            if ref["target"].startswith("wa-wsr:"):
                if (
                    ref["relation"] != "publisher_filing_citation"
                    or re.fullmatch(r"wa-wsr:[0-9]{2}-[0-9]{2}-[0-9]{3}", ref["target"]) is None
                ):
                    ref.update(
                        acquired_targets=[],
                        resolution_status="invalid_publisher_filing_citation",
                        resolution_basis="unresolved_publisher_filing_citation",
                    )
                    continue
                # A source-note filing number is not authority to construct a URL.
                # Candidate URLs below must come from an exact retained acquisition.
                washington = (ref["target"], None, "wa-wsr")
            if washington is not None:
                target, expected_url, expected_collection = washington
            prefix, params = eligible(as_of, observation_cutoff, target_key=target)
            targets = [
                dict(r)
                for r in self.db.execute(
                    prefix + "SELECT p.id,p.key,p.citation,p.url,v.snapshot_date FROM provisions p "
                    "JOIN chosen v ON v.id=p.version_id "
                    "JOIN documents d ON d.id=v.document_id "
                    "JOIN acquisitions a ON a.id=v.acquisition_id WHERE p.key=? "
                    "AND (? IS NULL OR p.url=?) AND (? IS NULL OR d.collection_id=?) "
                    "AND (?=0 OR (p.url=d.url AND (p.url=a.url OR p.url=a.final_url) "
                    "AND a.sha256=v.artifact_sha AND a.status BETWEEN 200 AND 299 "
                    "AND a.error IS NULL)) LIMIT 3",
                    (
                        *params,
                        target,
                        expected_url,
                        expected_url,
                        expected_collection,
                        expected_collection,
                        expected_collection == "wa-wsr",
                    ),
                )
            ]
            filing_candidates_saturated = expected_collection == "wa-wsr" and len(targets) == 3
            if expected_collection == "wa-wsr":
                targets = [
                    row
                    for row in targets
                    if _washington_reference(row["url"]) == (row["key"], row["url"], "wa-wsr")
                ]
            if washington is not None:
                ambiguous = len(targets) > 1 or filing_candidates_saturated
                ref["publisher_citation_family"] = expected_collection
                ref["resolution_status"] = (
                    "ambiguous_acquired_target"
                    if ambiguous
                    else "resolved"
                    if targets
                    else "not_acquired_at_cutoffs"
                )
                if ambiguous:
                    targets = []
            ref["acquired_targets"] = targets
            ref["resolution_basis"] = (
                "exact_acquired_filing_key_and_retained_publisher_url"
                if expected_collection == "wa-wsr"
                else "exact_acquired_publisher_url"
                if expected_url
                else "exact_acquired_source_key"
            )
        return {
            "references": references,
            "next_offset": offset + limit if len(rows) > limit else None,
            "warning": "Source links/citations are not inferred precedence or proof of enactment.",
        }

    def versions(self, key: str, *, limit: int = 30) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be 1..100")
        rows = self.db.execute(
            "SELECT p.id,p.key,p.citation,v.id AS version_id,v.snapshot_date,v.snapshot_basis,"
            "v.published_on,v.effective_on,v.amended_on,v.repealed_on,v.available_at AS observed_at,"
            "v.artifact_sha,a.url AS artifact_url,v.member,v.metadata FROM provisions p "
            "JOIN versions v ON v.id=p.version_id JOIN acquisitions a ON a.id=v.acquisition_id "
            "WHERE p.key=? ORDER BY v.snapshot_date DESC,v.available_at DESC,v.rowid DESC LIMIT ?",
            (key, limit),
        ).fetchall()
        result = [dict(r) for r in rows]
        for row in result:
            row["metadata"] = _display_metadata(row["metadata"], row["key"])
        return {
            "versions": result,
            "temporal_semantics": TEMPORAL_LIMIT,
            "coverage_warning": "Only acquired snapshots; not a complete legislative history.",
        }

    def receipt(self, acquisition_id: int) -> dict[str, Any]:
        """Expand a source receipt on demand; never read a caller-supplied file or URL."""
        row = self.db.execute(
            "SELECT a.*,b.bytes FROM acquisitions a LEFT JOIN artifacts b ON b.sha256=a.sha256 "
            "WHERE a.id=?",
            (acquisition_id,),
        ).fetchone()
        if row is None:
            return {"found": False}
        result = dict(row)
        # Imported receipts may retain headers excluded by our own HTTP acquisition.
        # Filter at the common Reader/MCP/CLI boundary without rewriting that evidence.
        headers = json.loads(result["headers"])
        result["headers"] = {
            name.lower(): value
            for name, value in headers.items()
            if name.lower() in RECEIPT_HEADERS
        }
        result["omitted_header_names"] = sorted(
            {name.lower() for name in headers if name.lower() not in RECEIPT_HEADERS}
        )
        result["header_projection"] = (
            "Case-insensitive acquisition SAFE_HEADERS plus the two internal observation-clock "
            "fields; names normalized to lowercase. Other values omitted; stored receipt unchanged."
        )
        return {"found": True, **result}
