"""Adapt CourtListener (Free Law Project) records into the governance graph.

CourtListener (``courtlistener.com``) is Free Law Project's public, free archive
of U.S. case law: every federal and many state courts, the opinions they issue,
and the judges/justices who author them. Its REST API v4
(``courtlistener.com/api/rest/v4``) is open by default — the keyless ``search``
endpoint (``type=o``) returns cluster-level opinion records, and the ``people``
and ``courts`` endpoints return judges and courts — so this source runs with **no
login** (a free token only raises rate limits; it is not required).

This module is a pure adapter (no network): it parses the row shapes the runner
streams from the API into canonical graph pieces, closing the *judicial* face of
the Person x Bill x Org graph:

* a **court** :class:`SourceRecord` (``entity_type='org'``), keyed on its
  CourtListener court id (``courtlistener_court:scotus``) — courts are durable
  governance orgs/jurisdictions, so they resolve across every opinion they issue;
* a **judge/justice** :class:`SourceRecord` (``entity_type='person'``), keyed on
  the Federal Judicial Center id (``fjc:NNNN``, the durable cross-source key that
  links a judge to their Senate confirmation/nomination already in the graph)
  when present, always also on the CourtListener person id
  (``courtlistener_person:NNNN``) so a judge resolves across all their opinions;
* a :class:`CourtOpinion` — a *new entity kind* (``court_opinion``) emitted as an
  edge endpoint rather than a clustered person/org row — with edges:
  - judge -> opinion ``authored_opinion`` / ``joined_opinion``,
  - opinion -> court ``decided_by``,
  - opinion -> opinion ``cites_opinion`` (CourtListener's ``cites`` graph), and
  - opinion -> bill ``cites_statute`` when a U.S. Code / Public Law citation is
    derivable from the available text (see :func:`statute_refs_in`).

Leakage discipline: an opinion is public when it is *filed/decided*
(``date_filed``), which is also its earliest knowable instant, so
:func:`opinion_provenance` stamps both ``valid_from`` and ``known_at`` there (the
conservative, leakage-safe choice). Malformed rows parse to ``None`` and are
skipped, never fabricated; any field the keyless API does not expose (e.g.
full-text statute citations, which need the gated opinion-text endpoint) is
documented as a gate rather than invented.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from src.graph.bills import BillRef
from src.graph.edges import GraphEdge
from src.graph.entity_resolution.records import ExternalId, SourceRecord
from src.graph.provenance import ProvenanceEnvelope

COURTLISTENER_SOURCE_SYSTEM = "courtlistener"
COURTLISTENER_BASE_URL = "https://www.courtlistener.com"
COURTLISTENER_API_URL = "https://www.courtlistener.com/api/rest/v4"

#: The opinion entity kind we mint. Opinions are not people/orgs, so they are not
#: run through the probabilistic linker; they resolve deterministically on their
#: CourtListener cluster id and live as edge endpoints + a sidecar row.
COURT_OPINION_ENTITY_TYPE = "court_opinion"

# A U.S. Code citation in opinion text / metadata, e.g. "42 U.S.C. § 1983",
# "18 U.S.C. 924(c)", "5 U. S. C. §706". Captures title + section.
_USC_RE = re.compile(
    r"\b(\d{1,2})\s*U\.?\s?S\.?\s?C\.?\s*(?:§+\s*)?(\d+[A-Za-z]*(?:-\d+)?)",
    re.IGNORECASE,
)
# A Public Law citation, e.g. "Pub. L. No. 111-148", "Public Law 117-103".
_PUBLIC_LAW_RE = re.compile(
    r"\bpub(?:lic)?\.?\s*l(?:aw)?\.?\s*(?:no\.?\s*)?(\d{2,3})[-–](\d+)",
    re.IGNORECASE,
)


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _opt_int(value: Any) -> int | None:
    """Coerce a possibly-None/garbage JSON value to ``int``, else ``None``."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ── Courts ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Court:
    """One parsed CourtListener court (a governance org/jurisdiction)."""

    court_id: str  # e.g. "scotus", "ca9", "cal" — the durable CourtListener key
    full_name: str
    citation_string: str | None
    jurisdiction: str | None  # CourtListener jurisdiction code: F (federal), S (state)...
    start_date: date | None
    url: str | None


def parse_court(record: dict[str, Any]) -> Court | None:
    """Parse a ``/courts/`` row into a :class:`Court`, or ``None`` to skip.

    A row is skipped when it lacks the identity needed to link: a court id and a
    full name. The start date (when present) anchors the court org's valid-time.
    """
    court_id = _clean(record.get("id"))
    if not court_id:
        return None
    full_name = _clean(record.get("full_name")) or _clean(record.get("short_name"))
    if not full_name:
        return None
    start_raw = _clean(record.get("start_date"))
    start_date: date | None = None
    if start_raw:
        try:
            start_date = date.fromisoformat(start_raw[:10])
        except ValueError:
            start_date = None
    return Court(
        court_id=court_id,
        full_name=full_name,
        citation_string=_clean(record.get("citation_string")) or None,
        jurisdiction=_clean(record.get("jurisdiction")) or None,
        start_date=start_date,
        url=_clean(record.get("url")) or None,
    )


def _court_jurisdiction(court: Court) -> str:
    """Map a CourtListener jurisdiction code to the graph's jurisdiction string.

    Federal courts ('F', 'FB', 'FD', ...) anchor to ``us``; everything else (state,
    territorial, tribal, special) is recorded under ``us`` as well since they are
    U.S. public courts, with the CourtListener code carried as ``region`` context.
    """
    return "us"


def parse_court_org(court: Court, *, provenance: ProvenanceEnvelope) -> SourceRecord:
    """Parse a court into an ``org`` source record keyed on its CourtListener id."""
    return SourceRecord(
        source_system=COURTLISTENER_SOURCE_SYSTEM,
        source_record_id=f"court:{court.court_id}",
        entity_type="org",
        display_name=court.full_name,
        external_ids=(ExternalId(system="courtlistener_court", value=court.court_id),),
        jurisdiction=_court_jurisdiction(court),
        region=court.jurisdiction,
        provenance=provenance,
    )


def court_provenance(
    court: Court,
    *,
    source_url: str,
    content_sha256: str,
    first_observed_at: datetime,
) -> ProvenanceEnvelope:
    """Provenance for a court org; ``known_at`` is the first-observed instant.

    A court's existence has no single "knowable" instant in the API (the start
    date can predate any reliable disclosure record and is sometimes null), so the
    leakage stamp is the conservative first-observed time. ``valid_from`` is the
    court's start date when known, else the observation date.
    """
    valid_from = court.start_date or first_observed_at.date()
    return ProvenanceEnvelope(
        source_url=source_url,
        content_sha256=content_sha256,
        first_observed_at=first_observed_at,
        valid_from=valid_from,
        known_at=first_observed_at,
    )


# ── Judges ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Judge:
    """One parsed CourtListener person who is a judge/justice."""

    person_id: int  # CourtListener person id (durable key)
    display_name: str
    fjc_id: str | None  # Federal Judicial Center id — links to nominations/votes
    slug: str | None
    dob_state: str | None


def _judge_display_name(record: dict[str, Any]) -> str:
    parts = [
        _clean(record.get("name_first")),
        _clean(record.get("name_middle")),
        _clean(record.get("name_last")),
    ]
    name = " ".join(p for p in parts if p)
    suffix = _clean(record.get("name_suffix"))
    if suffix:
        name = f"{name} {suffix}"
    return name.strip()


def parse_judge(record: dict[str, Any]) -> Judge | None:
    """Parse a ``/people/`` row into a :class:`Judge`, or ``None`` to skip.

    A row is skipped when it has no person id or no usable name. ``fjc_id`` (when
    present) is the durable Federal Judicial Center identifier, the join key to a
    judge's Senate confirmation/nomination elsewhere in the graph.
    """
    person_id = _opt_int(record.get("id"))
    if person_id is None:
        return None
    name = _judge_display_name(record)
    if not name:
        return None
    fjc_raw = record.get("fjc_id")
    fjc_id = str(fjc_raw).strip() if fjc_raw not in (None, "") else None
    return Judge(
        person_id=person_id,
        display_name=name,
        fjc_id=fjc_id,
        slug=_clean(record.get("slug")) or None,
        dob_state=_clean(record.get("dob_state")) or None,
    )


def _judge_external_ids(judge: Judge) -> tuple[ExternalId, ...]:
    """Strong external keys for a judge, strongest (FJC) first.

    The CourtListener person id always anchors the judge to their own opinions;
    the FJC id (when present) is the cross-source key that lets the resolver merge
    the judge with the same person seen in nomination/confirmation sources.
    """
    ids: list[ExternalId] = []
    if judge.fjc_id:
        ids.append(ExternalId(system="fjc", value=judge.fjc_id))
    ids.append(ExternalId(system="courtlistener_person", value=str(judge.person_id)))
    return tuple(ids)


def parse_judge_person(judge: Judge, *, provenance: ProvenanceEnvelope) -> SourceRecord:
    """Parse a judge into a ``person`` source record for the entity resolver."""
    return SourceRecord(
        source_system=COURTLISTENER_SOURCE_SYSTEM,
        source_record_id=f"person:{judge.person_id}",
        entity_type="person",
        display_name=judge.display_name,
        external_ids=_judge_external_ids(judge),
        jurisdiction="us",
        region=judge.dob_state,
        provenance=provenance,
    )


def judge_provenance(
    *,
    source_url: str,
    content_sha256: str,
    first_observed_at: datetime,
) -> ProvenanceEnvelope:
    """Provenance for a judge person; ``known_at`` is the first-observed instant.

    A judge's biographical record carries no single disclosure instant, so the
    leakage stamp is the conservative observation time (a judge is at least as old
    as the first opinion that references them, but we do not infer that here).
    """
    return ProvenanceEnvelope(
        source_url=source_url,
        content_sha256=content_sha256,
        first_observed_at=first_observed_at,
        valid_from=first_observed_at.date(),
        known_at=first_observed_at,
    )


# ── Opinions ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OpinionAuthorship:
    """One opinion within a cluster: its author + the judges who joined it."""

    opinion_id: int
    opinion_type: str | None  # e.g. "combined-opinion", "lead", "dissent", "concurrence"
    author_id: int | None  # CourtListener person id of the authoring judge
    joined_by_ids: tuple[int, ...]
    cited_opinion_ids: tuple[int, ...]  # CourtListener opinion ids this opinion cites


@dataclass(frozen=True)
class CourtOpinion:
    """One parsed opinion cluster — the ``court_opinion`` entity.

    Keyed on the CourtListener cluster id (a cluster groups the lead opinion with
    its dissents/concurrences for one decision); carries the court, the decision
    date, the case name/docket/reporter citations, the per-opinion authorship +
    citation graph, and the panel/non-participating judge ids.
    """

    cluster_id: int
    case_name: str
    court_id: str
    date_filed: date
    docket_number: str | None
    reporter_citations: tuple[str, ...]
    judge_text: str | None  # free-text judge attribution (fallback display)
    panel_ids: tuple[int, ...]
    non_participating_ids: tuple[int, ...]
    opinions: tuple[OpinionAuthorship, ...]
    statute_refs: tuple[BillRef, ...]  # U.S. Code / Public Law cites derivable from text


def _int_tuple(value: Any) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    out: list[int] = []
    for item in value:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return tuple(out)


def _reporter_citations(value: Any) -> tuple[str, ...]:
    """Normalize the cluster's ``citation`` field into reporter-cite strings.

    The search endpoint returns reporter citations either as plain strings or as
    ``{volume, reporter, page}`` objects; both are flattened to a display string.
    """
    if not isinstance(value, (list, tuple)):
        return ()
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
            if text:
                out.append(text)
        elif isinstance(item, dict):
            vol = _clean(item.get("volume"))
            rep = _clean(item.get("reporter"))
            page = _clean(item.get("page"))
            text = " ".join(p for p in (vol, rep, page) if p).strip()
            if text:
                out.append(text)
    return tuple(out)


def statute_refs_in(*texts: str) -> tuple[BillRef, ...]:
    """Distinct U.S. Code / Public Law :class:`BillRef`s cited in ``texts``.

    A ``NN U.S.C. § MMM`` cite resolves to a federal ``us-code`` bill ref keyed
    ``usc-<title>-<section>``; a ``Pub. L. NNN-MM`` cite resolves to the enacting
    congress's ``pl-NNN-MM`` (matching the USASpending public-law handle). Order is
    stable and duplicates collapse. Returns ``()`` when no statute is citable —
    note the keyless search endpoint exposes only metadata + case name, so most
    statute cites live in the gated opinion *full text* (a documented gate, not a
    fabrication).
    """
    seen: dict[str, BillRef] = {}
    for text in texts:
        if not text:
            continue
        for match in _USC_RE.finditer(text):
            title = int(match.group(1))
            section = match.group(2).lower()
            ref = BillRef(
                jurisdiction_id="us-code",
                session_id=str(title),
                identifier=f"usc-{title}-{section}",
            )
            seen.setdefault(ref.identifier, ref)
        for match in _PUBLIC_LAW_RE.finditer(text):
            congress = int(match.group(1))
            number = int(match.group(2))
            ref = BillRef(
                jurisdiction_id="us-congress",
                session_id=str(congress),
                identifier=f"pl-{congress}-{number}",
            )
            seen.setdefault(ref.identifier, ref)
    return tuple(seen.values())


def parse_court_opinion(record: dict[str, Any]) -> CourtOpinion | None:
    """Parse a keyless ``search?type=o`` cluster row into a :class:`CourtOpinion`.

    A row is skipped when it lacks the identity needed to link: a cluster id, a
    court id, and a parseable ``dateFiled``. Per-opinion authorship + citation
    graph is read from the nested ``opinions`` array; statute refs are derived
    from whatever text the row exposes (case name + reporter cites).
    """
    cluster_id = _opt_int(record.get("cluster_id"))
    if cluster_id is None:
        return None
    court_id = _clean(record.get("court_id"))
    if not court_id:
        return None
    raw_date = _clean(record.get("dateFiled")) or _clean(record.get("date_filed"))
    try:
        date_filed = date.fromisoformat(raw_date[:10])
    except ValueError:
        return None
    case_name = (
        _clean(record.get("caseName"))
        or _clean(record.get("caseNameFull"))
        or _clean(record.get("case_name"))
        or f"Opinion {cluster_id}"
    )
    opinions: list[OpinionAuthorship] = []
    for raw in record.get("opinions") or []:
        if not isinstance(raw, dict):
            continue
        op_id = _opt_int(raw.get("id"))
        if op_id is None:
            continue
        author_id = _opt_int(raw.get("author_id"))
        opinions.append(
            OpinionAuthorship(
                opinion_id=op_id,
                opinion_type=_clean(raw.get("type")) or None,
                author_id=author_id,
                joined_by_ids=_int_tuple(raw.get("joined_by_ids")),
                cited_opinion_ids=_int_tuple(raw.get("cites")),
            )
        )
    reporter = _reporter_citations(record.get("citation"))
    statute_refs = statute_refs_in(case_name, *reporter)
    return CourtOpinion(
        cluster_id=cluster_id,
        case_name=case_name,
        court_id=court_id,
        date_filed=date_filed,
        docket_number=_clean(record.get("docketNumber")) or None,
        reporter_citations=reporter,
        judge_text=_clean(record.get("judge")) or None,
        panel_ids=_int_tuple(record.get("panel_ids")),
        non_participating_ids=_int_tuple(record.get("non_participating_judge_ids")),
        opinions=tuple(opinions),
        statute_refs=statute_refs,
    )


def opinion_canonical_id(cluster_id: int) -> str:
    """The deterministic canonical id for a court_opinion (``co-<cluster_id>``)."""
    return f"co-{cluster_id}"


def opinion_url(opinion: CourtOpinion) -> str:
    """The public CourtListener URL of the opinion cluster."""
    return f"{COURTLISTENER_BASE_URL}/opinion/{opinion.cluster_id}/"


def opinion_sha(opinion: CourtOpinion) -> str:
    """Content-address of the opinion's identity + decision date (stable)."""
    payload = f"{opinion.cluster_id}|{opinion.court_id}|{opinion.date_filed.isoformat()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def opinion_provenance(
    opinion: CourtOpinion,
    *,
    source_url: str,
    content_sha256: str,
    first_observed_at: datetime,
    known_at: datetime | None = None,
) -> ProvenanceEnvelope:
    """Provenance for an opinion; ``known_at`` defaults to the decision date.

    An opinion becomes true and publicly knowable on the date it is filed/decided
    (there is no earlier "posted" instant for the decision itself), so the leakage
    stamp is conservatively the filing date at UTC midnight.
    """
    default_known = datetime(
        opinion.date_filed.year, opinion.date_filed.month, opinion.date_filed.day, tzinfo=UTC
    )
    return ProvenanceEnvelope(
        source_url=source_url,
        content_sha256=content_sha256,
        first_observed_at=first_observed_at,
        valid_from=opinion.date_filed,
        known_at=known_at if known_at is not None else default_known,
    )


# ── Edges ──────────────────────────────────────────────────────────────────


def opinion_court_edge(
    *,
    opinion: CourtOpinion,
    court_canonical_id: str,
    provenance: ProvenanceEnvelope,
) -> GraphEdge:
    """Build the opinion -> court ``decided_by`` edge."""
    attributes: dict[str, str] = {"case_name": opinion.case_name}
    if opinion.docket_number:
        attributes["docket_number"] = opinion.docket_number
    if opinion.reporter_citations:
        attributes["reporter_citation"] = opinion.reporter_citations[0]
    return GraphEdge(
        edge_type="decided_by",
        src_id=opinion_canonical_id(opinion.cluster_id),
        dst_id=court_canonical_id,
        attributes=attributes,
        external_key=str(opinion.cluster_id),
        provenance=provenance,
    )


def judge_opinion_edges(
    *,
    opinion: CourtOpinion,
    authorship: OpinionAuthorship,
    author_canonical_id: str | None,
    joined_canonical_ids: dict[int, str],
    provenance: ProvenanceEnvelope,
) -> list[GraphEdge]:
    """Build the judge -> opinion edges for one opinion within the cluster.

    The author (when resolvable) gets an ``authored_opinion`` edge; every judge in
    ``joined_by_ids`` whose canonical id is known gets a ``joined_opinion`` edge.
    Each edge is keyed by ``cluster:opinion`` so the same judge across multiple
    opinions in a cluster (rare) stays distinct.
    """
    opinion_cid = opinion_canonical_id(opinion.cluster_id)
    edges: list[GraphEdge] = []
    if author_canonical_id is not None and author_canonical_id != opinion_cid:
        attributes = {"case_name": opinion.case_name}
        if authorship.opinion_type:
            attributes["opinion_type"] = authorship.opinion_type
        edges.append(
            GraphEdge(
                edge_type="authored_opinion",
                src_id=author_canonical_id,
                dst_id=opinion_cid,
                attributes=attributes,
                external_key=f"{opinion.cluster_id}:{authorship.opinion_id}",
                provenance=provenance,
            )
        )
    for joiner_id in authorship.joined_by_ids:
        joiner_cid = joined_canonical_ids.get(joiner_id)
        if joiner_cid is None or joiner_cid == opinion_cid:
            continue
        edges.append(
            GraphEdge(
                edge_type="joined_opinion",
                src_id=joiner_cid,
                dst_id=opinion_cid,
                attributes={"case_name": opinion.case_name},
                external_key=f"{opinion.cluster_id}:{authorship.opinion_id}:{joiner_id}",
                provenance=provenance,
            )
        )
    return edges


def opinion_citation_edges(
    *,
    opinion: CourtOpinion,
    authorship: OpinionAuthorship,
    provenance: ProvenanceEnvelope,
    cited_cluster_for_opinion: dict[int, int] | None = None,
) -> list[GraphEdge]:
    """Build opinion -> opinion ``cites_opinion`` edges.

    CourtListener's ``cites`` graph is keyed by *opinion* id; we map each cited
    opinion id to its cluster id (when known via ``cited_cluster_for_opinion``,
    populated by the runner from the same pull) so the edge connects two
    ``court_opinion`` canonical ids. Cited opinions outside the pull are skipped
    (we never fabricate a canonical id for a cluster we did not ingest).
    """
    if not cited_cluster_for_opinion:
        return []
    src_cid = opinion_canonical_id(opinion.cluster_id)
    edges: list[GraphEdge] = []
    seen: set[str] = set()
    for cited_opinion_id in authorship.cited_opinion_ids:
        cited_cluster = cited_cluster_for_opinion.get(cited_opinion_id)
        if cited_cluster is None:
            continue
        dst_cid = opinion_canonical_id(cited_cluster)
        if dst_cid == src_cid or dst_cid in seen:
            continue
        seen.add(dst_cid)
        edges.append(
            GraphEdge(
                edge_type="cites_opinion",
                src_id=src_cid,
                dst_id=dst_cid,
                external_key=f"{opinion.cluster_id}:{cited_cluster}",
                provenance=provenance,
            )
        )
    return edges


def opinion_statute_edges(
    *,
    opinion: CourtOpinion,
    provenance: ProvenanceEnvelope,
) -> list[GraphEdge]:
    """Build opinion -> statute/bill ``cites_statute`` edges where derivable.

    One edge per distinct U.S. Code / Public Law ref parsed from the opinion's
    available text, pointing at the statute's canonical bill id. Returns ``[]``
    when no statute is citable from the keyless metadata (the common case — see
    :func:`statute_refs_in`).
    """
    src_cid = opinion_canonical_id(opinion.cluster_id)
    edges: list[GraphEdge] = []
    for ref in opinion.statute_refs:
        dst = ref.canonical_id
        if dst == src_cid:
            continue
        edges.append(
            GraphEdge(
                edge_type="cites_statute",
                src_id=src_cid,
                dst_id=dst,
                attributes={"identifier": ref.identifier},
                external_key=f"{opinion.cluster_id}:{ref.identifier}",
                provenance=provenance,
            )
        )
    return edges
