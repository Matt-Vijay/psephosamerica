from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from src.core.settings import Settings
from src.normalize.taxonomy_runtime import TaxonomyRuntime
from src.query.conflict_inputs import IssuerSectorResolver

ConnectFn = Callable[[Settings], Any]


@dataclass
class _ConnectionSettings:
    db_host: str
    db_port: int | str
    db_name: str
    db_user: str
    db_password: str


def null_issuer_sector_resolver(issuer_name: str, issuer_ticker: str | None) -> None:
    return None


@dataclass(frozen=True)
class RuntimeContext:
    settings: Settings
    taxonomy: TaxonomyRuntime
    issuer_sector_resolver: IssuerSectorResolver
    connect_fn: ConnectFn


def build_runtime_context(
    settings: Settings,
    taxonomy: TaxonomyRuntime,
    *,
    issuer_sector_resolver: IssuerSectorResolver | None = None,
    connect_fn: ConnectFn | None = None,
) -> RuntimeContext:
    from src.db.connection import connect as _connect

    def _default_connect(settings: Settings) -> Any:
        return _connect(
            _ConnectionSettings(
                db_host=settings.db_host,
                db_port=settings.db_port,
                db_name=settings.db_name,
                db_user=settings.db_user,
                db_password=settings.db_password,
            )
        )

    return RuntimeContext(
        settings=settings,
        taxonomy=taxonomy,
        issuer_sector_resolver=issuer_sector_resolver
        if issuer_sector_resolver is not None
        else null_issuer_sector_resolver,
        connect_fn=connect_fn if connect_fn is not None else _default_connect,
    )


def open_connection(ctx: RuntimeContext) -> Any:
    return ctx.connect_fn(ctx.settings)
