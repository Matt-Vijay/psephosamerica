"""External (non-bill) defection signal providers: statement / donor / cosponsor.

Each is a ``SignalProvider`` (record -> features) so it drops into the defection
head via ``defection_signals.evaluate_signal`` without touching the head. All are
pre-cutoff and join by bioguide. Where the source corpus is sparse or not yet
ingested the provider degrades to a neutral 0 and the ΔAUC is reported honestly --
the point is to *measure* each signal, not to assume it helps.

* **statement engagement** -- per (member, sector), the member's pre-cutoff count
  of public statements on the bill's policy area (a vocal member may hold stronger,
  defection-prone views). Real but sparse (House statement corpus covers ~44
  members), so expect a near-zero ΔAUC.
* **donor independence** -- gated on a *precomputed* per-member donor profile
  (small-dollar / out-of-party share). The raw FEC ``itcont`` file is 1.6 GB, so
  this reads a prepared ``donor_profile.json`` when present and is otherwise a
  neutral 0 (reported as gated, like the dense-bill signals).
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from src.prediction.defection_signals import SignalProvider
from src.prediction.vote_record import VoteRecord


def statement_engagement_provider(
    statement_rows_path: Path, *, cutoff: date
) -> SignalProvider:
    """Per (member, sector) pre-cutoff public-statement counts, normalised by log1p."""
    import math

    counts: dict[tuple[str, str], int] = defaultdict(int)
    if statement_rows_path.exists():
        for line in statement_rows_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            try:
                row_date = date.fromisoformat(str(row.get("statement_date"))[:10])
            except (ValueError, TypeError):
                continue
            if row_date <= cutoff:
                counts[(str(row["member_bioguide_id"]), str(row["sector"]))] += 1

    def provider(record: VoteRecord) -> dict[str, float]:
        total = sum(counts.get((record.member, sector), 0) for sector in record.sectors)
        return {"statement_engagement": math.log1p(total)}

    return provider


def donor_independence_provider(profile_path: Path) -> SignalProvider:
    """Per-member donor-independence score from a prepared profile (gated on the file).

    The profile maps bioguide -> a 0..1 independence score (e.g. small-dollar or
    out-of-party donor share). Absent file -> neutral 0 for every member, so the
    signal is reported as gated rather than faked.
    """
    profile: dict[str, float] = {}
    if profile_path.exists():
        raw = json.loads(profile_path.read_text(encoding="utf-8"))
        profile = {str(k): float(v) for k, v in raw.items()}

    def provider(record: VoteRecord) -> dict[str, float]:
        return {"donor_independence": profile.get(record.member, 0.0)}

    return provider


def statement_corpus_coverage(statement_rows_path: Path) -> int:
    """Distinct members covered by the statement corpus (for honest gating notes)."""
    if not statement_rows_path.exists():
        return 0
    members: set[str] = set()
    for line in statement_rows_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            member = json.loads(line).get("member_bioguide_id")
        except ValueError:
            continue  # partially-written or corrupt line: count what is readable
        if member:
            members.add(str(member))
    return len(members)
