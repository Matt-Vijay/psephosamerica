"""One SQLite catalog. Raw publisher bytes live in a SHA-256 object directory."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@contextmanager
def writer_lock(root: Path) -> Iterator[None]:
    """Serialize scheduled work and CLI sync without locking read-only MCP clients."""
    import fcntl

    root.mkdir(parents=True, exist_ok=True)
    with (root / "writer.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another Psephos writer is running; try later") from exc
        yield


TEXT_PROJECTION = "text-3"

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS artifacts (
 sha256 TEXT PRIMARY KEY, bytes INTEGER NOT NULL CHECK(bytes>=0)
);
CREATE TABLE IF NOT EXISTS acquisitions (
 id INTEGER PRIMARY KEY, url TEXT NOT NULL, final_url TEXT NOT NULL,
 observed_at TEXT NOT NULL, status INTEGER NOT NULL, sha256 TEXT REFERENCES artifacts,
 headers TEXT NOT NULL, error TEXT
);
CREATE INDEX IF NOT EXISTS acquisition_url ON acquisitions(url, id DESC);
CREATE TABLE IF NOT EXISTS jurisdictions (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, level TEXT NOT NULL,
 parent_id TEXT REFERENCES jurisdictions
);
CREATE TABLE IF NOT EXISTS collections (
 id TEXT PRIMARY KEY, jurisdiction_id TEXT NOT NULL REFERENCES jurisdictions,
 name TEXT NOT NULL, authority TEXT NOT NULL, kind TEXT NOT NULL, homepage TEXT NOT NULL,
 source_status TEXT NOT NULL, access TEXT NOT NULL, metadata TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inventories (
 collection_id TEXT REFERENCES collections, item TEXT NOT NULL, url TEXT NOT NULL,
 status TEXT NOT NULL, error TEXT, checked_at TEXT NOT NULL,
 PRIMARY KEY(collection_id,item)
);
CREATE TABLE IF NOT EXISTS documents (
 id TEXT PRIMARY KEY, collection_id TEXT NOT NULL REFERENCES collections,
 title TEXT NOT NULL, url TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS versions (
 id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents,
 artifact_sha TEXT NOT NULL REFERENCES artifacts, acquisition_id INTEGER NOT NULL REFERENCES acquisitions,
 member TEXT, snapshot_date TEXT, snapshot_basis TEXT NOT NULL,
 published_on TEXT, effective_on TEXT, amended_on TEXT, repealed_on TEXT,
 parser TEXT NOT NULL, metadata TEXT NOT NULL, available_at TEXT NOT NULL,
 UNIQUE(document_id,artifact_sha,member,snapshot_date,parser)
);
CREATE INDEX IF NOT EXISTS version_document ON versions(document_id,snapshot_date);
CREATE TABLE IF NOT EXISTS provisions (
 rowid INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL,
 version_id TEXT NOT NULL REFERENCES versions, key TEXT NOT NULL,
 parent_key TEXT, ordinal INTEGER NOT NULL, unit_kind TEXT NOT NULL,
 citation TEXT NOT NULL, heading TEXT NOT NULL, text TEXT NOT NULL, markup TEXT NOT NULL,
 url TEXT NOT NULL, metadata TEXT NOT NULL,
 UNIQUE(version_id,key)
);
CREATE INDEX IF NOT EXISTS provision_key ON provisions(key,version_id);
CREATE INDEX IF NOT EXISTS provision_citation ON provisions(citation COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS provision_order ON provisions(version_id,ordinal);
CREATE VIRTUAL TABLE IF NOT EXISTS provision_search USING fts5(
 citation, heading, text, content='provisions', content_rowid='rowid',
 tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS provision_insert AFTER INSERT ON provisions BEGIN
 INSERT INTO provision_search(rowid,citation,heading,text)
 VALUES(new.rowid,new.citation,new.heading,new.text);
END;
CREATE TABLE IF NOT EXISTS legal_references (
 provision_id TEXT NOT NULL REFERENCES provisions(id), target TEXT NOT NULL,
 relation TEXT NOT NULL, label TEXT NOT NULL, evidence TEXT NOT NULL,
 PRIMARY KEY(provision_id,target,relation,label)
);
CREATE TABLE IF NOT EXISTS features (
 rowid INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL,
 version_id TEXT NOT NULL REFERENCES versions, source_key TEXT NOT NULL,
 geometry TEXT NOT NULL, properties TEXT NOT NULL,
 layer TEXT NOT NULL, acquisition_id INTEGER NOT NULL REFERENCES acquisitions,
 UNIQUE(version_id,source_key)
);
CREATE VIRTUAL TABLE IF NOT EXISTS feature_bounds USING rtree(
 rowid,minx,maxx,miny,maxy
);
PRAGMA user_version=1;
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_id(*parts: str) -> str:
    return digest(json_text(parts).encode())[:32]


@dataclass(frozen=True)
class Reference:
    target: str
    relation: str
    label: str
    evidence: str


@dataclass(frozen=True)
class Provision:
    key: str
    citation: str
    heading: str
    text: str
    markup: str
    url: str
    parent_key: str | None = None
    unit_kind: str = "section"
    metadata: dict[str, Any] = field(default_factory=dict)
    references: tuple[Reference, ...] = ()


@dataclass(frozen=True)
class Feature:
    key: str
    geometry: dict[str, Any]
    properties: dict[str, Any]
    bounds: tuple[float, float, float, float]
    layer: str = ""
    acquisition_id: int | None = None


class Store:
    def __init__(self, root: Path, *, readonly: bool = False):
        self.root = root.resolve()
        database = self.root / "legal.sqlite3"
        if readonly:
            self.db = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
        else:
            self.root.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(database)
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError(f"Unsupported catalog schema: {version}")
            self.db.executescript(SCHEMA)
            self.db.execute("PRAGMA journal_mode=WAL")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=30000")
        if readonly:
            self.db.execute("PRAGMA query_only=ON")

    def close(self) -> None:
        self.db.close()

    def object_path(self, sha: str) -> Path:
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError("Invalid artifact hash")
        return self.root / "objects" / sha[:2] / sha

    def artifact(self, sha: str) -> bytes:
        data = self.object_path(sha).read_bytes()
        if digest(data) != sha:
            raise ValueError(f"Corrupt artifact {sha}")
        return data

    def collection(
        self,
        collection_id: str,
        jurisdiction: tuple[str, str, str, str | None],
        *,
        name: str,
        authority: str,
        kind: str,
        homepage: str,
        source_status: str,
        access: str,
        metadata: dict[str, Any] | None = None,
        parents: tuple[tuple[str, str, str, str | None], ...] = (),
    ) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO jurisdictions VALUES ('us','United States','federal',NULL)"
            )
            for entry in (*parents, jurisdiction):
                self.db.execute(
                    "INSERT INTO jurisdictions VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                    "name=excluded.name,level=excluded.level,parent_id=excluded.parent_id",
                    entry,
                )
            self.db.execute(
                "INSERT INTO collections VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name,authority=excluded.authority,kind=excluded.kind,"
                "homepage=excluded.homepage,source_status=excluded.source_status,"
                "access=excluded.access,metadata=excluded.metadata",
                (
                    collection_id,
                    jurisdiction[0],
                    name,
                    authority,
                    kind,
                    homepage,
                    source_status,
                    access,
                    json_text(metadata or {}),
                ),
            )

    def inventory(
        self, collection: str, item: str, url: str, status: str, error: str | None = None
    ) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO inventories VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(collection_id,item) DO UPDATE SET url=excluded.url,"
                "status=excluded.status,error=excluded.error,checked_at=excluded.checked_at",
                (collection, item, url, status, error, utc_now()),
            )

    def ingest(
        self,
        *,
        collection: str,
        document: str,
        title: str,
        url: str,
        acquisition: int,
        member: str | None = None,
        snapshot_date: str | None = None,
        snapshot_basis: str,
        parser: str,
        provisions: Iterable[Provision],
        metadata: dict[str, Any] | None = None,
        published_on: str | None = None,
        effective_on: str | None = None,
        amended_on: str | None = None,
        features: Iterable[Feature] = (),
        available_at: str | None = None,
    ) -> tuple[str, int, bool]:
        """Atomic per-document import; no partly parsed document becomes retrievable."""
        parser += "/" + TEXT_PROJECTION
        source = self.db.execute(
            "SELECT sha256,observed_at FROM acquisitions WHERE id=? AND status BETWEEN 200 AND 299",
            (acquisition,),
        ).fetchone()
        if source is None:
            raise ValueError("Version needs a successful source acquisition")
        version = stable_id(document, source[0], member or "", snapshot_date or "", parser)
        existing = self.db.execute("SELECT id FROM versions WHERE id=?", (version,)).fetchone()
        if existing:
            count = self.db.execute(
                "SELECT count(*) FROM provisions WHERE version_id=?", (version,)
            ).fetchone()[0]
            return version, int(count), False
        with self.db:
            self.db.execute(
                "INSERT INTO documents VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "title=excluded.title,url=excluded.url",
                (document, collection, title, url),
            )
            self.db.execute(
                "INSERT INTO versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    version,
                    document,
                    source[0],
                    acquisition,
                    member,
                    snapshot_date,
                    snapshot_basis,
                    published_on,
                    effective_on,
                    amended_on,
                    None,
                    parser,
                    json_text(metadata or {}),
                    available_at or source["observed_at"],
                ),
            )
            # Explicit field names avoid coupling the parser to table column order.
            count = 0
            for ordinal, unit in enumerate(provisions):
                if not unit.key or not unit.citation or not unit.text.strip():
                    raise ValueError(f"Empty source unit in {document}: {unit.key!r}")
                unit_id = stable_id(version, unit.key)
                self.db.execute(
                    "INSERT INTO provisions(id,version_id,key,parent_key,ordinal,unit_kind,"
                    "citation,heading,text,markup,url,metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        unit_id,
                        version,
                        unit.key,
                        unit.parent_key,
                        ordinal,
                        unit.unit_kind,
                        unit.citation,
                        unit.heading,
                        unit.text,
                        unit.markup,
                        unit.url,
                        json_text(unit.metadata),
                    ),
                )
                self.db.executemany(
                    "INSERT OR IGNORE INTO legal_references VALUES (?,?,?,?,?)",
                    (
                        (unit_id, ref.target, ref.relation, ref.label, ref.evidence)
                        for ref in unit.references
                    ),
                )
                count += 1
            if not count:
                raise ValueError(f"No provisions parsed from {document}")
            for feature in features:
                cursor = self.db.execute(
                    "INSERT INTO features(id,version_id,source_key,geometry,properties,layer,acquisition_id) VALUES (?,?,?,?,?,?,?)",
                    (
                        stable_id(version, feature.key),
                        version,
                        feature.key,
                        json_text(feature.geometry),
                        json_text(feature.properties),
                        feature.layer,
                        feature.acquisition_id or acquisition,
                    ),
                )
                minx, miny, maxx, maxy = feature.bounds
                self.db.execute(
                    "INSERT INTO feature_bounds VALUES (?,?,?,?,?)",
                    (cursor.lastrowid, minx, maxx, miny, maxy),
                )
        return version, count, True
