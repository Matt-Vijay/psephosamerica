"""Reconstruct VoteRecords from the flat per-(member,bill) corpus (no sectors).

The sector-rich corpus exists only for the 118th House; the other congresses
(115-119) are stored flat: one row per (member, bill) with party/state/date. For
cross-congress transfer we reconstruct roll-calls by grouping flat rows on the
bill id, compute each party's majority lean on that bill, and emit ``VoteRecord``s
with **empty sectors**. With no sectors, ``sector_divergence`` degrades to the
member's overall pre-cutoff loyalty (its documented fallback), so this exercises
the *loyalty* signal's transfer across congresses honestly -- sector-aware
transfer awaits multi-congress sector ingest, which is noted in the report.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from src.runtime.cross_pressured_experiment import VoteRecord

_BINARY = {"yea", "nay"}


def build_vote_records_from_flat(path: Path, *, max_rows: int | None = None) -> list[VoteRecord]:
    """Group flat rows into roll-calls and emit VoteRecords (empty sectors)."""
    # (bill, date) is the roll-call grain; grouping by bill id alone pools distinct
    # roll-calls and corrupts the party-majority / defection labels.
    by_rollcall: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    with path.open(encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if not line.strip():
                continue
            if max_rows is not None and index >= max_rows:
                break
            row = json.loads(line)
            if str(row.get("vote_option")) in _BINARY:
                key = (str(row["canonical_bill_id"]), str(row["vote_date"])[:10])
                by_rollcall[key].append(row)

    records: list[VoteRecord] = []
    for rows in by_rollcall.values():
        party_yea: dict[str, int] = defaultdict(int)
        party_total: dict[str, int] = defaultdict(int)
        for row in rows:
            party = str(row.get("party", ""))
            party_total[party] += 1
            if row["vote_option"] == "yea":
                party_yea[party] += 1
        leans_yea = {p: party_yea[p] * 2 >= party_total[p] for p in party_total}
        for row in rows:
            party = str(row.get("party", ""))
            is_yea = row["vote_option"] == "yea"
            lean = leans_yea.get(party, True)
            records.append(
                VoteRecord(
                    member=str(row["member_bioguide_id"]),
                    party=party,
                    state=str(row.get("state", "")),
                    vote_date=date.fromisoformat(str(row["vote_date"])[:10]),
                    is_yea=is_yea,
                    party_alignment=1.0 if lean else -1.0,
                    sectors=(),
                    is_cross_pressured=is_yea != lean,
                )
            )
    return records


def build_linked_flat_votes(
    path: Path, *, max_rows: int | None = None
) -> list[tuple[VoteRecord, str]]:
    """Like ``build_vote_records_from_flat`` but pairs each record with its bill id.

    VoteRecord carries no bill id, so consumers that need the (member, bill) key --
    e.g. the prediction registry -- use these (record, bill_id) pairs.
    """
    # Group by (bill, DATE): one bill id spans many roll-calls (procedural, amendments,
    # final passage) on different dates -- pooling them corrupts the party-majority and
    # defection labels. (bill_id, vote_date) is the roll-call grain.
    by_rollcall: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    with path.open(encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if not line.strip():
                continue
            if max_rows is not None and index >= max_rows:
                break
            row = json.loads(line)
            if str(row.get("vote_option")) in _BINARY:
                key = (str(row["canonical_bill_id"]), str(row["vote_date"])[:10])
                by_rollcall[key].append(row)

    out: list[tuple[VoteRecord, str]] = []
    for (bill_id, _vote_day), rows in by_rollcall.items():
        party_yea: dict[str, int] = defaultdict(int)
        party_total: dict[str, int] = defaultdict(int)
        for row in rows:
            party = str(row.get("party", ""))
            party_total[party] += 1
            if row["vote_option"] == "yea":
                party_yea[party] += 1
        leans_yea = {p: party_yea[p] * 2 >= party_total[p] for p in party_total}
        for row in rows:
            party = str(row.get("party", ""))
            is_yea = row["vote_option"] == "yea"
            lean = leans_yea.get(party, True)
            record = VoteRecord(
                member=str(row["member_bioguide_id"]),
                party=party,
                state=str(row.get("state", "")),
                vote_date=date.fromisoformat(str(row["vote_date"])[:10]),
                is_yea=is_yea,
                party_alignment=1.0 if lean else -1.0,
                sectors=(),
                is_cross_pressured=is_yea != lean,
            )
            out.append((record, bill_id))
    return out
