from __future__ import annotations

from typing import Any, Protocol, TypedDict, runtime_checkable


@runtime_checkable
class DBSettings(Protocol):
    db_host: str
    db_port: int | str
    db_name: str
    db_user: str
    db_password: str


class ConnectionKwargs(TypedDict):
    host: str
    port: int
    dbname: str
    user: str
    password: str


def build_connection_kwargs(settings: DBSettings) -> ConnectionKwargs:
    # getattr lets any duck-typed object work (Pydantic model, dataclass, SimpleNamespace)
    return {
        "host": settings.db_host,
        "port": int(settings.db_port),
        "dbname": settings.db_name,
        "user": settings.db_user,
        "password": settings.db_password,
    }


def connect(settings: DBSettings) -> Any:
    # Caller owns the connection lifecycle; use as context manager or close explicitly.
    import psycopg  # local import keeps the module importable without psycopg installed

    kwargs = build_connection_kwargs(settings)
    return psycopg.connect(
        host=kwargs["host"],
        port=kwargs["port"],
        dbname=kwargs["dbname"],
        user=kwargs["user"],
        password=kwargs["password"],
        autocommit=False,
    )
