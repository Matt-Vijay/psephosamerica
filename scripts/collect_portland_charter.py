"""Collect/verify an isolated Charter store; never writes the canonical catalog.

python scripts/collect_portland_charter.py preflight --root data/collectors/portland-charter
python scripts/collect_portland_charter.py sync --root data/collectors/portland-charter
python scripts/collect_portland_charter.py verify --root data/collectors/portland-charter
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import platform
import time
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx
from lxml import html

from psephos import acquire, campaign, parse, portland_charter, portland_code
from psephos import store as store_module
from psephos.campaign import save
from psephos.parse import readable
from psephos.portland_charter import (
    BASE,
    CAP,
    COLLECTION,
    INDEX,
    CharterAcquirer,
    chapter_context_signature,
    chapter_list,
    chapter_units,
    members,
    own_content,
    preflight,
    source_key,
    sync_portland_charter,
)
from psephos.portland_code import page, view
from psephos.store import Store, utc_now


def check_resume(root: Path) -> dict[str, Any]:
    """Prove cached completion needs neither new bytes nor derived-record rewrites."""
    store = Store(root / "store")
    acquirer = CharterAcquirer(store, root)
    tables = (
        "artifacts",
        "acquisitions",
        "documents",
        "versions",
        "provisions",
        "legal_references",
    )

    def snapshot() -> dict[str, list[tuple[Any, ...]]]:
        return {
            table: [tuple(row) for row in store.db.execute("SELECT * FROM " + table)]
            for table in tables
        }

    def forbidden(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Completed Charter resume attempted HTTP: " + str(request.url))

    acquirer.client.close()
    acquirer.client = httpx.Client(transport=httpx.MockTransport(forbidden))
    try:
        before, charge = snapshot(), acquirer.downloaded
        sync_portland_charter(store, acquirer, None, None)
        assert snapshot() == before and acquirer.downloaded == charge
        return {
            "passed": True,
            "network_forbidden": True,
            "network_requests": 0,
            "unchanged_rows": {name: len(rows) for name, rows in before.items()},
            "charged_bytes_before_and_after": charge,
        }
    finally:
        acquirer.close()
        store.close()


def verify(root: Path) -> dict[str, Any]:
    started = time.monotonic()
    store = Store(root / "store", readonly=True)
    try:
        assert store.db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert not list(store.db.execute("PRAGMA foreign_key_check"))
        artifacts = []
        for artifact in store.db.execute("SELECT * FROM artifacts ORDER BY sha256"):
            assert len(store.artifact(artifact["sha256"])) == artifact["bytes"]
            artifacts.append(dict(artifact))

        def data(identifier: int) -> bytes:
            receipt = store.db.execute(
                "SELECT * FROM acquisitions WHERE id=?", (identifier,)
            ).fetchone()
            assert receipt and receipt["status"] == 200 and receipt["error"] is None
            assert receipt["url"].startswith(BASE + "/") and receipt["final_url"].startswith(
                BASE + "/"
            )
            return store.artifact(receipt["sha256"])

        collection = dict(
            store.db.execute("SELECT * FROM collections WHERE id=?", (COLLECTION,)).fetchone()
        )
        metadata = json.loads(collection["metadata"])
        chapters = chapter_list(data(metadata["inventory_acquisition_id"]))
        # Independent raw anchors confirm the parser didn't manufacture a chapter.
        root_view = view(page(data(metadata["inventory_acquisition_id"])), "page_1")
        raw_chapters = root_view.xpath('./div[@class="view-content"]//a/@href')
        assert [BASE + href for href in raw_chapters] == [c["url"] for c in chapters]
        inventories = [
            dict(r)
            for r in store.db.execute(
                "SELECT * FROM inventories WHERE collection_id=?", (COLLECTION,)
            )
        ]
        assert {row["url"] for row in inventories} == {c["url"] for c in chapters}
        assert all(row["status"] == "indexed" for row in inventories)
        reports, paragraph_count, table_count, list_count = [], 0, 0, 0
        for chapter in chapters:
            version_rows = store.db.execute(
                "SELECT * FROM versions WHERE document_id=?", (source_key(chapter["url"]),)
            ).fetchall()
            assert len(version_rows) == 1, (
                "Audit scope is this one acquisition batch, not multiple editions"
            )
            version = dict(version_rows[0])
            vm = json.loads(version["metadata"])
            membership = {
                url: members(data(identifier), url)
                for url, identifier in vm["membership_acquisitions"].items()
            }
            signatures = {
                url: own_content(data(identifier))
                for url, identifier in vm["membership_acquisitions"].items()
            }
            assert membership == vm["membership"] and signatures == vm["article_content_signatures"]
            chapter["context_signature"] = chapter_context_signature(
                data(vm["membership_acquisitions"][chapter["url"]])
            )
            body = data(version["acquisition_id"])
            expected = chapter_units(body, chapter, membership, signatures)
            rows = store.db.execute(
                "SELECT * FROM provisions WHERE version_id=? ORDER BY ordinal", (version["id"],)
            ).fetchall()
            core = [row for row in rows if row["unit_kind"] != "changes_context"]
            assert len(core) == len(expected)
            for row, unit in zip(core, expected, strict=True):
                for key in (
                    "key",
                    "citation",
                    "heading",
                    "text",
                    "markup",
                    "url",
                    "parent_key",
                    "unit_kind",
                ):
                    assert row[key] == getattr(unit, key), (unit.key, key)
                assert json.loads(row["metadata"]) == unit.metadata
                refs = {
                    tuple(r)
                    for r in store.db.execute(
                        "SELECT target,relation,label,evidence FROM legal_references WHERE provision_id=?",
                        (row["id"],),
                    )
                }
                assert refs == {
                    (r.target, r.relation, r.label, r.evidence) for r in unit.references
                }
            # Independent raw export containers, not generated retrieval text as its own oracle.
            nodes = view(page(body), "page_chapter_all").xpath('.//div[@class="node--embedded"]')
            assert len(nodes) == len(core) - 1
            for node, row in zip(nodes, core[1:], strict=True):
                raw = deepcopy(node)
                for child in raw.xpath('./div[@class="views-element-container"]'):
                    child.drop_tree()
                restored = html.fromstring(row["markup"])
                assert "".join(raw.text_content().split()) == "".join(
                    restored.text_content().split()
                )
                for selector in (".//table", ".//img", ".//ol", ".//ul", ".//li"):
                    assert [html.tostring(n) for n in raw.xpath(selector)] == [
                        html.tostring(n) for n in restored.xpath(selector)
                    ]
                for paragraph in raw.xpath(".//p"):
                    assert readable(paragraph) in row["text"]
                    paragraph_count += 1
                table_count += len(raw.xpath(".//table"))
                list_count += len(raw.xpath(".//ol|.//ul"))
            context_urls = set()
            for row in rows:
                if row["unit_kind"] == "changes_context":
                    rm = json.loads(row["metadata"])
                    blocks = page(data(rm["source_acquisition_id"])).xpath(
                        './/div[@data-block-plugin-id="views_block:change_sets-effected_on_codecharterpolicy"]'
                    )
                    assert len(blocks) == 1 and readable(blocks[0]) == row["text"]
                    assert source_key(row["url"]) + "/changes-context" == row["key"]
                    context_urls.add(row["url"])
            required_contexts = {
                url
                for url, identifier in vm["membership_acquisitions"].items()
                if page(data(identifier)).xpath(
                    './/div[@data-block-plugin-id="views_block:change_sets-effected_on_codecharterpolicy"]'
                )
            }
            assert context_urls == required_contexts
            media = {
                m["url"]
                for unit in expected
                for m in unit.metadata.get("media", [])
                if m["url"] and not m.get("original_url")
            }
            assert media == {m["url"] for m in vm["media_outcomes"]}
            for asset in vm["media_outcomes"]:
                if "acquisition_id" in asset:
                    assert (
                        hashlib.sha256(data(asset["acquisition_id"])).hexdigest() == asset["sha256"]
                    )
            assert all(
                version[key] is None
                for key in (
                    "snapshot_date",
                    "published_on",
                    "effective_on",
                    "amended_on",
                    "repealed_on",
                )
            )
            dependencies = [
                metadata["inventory_acquisition_id"],
                metadata["terms_acquisition_id"],
                version["acquisition_id"],
                *vm["membership_acquisitions"].values(),
                *(m["acquisition_id"] for m in vm["media_outcomes"] if "acquisition_id" in m),
            ]
            assert version["available_at"] == max(
                store.db.execute(
                    "SELECT observed_at FROM acquisitions WHERE id=?", (identifier,)
                ).fetchone()[0]
                for identifier in dependencies
            )
            reports.append(
                {
                    "document_id": version["document_id"],
                    "version_id": version["id"],
                    "artifact_sha256": version["artifact_sha"],
                    "available_at": version["available_at"],
                    "unit_kinds": dict(Counter(row["unit_kind"] for row in rows)),
                    "units": len(rows),
                    "source_members": sum(len(group) for group in membership.values()),
                    "media": vm["media_outcomes"],
                    "ordered_text_sha256": hashlib.sha256(
                        "\n".join(row["text"] for row in rows).encode()
                    ).hexdigest(),
                }
            )
        receipts = [dict(row) for row in store.db.execute("SELECT * FROM acquisitions ORDER BY id")]
        start = json.loads((root / "campaign-start.json").read_bytes())
        budget = json.loads((root / "budget.json").read_bytes())
        new = [r for r in receipts if r["id"] >= start["first_new_acquisition_id"]]
        payload = sum(
            int(json.loads(r["headers"]).get("psephos_downloaded_bytes", "0")) for r in new
        )
        assert budget["consumed_bytes"] == payload + budget.get(
            "request_overhead_bytes", 0
        ) + budget.get("uncertain_reserved_bytes", 0)
        assert budget["consumed_bytes"] <= budget["cap_bytes"] == CAP and not budget.get(
            "reserved_bytes"
        )
        counts = {
            table: store.db.execute("SELECT count(*) FROM " + table).fetchone()[0]
            for table in (
                "documents",
                "versions",
                "provisions",
                "artifacts",
                "acquisitions",
                "legal_references",
            )
        }
        assert counts["documents"] == counts["versions"] == len(chapters)
        result = {
            "status": "complete_publisher_inventory",
            "passed": True,
            "ready_for_main_independent_verification_and_import": True,
            "canonical_import_performed": False,
            "source_store_path": str(store.root),
            "collection_ids": [COLLECTION],
            "jurisdiction_id": "us-or-portland",
            "complete": len(chapters),
            "pending": 0,
            "error": 0,
            "denominator_unit": "Council Clerk-listed Charter chapters, reconciled through all native articles/sections",
            "counts": counts,
            "unit_kinds": dict(
                store.db.execute("SELECT unit_kind,count(*) FROM provisions GROUP BY unit_kind")
            ),
            "retained_bytes": sum(a["bytes"] for a in artifacts),
            "native_index_url": INDEX,
            "native_inventory_sha256": metadata["inventory_sha256"],
            "fidelity": {
                "all_native_parent_order_and_body_checks": True,
                "paragraphs": paragraph_count,
                "tables": table_count,
                "lists": list_count,
                "unknown_legal_dates_preserved": True,
            },
            "budget": {**budget, "actual_new_payload_bytes": payload},
            "documents": reports,
            "artifacts": artifacts,
            "receipts": receipts,
            "observed_at_min": min(r["observed_at"] for r in new),
            "observed_at_max": max(r["observed_at"] for r in new),
            "scope_exclusions": metadata["excluded"],
            "all_current_city_law": False,
            "licensing": "Publisher accuracy/privacy notice retained; no blanket redistribution license inferred.",
            "verification": {
                "network_requests": 0,
                "read_only": True,
                "python": platform.python_version(),
                "seconds": round(time.monotonic() - started, 3),
                "completed_at": utc_now(),
            },
            "source_code_sha256": {
                module.__name__: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                for module in (
                    acquire,
                    campaign,
                    parse,
                    portland_charter,
                    portland_code,
                    store_module,
                )
                if module.__file__
            },
            "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }
        return result
    finally:
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("preflight", "sync", "verify"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--check-resume", action="store_true", help="Verify cached sync with HTTP forbidden"
    )
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root / "writer.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.phase == "verify":
            resume = check_resume(root) if args.check_resume else None
            result = verify(root)
            if resume:
                result["cache_resume"] = resume
            save(root / "acceptance.json", result)
            print(
                json.dumps(
                    {
                        key: result[key]
                        for key in (
                            "status",
                            "counts",
                            "unit_kinds",
                            "retained_bytes",
                            "verification",
                        )
                    },
                    indent=2,
                )
            )
            return
        store = Store(root / "store")
        acquirer = None
        try:
            if not (root / "campaign-start.json").exists():
                if store.db.execute("SELECT count(*) FROM acquisitions").fetchone()[0]:
                    raise ValueError(
                        "Restore original campaign-start.json for this existing source store"
                    )
                save(
                    root / "campaign-start.json",
                    {"first_new_acquisition_id": 1, "reused_policy_urls": []},
                )
            acquirer = CharterAcquirer(store, root, preflight=args.phase == "preflight")
            if args.phase == "preflight":
                result = preflight(store, acquirer)
                save(
                    root / "preflight.json",
                    {
                        **result,
                        "charged_bytes_at_preflight": acquirer.downloaded,
                        "ceiling_bytes": acquirer.max_bytes,
                    },
                )
            else:
                sync_portland_charter(store, acquirer, args.limit, None)
        finally:
            if acquirer is not None:
                acquirer.close()
            store.close()


if __name__ == "__main__":
    main()
