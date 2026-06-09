"""Real declared-position features from public statements (for the transfer head).

OVERALL_GOAL.md's stated-position-to-vote-stance transfer head is trained on
declared positions -- here the sector attention of each member's public
statements (``data/prepared/public-statement-sector-rows.jsonl``). This loads
those real rows into per-member normalized sector-attention vectors, optionally
restricted to statements before a cutoff date (no leakage), which the
``stated_position_transfer`` head maps to vote stance.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path


def load_declared_positions(
    path: Path,
    *,
    before: date | None = None,
) -> dict[str, dict[str, float]]:
    """Per-member normalized sector-attention from public-statement sector rows.

    ``before`` restricts to statements dated strictly before that date, so a
    member's declared positions used to predict a vote never post-date it.
    """
    counts: dict[str, Counter[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        statement_date = record.get("statement_date")
        if before is not None and statement_date is not None:
            try:
                if date.fromisoformat(statement_date) >= before:
                    continue
            except ValueError:
                continue
        member = record.get("member_bioguide_id")
        sector = record.get("sector")
        if not member or not sector:
            continue
        counts.setdefault(member, Counter())[sector] += 1

    positions: dict[str, dict[str, float]] = {}
    for member, sector_counts in counts.items():
        total = sum(sector_counts.values())
        positions[member] = {sector: value / total for sector, value in sector_counts.items()}
    return positions
