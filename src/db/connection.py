from __future__ import annotations

from typing import Any


def build_connection_kwargs(settings: Any) -> dict[str, Any]:
    # getattr lets any duck-typed object work (Pydantic model, dataclass, SimpleNamespace)
    return {
        "host": getattr(settings, "db_host"),
        "port": int(getattr(settings, "db_port")),
        "dbname": getattr(settings, "db_name"),
        "user": getattr(settings, "db_user"),
        "password": getattr(settings, "db_password"),
    }


def connect(settings: Any):
    # Caller owns the connection lifecycle; use as context manager or close explicitly.
    import psycopg  # local import keeps the module importable without psycopg installed

    kwargs = build_connection_kwargs(settings)
    return psycopg.connect(**kwargs)
