"""Parse FEC individual-contribution (``itcont.txt``) rows into typed records.

Each row is one disclosed contribution to a recipient committee. The contributor
is either another committee (when ``OTHER_ID`` is set — PAC-to-PAC and earmarked
flows) or an individual (by name). :class:`ContributionRow` is the typed,
parsed form; a runner resolves the committee IDs to canonical IDs and builds
``donation`` edges (positive amounts only — refunds are negative and dropped by
:func:`~src.graph.ingest.donations.donation_edge`).

Amounts are parsed to integer cents (no float drift); dates are FEC ``MMDDYYYY``.
This is a pure parser; provenance and the disclosure-lag ``known_at`` are applied
by the edge builder.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

_RECIPIENT = 0
_NAME = 7
_TXN_DATE = 13
_AMOUNT = 14
_OTHER_ID = 15
_SUB_ID = 20
_MIN_FIELDS = 21


@dataclass(frozen=True)
class ContributionRow:
    """One parsed FEC contribution."""

    recipient_committee_id: str
    contributor_committee_id: str | None
    contributor_name: str | None
    amount_cents: int
    transaction_date: date | None
    transaction_id: str


def _parse_amount_cents(raw: str) -> int:
    try:
        return int((Decimal(raw.strip()) * 100).to_integral_value())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"unparseable contribution amount: {raw!r}") from exc


def _parse_fec_date(raw: str) -> date | None:
    digits = raw.strip()
    if len(digits) != 8 or not digits.isdigit():
        return None
    try:
        return date(int(digits[4:8]), int(digits[0:2]), int(digits[2:4]))
    except ValueError:
        return None


def parse_contribution_line(line: str) -> ContributionRow:
    """Parse one ``itcont.txt`` line into a :class:`ContributionRow`."""
    fields = line.rstrip("\n").split("|")
    if len(fields) < _MIN_FIELDS:
        raise ValueError(f"contribution line has {len(fields)} fields, expected >= {_MIN_FIELDS}")
    recipient = fields[_RECIPIENT].strip()
    if not recipient:
        raise ValueError("contribution line has a blank recipient committee id")
    return ContributionRow(
        recipient_committee_id=recipient,
        contributor_committee_id=fields[_OTHER_ID].strip() or None,
        contributor_name=fields[_NAME].strip() or None,
        amount_cents=_parse_amount_cents(fields[_AMOUNT]),
        transaction_date=_parse_fec_date(fields[_TXN_DATE]),
        transaction_id=fields[_SUB_ID].strip(),
    )
