"""Municipal roll-call backfill across the Legistar registry (v7 #6).

For each registered government, over the keyless Legistar OData API:

1. ``/persons`` -> active council/board members -> canonical Person IDs (via the
   normal materialize path; persons also written to a feed for the corpus merge),
2. ``/events`` (newest first) -> ``/events/<id>/eventitems`` -> items that carry
   a matter id and a roll call -> ``/eventitems/<id>/votes``,
3. each member vote -> a provenance-carrying ``vote`` edge from the member's
   canonical Person ID to the matter's canonical Bill ID (the same
   ``matter_bill_canonical_id`` scheme the municipal-bill ingest mints).

Resumable per government (a client with any edge in the feed is skipped);
paced + bounded per client so a full-registry pass stays polite. Per-row
failures are skipped and counted, never fabricated.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

from src.graph.ingest.legistar import is_active_person, parse_legistar_person
from src.graph.ingest.legistar_matters import matter_bill_canonical_id, parse_legistar_matter
from src.graph.ingest.legistar_registry import LEGISTAR_CLIENTS, LegistarClient
from src.graph.ingest.legistar_votes import legistar_vote_edge
from src.graph.materialize import materialize_person_nodes
from src.graph.provenance import ProvenanceEnvelope
from src.runtime.http_client import client_or_default

_API = "https://webapi.legistar.com/v1"


def _get_json(client: httpx.Client, url: str, params: dict[str, str]) -> Any:
    response = client.get(url, params=params)
    response.raise_for_status()
    return response.json()


def _provenance(
    *, source_url: str, payload: Any, valid_from: date, first_observed_at: datetime
) -> ProvenanceEnvelope:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    known = datetime(valid_from.year, valid_from.month, valid_from.day, tzinfo=UTC)
    return ProvenanceEnvelope(
        source_url=source_url,
        content_sha256=digest,
        first_observed_at=first_observed_at,
        valid_from=valid_from,
        known_at=min(known, first_observed_at),
    )


@dataclass(frozen=True)
class MunicipalBackfillReport:
    """Counts from one municipal backfill pass."""

    clients_seen: int
    clients_processed: int
    clients_skipped_done: int
    persons_written: int
    edges_written: int
    rows_skipped: int


def _done_clients(edges_path: Path) -> set[str]:
    done: set[str] = set()
    if not edges_path.exists():
        return done
    with edges_path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                key = str(json.loads(line).get("external_key") or "")
                if ":" in key:
                    done.add(key.split(":", 1)[0])
    return done


def _event_date(event: dict[str, Any]) -> date | None:
    raw = str(event.get("EventDate") or "")
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _resolver_entries(nodes: Sequence[Any]) -> list[tuple[str, str]]:
    """``(client:<PersonId>, canonical_person_id)`` pairs from materialized nodes."""
    entries: list[tuple[str, str]] = []
    for node in nodes:
        for external in node.external_ids:
            if external.startswith("legistar:"):
                entries.append((external.removeprefix("legistar:"), node.canonical_id))
    return entries


def backfill_municipal_votes(
    *,
    out_edges: Path | str,
    out_persons: Path | str,
    registry: Sequence[LegistarClient] = LEGISTAR_CLIENTS,
    client: httpx.Client | None = None,
    first_observed_at: datetime | None = None,
    events_per_client: int = 20,
    max_vote_items_per_client: int = 150,
    max_clients: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
    delay_seconds: float = 0.15,
) -> MunicipalBackfillReport:
    """One paced pass over the registry; append unseen governments' votes."""
    edges_path = Path(out_edges)
    persons_path = Path(out_persons)
    edges_path.parent.mkdir(parents=True, exist_ok=True)
    persons_path.parent.mkdir(parents=True, exist_ok=True)
    observed = first_observed_at if first_observed_at is not None else datetime.now(UTC)
    done = _done_clients(edges_path)
    http, owns = client_or_default(client)

    seen = processed = skipped_done = persons_written = edges_written = rows_skipped = 0
    try:
        with (
            edges_path.open("a", encoding="utf-8") as edges_out,
            persons_path.open("a", encoding="utf-8") as persons_out,
        ):
            for entry in registry:
                seen += 1
                if entry.client in done:
                    skipped_done += 1
                    continue
                if max_clients is not None and processed >= max_clients:
                    break
                processed += 1
                base = f"{_API}/{entry.client}"
                jurisdiction = entry.jurisdiction()
                try:
                    sleep(delay_seconds)
                    raw_persons = _get_json(http, f"{base}/persons", {"$top": "1000"})
                except (httpx.HTTPError, ValueError):
                    rows_skipped += 1
                    continue

                records = []
                for raw in raw_persons if isinstance(raw_persons, list) else []:
                    if not is_active_person(raw):
                        continue
                    try:
                        records.append(
                            parse_legistar_person(
                                raw,
                                client=entry.client,
                                jurisdiction=jurisdiction,
                                region=entry.state.upper(),
                                provenance=_provenance(
                                    source_url=f"{base}/persons",
                                    payload=raw,
                                    valid_from=observed.date(),
                                    first_observed_at=observed,
                                ),
                            )
                        )
                    except ValueError:
                        rows_skipped += 1
                nodes, _assignment = materialize_person_nodes(records)
                resolve = dict(_resolver_entries(nodes))
                for node in nodes:
                    persons_out.write(node.model_dump_json() + "\n")
                    persons_written += 1

                try:
                    sleep(delay_seconds)
                    events = _get_json(
                        http,
                        f"{base}/events",
                        {"$top": str(events_per_client), "$orderby": "EventDate desc"},
                    )
                except (httpx.HTTPError, ValueError):
                    rows_skipped += 1
                    continue

                vote_items = 0
                bill_ids: dict[int, str] = {}  # matter id -> canonical bill id (per client)
                for event in events if isinstance(events, list) else []:
                    event_id = event.get("EventId")
                    event_date = _event_date(event)
                    if event_id is None or event_date is None:
                        rows_skipped += 1
                        continue
                    if vote_items >= max_vote_items_per_client:
                        break
                    try:
                        sleep(delay_seconds)
                        items = _get_json(
                            http, f"{base}/events/{event_id}/eventitems", {"$top": "200"}
                        )
                    except (httpx.HTTPError, ValueError):
                        rows_skipped += 1
                        continue
                    for item in items if isinstance(items, list) else []:
                        item_id = item.get("EventItemId")
                        matter_id = item.get("EventItemMatterId")
                        if item_id is None or not matter_id:
                            continue
                        if item.get("EventItemRollCallFlag") in (0, None) and not item.get(
                            "EventItemPassedFlag"
                        ):
                            continue
                        if vote_items >= max_vote_items_per_client:
                            break
                        vote_items += 1
                        # The REAL matter record (cached per client) so the bill id
                        # matches what the municipal-bill ingest mints (the session
                        # comes from the matter's intro date, absent on event items).
                        if matter_id not in bill_ids:
                            try:
                                sleep(delay_seconds)
                                matter_record = _get_json(http, f"{base}/matters/{matter_id}", {})
                                matter = parse_legistar_matter(matter_record)
                                bill_ids[matter_id] = matter_bill_canonical_id(
                                    matter, jurisdiction_code=jurisdiction.code
                                )
                            except (httpx.HTTPError, ValueError):
                                rows_skipped += 1
                                continue
                        bill_id = bill_ids[matter_id]
                        try:
                            sleep(delay_seconds)
                            votes = _get_json(
                                http, f"{base}/eventitems/{item_id}/votes", {"$top": "100"}
                            )
                        except (httpx.HTTPError, ValueError):
                            rows_skipped += 1
                            continue
                        for vote in votes if isinstance(votes, list) else []:
                            person_key = f"{entry.client}:{vote.get('VotePersonId')}"
                            member_id = resolve.get(person_key)
                            if member_id is None:
                                rows_skipped += 1
                                continue
                            edge = legistar_vote_edge(
                                member_canonical_id=member_id,
                                bill_canonical_id=bill_id,
                                vote_value=str(vote.get("VoteValueName") or ""),
                                vote_external_key=f"{entry.client}:{item_id}:{vote.get('VoteId')}",
                                provenance=_provenance(
                                    source_url=f"{base}/eventitems/{item_id}/votes",
                                    payload=vote,
                                    valid_from=event_date,
                                    first_observed_at=observed,
                                ),
                            )
                            if edge is None:
                                continue
                            edges_out.write(edge.model_dump_json() + "\n")
                            edges_written += 1
    finally:
        if owns:
            http.close()
    return MunicipalBackfillReport(
        clients_seen=seen,
        clients_processed=processed,
        clients_skipped_done=skipped_done,
        persons_written=persons_written,
        edges_written=edges_written,
        rows_skipped=rows_skipped,
    )


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI glue
    import argparse

    parser = argparse.ArgumentParser(description="Municipal Legistar vote backfill")
    parser.add_argument("--edges", default="data/exports/municipal/municipal_vote_edges.jsonl")
    parser.add_argument("--persons", default="data/exports/municipal/municipal_persons.jsonl")
    parser.add_argument("--events", type=int, default=20)
    parser.add_argument("--max-clients", type=int, default=None)
    args = parser.parse_args(argv)
    report = backfill_municipal_votes(
        out_edges=args.edges,
        out_persons=args.persons,
        events_per_client=args.events,
        max_clients=args.max_clients,
    )
    print(
        f"clients={report.clients_processed}/{report.clients_seen} "
        f"(done-skip={report.clients_skipped_done}) persons={report.persons_written} "
        f"edges={report.edges_written} skipped={report.rows_skipped}",
        flush=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
