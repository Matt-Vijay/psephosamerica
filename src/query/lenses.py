"""High-value analytical lenses over the connected graph (V8 deliverable #3).

These are the cross-cutting *reasoning* views a human actually asks for, each
returning rows that carry their provenance so every served answer is cited:

* :func:`copied_bill_clusters` — **BILLSTATUS-dossier similarity across federal
  bills**. MinHash + LSH banding compares the legacy sidecar's assembled title,
  policy-area, subject, and CRS-summary language. It does not compare GovInfo
  BILLS legislative version text and therefore does not establish copied/model
  legislation. Numpy + stdlib only, streams the sidecar.

* :func:`accountability_rankings` — **opacity / accountability rankings** per
  jurisdiction and per official, derived from what the graph can observe today:
  roll-call participation, source-anchor coverage, and (when present) floor
  speech and news-mention density. A transparent, fully-explained composite —
  not a black box — so a reader can see exactly which observable drove a rank.

* :func:`said_vs_voted` — a **"said vs voted"** view: an official's floor
  speeches (CREC ``floor_speech`` edges) lined up against their roll-call
  votes. It returns an honest empty/partial result until Track A wires CREC
  speech edges into the store's ``edge_paths``; the moment they land it lights
  up with no code change here.

All cited. All numpy/stdlib. Builds on the existing :mod:`src.query.graph_store`
and :mod:`src.query.locus` so it sharpens automatically as richer data lands.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from src.query.graph_store import GraphStore, Node
from src.query.locus import _TOKEN_RE, _minhash, _shingles

# Legacy BILLSTATUS dossier sidecars Track A emits (full corpus first, slim fallback).
BILL_CONTENT_PATHS = (
    Path("data/exports/govinfo_bills/bill_content_full.jsonl"),
    Path("data/exports/govinfo_bills/bill_content.jsonl"),
)


def _citation(node: Node) -> dict[str, str | None]:
    if node.citations:
        return node.citations[0].as_citation()
    return {"source_url": None, "content_sha256": None, "known_at": node.known_at}


# -- (a) copied / model-legislation detection ACROSS BILLS --------------


@dataclass(frozen=True)
class _BillText:
    canonical_id: str
    title: str
    jurisdiction: str | None
    text: str
    source_url: str | None
    content_sha256: str | None


def _iter_bill_text(
    paths: tuple[Path, ...] = BILL_CONTENT_PATHS, *, limit: int | None = None
) -> Iterator[_BillText]:
    """Stream BILLSTATUS dossier rows from the first legacy sidecar that exists."""
    path = next((p for p in paths if p.exists()), None)
    if path is None:
        return
    count = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            text = str(record.get("text") or record.get("summary_text") or "")
            if not text:
                continue
            prov = record.get("provenance") if isinstance(record.get("provenance"), dict) else {}
            yield _BillText(
                canonical_id=str(record.get("canonical_id", "")),
                title=str(record.get("title") or ""),
                jurisdiction=_juris_of(str(record.get("canonical_id", "")), record),
                text=text,
                source_url=_opt(prov.get("source_url")),
                content_sha256=_opt(prov.get("content_sha256")),
            )
            count += 1
            if limit is not None and count >= limit:
                return


def _opt(value: object) -> str | None:
    return None if value is None else str(value)


def _juris_of(canonical_id: str, record: dict[str, object]) -> str | None:
    """Coarse jurisdiction for a bill row: federal congress vs CA vs unknown."""
    if record.get("congress"):
        return "us-congress"
    bt = str(record.get("bill_type") or "")
    if bt:
        return "us-congress"
    return None


@dataclass(frozen=True)
class CopiedBillPair:
    """Two bills whose *text* is near-identical (estimated Jaccard), cited."""

    jaccard: float
    a: dict[str, object]
    b: dict[str, object]
    cross_jurisdiction: bool


def copied_bill_clusters(
    paths: tuple[Path, ...] = BILL_CONTENT_PATHS,
    *,
    store: GraphStore | None = None,
    limit: int | None = 20000,
    num_perm: int = 64,
    bands: int = 16,
    threshold: float = 0.7,
    max_pairs: int = 200,
    min_tokens: int = 40,
    cross_jurisdiction_only: bool = False,
    seed: int = 20260620,
) -> list[CopiedBillPair]:
    """Find near-duplicate BILLSTATUS dossier language via MinHash + LSH.

    The legacy sidecar's ``text`` field is assembled from title, policy area,
    subjects, and CRS summary. Similarity can identify companion or closely
    described bills, but it does not compare legislative version text and must
    not be reported as proof of copied/model legislation.

    ``threshold`` is the estimated-Jaccard cutoff; ``limit`` bounds how many
    bills are scanned. Each side of every pair is cited to its source.
    """
    rng = np.random.default_rng(seed)
    seeds = rng.integers(1, np.iinfo(np.uint64).max, size=num_perm, dtype=np.uint64)
    rows_per_band = max(1, num_perm // bands)

    bills: list[_BillText] = []
    signatures: list[np.ndarray] = []
    buckets: dict[tuple[int, bytes], list[int]] = defaultdict(list)
    for bill in _iter_bill_text(paths, limit=limit):
        if len(_TOKEN_RE.findall(bill.text)) < min_tokens:
            continue
        signature = _minhash(_shingles(bill.text), num_perm=num_perm, seeds=seeds)
        index = len(bills)
        bills.append(bill)
        signatures.append(signature)
        for band in range(bands):
            chunk = signature[band * rows_per_band : (band + 1) * rows_per_band]
            buckets[(band, chunk.tobytes())].append(index)

    seen: set[tuple[int, int]] = set()
    pairs: list[CopiedBillPair] = []
    for members in buckets.values():
        if len(members) < 2:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = sorted((members[i], members[j]))
                if (a, b) in seen:
                    continue
                seen.add((a, b))
                cross = bills[a].jurisdiction != bills[b].jurisdiction
                if cross_jurisdiction_only and not cross:
                    continue
                jaccard = float((signatures[a] == signatures[b]).mean())
                if jaccard < threshold:
                    continue
                pairs.append(
                    CopiedBillPair(
                        jaccard=jaccard,
                        a=_bill_brief(bills[a], store),
                        b=_bill_brief(bills[b], store),
                        cross_jurisdiction=cross,
                    )
                )
    pairs.sort(key=lambda p: -p.jaccard)
    return pairs[:max_pairs]


def _bill_brief(bill: _BillText, store: GraphStore | None = None) -> dict[str, object]:
    citation: dict[str, str | None] = {
        "source_url": bill.source_url,
        "content_sha256": bill.content_sha256,
        "known_at": None,
    }
    # The legacy BILLSTATUS dossier sidecar often lacks provenance; fall back
    # to the canonical bill node's source anchor when available.
    if citation["source_url"] is None and store is not None:
        node = store.node(bill.canonical_id)
        if node is not None:
            citation = _citation(node)
    return {
        "bill_id": bill.canonical_id,
        "title": bill.title,
        "jurisdiction": bill.jurisdiction,
        "text_excerpt": bill.text[:200],
        "citation": citation,
    }


# -- (c) accountability / opacity rankings ------------------------------


@dataclass(frozen=True)
class AccountabilityScore:
    """A transparent, fully-explained accountability score for one entity."""

    entity_id: str
    display_name: str
    jurisdiction: str | None
    score: float
    components: dict[str, float]
    observations: dict[str, int]
    citation: dict[str, str | None]


def official_accountability(
    store: GraphStore, *, limit: int = 50, min_votes: int = 1
) -> list[AccountabilityScore]:
    """Rank officials by an observable accountability composite (most → least).

    The score is intentionally transparent — a weighted blend of what the graph
    can *observe* about each official's public record:

    * ``record_depth``   — how many roll-call votes are on record (log-scaled).
    * ``source_coverage``— fraction of those votes that carry a real source_url.
    * ``voice``          — whether floor speeches / news mentions are attached.

    A higher score means *more* publicly-observable accountability surface, not
    a value judgement. ``components`` and ``observations`` expose every input so
    a reader sees exactly why an official ranks where they do. Each row is cited
    to the official's canonical record.
    """
    scored: list[AccountabilityScore] = []
    for node in store.nodes_of_type("person"):
        votes = 0
        cited_votes = 0
        speeches = 0
        mentions = 0
        for edge in store.out_edges(node.canonical_id):
            if edge.edge_type == "vote":
                votes += 1
                if edge.provenance.source_url:
                    cited_votes += 1
            elif edge.edge_type == "floor_speech":
                speeches += 1
            elif edge.edge_type == "news_mention":
                mentions += 1
        for edge in store.in_edges(node.canonical_id):
            if edge.edge_type == "news_mention":
                mentions += 1
        if votes < min_votes and speeches == 0:
            continue
        record_depth = float(np.log1p(votes) / np.log1p(500))
        source_coverage = (cited_votes / votes) if votes else 0.0
        voice = 1.0 if (speeches or mentions) else 0.0
        score = round(0.5 * min(record_depth, 1.0) + 0.4 * source_coverage + 0.1 * voice, 4)
        scored.append(
            AccountabilityScore(
                entity_id=node.canonical_id,
                display_name=node.display_name,
                jurisdiction=node.jurisdiction,
                score=score,
                components={
                    "record_depth": round(min(record_depth, 1.0), 4),
                    "source_coverage": round(source_coverage, 4),
                    "voice": voice,
                },
                observations={
                    "votes": votes,
                    "cited_votes": cited_votes,
                    "floor_speeches": speeches,
                    "news_mentions": mentions,
                },
                citation=_citation(node),
            )
        )
    scored.sort(key=lambda s: (-s.score, s.display_name))
    return scored[:limit]


@dataclass(frozen=True)
class JurisdictionAccountability:
    """Aggregate accountability surface for a whole jurisdiction (cited corpus)."""

    jurisdiction: str
    officials: int
    mean_score: float
    mean_source_coverage: float
    officials_with_voice: int


def jurisdiction_accountability(
    store: GraphStore, *, limit: int = 50
) -> list[JurisdictionAccountability]:
    """Roll the per-official accountability composite up to each jurisdiction."""
    per_official = official_accountability(store, limit=1_000_000, min_votes=0)
    by_juris: dict[str, list[AccountabilityScore]] = defaultdict(list)
    for score in per_official:
        by_juris[score.jurisdiction or "unknown"].append(score)
    rows: list[JurisdictionAccountability] = []
    for juris, scores in by_juris.items():
        n = len(scores)
        if n == 0:
            continue
        rows.append(
            JurisdictionAccountability(
                jurisdiction=juris,
                officials=n,
                mean_score=round(sum(s.score for s in scores) / n, 4),
                mean_source_coverage=round(
                    sum(s.components["source_coverage"] for s in scores) / n, 4
                ),
                officials_with_voice=sum(1 for s in scores if s.components["voice"] > 0),
            )
        )
    rows.sort(key=lambda r: (-r.officials, r.jurisdiction))
    return rows[:limit]


# -- (d) said vs voted --------------------------------------------------


@dataclass(frozen=True)
class SaidVsVoted:
    """An official's floor speeches lined up against their roll-call votes."""

    official: dict[str, object]
    speeches: tuple[dict[str, object], ...]
    votes: tuple[dict[str, object], ...]
    note: str = ""


def said_vs_voted(store: GraphStore, person_id: str, *, max_rows: int = 25) -> SaidVsVoted | None:
    """Compare what an official *said* (floor speeches) vs how they *voted*.

    Lights up automatically when Track A's CREC ``floor_speech`` edges are wired
    into the store's ``edge_paths``. Until then it returns the votes plus an
    honest note that no speeches are loaded yet — never a fabricated claim.
    Every speech and vote row is cited.
    """
    node = store.node(person_id)
    if node is None or node.entity_type != "person":
        return None
    speeches: list[dict[str, object]] = []
    votes: list[dict[str, object]] = []
    for edge in store.out_edges(person_id):
        if edge.edge_type == "floor_speech" and len(speeches) < max_rows:
            speeches.append(
                {
                    "venue": edge.dst_id,
                    "chamber": edge.attributes.get("chamber"),
                    "role": edge.attributes.get("role"),
                    "citation": edge.provenance.as_citation(),
                }
            )
        elif edge.edge_type == "vote" and len(votes) < max_rows:
            bill = store.node(edge.dst_id)
            votes.append(
                {
                    "bill_id": edge.dst_id,
                    "bill_name": bill.display_name if bill else None,
                    "choice": edge.attributes.get("choice"),
                    "citation": edge.provenance.as_citation(),
                }
            )
    note = (
        ""
        if speeches
        else (
            "No floor-speech (CREC) edges are loaded for this official yet; "
            "this view lights up when Track A wires floor_speech edges into the store."
        )
    )
    return SaidVsVoted(
        official={
            "canonical_id": node.canonical_id,
            "display_name": node.display_name,
            "jurisdiction": node.jurisdiction,
            "citation": _citation(node),
        },
        speeches=tuple(speeches),
        votes=tuple(votes),
        note=note,
    )


__all__ = [
    "BILL_CONTENT_PATHS",
    "CopiedBillPair",
    "AccountabilityScore",
    "JurisdictionAccountability",
    "SaidVsVoted",
    "copied_bill_clusters",
    "official_accountability",
    "jurisdiction_accountability",
    "said_vs_voted",
]
