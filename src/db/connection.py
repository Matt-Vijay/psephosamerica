"""Minimal Postgres connection helpers.

Exposes:
  build_connection_kwargs(settings) -> dict
  connect(settings)                 -> psycopg.Connection
"""

from __future__ import annotations

from typing import Any


def build_connection_kwargs(settings: Any) -> dict[str, Any]:
    """Build psycopg keyword arguments from a settings object.

    The settings object must expose the following attributes:
      db_host, db_port, db_name, db_user, db_password

    All values are read via getattr so any object (dataclass, Pydantic model,
    plain namespace) works without coupling to a specific settings type.
    """
    return {
        "host": getattr(settings, "db_host"),
        "port": int(getattr(settings, "db_port")),
        "dbname": getattr(settings, "db_name"),
        "user": getattr(settings, "db_user"),
        "password": getattr(settings, "db_password"),
    }


def connect(settings: Any):
    """Return an open psycopg Connection for the given settings.

    Callers are responsible for closing the connection (or using it as a
    context manager).  autocommit is left at the psycopg default (False).
    """
    import psycopg  # local import so the module is importable without psycopg installed

    kwargs = build_connection_kwargs(settings)
    return psycopg.connect(**kwargs)
