"""FK resolver maps and resolver builders for recompute persistence.

Loads the three lookup dicts needed to resolve FK hint keys in recompute
load-plan rows, then wraps them as a :data:`~src.db.load_executor.Resolvers`
dict that plugs directly into ``execute_load_plan(..., resolvers=...)``.

Hint keys produced by this module
----------------------------------
``_member_bioguide_id``
    Resolves ``bioguide_id`` → ``member.id``; target column ``member_id``.

``_financial_disclosure_source_record_id``
    Resolves ``source_record_id`` → ``financial_disclosure.id``;
    target column ``financial_disclosure_id``.

``_rule_fire_source_record_id``
    Resolves ``source_record_id`` → ``rule_fire.id``;
    target column ``rule_fire_id``.

Public API
----------
load_recompute_resolver_maps(conn) -> RecomputeResolverMaps
build_recompute_resolvers(maps) -> Resolvers
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.db.load_executor import Resolvers
from src.db.repositories import fetch_all

# ---------------------------------------------------------------------------
# SQL — fetch only the columns each map needs
# ---------------------------------------------------------------------------

_MEMBER_SQL = """
SELECT id, bioguide_id
FROM member
WHERE bioguide_id IS NOT NULL
"""

_DISCLOSURE_SQL = """
SELECT id, source_record_id
FROM financial_disclosure
WHERE source_record_id IS NOT NULL
"""

_RULE_FIRE_SQL = """
SELECT id, source_record_id
FROM rule_fire
WHERE source_record_id IS NOT NULL
"""

# ---------------------------------------------------------------------------
# Map container
# ---------------------------------------------------------------------------


@dataclass
class RecomputeResolverMaps:
    """Pre-fetched lookup dicts for the three recompute FK resolution paths."""

    member_by_bioguide: dict[str, int] = field(default_factory=dict)
    # bioguide_id → member.id

    disclosure_by_source_record: dict[str, int] = field(default_factory=dict)
    # financial_disclosure.source_record_id → financial_disclosure.id

    rule_fire_by_source_record: dict[str, int] = field(default_factory=dict)
    # rule_fire.source_record_id → rule_fire.id


# ---------------------------------------------------------------------------
# Individual row fetchers
# ---------------------------------------------------------------------------


def _fetch_member_by_bioguide(conn) -> dict[str, int]:
    rows = fetch_all(conn, _MEMBER_SQL)
    return {r["bioguide_id"]: r["id"] for r in rows}


def _fetch_disclosure_by_source_record(conn) -> dict[str, int]:
    rows = fetch_all(conn, _DISCLOSURE_SQL)
    return {r["source_record_id"]: r["id"] for r in rows}


def _fetch_rule_fire_by_source_record(conn) -> dict[str, int]:
    rows = fetch_all(conn, _RULE_FIRE_SQL)
    return {r["source_record_id"]: r["id"] for r in rows}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_recompute_resolver_maps(conn) -> RecomputeResolverMaps:
    """Fetch the three lookup maps needed for recompute FK resolution.

    Makes three small sequential queries; each touches only the columns the
    corresponding map needs.
    """
    return RecomputeResolverMaps(
        member_by_bioguide=_fetch_member_by_bioguide(conn),
        disclosure_by_source_record=_fetch_disclosure_by_source_record(conn),
        rule_fire_by_source_record=_fetch_rule_fire_by_source_record(conn),
    )


def build_recompute_resolvers(maps: RecomputeResolverMaps) -> Resolvers:
    """Return a :data:`~src.db.load_executor.Resolvers` dict built from *maps*.

    The returned dict plugs directly into
    ``execute_load_plan(..., resolvers=...)``.

    Resolver returns ``None`` when the hint value is absent from the map;
    ``load_executor`` will write ``None`` into the target FK column and the
    caller is responsible for deciding whether that constitutes an error.
    """
    m = maps.member_by_bioguide
    d = maps.disclosure_by_source_record
    rf = maps.rule_fire_by_source_record

    return {
        "_member_bioguide_id": (
            "member_id",
            lambda row: m.get(row["_member_bioguide_id"]),
        ),
        "_financial_disclosure_source_record_id": (
            "financial_disclosure_id",
            lambda row: d.get(row["_financial_disclosure_source_record_id"]),
        ),
        "_rule_fire_source_record_id": (
            "rule_fire_id",
            lambda row: rf.get(row["_rule_fire_source_record_id"]),
        ),
    }
