"""Database access: connection building, schema bootstrap, lookups, repositories."""

from src.db.bootstrap import apply_sql, read_migration_sql, read_schema_sql
from src.db.connection import build_connection_kwargs, connect
from src.db.lookups import LookupBuildError, LookupBundle, build_lookup_bundle
from src.db.repositories import execute_many, execute_one, fetch_all
from src.db.runtime_lookups import (
    fetch_committee_lookup_rows,
    fetch_fec_committee_lookup_rows,
    fetch_financial_disclosure_lookup_rows,
    fetch_member_lookup_rows,
    load_lookup_bundle,
)

__all__ = [
    "LookupBuildError",
    "LookupBundle",
    "build_connection_kwargs",
    "build_lookup_bundle",
    "connect",
    "apply_sql",
    "execute_one",
    "execute_many",
    "fetch_all",
    "fetch_committee_lookup_rows",
    "fetch_fec_committee_lookup_rows",
    "fetch_financial_disclosure_lookup_rows",
    "fetch_member_lookup_rows",
    "load_lookup_bundle",
    "read_migration_sql",
    "read_schema_sql",
]
