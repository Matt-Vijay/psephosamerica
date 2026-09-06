"""Resumable Senate roll-call backfill -> rich roll-call JSONL for Track B.

Track B's cross-chamber model trains on House votes and evaluates on Senate; it
consumes the same rich format as ``house_*_rich.jsonl``
(``{vote_id, bill_id, date, congress, sectors, votes:[[member, party, state,
choice], ...]}``). This backfills every Senate roll-call for congresses 113-119
from keyless senate.gov XML and attaches each bill's **CRS sectors** from the
content sidecar (``bill_content.jsonl``) -- so Senate votes carry the same
sector signal the bill ingest gave the House.

Two keyless senate.gov endpoints:
* menu   ``.../roll_call_lists/vote_menu_<congress>_<session>.xml`` (vote numbers)
* a vote ``.../roll_call_votes/vote<c><s>/vote_<c>_<s>_<NNNNN>.xml``

Members are keyed by their LIS id (the Senate vote id space) -- consistent
within the chamber, which is all the per-member loyalty model needs. Resumable:
votes already in the output are skipped; per-vote errors are skipped + counted.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx

from src.graph.bills import parse_congress_bill_identifier
from src.graph.ingest.senate import bill_canonical_id_for, parse_senate_rollcall_xml
from src.ingest.congress.senate_votes import roll_call_list_url, roll_call_url
from src.runtime.http_client import client_or_default

_VOTE_NUMBER_RE = re.compile(r"<vote_number>\s*(\d+)\s*</vote_number>")

# The URL builders live on the ingest layer (these started as local copies while
# src/ingest/congress pointed the index at a URL that answers HTML; that is fixed,
# so the backfill delegates).
senate_menu_url = roll_call_list_url
senate_vote_url = roll_call_url


def parse_menu_vote_numbers(xml: str) -> list[int]:
    """The roll-call numbers in a Senate vote menu (sorted, unique)."""
    return sorted({int(match) for match in _VOTE_NUMBER_RE.findall(xml)})


def load_bill_sectors(path: Path | str) -> dict[str, list[str]]:
    """``{cb-id: [policy_area, *subjects]}`` from the content sidecar (empty if absent)."""
    sidecar = Path(path)
    if not sidecar.exists():
        return {}
    out: dict[str, list[str]] = {}
    for line in sidecar.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        sectors: list[str] = []
        if record.get("policy_area"):
            sectors.append(str(record["policy_area"]))
        sectors.extend(str(s) for s in record.get("subjects", []))
        out[str(record["canonical_id"])] = sectors
    return out


def _rich_bill_id(congress: int, document_name: str | None) -> str:
    if not document_name:
        return f"us_congress:{congress}:unknown"
    try:
        return f"us_congress:{congress}:{parse_congress_bill_identifier(document_name)}"
    except ValueError:
        return f"us_congress:{congress}:unknown"


_QUESTION_RE = re.compile(r"<(?:vote_question_text|question)>\s*([^<]*?)\s*</", re.S)


def _vote_question(xml: str) -> str:
    """The roll-call's question text (e.g. "On the Nomination"), from the raw XML.

    Extracted here (not in the graph parser, which is Track A's) so the rich
    record can label confirmation votes; empty string when absent.
    """
    match = _QUESTION_RE.search(xml)
    return match.group(1).strip() if match else ""


def senate_rich_record(xml: str, *, sectors_by_bill: Mapping[str, list[str]]) -> dict[str, object]:
    """Turn one Senate roll-call XML into a rich roll-call record (sectors attached)."""
    rollcall = parse_senate_rollcall_xml(xml)
    cb_id = bill_canonical_id_for(rollcall)
    sectors = sectors_by_bill.get(cb_id, []) if cb_id else []
    return {
        "vote_id": f"senate-{rollcall.congress}-{rollcall.session}-{rollcall.vote_number}",
        "bill_id": _rich_bill_id(rollcall.congress, rollcall.document_name),
        "date": rollcall.vote_date.isoformat(),
        "congress": rollcall.congress,
        "question": _vote_question(xml),
        "sectors": sectors,
        "votes": [
            [member.lis_member_id, member.party, member.state, member.choice.strip().lower()]
            for member in rollcall.recorded_votes
        ],
    }


@dataclass(frozen=True)
class SenateBackfillProgress:
    """Counts from one Senate backfill pass."""

    sessions: int
    votes_listed: int
    votes_new: int
    votes_skipped: int
    rows_total: int


def _existing_vote_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            ids.add(str(json.loads(line)["vote_id"]))
    return ids


def backfill_senate_votes(
    *,
    congresses: Iterable[int],
    out_path: Path | str,
    content_sidecar: Path | str,
    sessions: Iterable[int] = (1, 2),
    client: httpx.Client | None = None,
    max_votes: int | None = None,
) -> SenateBackfillProgress:
    """Fetch Senate roll-calls not yet in ``out_path`` and append rich records."""
    out = Path(out_path)
    sectors_by_bill = load_bill_sectors(content_sidecar)
    done = _existing_vote_ids(out)
    http, owns = client_or_default(client)
    sessions_seen = listed = new = skipped = 0
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = (
        sum(1 for line in out.read_text().splitlines() if line.strip()) if out.exists() else 0
    )
    try:
        with out.open("a", encoding="utf-8") as handle:
            for congress in congresses:
                for session in sessions:
                    menu = http.get(senate_menu_url(congress, session), follow_redirects=True)
                    if menu.status_code != 200:
                        continue
                    sessions_seen += 1
                    for number in parse_menu_vote_numbers(menu.text):
                        listed += 1
                        vote_id = f"senate-{congress}-{session}-{number}"
                        if vote_id in done:
                            continue
                        if max_votes is not None and new >= max_votes:
                            continue
                        try:
                            resp = http.get(
                                senate_vote_url(congress, session, number), follow_redirects=True
                            )
                            resp.raise_for_status()
                            record = senate_rich_record(resp.text, sectors_by_bill=sectors_by_bill)
                            handle.write(json.dumps(record) + "\n")
                            new += 1
                        except (httpx.HTTPError, ValueError, KeyError):
                            skipped += 1
    finally:
        if owns:
            http.close()
    return SenateBackfillProgress(
        sessions=sessions_seen,
        votes_listed=listed,
        votes_new=new,
        votes_skipped=skipped,
        rows_total=existing + new,
    )


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI glue
    import argparse

    parser = argparse.ArgumentParser(description="Resumable Senate roll-call backfill")
    parser.add_argument("--congresses", default="113-119")
    parser.add_argument("--out", default="data/real/senate_113_119_rich.jsonl")
    parser.add_argument("--content", default="data/exports/govinfo_bills/bill_content.jsonl")
    args = parser.parse_args(argv)
    if "-" in args.congresses:
        lo, hi = (int(x) for x in args.congresses.split("-", 1))
        congresses = list(range(lo, hi + 1))
    else:
        congresses = [int(x) for x in args.congresses.split(",") if x.strip()]
    progress = backfill_senate_votes(
        congresses=congresses, out_path=args.out, content_sidecar=args.content
    )
    print(
        f"sessions={progress.sessions} listed={progress.votes_listed} "
        f"new={progress.votes_new} skipped={progress.votes_skipped} rows={progress.rows_total}",
        flush=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
