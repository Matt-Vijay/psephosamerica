"""Edges that fall out of a parsed BILLSTATUS record: classification + sponsorship.

A :class:`~src.graph.ingest.govinfo_billstatus.BillStatus` already carries the
relations a bill participates in. This module turns them into leakage-gated
:class:`~src.graph.edges.GraphEdge` facts:

* **Classification** -- ``bill -> policy_area`` (one CRS policy area) and
  ``bill -> legislative_subject`` (many CRS subjects). These give *every* bill a
  real topic, killing the sector-coverage gap Track B hit (only ~10% of bills had
  a sector). Because they are edges *from* the bill, they also flow into the
  bill's dossier context -- enriching its embedding with its own subject text.
* **Sponsorship** -- ``member -> bill`` sponsor + cosponsor edges (reusing
  :func:`~src.graph.ingest.sponsorships.sponsorship_edge`), keyed off the bill's
  bioguide ids via an injected resolver to canonical Person IDs. Cosponsorships
  carry their own action date.

All ids are minted by the same deterministic schemes the rest of the graph uses,
so the edges join cleanly to the canonical bill + person nodes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime

from src.graph.committees import CommitteeRef
from src.graph.edges import GraphEdge
from src.graph.entity_resolution.ids import stable_id
from src.graph.ingest.govinfo_billstatus import BillStatus, canonical_bill_id
from src.graph.ingest.sponsorships import sponsorship_edge, sponsorship_provenance
from src.graph.provenance import ProvenanceEnvelope

# bioguide id -> canonical_person_id (or None when the member is unresolved).
BioguideResolver = Callable[[str], str | None]


def subject_id(name: str) -> str:
    """Stable ``csub-<digest>`` id for a CRS policy area / legislative subject."""
    return stable_id(["us-congress-subject", name.strip().lower()], "csub")


def classification_edges_from_fields(
    canonical_id: str,
    policy_area: str | None,
    subjects: Iterable[str],
    *,
    provenance: ProvenanceEnvelope,
) -> list[GraphEdge]:
    """``bill -> policy_area`` + ``bill -> legislative_subject`` edges from plain fields.

    Lets a downstream pass emit sector edges straight from the content sidecar
    (which carries ``policy_area`` + ``subjects`` without the full BillStatus).
    """
    edges: list[GraphEdge] = []
    if policy_area:
        edges.append(
            GraphEdge(
                edge_type="policy_area",
                src_id=canonical_id,
                dst_id=subject_id(policy_area),
                attributes={"name": policy_area},
                provenance=provenance,
            )
        )
    seen: set[str] = set()
    for subject in subjects:
        target = subject_id(subject)
        if target in seen:
            continue
        seen.add(target)
        edges.append(
            GraphEdge(
                edge_type="legislative_subject",
                src_id=canonical_id,
                dst_id=target,
                attributes={"name": subject},
                provenance=provenance,
            )
        )
    return edges


def classification_edges(status: BillStatus, *, provenance: ProvenanceEnvelope) -> list[GraphEdge]:
    """``bill -> policy_area`` + ``bill -> legislative_subject`` edges (CRS topics)."""
    return classification_edges_from_fields(
        canonical_bill_id(status), status.policy_area, status.subjects, provenance=provenance
    )


def committee_referral_edges(
    status: BillStatus, *, provenance: ProvenanceEnvelope
) -> list[GraphEdge]:
    """``bill -> committee`` referral edges (one per referred committee w/ a code).

    The committee node is the canonical :class:`~src.graph.committees.CommitteeRef`
    (``cc-<digest>``) minted from the Thomas ``systemCode`` + chamber, so the edge
    joins to the same committee the committee-membership adapter uses. Committees
    without a system code (or with an unparseable chamber) are skipped.
    """
    bill = canonical_bill_id(status)
    edges: list[GraphEdge] = []
    seen: set[str] = set()
    for committee in status.committees:
        if not committee.system_code:
            continue
        try:
            ref = CommitteeRef(
                jurisdiction_id="us-congress",
                code=committee.system_code,
                chamber=committee.chamber,  # type: ignore[arg-type]
            )
        except ValueError:
            continue
        target = ref.canonical_id
        if target in seen:
            continue
        seen.add(target)
        edges.append(
            GraphEdge(
                edge_type="referred_to",
                src_id=bill,
                dst_id=target,
                attributes={"name": committee.name},
                provenance=provenance,
            )
        )
    return edges


def sponsorship_edges(
    status: BillStatus,
    *,
    resolve_bioguide: BioguideResolver,
    source_url: str,
    content_sha256: str,
    first_observed_at: datetime,
) -> list[GraphEdge]:
    """Sponsor + cosponsor ``member -> bill`` edges for the bill's resolved members.

    Unresolved bioguides (no canonical Person ID yet) are skipped. The sponsor's
    action date is the bill's introduced date; each cosponsor uses its own
    ``sponsorshipDate`` when present, else the introduced date.
    """
    bill = canonical_bill_id(status)
    introduced = status.introduced_date
    edges: list[GraphEdge] = []

    def _edge(bioguide: str, role: str, action: object) -> GraphEdge | None:
        member = resolve_bioguide(bioguide)
        if member is None or action is None:
            return None
        provenance = sponsorship_provenance(
            source_url=source_url,
            content_sha256=content_sha256,
            action_date=action,  # type: ignore[arg-type]
            first_observed_at=first_observed_at,
        )
        return sponsorship_edge(
            member_canonical_id=member,
            bill_canonical_id=bill,
            role=role,
            provenance=provenance,
        )

    for sponsor in status.sponsors:
        edge = _edge(sponsor.bioguide_id, "sponsor", introduced)
        if edge is not None:
            edges.append(edge)
    for cosponsor in status.cosponsors:
        edge = _edge(cosponsor.bioguide_id, "cosponsor", cosponsor.sponsorship_date or introduced)
        if edge is not None:
            edges.append(edge)
    return edges
