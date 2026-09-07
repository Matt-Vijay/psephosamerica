"""Read-only integrity measurements over actual retained evidence, not fixtures."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from importlib.metadata import version
from typing import Any
from urllib.parse import urlsplit

from shapely.geometry import shape

from .retrieve import Reader, eligible
from .store import Store, utc_now


def audit(store: Store) -> dict[str, Any]:
    db = store.db
    failures = []
    sources = []
    for row in db.execute("SELECT sha256,bytes FROM artifacts ORDER BY sha256"):
        sha, size = row
        path = store.object_path(sha)
        try:
            with path.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != sha or path.stat().st_size != size:
                failures.append({"sha256": sha, "error": "hash/size mismatch"})
        except OSError as exc:
            failures.append({"sha256": sha, "error": type(exc).__name__})
        sources.append(
            {
                "sha256": sha,
                "bytes": size,
                "receipts": [
                    dict(r)
                    for r in db.execute(
                        "SELECT id,url,final_url,observed_at,status,headers FROM acquisitions "
                        "WHERE sha256=? ORDER BY id",
                        (sha,),
                    )
                ],
            }
        )
    prefix, _ = eligible(None, None)

    def rows(sql: str) -> list[dict[str, Any]]:
        return [dict(r) for r in db.execute(prefix + sql)]

    checks = {
        "sqlite_quick_check": [r[0] for r in db.execute("PRAGMA quick_check")],
        "foreign_key_violations": [tuple(r) for r in db.execute("PRAGMA foreign_key_check")],
        "duplicate_version_keys": db.execute(
            "SELECT count(*) FROM (SELECT version_id,key FROM provisions GROUP BY 1,2 HAVING count(*)>1)"
        ).fetchone()[0],
        "missing_provision_url_or_text": db.execute(
            "SELECT count(*) FROM provisions WHERE trim(url)='' OR trim(text)=''"
        ).fetchone()[0],
        "invalid_source_versions": db.execute(
            "SELECT count(*) FROM versions v JOIN acquisitions a ON a.id=v.acquisition_id "
            "WHERE a.sha256 IS NULL OR a.sha256!=v.artifact_sha OR a.status NOT BETWEEN 200 AND 299 "
            "OR a.error IS NOT NULL OR v.available_at IS NULL OR v.available_at<a.observed_at"
        ).fetchone()[0],
        "missing_geometry_bounds": db.execute(
            "SELECT count(*) FROM features f LEFT JOIN feature_bounds b ON b.rowid=f.rowid WHERE b.rowid IS NULL"
        ).fetchone()[0],
        "orphan_geometry_bounds": db.execute(
            "SELECT count(*) FROM feature_bounds b LEFT JOIN features f ON f.rowid=b.rowid WHERE f.rowid IS NULL"
        ).fetchone()[0],
    }
    references = rows(
        "SELECT r.relation,count(*) AS count FROM legal_references r JOIN provisions p ON p.id=r.provision_id "
        "JOIN chosen v ON v.id=p.version_id GROUP BY r.relation ORDER BY r.relation"
    )
    internal = rows(
        "SELECT count(*) AS references_count,sum(target.key IS NOT NULL) AS acquired_exact_targets "
        "FROM legal_references r JOIN provisions p ON p.id=r.provision_id JOIN chosen v ON v.id=p.version_id "
        "LEFT JOIN (SELECT DISTINCT tp.key FROM provisions tp JOIN chosen tv ON tv.id=tp.version_id) target "
        "ON target.key=CASE WHEN r.target LIKE '/us/usc/%' THEN 'usc:'||r.target ELSE r.target END "
        "WHERE r.target LIKE '/us/usc/%' OR r.target LIKE 'dc-code:%' OR r.target LIKE 'dc-law:%'"
    )[0]
    geometry: Counter[str] = Counter()
    invalid_layers: Counter[str] = Counter()
    for row in db.execute(
        prefix
        + "SELECT f.geometry,d.collection_id,f.layer FROM features f JOIN chosen v ON v.id=f.version_id "
        "JOIN documents d ON d.id=v.document_id"
    ):
        item = shape(json.loads(row[0]))
        geometry["features"] += 1
        geometry["invalid"] += not item.is_valid
        geometry["empty"] += item.is_empty
        if not item.is_valid:
            invalid_layers[row["collection_id"] + "/" + row["layer"]] += 1
    domains = Counter(urlsplit(r[0]).hostname for r in db.execute("SELECT url FROM acquisitions"))
    good = (
        not failures
        and checks["sqlite_quick_check"] == ["ok"]
        and not checks["foreign_key_violations"]
        and not any(
            value
            for key, value in checks.items()
            if key not in {"sqlite_quick_check", "foreign_key_violations"}
        )
    )
    return {
        "generated_at": utc_now(),
        "status": "PASS" if good else "FAIL",
        "meaning": "Storage/source invariants only; not a completeness or legal-accuracy certification.",
        "coverage": Reader(store).coverage(),
        "checks": checks,
        "artifacts_rehashed": len(sources),
        "artifact_failures": failures,
        "acquisition_hosts": dict(sorted(domains.items())),
        "version_clocks": rows(
            "SELECT d.collection_id,count(*) AS versions,sum(v.snapshot_date IS NULL) AS undated,"
            "sum(v.effective_on IS NOT NULL) AS explicit_effective_dates "
            "FROM versions v JOIN documents d ON d.id=v.document_id GROUP BY d.collection_id"
        ),
        "latest_reference_relations": references,
        "exact_identifier_resolution": internal,
        "geometry": dict(geometry),
        "invalid_geometry_by_layer": dict(invalid_layers),
        "text_quality": rows(
            "SELECT d.collection_id,coalesce(json_extract(p.metadata,'$.text_quality'),'publisher_text_projection') AS quality,count(*) AS units "
            "FROM provisions p JOIN chosen v ON v.id=p.version_id JOIN documents d ON d.id=v.document_id GROUP BY 1,2"
        ),
        "media_bearing_units": rows(
            "SELECT d.collection_id,count(*) AS units FROM provisions p JOIN chosen v ON v.id=p.version_id "
            "JOIN documents d ON d.id=v.document_id WHERE lower(p.markup) LIKE '%<img%' OR lower(p.markup) LIKE '%<graphic%' "
            "OR lower(p.markup) LIKE '%<gph%' OR json_array_length(p.metadata,'$.media')>0 GROUP BY d.collection_id"
        ),
        "software": {
            name: version(name)
            for name in (
                "psephos-legal",
                "httpx",
                "lxml",
                "mcp",
                "pypdf",
                "shapely",
                "pyogrio",
                "pyproj",
            )
        },
        "source_manifest": sources,
    }
