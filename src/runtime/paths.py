from __future__ import annotations

from pathlib import Path

# src/runtime/paths.py is two levels below the repo root (psephosamerica/)
_MODULE_DIR = Path(__file__).parent
_REPO_ROOT = _MODULE_DIR.parent.parent


def repo_root() -> Path:
    return _REPO_ROOT


def db_schema_path() -> Path:
    return _REPO_ROOT / "db" / "schema.sql"


def db_migrations_dir() -> Path:
    return _REPO_ROOT / "db" / "migrations"


def taxonomy_dir() -> Path:
    return _REPO_ROOT / "data" / "taxonomy"


def crosswalks_dir() -> Path:
    return _REPO_ROOT / "data" / "crosswalks"


def local_publish_root() -> Path:
    return _REPO_ROOT / "publish"


def local_artifact_root() -> Path:
    return _REPO_ROOT / "artifacts"


def local_congress_bundle_root() -> Path:
    return _REPO_ROOT / "bundles" / "congress"


def local_disclosure_bundle_root() -> Path:
    return _REPO_ROOT / "bundles" / "disclosures"
