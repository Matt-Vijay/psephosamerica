"""Bounded read-only retrieval. A source snapshot is not an effective-law opinion."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlsplit

from lxml import etree

from .parse import media_links, source_medium, xml_root
from .store import Store

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


def eligible(
    as_of: str | None, observed: str | None, *, exact: bool = False
) -> tuple[str, list[Any]]:
    day, clock = cutoff_date(as_of), cutoff_observation(observed)
    clauses, params = [], []
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


class Reader:
    def __init__(self, store: Store):
        self.db = store.db

    def coverage(self, *, detailed: bool = False) -> dict[str, Any]:
        # Count the latest units once, not one scan of the national text table per collection.
        chosen, _ = eligible(None, None)
        unit_counts: dict[str, list[dict[str, Any]]] = {}
        for row in self.db.execute(
            chosen + "SELECT d.collection_id,p.unit_kind,count(*) AS count FROM provisions p "
            "JOIN chosen v ON v.id=p.version_id JOIN documents d ON d.id=v.document_id "
            "GROUP BY d.collection_id,p.unit_kind"
        ):
            unit_counts.setdefault(row["collection_id"], []).append(
                {"unit_kind": row["unit_kind"], "count": row["count"]}
            )
        feature_counts = dict(
            self.db.execute(
                chosen
                + "SELECT d.collection_id,count(*) FROM features f JOIN chosen v ON v.id=f.version_id "
                "JOIN documents d ON d.id=v.document_id GROUP BY d.collection_id"
            )
        )
        collections = []
        for row in self.db.execute("SELECT * FROM collections ORDER BY id"):
            entry = dict(row)
            entry["metadata"] = json.loads(entry["metadata"])
            if not detailed:
                important = {
                    "currency_notice",
                    "release_point",
                    "current_through",
                    "current_through_public_law_date",
                    "codified_date",
                    "api_meta",
                    "approved_changes_through",
                    "publisher_edition_effective_on",
                    "expected_features",
                    "warning",
                    "snapshot_warning",
                    "limitation",
                }
                entry["metadata"] = {
                    k: v
                    for k, v in entry["metadata"].items()
                    if k in important or k.endswith("_artifact")
                }
            entry["inventory"] = [
                dict(r)
                for r in self.db.execute(
                    "SELECT status,count(*) AS items FROM inventories WHERE collection_id=? "
                    "GROUP BY status",
                    (row["id"],),
                )
            ]
            entry["documents"] = self.db.execute(
                "SELECT count(*) FROM documents WHERE collection_id=?",
                (row["id"],),
            ).fetchone()[0]
            entry["versions"] = self.db.execute(
                "SELECT count(*) FROM versions v JOIN documents d ON d.id=v.document_id "
                "WHERE d.collection_id=?",
                (row["id"],),
            ).fetchone()[0]
            entry["units_in_latest_documents"] = unit_counts.get(row["id"], [])
            entry["features_in_latest_documents"] = feature_counts.get(row["id"], 0)
            entry["clocks"] = dict(
                self.db.execute(
                    "SELECT min(v.snapshot_date) AS earliest_snapshot,max(v.snapshot_date) AS "
                    "latest_snapshot,min(a.observed_at) AS first_acquired,max(a.observed_at) AS "
                    "last_acquired FROM versions v JOIN documents d ON d.id=v.document_id "
                    "JOIN acquisitions a ON a.id=v.acquisition_id WHERE d.collection_id=?",
                    (row["id"],),
                ).fetchone()
            )
            entry["failures"] = [
                dict(r)
                for r in self.db.execute(
                    "SELECT item,url,error FROM inventories WHERE collection_id=? AND status='failed' LIMIT 30",
                    (row["id"],),
                )
            ]
            collections.append(entry)
        return {
            "collections": collections,
            "jurisdictions": [
                dict(r) for r in self.db.execute("SELECT * FROM jurisdictions ORDER BY id")
            ],
            "raw_artifacts": dict(
                self.db.execute(
                    "SELECT count(*) AS count,coalesce(sum(bytes),0) AS bytes FROM artifacts"
                ).fetchone()
            ),
            "scope_warning": "Coverage is the listed acquired inventories, not all US law.",
            "temporal_semantics": TEMPORAL_LIMIT,
        }

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
            (*params, key_or_id, key_or_id, key_or_id, key_or_id + "/_occurrence/[0-9]*"),
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
        result["metadata"] = json.loads(result["metadata"])
        result["version_metadata"] = json.loads(result["version_metadata"])
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
        prefix, params = eligible(as_of, observation_cutoff)
        for ref in references:
            target = (
                "usc:" + ref["target"] if ref["target"].startswith("/us/usc/") else ref["target"]
            )
            expected_url = None
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
            ref["acquired_targets"] = [
                dict(r)
                for r in self.db.execute(
                    prefix + "SELECT p.id,p.key,p.citation,v.snapshot_date FROM provisions p "
                    "JOIN chosen v ON v.id=p.version_id WHERE p.key=? "
                    "AND (? IS NULL OR p.url=?) LIMIT 3",
                    (*params, target, expected_url, expected_url),
                )
            ]
            ref["resolution_basis"] = (
                "exact_acquired_publisher_url" if expected_url else "exact_acquired_source_key"
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
            row["metadata"] = json.loads(row["metadata"])
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
        result["headers"] = json.loads(result["headers"])
        return {"found": True, **result}
