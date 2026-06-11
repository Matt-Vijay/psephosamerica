"""Senate rich roll-calls (113-119) -> canonical ``vote`` edges (v7 #2).

The v5 backfill fetched every Senate roll-call 113-119 into
``data/real/senate_113_119_rich.jsonl`` keyed by LIS member id, but those
member-votes never became graph edges: the corpus carried no LIS ids to resolve
against. The roster merge fixed that (every senator since 2013 now carries an
``lis:`` external id), so this exporter closes the loop:

* members resolve LIS id -> canonical person id from the corpus,
* bills resolve through the same :class:`~src.graph.bills.BillRef` scheme that
  minted the bill corpus (the rich ``bill_id`` is
  ``us_congress:<congress>:<identifier>``),
* each member-vote becomes a provenance-carrying ``vote`` edge whose
  ``external_key`` is the roll-call's vote id (so two votes by the same member
  on the same bill in different roll-calls stay distinct edges).

Resumable by vote id; per-roll-call resolution misses are counted, never
fabricated. Output is an edge feed JSONL (like ``bill_edges_full.jsonl``),
reported per congress.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from src.graph.bills import BillRef
from src.graph.edges import GraphEdge
from src.graph.export import RECORDS_FILENAME, iter_contract_corpus
from src.graph.ingest.votes import vote_edge, vote_provenance
from src.ingest.congress.senate_votes import roll_call_url


def build_lis_resolver(corpus_directory: Path | str) -> dict[str, str]:
    """``{LIS id (upper): canonical person id}`` from the corpus person rows."""
    resolver: dict[str, str] = {}
    if not (Path(corpus_directory) / RECORDS_FILENAME).exists():
        return resolver
    for row in iter_contract_corpus(corpus_directory):
        if row.entity_type != "person":
            continue
        for external in row.external_ids:
            if external.startswith("lis:"):
                resolver.setdefault(external.removeprefix("lis:").upper(), row.canonical_id)
    return resolver


def _bill_canonical_id(rich_bill_id: str) -> str | None:
    """``us_congress:113:sres-15`` -> the corpus ``cb-…`` id (None if malformed)."""
    parts = rich_bill_id.split(":")
    if len(parts) != 3 or parts[0] != "us_congress" or not parts[1].isdigit():
        return None
    return BillRef(
        jurisdiction_id="us-congress", session_id=parts[1], identifier=parts[2]
    ).canonical_id


def _vote_source_url(vote_id: str) -> str:
    """``senate-113-1-24`` -> the senate.gov roll-call XML URL (best effort)."""
    parts = vote_id.split("-")
    if len(parts) == 4 and parts[0] == "senate" and all(p.isdigit() for p in parts[1:]):
        return roll_call_url(int(parts[1]), int(parts[2]), int(parts[3]))
    return "https://www.senate.gov/legislative/votes_new.htm"


@dataclass(frozen=True)
class RollcallEdgesResult:
    """One roll-call's conversion outcome."""

    edges: tuple[GraphEdge, ...]
    members_unresolved: int


def edges_for_rollcall(
    record: dict[str, Any],
    *,
    resolve_lis: dict[str, str],
    first_observed_at: datetime,
) -> RollcallEdgesResult | None:
    """A rich roll-call -> vote edges (``None`` when the bill id is malformed)."""
    bill_id = _bill_canonical_id(str(record.get("bill_id") or ""))
    if bill_id is None:
        return None
    vote_id = str(record.get("vote_id") or "")
    vote_date = date.fromisoformat(str(record["date"]))
    provenance = vote_provenance(
        source_url=_vote_source_url(vote_id),
        content_sha256=hashlib.sha256(
            json.dumps(record, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        vote_date=vote_date,
        first_observed_at=first_observed_at,
    )
    edges: list[GraphEdge] = []
    unresolved = 0
    for member, _party, _state, choice in record.get("votes", []):
        canonical = resolve_lis.get(str(member).upper())
        if canonical is None:
            unresolved += 1
            continue
        try:
            edge = vote_edge(
                member_canonical_id=canonical,
                bill_canonical_id=bill_id,
                choice=str(choice),
                provenance=provenance,
            )
        except ValueError:
            unresolved += 1
            continue
        edges.append(edge.model_copy(update={"external_key": vote_id}))
    return RollcallEdgesResult(edges=tuple(edges), members_unresolved=unresolved)


@dataclass(frozen=True)
class SenateEdgesReport:
    """Counts from one export pass."""

    rollcalls_seen: int
    rollcalls_converted: int
    edges_written: int
    members_unresolved: int
    edges_per_congress: dict[int, int]
    edges_total: int


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


def export_senate_vote_edges(
    *,
    rich_path: Path | str,
    corpus_directory: Path | str,
    out_path: Path | str,
    first_observed_at: datetime | None = None,
    max_rollcalls: int | None = None,
) -> SenateEdgesReport:
    """Convert unseen rich roll-calls to vote edges and append them to the feed."""
    observed = first_observed_at if first_observed_at is not None else datetime.now(UTC)
    out = Path(out_path)
    resolver = build_lis_resolver(corpus_directory)
    done = _done_vote_ids(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = (
        sum(1 for line in out.read_text().splitlines() if line.strip()) if out.exists() else 0
    )

    seen = converted = written = unresolved = 0
    per_congress: dict[int, int] = {}
    with out.open("a", encoding="utf-8") as handle:
        with Path(rich_path).open(encoding="utf-8") as rich:
            for line in rich:
                if not line.strip():
                    continue
                record = json.loads(line)
                seen += 1
                vote_id = str(record.get("vote_id") or "")
                if vote_id in done:
                    continue
                if max_rollcalls is not None and converted >= max_rollcalls:
                    break
                result = edges_for_rollcall(
                    record, resolve_lis=resolver, first_observed_at=observed
                )
                if result is None:
                    continue
                converted += 1
                unresolved += result.members_unresolved
                congress = int(record.get("congress") or 0)
                for edge in result.edges:
                    handle.write(edge.model_dump_json() + "\n")
                    written += 1
                    per_congress[congress] = per_congress.get(congress, 0) + 1
    return SenateEdgesReport(
        rollcalls_seen=seen,
        rollcalls_converted=converted,
        edges_written=written,
        members_unresolved=unresolved,
        edges_per_congress=per_congress,
        edges_total=existing + written,
    )


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI glue
    import argparse

    parser = argparse.ArgumentParser(description="Senate rich roll-calls -> vote edge feed")
    parser.add_argument("--rich", default="data/real/senate_113_119_rich.jsonl")
    parser.add_argument("--corpus", default="data/exports/contract_records")
    parser.add_argument("--out", default="data/exports/govinfo_bills/senate_vote_edges.jsonl")
    args = parser.parse_args(argv)
    report = export_senate_vote_edges(
        rich_path=args.rich, corpus_directory=args.corpus, out_path=args.out
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
