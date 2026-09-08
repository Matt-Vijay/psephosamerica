"""Offline VAC source replay and MCP verification; optional network-forbidden resume."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import io
import json
import sys
import time
from collections import Counter
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import urlsplit

from complete_virginia import media, prefaces, run, validate_inventory
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from psephos.campaign import save
from psephos.store import Provision, Store, digest, json_text, utc_now
from psephos.virginia_rules import (
    COLLECTION,
    PARSER,
    chapter_id,
    chapter_index,
    chapter_units,
    preface_units,
)


def verify_units(s: Store, version: str, units: list[Provision]) -> None:
    rows = s.db.execute(
        "SELECT * FROM provisions WHERE version_id=? ORDER BY ordinal", (version,)
    ).fetchall()
    assert len(rows) == len(units), version
    for n, (row, unit) in enumerate(zip(rows, units, strict=True)):
        assert row["ordinal"] == n
        for field in (
            "key",
            "citation",
            "heading",
            "text",
            "markup",
            "url",
            "parent_key",
            "unit_kind",
        ):
            assert row[field] == getattr(unit, field), (version, unit.key, field)
        assert json.loads(row["metadata"]) == unit.metadata, (version, unit.key, "metadata")
        refs: dict[tuple[str, str, str], str] = {}
        for r in unit.references:
            refs.setdefault((r.target, r.relation, r.label), r.evidence)
        saved = {
            tuple(r)
            for r in s.db.execute(
                "SELECT target,relation,label,evidence FROM legal_references WHERE provision_id=?",
                (row["id"],),
            )
        }
        assert saved == {(*key, evidence) for key, evidence in refs.items()}, (
            version,
            unit.key,
            "references",
        )


def audit_campaign(s: Store, directory: Path) -> dict[str, Any]:
    start = json.loads((directory / "campaign-start.json").read_bytes())
    budget = json.loads((directory / "budget.json").read_bytes())
    rows = list(
        s.db.execute(
            "SELECT * FROM acquisitions WHERE id>=? AND (? IS NULL OR id<=?) ORDER BY id",
            (
                start["first_acquisition_id"],
                start.get("last_acquisition_id"),
                start.get("last_acquisition_id"),
            ),
        )
    )
    assert all(
        urlsplit(r["url"]).netloc
        in {"law.lis.virginia.gov", "codecommission.dls.virginia.gov", "ris.dls.virginia.gov"}
        for r in rows
    ), "Another source writer crossed this campaign's receipt boundary"
    payload = {
        r["id"]: int(json.loads(r["headers"]).get("psephos_downloaded_bytes", 0)) for r in rows
    }
    measured = sum(payload.values())
    assert not budget.get("reserved_bytes"), "Acquisition still active or interrupted"
    assert (
        measured + start["initial_consumed_bytes"] + budget.get("uncertain_reserved_bytes", 0)
        == budget["consumed_bytes"]
    )
    assert budget["consumed_bytes"] <= budget["cap_bytes"]
    artifacts = {}
    for r in rows:
        if r["sha256"]:
            raw = s.artifact(r["sha256"])
            assert r["status"] == 200 and r["error"] is None
            assert len(raw) == payload[r["id"]]
            artifacts[r["sha256"]] = len(raw)
    preflight = start.get("initial_preflight_consumed_bytes")
    if preflight is not None:
        assert (
            sum(
                n for i, n in payload.items() if i <= start["initial_preflight_last_acquisition_id"]
            )
            == preflight
        )
        assert preflight <= 16 * 1024**2
    return {
        "first_acquisition_id": rows[0]["id"],
        "last_acquisition_id": rows[-1]["id"],
        "observation_range": [rows[0]["observed_at"], rows[-1]["observed_at"]],
        "http_status_counts": dict(Counter(str(r["status"]) for r in rows)),
        "receipted_payload_bytes": measured,
        "unique_new_objects_rehashed": len(artifacts),
        "unique_new_object_bytes": sum(artifacts.values()),
        "initial_preflight_bytes": preflight,
        "budget": budget,
        "semantics": "Application-read payload charges, including failed transfers; not HTTP framing or unread redirect/error bodies. Uncertain interruption allowances are separate from successfully retained bytes.",
    }


def verify_cached_resume(data: Path) -> dict[str, Any]:
    """Exercise the existing writer with HTTP forbidden, before the full replay."""

    def state() -> dict[str, int]:
        s = Store(data, readonly=True)
        try:
            return {
                "acquisitions": s.db.execute("SELECT count(*) FROM acquisitions").fetchone()[0],
                "versions": s.db.execute(
                    "SELECT count(*) FROM versions WHERE document_id IN (SELECT id FROM documents WHERE collection_id=?)",
                    (COLLECTION,),
                ).fetchone()[0],
                "provisions": s.db.execute(
                    "SELECT count(*) FROM provisions WHERE version_id IN (SELECT v.id FROM versions v JOIN documents d ON d.id=v.document_id WHERE d.collection_id=?)",
                    (COLLECTION,),
                ).fetchone()[0],
                "consumed_bytes": json.loads(
                    (data / "virginia-completion/budget.json").read_bytes()
                )["consumed_bytes"],
            }
        finally:
            s.close()

    before = state()
    with (
        patch("httpx.Client.send", side_effect=AssertionError("Unexpected network on resume")),
        contextlib.redirect_stdout(io.StringIO()),
    ):
        result = run(data)
        assert result["new_chapters"] == 0 and not result["errors"]
        prefaces(data)
        media(data)
    after = state()
    assert before == after, (before, after)
    return {"status": "PASS", "network": "forbidden", "before": before, "after": after}


def verify(data: Path) -> dict[str, Any]:
    s = Store(data, readonly=True)
    directory = data / "virginia-completion"
    plan = json.loads((directory / "inventory.json").read_bytes())
    counts: Counter[str] = Counter()
    artifacts: dict[str, int] = {}
    receipts = []
    quirks = []
    dates: set[str] = set()
    section_citations: set[str] = set()
    image_urls: Counter[str] = Counter()

    def account_media(units: list[Provision]) -> None:
        for unit in units:
            for medium in unit.metadata.get("media", []):
                image_urls[medium["url"]] += 1

    try:
        validate_inventory(s, plan)
        inventory_times = {r["id"]: r["observed_at"] for r in plan["title_receipts"]}
        for receipt in plan["title_receipts"]:
            artifacts[receipt["sha256"]] = len(s.artifact(receipt["sha256"]))
        expected = {chapter_id(item) for item in plan["chapters"]}
        actual = {
            r["item"]
            for r in s.db.execute(
                "SELECT item FROM inventories WHERE collection_id=?", (COLLECTION,)
            )
        }
        assert expected == actual, "Chapter denominator differs"
        for item in plan["chapters"]:
            cid = chapter_id(item)
            v = s.db.execute(
                "SELECT v.*,a.url,a.final_url,a.observed_at,a.status,a.error FROM versions v JOIN acquisitions a ON a.id=v.acquisition_id WHERE v.document_id=? AND v.parser=? ORDER BY v.rowid DESC LIMIT 1",
                ("va-vac:" + cid, PARSER + "/text-3"),
            ).fetchone()
            assert v is not None, "Missing chapter: " + cid
            raw = s.artifact(v["artifact_sha"])
            meta = json.loads(v["metadata"])
            ir = s.db.execute(
                "SELECT * FROM acquisitions WHERE id=?", (meta["section_inventory_acquisition_id"],)
            ).fetchone()
            assert ir["url"] == item["url"] and ir["error"] is None
            index_raw = s.artifact(ir["sha256"])
            info = chapter_index(index_raw, item)
            assert v["status"] == 200 and v["error"] is None
            assert v["final_url"].rstrip("/") == info["full_url"].rstrip("/")
            units, vm = chapter_units(raw, item, info)
            vm.update(
                title_inventory_acquisition_id=item["inventory_acquisition_id"],
                section_inventory_acquisition_id=ir["id"],
                native_chapter_url=item["url"],
                native_read_chapter_url=info["full_url"],
            )
            assert vm == meta, (cid, "version metadata")
            verify_units(s, v["id"], units)
            account_media(units)
            section_citations.update(
                u.citation for u in units if u.unit_kind in {"section", "source_reference_list"}
            )
            assert all(
                v[k] is None for k in ("effective_on", "published_on", "amended_on", "repealed_on")
            )
            assert v["snapshot_date"] == v["observed_at"][:10]
            assert v["available_at"] == max(
                v["observed_at"],
                ir["observed_at"],
                inventory_times[item["inventory_acquisition_id"]],
            )
            dates.add(v["snapshot_date"])
            counts.update(
                chapters=1,
                chapter_units=len(units),
                native_section_units=meta["section_count"],
                repeated_native_index_entries=sum(
                    meta.get("repeated_native_index_entries", {}).values()
                ),
                tables=meta["tables"],
                images=meta["images"],
                chapter_html_bytes=len(raw),
                index_html_bytes=len(index_raw),
            )
            counts.update(unit.unit_kind + "_units" for unit in units)
            for sha, body in ((v["artifact_sha"], raw), (ir["sha256"], index_raw)):
                artifacts[sha] = len(body)
            source_quirks = {
                k: meta[k]
                for k in (
                    "unclosed_layout_wrappers",
                    "empty_source_section_headings",
                    "repeated_native_index_entries",
                    "empty_source_hierarchy_blocks",
                    "malformed_anchor_whitespace_repairs",
                    "preserved_cross_section_wrappers",
                    "scoped_native_occurrences",
                )
                if meta.get(k)
            }
            if source_quirks:
                quirks.append({"chapter": cid, **source_quirks})
            receipts.append(
                {
                    "chapter": cid,
                    "version_id": v["id"],
                    "source_acquisition_id": v["acquisition_id"],
                    "source_sha256": v["artifact_sha"],
                    "section_index_acquisition_id": ir["id"],
                    "section_index_sha256": ir["sha256"],
                    "sections": meta["section_count"],
                    "units": len(units),
                    "projection_sha256": digest(json_text([asdict(u) for u in units]).encode()),
                }
            )
        prefaces = json.loads((directory / "prefaces.json").read_bytes())
        agency_items = {(i["title"], i["agency"]): i for i in plan["agencies"]}
        assert len(prefaces) == len(agency_items)
        assert {(p["title"], p["agency"]) for p in prefaces} == set(agency_items)
        for p in prefaces:
            source = p["source"]
            r = s.db.execute("SELECT * FROM acquisitions WHERE id=?", (source["id"],)).fetchone()
            assert (
                r["sha256"] == source["sha256"] and r["url"] == source["url"] and r["error"] is None
            )
            raw = s.artifact(r["sha256"])
            units = preface_units(raw, agency_items[p["title"], p["agency"]], r["url"])
            if units:
                v = s.db.execute("SELECT * FROM versions WHERE id=?", (p["version_id"],)).fetchone()
                assert v["artifact_sha"] == r["sha256"] and v["acquisition_id"] == r["id"]
                assert (
                    v["document_id"] == units[0].key and v["snapshot_date"] == r["observed_at"][:10]
                )
                assert v["available_at"] == max(
                    r["observed_at"],
                    inventory_times[
                        agency_items[p["title"], p["agency"]]["inventory_acquisition_id"]
                    ],
                )
                assert all(
                    v[k] is None
                    for k in ("effective_on", "published_on", "amended_on", "repealed_on")
                )
                verify_units(s, p["version_id"], units)
                account_media(units)
                counts["agency_summaries_with_body"] += 1
            else:
                assert p["version_id"] is None and p["status"] == "no_publisher_body"
                counts["agency_summaries_without_body"] += 1
            artifacts[r["sha256"]] = len(raw)
            counts["preface_json_bytes"] += len(raw)
        media = json.loads((directory / "media.json").read_bytes())
        expected_documents = {"va-vac:" + cid for cid in expected} | {
            f"va-vac:{p['title']}VAC{p['agency']}/preface"
            for p in prefaces
            if p["status"] == "retained_body"
        }
        assert expected_documents == {
            r[0]
            for r in s.db.execute("SELECT id FROM documents WHERE collection_id=?", (COLLECTION,))
        }, "Catalog documents differ from accounted chapters/summaries"
        counts["documents"] = len(expected_documents)
        counts["distinct_native_section_citations"] = len(section_citations)
        assert len(media) == len({m["url"] for m in media})
        assert {m["url"] for m in media} == set(image_urls) - {""}, (
            "Image URL accounting incomplete"
        )
        for medium in media:
            source = medium.get("source")
            if source:
                r = s.db.execute(
                    "SELECT * FROM acquisitions WHERE id=?", (source["id"],)
                ).fetchone()
                assert r["url"] == medium["url"] == source["url"]
                assert r["sha256"] == source["sha256"] and r["status"] == 200 and not r["error"]
                raw = s.artifact(r["sha256"])
                assert len(raw) == source["size"]
                artifacts[r["sha256"]] = len(raw)
                counts["image_response_bytes"] += len(raw)
            elif medium["status"] == "unavailable":
                r = s.db.execute(
                    "SELECT * FROM acquisitions WHERE id=?", (medium["acquisition_id"],)
                ).fetchone()
                assert (
                    r["url"] == medium["url"]
                    and r["status"] == medium["http_status"]
                    and r["error"]
                )
            else:
                assert medium["status"] == "not_acquired_outside_reviewed_image_routes"
        status_counts = {
            r["status"]: r["n"]
            for r in s.db.execute(
                "SELECT status,count(*) n FROM inventories WHERE collection_id=? GROUP BY status",
                (COLLECTION,),
            )
        }
        assert set(status_counts) <= {"ingested", "ingested_with_source_media"}, status_counts
        manifest = {
            "chapters": receipts,
            "artifact_count": len(artifacts),
            "unique_artifact_bytes": sum(artifacts.values()),
        }
        save(directory / "verified-chapters.json", manifest)
        return {
            "status": "PASS",
            "recorded_at": utc_now(),
            "counts": dict(counts),
            "titles": len(plan["title_receipts"]),
            "agencies": len(plan["agencies"]),
            "publisher_labeled_repealed_chapters": sum(
                "[Repealed]" in c["label"] for c in plan["chapters"]
            ),
            "snapshot_dates": sorted(dates),
            "status_counts": status_counts,
            "all_chapters_exact_replay": "PASS",
            "independent_raw_native_link_and_table_image_counts": "PASS",
            "source_quirks": quirks,
            "media": {
                "source_image_occurrences": sum(image_urls.values()),
                "without_fetchable_descriptor": image_urls[""],
                "unique_external_urls": len(media),
                "status_counts": dict(Counter(m["status"] for m in media)),
                "unavailable_or_unreviewed": [
                    m for m in media if m["status"] != "retained_image_response"
                ],
                "semantics": "Separately observed image responses, not a certification of diagrams or snapshot-bound historical assets. Embedded image bytes remain in original HTML/markup.",
            },
            "acquisition": audit_campaign(s, directory),
            "local_manifest": {
                "path_relative_to_data": "virginia-completion/verified-chapters.json",
                "sha256": digest((directory / "verified-chapters.json").read_bytes()),
            },
            "limits": [
                "Native publisher permanent-code chapters; emergency rules and some lagging exempt actions are outside this source.",
                "Observed snapshots, not a guarantee of complete effective regulations on those dates.",
                "Forms/IBR links are not retained incorporated documents; images are not certified transcriptions.",
                "Rule/section counts include repealed, removed, and reference-list entries.",
            ],
        }
    finally:
        s.close()


async def exercise_mcp(data: Path) -> list[dict[str, Any]]:
    parameters = StdioServerParameters(
        command=sys.executable, args=["-m", "psephos.cli", "--data", str(data.resolve()), "serve"]
    )
    cases: list[tuple[str, dict[str, Any]]] = [
        ("legal_coverage", {"collection": COLLECTION}),
        ("legal_search", {"collection": COLLECTION, "query": "stormwater", "limit": 2}),
        ("legal_read", {"key_or_id": "va-vac:9VAC25-875-10", "length": 2000}),
        ("legal_read", {"key_or_id": "va-vac:9VAC25-875-20", "length": 2000}),
        ("legal_read", {"key_or_id": "va-vac:9VAC25-875-1375", "length": 1000}),
        ("legal_read", {"key_or_id": "va-vac:2VAC5-332-70", "length": 2000}),
        ("legal_read", {"key_or_id": "va-vac:1VAC17-10-10", "length": 1000}),
        ("legal_read", {"key_or_id": "va-vac:18VAC115-100-60/_occurrence/1", "length": 1000}),
        ("legal_read", {"key_or_id": "va-vac:18VAC115-100-60/_occurrence/2", "length": 1000}),
    ]
    media = {
        row["url"]: row
        for row in json.loads((data / "virginia-completion/media.json").read_bytes())
    }
    results = []
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for name, arguments in cases:
                response = await session.call_tool(name, arguments)
                assert not response.isError
                value = response.structuredContent
                assert value is not None
                if name == "legal_read":
                    assert value["found"] and value["effective_on"] is None
                    assert len(json.dumps(value).encode()) < 24000
                    if value["media_count"]:
                        assert value["text_completeness"] == "incomplete_without_source_media"
                        for medium in value["media"]:
                            source = media[medium["url"]].get("source")
                            assert medium["acquired_receipt"] == (
                                {"id": source["id"], "sha256": source["sha256"]} if source else None
                            )
                    receipt = await session.call_tool(
                        "source_receipt", {"acquisition_id": value["acquisition_id"]}
                    )
                    assert (
                        receipt.structuredContent is not None
                        and receipt.structuredContent["sha256"] == value["artifact_sha"]
                    )
                    cutoff = await session.call_tool(
                        "legal_read",
                        {
                            "key_or_id": arguments["key_or_id"],
                            "observation_cutoff": "2026-01-01T00:00:00Z",
                        },
                    )
                    assert (
                        cutoff.structuredContent is not None
                        and not cutoff.structuredContent["found"]
                    )
                results.append(
                    {
                        "tool": name,
                        "arguments": arguments,
                        "response_sha256": digest(json_text(value).encode()),
                        "json_bytes": len(json.dumps(value).encode()),
                    }
                )
            arguments = {"key_or_id": "va-vac:18VAC115-100-60", "length": 1000}
            response = await session.call_tool("legal_read", arguments)
            value = response.structuredContent
            assert not response.isError and value is not None and not value["found"]
            assert {m["key"] for m in value["matches"]} == {
                "va-vac:18VAC115-100-60/_occurrence/1",
                "va-vac:18VAC115-100-60/_occurrence/2",
            }
            results.append(
                {
                    "tool": "legal_read",
                    "arguments": arguments,
                    "expectation": "ambiguous native citation; both publisher occurrences offered",
                    "response_sha256": digest(json_text(value).encode()),
                    "json_bytes": len(json.dumps(value).encode()),
                }
            )
    return results


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, default=Path("data"))
    p.add_argument("--out", type=Path, default=Path("docs/virginia-edition-verification.json"))
    p.add_argument(
        "--check-resume",
        action="store_true",
        help="First exercise cached chapter/preface/media writers with HTTP forbidden; updates local progress only",
    )
    args = p.parse_args()
    started = time.monotonic()
    with (args.data / "virginia-completion/writer.lock").open() as lock:
        mode = fcntl.LOCK_EX if args.check_resume else fcntl.LOCK_SH
        fcntl.flock(lock, mode | fcntl.LOCK_NB)
        resume = verify_cached_resume(args.data) if args.check_resume else None
        report = verify(args.data)
        if resume is not None:
            report["cached_resume"] = resume
        report["mcp_cases"] = asyncio.run(exercise_mcp(args.data))
        report["verification_seconds"] = round(time.monotonic() - started, 3)
        report["runtime"] = {
            "python": sys.version.split()[0],
            **{name: version(name) for name in ("lxml", "httpx", "mcp")},
        }
        # Freeze this completed campaign before later independent source imports.
        boundary_path = args.data / "virginia-completion/campaign-start.json"
        boundary = json.loads(boundary_path.read_bytes())
        if "last_acquisition_id" not in boundary:
            boundary["last_acquisition_id"] = report["acquisition"]["last_acquisition_id"]
            save(boundary_path, boundary)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        save(args.out, report)
    print(json.dumps({"status": report["status"], "counts": report["counts"]}, indent=2))
