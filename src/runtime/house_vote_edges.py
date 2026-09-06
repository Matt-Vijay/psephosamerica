"""Resumable House roll-call backfill -> canonical ``vote`` edge feed (federal).

The Senate side already ships a comprehensive ``vote`` edge feed
(``senate_vote_edges.jsonl``, 113-119) built from a rich roll-call corpus. The
House had no equivalent edge feed in the query store — House votes only ever
landed in the DB archive path and a handful of ``house_*_rich.jsonl`` windows
(113, 118, 119). This closes that gap directly from the keyless House Clerk EVS
endpoint so the graph carries House federal roll-calls for the full 113-119 span.

Source (keyless, no auth): ``clerk.house.gov/evs/<year>/roll<NNN>.xml``. Each
roll XML tags every member by **bioguide id**, so casts resolve straight to
``canonical_person_id`` with no NER, and the ``legis-num`` (e.g. ``H R 1234``)
resolves to the canonical bill id under the sitting congress. Procedural votes
(``QUORUM``, motions with no measure) carry no bill and are counted but emit no
edge.

The Clerk's per-year XML *index* (``index.xml``) was retired, so this enumerates
roll numbers by walking ``roll001.xml`` upward and stopping after a run of
consecutive misses (House roll numbers are dense within a calendar year). Each
member-cast becomes a provenance-carrying ``vote`` edge whose ``external_key`` is
the roll-call id (``house-<congress>-<session>-<roll>``), so the backfill is
resumable per roll-call and ``known_at`` is stamped at the vote day (leakage-safe).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

from src.graph.bills import BillRef
from src.graph.edges import GraphEdge
from src.graph.ingest.votes import normalize_vote_choice, vote_edge, vote_provenance
from src.ingest.congress.house_votes import parse_house_vote_xml, roll_call_url
from src.runtime.bill_edges_export import build_bioguide_resolver
from src.runtime.http_client import client_or_default, get_with_backoff as _get_with_backoff


def years_for_congress(congress: int) -> tuple[int, int]:
    """The two calendar years a congress sits (113 -> (2013, 2014))."""
    first = 1789 + (congress - 1) * 2
    return (first, first + 1)


def congress_for_year(year: int) -> int:
    """The congress whose roll numbers a House calendar year belongs to."""
    return 113 + (year - 2013) // 2


def session_for_year(year: int) -> int:
    """House session number for a calendar year (odd year -> 1, even -> 2)."""
    return 1 if year % 2 == 1 else 2


def _bill_canonical_id(congress: int, legis_num: str) -> str | None:
    """``H R 1234`` under congress 118 -> the corpus ``cb-…`` id (None if not a bill)."""
    token = legis_num.strip()
    if not token:
        return None
    try:
        return BillRef.for_congress(congress, token).canonical_id
    except ValueError:
        return None


@dataclass(frozen=True)
class HouseRollcallEdges:
    """One roll-call's conversion outcome."""

    edges: tuple[GraphEdge, ...]
    members_unresolved: int
    had_bill: bool


def edges_for_house_rollcall(
    xml_text: str,
    *,
    resolve_bioguide: dict[str, str],
    first_observed_at: datetime,
) -> HouseRollcallEdges | None:
    """A House roll-call XML -> ``vote`` edges (``None`` when the measure is not a bill)."""
    event, casts = parse_house_vote_xml(xml_text)
    # The measure citation lives on the roll-call's ``legis-num`` (e.g. ``H R 1234``);
    # procedural votes (QUORUM, motions with no measure) resolve to None and emit no edge.
    bill_id = _bill_canonical_id(event.congress, _legis_num(xml_text))
    if bill_id is None:
        return None
    vote_id = f"house-{event.congress}-{event.session_number}-{event.roll_call_number}"
    provenance = vote_provenance(
        source_url=roll_call_url(event.vote_date.year, event.roll_call_number),
        content_sha256=hashlib.sha256(xml_text.encode("utf-8")).hexdigest(),
        vote_date=event.vote_date,
        first_observed_at=first_observed_at,
    )
    edges: list[GraphEdge] = []
    unresolved = 0
    for cast in casts:
        if cast.bioguide_id is None:
            unresolved += 1
            continue
        canonical = resolve_bioguide.get(cast.bioguide_id.upper())
        if canonical is None:
            unresolved += 1
            continue
        try:
            choice = normalize_vote_choice(cast.vote_option)
            edge = vote_edge(
                member_canonical_id=canonical,
                bill_canonical_id=bill_id,
                choice=choice,
                provenance=provenance,
            )
        except ValueError:
            unresolved += 1
            continue
        edges.append(edge.model_copy(update={"external_key": vote_id}))
    return HouseRollcallEdges(edges=tuple(edges), members_unresolved=unresolved, had_bill=True)


def _legis_num(xml_text: str) -> str:
    """The roll-call's ``legis-num`` measure citation (empty when absent)."""
    from defusedxml.ElementTree import fromstring

    root = fromstring(xml_text)
    meta = root.find("vote-metadata")
    if meta is None:
        return ""
    return (meta.findtext("legis-num") or "").strip()


@dataclass(frozen=True)
class HouseEdgesReport:
    """Counts from one House backfill pass."""

    rollcalls_seen: int
    rollcalls_converted: int
    edges_written: int
    members_unresolved: int
    edges_per_congress: dict[int, int] = field(default_factory=dict)
    edges_total: int = 0


def _done_vote_ids(path: Path) -> set[str]:
    done: set[str] = set()
    if not path.exists():
        return done
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                key = json.loads(line).get("external_key")
                if key:
                    done.add(str(key))
    return done


def backfill_house_vote_edges(
    *,
    years: list[int],
    corpus_directory: Path | str,
    out_path: Path | str,
    client: httpx.Client | None = None,
    first_observed_at: datetime | None = None,
    stop_after_misses: int = 8,
    max_rollcalls: int | None = None,
) -> HouseEdgesReport:
    """Fetch House roll-calls for *years* not yet in the feed and append vote edges."""
    observed = first_observed_at if first_observed_at is not None else datetime.now(UTC)
    out = Path(out_path)
    resolver = build_bioguide_resolver(corpus_directory)
    done = _done_vote_ids(out)
    http, owns = client_or_default(client)
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = (
        sum(1 for line in out.read_text().splitlines() if line.strip()) if out.exists() else 0
    )
    seen = converted = written = unresolved = 0
    per_congress: dict[int, int] = {}
    try:
        with out.open("a", encoding="utf-8") as handle:
            for year in years:
                congress = congress_for_year(year)
                session = session_for_year(year)
                misses = 0
                roll = 0
                while misses < stop_after_misses:
                    roll += 1
                    vote_id = f"house-{congress}-{session}-{roll}"
                    if vote_id in done:
                        misses = 0
                        seen += 1
                        continue
                    if max_rollcalls is not None and converted >= max_rollcalls:
                        break
                    resp = _get_with_backoff(http, roll_call_url(year, roll))
                    if resp is None or resp.status_code != 200:
                        misses += 1
                        continue
                    misses = 0
                    seen += 1
                    try:
                        result = edges_for_house_rollcall(
                            resp.text,
                            resolve_bioguide=resolver,
                            first_observed_at=observed,
                        )
                    except (ValueError, KeyError):
                        continue
                    if result is None:
                        continue
                    converted += 1
                    unresolved += result.members_unresolved
                    for edge in result.edges:
                        handle.write(edge.model_dump_json() + "\n")
                        written += 1
                        per_congress[congress] = per_congress.get(congress, 0) + 1
    finally:
        if owns:
            http.close()
    return HouseEdgesReport(
        rollcalls_seen=seen,
        rollcalls_converted=converted,
        edges_written=written,
        members_unresolved=unresolved,
        edges_per_congress=per_congress,
        edges_total=existing + written,
    )


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI glue
    import argparse

    parser = argparse.ArgumentParser(description="Resumable House roll-call -> vote edge feed")
    parser.add_argument("--years", default="2013-2026")
    parser.add_argument("--corpus", default="data/exports/contract_records")
    parser.add_argument("--out", default="data/exports/govinfo_bills/house_vote_edges.jsonl")
    parser.add_argument("--max-rollcalls", type=int, default=None)
    args = parser.parse_args(argv)
    if "-" in args.years:
        lo, hi = (int(x) for x in args.years.split("-", 1))
        years = list(range(lo, hi + 1))
    else:
        years = [int(x) for x in args.years.split(",") if x.strip()]
    report = backfill_house_vote_edges(
        years=years,
        corpus_directory=args.corpus,
        out_path=args.out,
        max_rollcalls=args.max_rollcalls,
    )
    per = ", ".join(f"{c}:{n}" for c, n in sorted(report.edges_per_congress.items()))
    print(
        f"rollcalls={report.rollcalls_converted}/{report.rollcalls_seen} "
        f"edges={report.edges_written} unresolved={report.members_unresolved} "
        f"per-congress=[{per}] total={report.edges_total}",
        flush=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
