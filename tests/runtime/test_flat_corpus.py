"""Both flat-corpus views must preserve the (bill, date) roll-call grain."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.runtime.flat_corpus import build_linked_flat_votes, build_vote_records_from_flat


def test_flat_views_agree_without_pooling_distinct_rollcalls(tmp_path: Path) -> None:
    rows = [
        {
            "canonical_bill_id": "hr1",
            "member_bioguide_id": member,
            "party": "D",
            "vote_date": day,
            "vote_option": choice,
        }
        for day, choices in (
            ("2024-01-01", ("yea", "yea", "nay")),
            ("2024-01-02", ("nay", "nay", "yea")),
        )
        for member, choice in zip(("A", "B", "C"), choices, strict=True)
    ]
    rows.append({**rows[0], "vote_option": "not voting"})
    source = tmp_path / "flat.jsonl"
    source.write_text("\n" + "\n".join(json.dumps(row) for row in rows) + "\n")
    linked = build_linked_flat_votes(source)
    records = build_vote_records_from_flat(source)
    assert records == [record for record, _bill in linked]
    assert len(records) == 6 and {bill for _record, bill in linked} == {"hr1"}
    assert [record.party_alignment for record in records] == [1, 1, 1, -1, -1, -1]
    assert [record.is_cross_pressured for record in records] == [False, False, True] * 2
    assert records[3].vote_date == date(2024, 1, 2)
    assert all(record.sectors == () for record in records)
    assert build_vote_records_from_flat(source, max_rows=3) == records[:2]
