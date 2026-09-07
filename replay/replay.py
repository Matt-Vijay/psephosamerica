"""Offline reproduction of three sealed, accepted source projections. Not a collector."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.metadata
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zipfile import ZipFile

if TYPE_CHECKING:
    from psephos.store import Provision

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
CORE_REVISION = "d4f7796724f8718d86dfa3d2beaa3de09f9300d7"
CORE_HASHES = {
    "__init__.py": "753a7fc67b4f724961e57ea098a43a7c72d83db8389aef40e816c4b65796ad31",
    "parse.py": "6eab276c8e50f35e80807053ade6cbe3b1479daec8f651fa9848e4c48c4942a9",
    "store.py": "7ff762ffd3e66c8ebd951174a86183fb05ce97c60c07f0fd9774efd8a92f8b9e",
}
FAMILIES = ("fl", "nj", "ms")
RECIPE_HASHES = {
    "fl": "a656c801a0f6f6dc1115e5c7b4390b665bb24adb78196329c602d662639b5543",
    "nj": "bf6e21d7bd0b323bdd65e9a7bef10cede55333aa551854dbb1bb724a020aaea0",
    "ms": "0f9419232a7c1ab6c8bedb25ebc781b26771e7832be5847b2759881eb60c0740",
}
UNIT_FIELDS = ("key", "parent_key", "unit_kind", "citation", "heading", "text", "markup", "url")


def encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def unit_fingerprint(units: Iterable[Provision]) -> tuple[str, int, int]:
    """All source fields; Store's first-wins reference deduplication, no rowid dependence."""
    result = hashlib.sha256()
    count = references = 0
    for unit in units:
        refs: dict[tuple[str, str, str], str] = {}
        for ref in unit.references:
            refs.setdefault((ref.target, ref.relation, ref.label), ref.evidence)
        row = [getattr(unit, field) for field in UNIT_FIELDS]
        row += [unit.metadata, [[*key, value] for key, value in sorted(refs.items())]]
        result.update(encoded(row) + b"\n")
        count += 1
        references += len(refs)
    return result.hexdigest(), count, references


def stored_fingerprint(db: sqlite3.Connection, version: str) -> tuple[str, int, int]:
    """Same contract against persisted accepted rows, without importing parser code."""
    result = hashlib.sha256()
    count = references = 0
    for unit in db.execute(
        "SELECT * FROM provisions WHERE version_id=? ORDER BY ordinal", (version,)
    ):
        refs = [
            list(row)
            for row in db.execute(
                "SELECT target,relation,label,evidence FROM legal_references WHERE provision_id=? "
                "ORDER BY target,relation,label",
                (unit["id"],),
            )
        ]
        row = [unit[field] for field in UNIT_FIELDS] + [json.loads(unit["metadata"]), refs]
        result.update(encoded(row) + b"\n")
        count += 1
        references += len(refs)
    return result.hexdigest(), count, references


def object_path(root: Path, sha: str) -> Path:
    require(len(sha) == 64 and all(c in "0123456789abcdef" for c in sha), "Bad object identity")
    return root / sha[:2] / sha


def environment(family: str) -> dict[str, str]:
    versions = {"python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version}
    for package in ("lxml", "pypdf"):
        versions[package] = importlib.metadata.version(package)
    require(sys.version_info[:2] == (3, 12), "Replay profile requires Python 3.12")
    require(versions["lxml"] == "6.1.3", "Replay profile requires lxml 6.1.3")
    if family == "ms":
        require(versions["pypdf"] == "6.17.0", "PDF profile requires pypdf 6.17.0")
        executable = shutil.which("pdftotext")
        require(executable is not None, "PDF replay requires Poppler pdftotext 26.02.0")
        output = subprocess.run([str(executable), "-v"], capture_output=True, check=True)
        versions["pdftotext"] = (output.stdout + output.stderr).decode().splitlines()[0]
        require(versions["pdftotext"] == "pdftotext version 26.02.0", "Poppler version mismatch")
    return versions


@contextmanager
def historical_runtime() -> Iterator[None]:
    """Only these three hash-pinned repository blobs execute; no data-supplied code paths."""
    require("psephos" not in sys.modules, "Replay must run in its own process, not the MCP server")
    with tempfile.TemporaryDirectory(prefix="psephos-text2-") as directory:
        package = Path(directory) / "psephos"
        package.mkdir()
        for name, expected in CORE_HASHES.items():
            result = subprocess.run(
                ["git", "show", f"{CORE_REVISION}:src/psephos/{name}"],
                cwd=REPO,
                capture_output=True,
                check=True,
                env={**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_ALLOW_PROTOCOL": ""},
            )
            require(digest(result.stdout) == expected, f"Historical core hash mismatch: {name}")
            (package / name).write_bytes(result.stdout)
        sys.path.insert(0, directory)
        try:
            yield
        finally:
            sys.path.remove(directory)


def offline(event: str, args: tuple[Any, ...]) -> None:
    if event.startswith("socket.") or event.startswith("http.client."):
        raise PermissionError("Offline replay forbids network access")


def no_symlinks(path: Path, root: Path) -> None:
    """Check every output component before any mkdir/open, including SQLite sidecars."""
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current /= part
        require(not current.is_symlink(), "Symlinked replay output")


@contextmanager
def owned_output(output: Path, objects: Path, identity: dict[str, str]) -> Iterator[None]:
    output, objects = output.resolve(), objects.resolve()
    require(
        output != objects and output not in objects.parents and objects not in output.parents,
        "Output and retained object tree must be separate",
    )
    for parent in output.parents:
        require(not (parent / "legal.sqlite3").exists(), "Output cannot be inside another Store")
    marker = output / "replay-owner.json"
    if output.exists():
        if marker.is_file() and not marker.is_symlink():
            require(json.loads(marker.read_bytes()) == identity, "Output belongs to another replay")
        else:
            require(not any(output.iterdir()), "Refusing nonempty, unowned output")
    else:
        output.mkdir(parents=True)
    if not marker.exists():
        with marker.open("xb") as stream:
            stream.write(encoded(identity) + b"\n")
    for name in (
        "legal.sqlite3",
        "legal.sqlite3-wal",
        "legal.sqlite3-shm",
        "legal.sqlite3-journal",
        "replay.lock",
        "replay-result.json",
    ):
        no_symlinks(output / name, output)
    with (output / "replay.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def install_inputs(store: Any, recipe: dict[str, Any], objects: Path) -> None:
    """Rehash before use; retain source receipts rather than inventing new observation clocks."""
    for artifact in recipe["artifacts"]:
        source = object_path(objects, artifact["sha256"])
        require(
            source.stat().st_size == artifact["bytes"] and file_hash(source) == artifact["sha256"],
            f"Missing/corrupt retained artifact {artifact['sha256']}",
        )
        target = store.object_path(artifact["sha256"])
        no_symlinks(target, store.root)
        target.parent.mkdir(parents=True, exist_ok=True)
        require(
            not target.parent.is_symlink() and not target.is_symlink(), "Symlinked output object"
        )
        if target.exists():
            require(file_hash(target) == artifact["sha256"], "Existing output object changed")
        else:
            try:
                os.link(source, target)
            except OSError:
                shutil.copyfile(source, target)
    with store.db:
        for table in ("artifacts", "acquisitions", "jurisdictions", "collections"):
            for row in recipe[table]:
                columns = list(row)
                key = "sha256" if table == "artifacts" else "id"
                prior = store.db.execute(
                    f"SELECT * FROM {table} WHERE {key}=?", (row[key],)
                ).fetchone()
                if prior is not None:
                    require(dict(prior) == row, f"Conflicting retained {table} receipt")
                else:
                    store.db.execute(
                        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                        tuple(row.values()),
                    )


def zip_member(path: Path, name: str, expected_size: int, expected_hash: str) -> bytes:
    with ZipFile(path) as archive:
        require(archive.namelist().count(name) == 1, f"Missing/duplicate ZIP member: {name}")
        require(archive.getinfo(name).file_size == expected_size, f"Unexpected member size: {name}")
        raw = archive.read(name)
    require(digest(raw) == expected_hash, f"Corrupt member: {name}")
    return raw


def project(
    family: str, item: dict[str, Any], store: Any, cache: dict[str, Any]
) -> list[Provision]:
    # Static dispatch is deliberate. Recipes cannot import modules or choose an executable.
    import fl_nj
    import mississippi

    version, document = item["version"], item["document"]
    sha = version["artifact_sha"]
    path = store.object_path(sha)
    if family == "fl":
        return list(
            fl_nj.florida(
                path.read_bytes(),
                document["id"],
                document["url"],
                constitution=document["collection_id"] == "florida-constitution",
            )
        )
    if family == "nj":
        if document["collection_id"] == "nj-constitution":
            member = item["members"][0]
            raw = zip_member(path, member["name"], member["bytes"], member["sha256"])
            return list(fl_nj.nj_constitution(raw, document["id"], document["url"]))
        if sha not in cache:
            members = {
                m["name"]: zip_member(path, m["name"], m["bytes"], m["sha256"])
                for m in item["members"]
            }
            cache[sha] = fl_nj.prepare_nj(
                members["STATUTES.TXT"], members["STATUTES.RTF"], artifact_sha=sha
            )
        return list(fl_nj.nj_statute_units(cache[sha], document["id"]))
    require(family == "ms", "Unsupported parser family")
    return [
        mississippi.project_pdf(
            path,
            document["id"],
            document["title"],
            document["url"],
            item["inventory_receipt"],
            cache["reviews"].get(sha, {}),
            snapshot_profile=item["snapshot_profile"],
        )
    ]


def run(
    family: str, objects: Path, output: Path, limit: int | None, ms_review: Path | None = None
) -> dict[str, Any]:
    raw = (HERE / "recipes" / f"{family}.json").read_bytes()
    require(digest(raw) == RECIPE_HASHES[family], "Sealed recipe changed; review/reseal explicitly")
    recipe = json.loads(raw)
    require(
        recipe["family"] == family and recipe["core_revision"] == CORE_REVISION,
        "Recipe profile mismatch",
    )
    selected = recipe["documents"] if limit is None else recipe["documents"][:limit]
    require(limit is None or limit > 0, "Limit must be positive")
    identity = {"family": family, "recipe_sha256": digest(raw), "profile": "accepted-text-2"}
    versions = environment(family)
    reviews: dict[str, Any] = {}
    if family == "ms":
        require(ms_review is not None, "PDF replay needs the retained --ms-review sidecar")
        assert ms_review is not None
        require(file_hash(ms_review) == recipe["review"]["sha256"], "Sparse-page review changed")
        reviews = json.loads(ms_review.read_bytes())
    started = time.monotonic()
    with owned_output(output, objects, identity), historical_runtime():
        from psephos.store import Store, stable_id

        sys.addaudithook(offline)
        store = Store(output)
        try:
            install_inputs(store, recipe, objects)
            cache: dict[str, Any] = {"reviews": reviews}
            results = []
            for item in selected:
                version, doc = item["version"], item["document"]
                require(version["parser"].endswith("/text-2"), "Not an accepted text-2 label")
                units = project(family, item, store, cache)
                fingerprint = unit_fingerprint(units)
                require(
                    list(fingerprint) == item["expected_units"], f"Projection differs: {doc['id']}"
                )
                metadata = units[0].metadata if family == "ms" else item["version_metadata"]
                require(
                    digest(encoded(metadata)) == item["version_metadata_sha256"],
                    f"Version metadata differs: {doc['id']}",
                )
                expected_id = stable_id(
                    doc["id"],
                    version["artifact_sha"],
                    version["member"] or "",
                    version["snapshot_date"] or "",
                    version["parser"],
                )
                require(version["id"] == expected_id, "Historical identity mismatch")
                restored_version = {**version, "metadata": encoded(metadata).decode()}
                existing = store.db.execute(
                    "SELECT * FROM versions WHERE id=?", (expected_id,)
                ).fetchone()
                if existing is not None:
                    require(dict(existing) == restored_version, "Existing version changed")
                    require(
                        stored_fingerprint(store.db, expected_id) == fingerprint,
                        "Existing projection changed",
                    )
                    stored_doc = store.db.execute(
                        "SELECT * FROM documents WHERE id=?", (doc["id"],)
                    ).fetchone()
                    require(dict(stored_doc) == doc, "Existing document changed")
                kwargs = {
                    key: version[key]
                    for key in (
                        "member",
                        "snapshot_date",
                        "snapshot_basis",
                        "published_on",
                        "effective_on",
                        "amended_on",
                        "available_at",
                    )
                }
                actual, count, created = store.ingest(
                    collection=doc["collection_id"],
                    document=doc["id"],
                    title=doc["title"],
                    url=doc["url"],
                    acquisition=version["acquisition_id"],
                    parser=version["parser"].removesuffix("/text-2"),
                    metadata=metadata,
                    provisions=units,
                    **kwargs,
                )
                require(
                    actual == expected_id and count == fingerprint[1],
                    "Ingest identity/count mismatch",
                )
                require(
                    dict(
                        store.db.execute("SELECT * FROM versions WHERE id=?", (actual,)).fetchone()
                    )
                    == restored_version,
                    "Persisted version clocks/metadata mismatch",
                )
                require(
                    stored_fingerprint(store.db, actual) == fingerprint,
                    "Persisted projection mismatch",
                )
                ids = [
                    row[0]
                    for row in store.db.execute(
                        "SELECT id FROM provisions WHERE version_id=? ORDER BY ordinal", (actual,)
                    )
                ]
                require(
                    ids == [stable_id(actual, unit.key) for unit in units], "Provision ID mismatch"
                )
                results.append(
                    {
                        "document": doc["id"],
                        "version": actual,
                        "units": count,
                        "references": fingerprint[2],
                        "projection_sha256": fingerprint[0],
                        "created": created,
                    }
                )
            require(
                not store.db.execute("PRAGMA foreign_key_check").fetchall(), "Foreign-key failure"
            )
            require(
                store.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok",
                "SQLite integrity failure",
            )
        finally:
            store.close()
        report = {
            **identity,
            "status": "PASS",
            "core_revision": CORE_REVISION,
            "runtime_sha256": {
                name: file_hash(HERE / name) for name in ("replay.py", "fl_nj.py", "mississippi.py")
            },
            "environment": versions,
            "artifact_count": len(recipe["artifacts"]),
            "artifact_bytes": sum(a["bytes"] for a in recipe["artifacts"]),
            "documents": results,
            "created": sum(r["created"] for r in results),
            "seconds": round(time.monotonic() - started, 3),
        }
        no_symlinks(output / "replay-result.json", output)
        with tempfile.NamedTemporaryFile(dir=output, prefix=".report-", delete=False) as stream:
            stream.write(encoded(report) + b"\n")
        os.replace(stream.name, output / "replay-result.json")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("family", choices=FAMILIES)
    parser.add_argument(
        "--objects",
        required=True,
        type=Path,
        help="Retained objects directory; no SQLite/source workspace needed",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="Empty directory, or this same replay's owned resumable Store",
    )
    parser.add_argument(
        "--limit", type=int, help="Bound documents in stable recipe order (not a new source scope)"
    )
    parser.add_argument(
        "--ms-review", type=Path, help="Exact accepted sparse-page JSON sidecar (MS only)"
    )
    args = parser.parse_args()
    print(
        json.dumps(
            run(
                args.family, args.objects.resolve(), args.out.resolve(), args.limit, args.ms_review
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
