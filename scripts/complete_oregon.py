"""Complete only retained 2025 ORS inventory gaps, preserving accepted projections."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from lxml import html

from psephos.acquire import Acquirer
from psephos.collect_oregon import BASE, CHAPTERS, COLLECTION, chapter_units, sync_oregon
from psephos.parse import readable
from psephos.store import Store, digest, json_text, utc_now

CAP = 64 * 1024**2


def versions(s: Store) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in s.db.execute(
            "SELECT v.* FROM versions v JOIN documents d ON d.id=v.document_id WHERE d.collection_id=? ORDER BY v.id",
            (COLLECTION,),
        )
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    s = Store(args.data)
    checkpoint = args.data / "oregon-completion.json"
    a = None
    try:
        if checkpoint.exists():
            before = json.loads(checkpoint.read_bytes())
        else:
            old = versions(s)
            ids = sorted(
                r[0]
                for r in s.db.execute(
                    "SELECT p.id FROM provisions p JOIN versions v ON v.id=p.version_id JOIN documents d ON d.id=v.document_id WHERE d.collection_id=?",
                    (COLLECTION,),
                )
            )
            before = {
                "first_acquisition_id": s.db.execute(
                    "SELECT coalesce(max(id),0)+1 FROM acquisitions"
                ).fetchone()[0],
                "versions": old,
                "provision_ids_sha256": digest(json_text(ids).encode()),
                "provisions": len(ids),
            }
            checkpoint.write_text(json.dumps(before) + "\n")
        spent = s.db.execute(
            "SELECT coalesce(sum(CAST(coalesce(json_extract(a.headers,'$.psephos_downloaded_bytes'),CASE WHEN a.status=200 THEN b.bytes ELSE 0 END) AS INTEGER)),0) FROM acquisitions a LEFT JOIN artifacts b ON b.sha256=a.sha256 WHERE a.id>=? AND a.url LIKE ?",
            (before["first_acquisition_id"], BASE + "/%"),
        ).fetchone()[0]
        a = Acquirer(s, delay=2.1, max_bytes=max(0, CAP - spent))
        sync_oregon(s, a)
        now = versions(s)
        old_ids = {v["id"] for v in before["versions"]}
        assert [v for v in now if v["id"] in old_ids] == before["versions"]
        ids = sorted(
            r[0]
            for v in before["versions"]
            for r in s.db.execute("SELECT id FROM provisions WHERE version_id=?", (v["id"],))
        )
        assert (
            len(ids) == before["provisions"]
            and digest(json_text(ids).encode()) == before["provision_ids_sha256"]
        )
        inventory = [
            dict(r)
            for r in s.db.execute(
                "SELECT item,url,status,error FROM inventories WHERE collection_id=? ORDER BY item",
                (COLLECTION,),
            )
        ]
        assert {r["item"] for r in inventory} == set(CHAPTERS)
        assert all(r["status"] == "indexed" for r in inventory), (
            "Oregon retains unresolved inventory gaps"
        )
        assert len(now) == len({v["document_id"] for v in now}) == len(CHAPTERS)
        new = [v for v in now if v["id"] not in old_ids]
        counts: Counter[str] = Counter()
        receipts = []
        for v in new:
            raw = s.artifact(v["artifact_sha"])
            meta = json.loads(v["metadata"])
            chapter = meta["source_chapter"].zfill(3)
            receipt = dict(
                s.db.execute(
                    "SELECT * FROM acquisitions WHERE id=?", (v["acquisition_id"],)
                ).fetchone()
            )
            _, units, _ = chapter_units(raw, chapter, receipt["url"])
            saved = s.db.execute(
                "SELECT * FROM provisions WHERE version_id=? ORDER BY ordinal", (v["id"],)
            ).fetchall()
            for p, unit in zip(saved, units, strict=True):
                for key in (
                    "key",
                    "parent_key",
                    "citation",
                    "heading",
                    "text",
                    "markup",
                    "url",
                    "unit_kind",
                ):
                    assert p[key] == getattr(unit, key)
            native = " ".join(
                readable(n)
                for n in html.fromstring(raw).xpath('//div[starts-with(@class,"WordSection")]')
            )
            assert " ".join(native.split()) == " ".join(" ".join(p.text for p in units).split())
            assert v["snapshot_date"] is None and v["effective_on"] is None
            counts.update(p.unit_kind for p in units)
            receipts.append(
                {
                    **{k: receipt[k] for k in ("id", "url", "sha256", "observed_at")},
                    "bytes": len(raw),
                    "chapter": chapter,
                    "units": len(units),
                }
            )
        report = {
            "status": "PASS",
            "recorded_at": utc_now(),
            "publisher_edition": "2025 ORS; excludes later special/regular sessions; online text not legally official print edition",
            "expected_chapters": len(CHAPTERS),
            "retained_chapters": len(now),
            "missing_chapters": 0,
            "old_versions_unchanged": len(old_ids),
            "old_provisions_unchanged": before["provisions"],
            "old_provision_ids_sha256": before["provision_ids_sha256"],
            "new_chapters": len(new),
            "new_unit_kinds": dict(counts),
            "new_chapter_parser_and_native_text_preservation": "PASS",
            "chapter_receipts": receipts,
            "campaign_payload_bytes": spent + a.downloaded,
            "cap_bytes": CAP,
            "file_cap_bytes": 12 * 1024**2,
            "minimum_host_seconds": 2.1,
            "first_acquisition_id": before["first_acquisition_id"],
            "limitations": "Closed retained publisher chapter inventory, not complete current state law, regulations, subsequent amendments or independently transcribed embedded media.",
        }
        args.out.write_text(json.dumps(report, indent=2) + "\n")
        print(
            json.dumps(
                {
                    k: report[k]
                    for k in (
                        "status",
                        "retained_chapters",
                        "new_chapters",
                        "campaign_payload_bytes",
                        "new_unit_kinds",
                    )
                }
            )
        )
    finally:
        if a is not None:
            a.close()
        s.close()


if __name__ == "__main__":
    main()
