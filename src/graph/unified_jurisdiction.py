"""The canonical jurisdiction join — LOCUS ordinances <-> officials (deliverable #3).

The unified graph already shares one node schema (``EntityResolutionOutput``) and
one bill-ID scheme (``BillRef``) across federal, state, and municipal tiers, so
LOCUS ordinances, CA bills, and federal bills are the *same* kind of node. What
this module adds is the cross-tier *connectivity* the v8 scorecard measures: a
typed crosswalk that joins, through the canonical jurisdiction code,

* a **LOCUS ordinance**'s jurisdiction (``us-ca-city-oakland``), to
* the **Legistar client** governing that jurisdiction (``oakland``), to
* the **municipal officials** resolved for that client.

Each :class:`JurisdictionBridge` is the join row for one canonical jurisdiction:
the LOCUS ordinance count, the Legistar client + level, and the count of resolved
officials — i.e. how a city's *ordinances* connect to the people who *enact* them.
The bridge is the auditable cross-tier link the scorecard counts; the join key is
the canonical jurisdiction code (zero false merges: a bridge exists only on an
exact-or-folded jurisdiction match, never a guess).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from src.graph.entity_resolution.jurisdiction_link import (
    JurisdictionUniverse,
    resolve_jurisdiction,
)
from src.graph.entity_resolution.jurisdiction_report import jurisdiction_of_code
from src.graph.ingest.legistar_registry import LEGISTAR_CLIENTS, LegistarClient


def legistar_client_by_jurisdiction() -> dict[str, LegistarClient]:
    """Index Legistar clients by their canonical jurisdiction code."""
    return {client.jurisdiction().code: client for client in LEGISTAR_CLIENTS}


def officials_per_client(municipal_external_ids: Iterable[str]) -> dict[str, int]:
    """Count resolved officials per Legistar client from ``legistar:<client>:<id>`` IDs.

    ``municipal_external_ids`` is the flattened ``external_ids`` of the municipal
    person nodes; only ``legistar:`` keys are counted, grouped by client.
    """
    counts: Counter[str] = Counter()
    for external_id in municipal_external_ids:
        parts = external_id.split(":")
        if len(parts) >= 3 and parts[0] == "legistar":
            counts[parts[1]] += 1
    return dict(counts)


@dataclass(frozen=True)
class JurisdictionBridge:
    """One canonical jurisdiction's cross-tier join row."""

    canonical_code: str
    legistar_client: str | None
    level: str
    locus_ordinances: int
    officials: int

    @property
    def is_connected(self) -> bool:
        """True when this jurisdiction links LOCUS ordinances to a governing body."""
        return self.legistar_client is not None and self.locus_ordinances > 0


def build_jurisdiction_bridges(
    *,
    locus_jurisdiction_counts: dict[str, int],
    municipal_external_ids: Iterable[str],
    universe: JurisdictionUniverse | None = None,
) -> list[JurisdictionBridge]:
    """Join LOCUS jurisdictions to Legistar clients + official counts.

    ``locus_jurisdiction_counts`` maps a LOCUS jurisdiction code to its ordinance
    count (from the corpus). For each, resolve to a canonical jurisdiction; if it
    links to a Legistar client, attach that client and its resolved-official
    count. Only resolved (exact/folded) jurisdictions become connected bridges;
    minted/ambiguous ones are emitted with ``legistar_client=None`` (still part of
    the graph, just not yet cross-linked).
    """
    clients = legistar_client_by_jurisdiction()
    official_counts = officials_per_client(municipal_external_ids)
    if universe is None:
        universe = JurisdictionUniverse()
        for registry_client in LEGISTAR_CLIENTS:
            universe.add(registry_client.jurisdiction())

    bridges: list[JurisdictionBridge] = []
    for code, ordinance_count in sorted(locus_jurisdiction_counts.items()):
        jurisdiction = jurisdiction_of_code(code)
        if jurisdiction is None:
            continue
        match = resolve_jurisdiction(jurisdiction, universe)
        canonical = match.canonical_code if (match.is_linked and match.canonical_code) else code
        client = clients.get(canonical) if match.is_linked else None
        client_code = client.client if client is not None else None
        officials = official_counts.get(client_code, 0) if client_code is not None else 0
        bridges.append(
            JurisdictionBridge(
                canonical_code=canonical,
                legistar_client=client_code,
                level=jurisdiction.level,
                locus_ordinances=ordinance_count,
                officials=officials,
            )
        )
    return bridges


@dataclass(frozen=True)
class BridgeSummary:
    """Aggregate connectivity over a set of jurisdiction bridges."""

    total_jurisdictions: int
    connected_jurisdictions: int
    connected_ordinances: int
    connected_officials: int


def summarize_bridges(bridges: Iterable[JurisdictionBridge]) -> BridgeSummary:
    """Aggregate the cross-tier connectivity the scorecard reports."""
    bridges = list(bridges)
    connected = [b for b in bridges if b.is_connected]
    return BridgeSummary(
        total_jurisdictions=len(bridges),
        connected_jurisdictions=len(connected),
        connected_ordinances=sum(b.locus_ordinances for b in connected),
        connected_officials=sum(b.officials for b in connected),
    )
