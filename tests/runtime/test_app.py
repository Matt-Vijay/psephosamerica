from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import MagicMock, sentinel

import pytest

from src.core.settings import Settings
from src.normalize.taxonomy_runtime import TaxonomyRuntime, load_taxonomy_runtime
from src.runtime.app import OpenPactRuntime, build_runtime, open_runtime_connection
from src.runtime.context import build_runtime_context
from src.runtime.paths import local_publish_root, repo_root


def _stub_connect(settings: Settings) -> object:
    return sentinel.conn


# ---------------------------------------------------------------------------
# build_runtime
# ---------------------------------------------------------------------------

def test_build_runtime_returns_openpact_runtime():
    rt = build_runtime(connect_fn=_stub_connect)
    assert isinstance(rt, OpenPactRuntime)


def test_build_runtime_repo_root_matches_paths():
    rt = build_runtime(connect_fn=_stub_connect)
    assert rt.repo_root == repo_root()


def test_build_runtime_publish_root_default():
    rt = build_runtime(connect_fn=_stub_connect)
    assert rt.publish_root == local_publish_root()


def test_build_runtime_custom_publish_root(tmp_path: Path):
    rt = build_runtime(publish_root=tmp_path, connect_fn=_stub_connect)
    assert rt.publish_root == tmp_path


def test_build_runtime_stores_settings():
    settings = Settings()
    rt = build_runtime(settings, connect_fn=_stub_connect)
    assert rt.context.settings is settings


def test_build_runtime_default_settings_when_none():
    rt = build_runtime(connect_fn=_stub_connect)
    assert isinstance(rt.context.settings, Settings)


def test_build_runtime_taxonomy_loaded():
    rt = build_runtime(connect_fn=_stub_connect)
    assert isinstance(rt.context.taxonomy, TaxonomyRuntime)
    assert len(rt.context.taxonomy.sectors) > 0


def test_build_runtime_cwd_independent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import os

    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        rt = build_runtime(connect_fn=_stub_connect)
        assert rt.repo_root == repo_root()
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# open_runtime_connection
# ---------------------------------------------------------------------------

def test_open_runtime_connection_delegates_to_connect_fn():
    conn_mock = MagicMock(return_value=sentinel.conn)
    settings = Settings()
    taxonomy = load_taxonomy_runtime(repo_root() / "data")
    context = build_runtime_context(settings, taxonomy, connect_fn=conn_mock)
    rt = OpenPactRuntime(context=context, repo_root=repo_root(), publish_root=local_publish_root())

    result = open_runtime_connection(rt)

    conn_mock.assert_called_once_with(settings)
    assert result is sentinel.conn


# ---------------------------------------------------------------------------
# OpenPactRuntime frozen invariant
# ---------------------------------------------------------------------------

def test_openpact_runtime_is_frozen():
    rt = build_runtime(connect_fn=_stub_connect)
    with pytest.raises(FrozenInstanceError):
        rt.repo_root = Path("/other")  # type: ignore[misc]
