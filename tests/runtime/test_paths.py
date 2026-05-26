from __future__ import annotations

from pathlib import Path

from src.runtime.paths import (
    crosswalks_dir,
    db_migrations_dir,
    db_schema_path,
    local_artifact_root,
    local_publish_root,
    repo_root,
    taxonomy_dir,
)


def test_repo_root_is_absolute() -> None:
    assert repo_root().is_absolute()


def test_repo_root_contains_engineering_spec() -> None:
    # Anchor: the spec file must exist at the repo root.
    assert (repo_root() / "ENGINEERING_SPEC_V1.md").exists()


def test_db_schema_path_exists() -> None:
    assert db_schema_path().exists()
    assert db_schema_path().name == "schema.sql"


def test_db_migrations_dir_exists() -> None:
    assert db_migrations_dir().exists()
    assert db_migrations_dir().is_dir()


def test_taxonomy_dir_exists() -> None:
    assert taxonomy_dir().exists()
    assert taxonomy_dir().is_dir()


def test_crosswalks_dir_exists() -> None:
    assert crosswalks_dir().exists()
    assert crosswalks_dir().is_dir()


def test_local_publish_root_is_under_repo_root() -> None:
    # local_publish_root may not exist on disk; we only check the relationship.
    assert local_publish_root().parent == repo_root()
    assert local_publish_root().name == "publish"


def test_local_artifact_root_is_under_repo_root() -> None:
    # local_artifact_root may not exist on disk; we only check the relationship.
    assert local_artifact_root().parent == repo_root()
    assert local_artifact_root().name == "artifacts"


def test_all_paths_are_absolute() -> None:
    paths: list[Path] = [
        repo_root(),
        db_schema_path(),
        db_migrations_dir(),
        taxonomy_dir(),
        crosswalks_dir(),
        local_publish_root(),
        local_artifact_root(),
    ]
    for p in paths:
        assert p.is_absolute(), f"Expected absolute path, got: {p}"


def test_paths_resolve_from_module_not_cwd(monkeypatch: object, tmp_path: Path) -> None:
    # Changing cwd must not change any returned path.
    import os

    original = {
        name: fn()
        for name, fn in [
            ("repo_root", repo_root),
            ("db_schema_path", db_schema_path),
            ("db_migrations_dir", db_migrations_dir),
            ("taxonomy_dir", taxonomy_dir),
            ("crosswalks_dir", crosswalks_dir),
            ("local_publish_root", local_publish_root),
            ("local_artifact_root", local_artifact_root),
        ]
    }

    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert repo_root() == original["repo_root"]
        assert db_schema_path() == original["db_schema_path"]
        assert db_migrations_dir() == original["db_migrations_dir"]
        assert taxonomy_dir() == original["taxonomy_dir"]
        assert crosswalks_dir() == original["crosswalks_dir"]
        assert local_publish_root() == original["local_publish_root"]
        assert local_artifact_root() == original["local_artifact_root"]
    finally:
        os.chdir(old_cwd)
