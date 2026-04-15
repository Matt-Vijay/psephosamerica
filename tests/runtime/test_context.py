from __future__ import annotations

from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock

import pytest

from src.core.settings import Settings
from src.normalize.taxonomy_runtime import TaxonomyRuntime
from src.runtime.context import (
    build_runtime_context,
    null_issuer_sector_resolver,
    open_connection,
)


def _empty_taxonomy() -> TaxonomyRuntime:
    return TaxonomyRuntime(sectors=[], committee_mappings=[], crp_mappings=[])


def test_null_resolver_always_returns_none():
    assert null_issuer_sector_resolver("ExxonMobil", "XOM") is None
    assert null_issuer_sector_resolver("Unknown Co", None) is None


def test_build_defaults_to_null_resolver():
    ctx = build_runtime_context(Settings(), _empty_taxonomy())
    assert ctx.issuer_sector_resolver is null_issuer_sector_resolver


def test_build_defaults_connect_fn_is_callable():
    ctx = build_runtime_context(Settings(), _empty_taxonomy())
    assert callable(ctx.connect_fn)


def test_build_stores_settings_and_taxonomy():
    settings = Settings()
    taxonomy = _empty_taxonomy()
    ctx = build_runtime_context(settings, taxonomy)
    assert ctx.settings is settings
    assert ctx.taxonomy is taxonomy


def test_build_accepts_custom_resolver():
    def resolver(name: str, ticker: str | None) -> str | None:
        return "energy"

    ctx = build_runtime_context(Settings(), _empty_taxonomy(), issuer_sector_resolver=resolver)
    assert ctx.issuer_sector_resolver is resolver
    assert ctx.issuer_sector_resolver("ExxonMobil", "XOM") == "energy"


def test_build_accepts_custom_connect_fn():
    stub = MagicMock(return_value=object())
    ctx = build_runtime_context(Settings(), _empty_taxonomy(), connect_fn=stub)
    assert ctx.connect_fn is stub


def test_open_connection_delegates_to_connect_fn():
    settings = Settings()
    sentinel = object()
    connect_stub = MagicMock(return_value=sentinel)
    ctx = build_runtime_context(settings, _empty_taxonomy(), connect_fn=connect_stub)

    result = open_connection(ctx)

    connect_stub.assert_called_once_with(settings)
    assert result is sentinel


def test_runtime_context_is_frozen():
    ctx = build_runtime_context(Settings(), _empty_taxonomy())
    with pytest.raises(FrozenInstanceError):
        ctx.settings = Settings()  # type: ignore[misc]
