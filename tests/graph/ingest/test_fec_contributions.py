from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from src.graph.ingest.fec_contributions import parse_contribution_line

# Real FEC individual-contributions (itcont.txt) format, 21 pipe-delimited fields.
_INDIV = (
    "C00878454|N|12P|P2024|202408299675313804|15E|IND|JENNINGS, EMILY|SOMERVILLE|MA|"
    "021432389|SKADDEN ARPS|ATTORNEY|08052024|250|C00401224|4187316|1813451||"
    "* EARMARKED CONTRIBUTION: SEE BELOW|4083020242017155126"
)
# A committee-to-committee transfer with cents and a refund (negative).
_C2C = "C00100|N|Q2|P|img|24K|CCM|SOME PAC||DC|20001|||03152024|1500.50|C00200|t|f|||sub-1"
_REFUND = "C00100|N|Q2|P|img|24K|CCM|SOME PAC||DC|20001|||03152024|-50|C00200|t|f|||sub-2"


def test_parse_individual_contribution() -> None:
    row = parse_contribution_line(_INDIV)
    assert row.recipient_committee_id == "C00878454"
    assert row.contributor_committee_id == "C00401224"  # OTHER_ID
    assert row.contributor_name == "JENNINGS, EMILY"
    assert row.amount_cents == 25000  # $250
    assert row.transaction_date == date(2024, 8, 5)
    assert row.transaction_id == "4083020242017155126"


def test_parse_committee_to_committee_with_cents() -> None:
    row = parse_contribution_line(_C2C)
    assert row.recipient_committee_id == "C00100"
    assert row.contributor_committee_id == "C00200"
    assert row.amount_cents == 150050  # $1500.50


def test_parse_refund_keeps_sign() -> None:
    # Refunds are negative; the donation_edge builder is what rejects non-positive.
    assert parse_contribution_line(_REFUND).amount_cents == -5000


def test_missing_other_id_is_none() -> None:
    line = "C00100|N|Q2|P|img|15|IND|DOE, JANE|NYC|NY|10001|ACME|CEO|03152024|100||t|f|||sub-3"
    row = parse_contribution_line(line)
    assert row.contributor_committee_id is None
    assert row.contributor_name == "DOE, JANE"


def test_blank_date_is_none() -> None:
    line = "C00100|N|Q2|P|img|15|IND|DOE, JANE|NYC|NY|10001|ACME|CEO||100|C00200|t|f|||sub-4"
    assert parse_contribution_line(line).transaction_date is None


def test_rejects_short_line() -> None:
    with pytest.raises(ValueError, match="contribution line"):
        parse_contribution_line("C00100|N|Q2")


def test_rejects_blank_recipient() -> None:
    line = "|N|Q2|P|img|15|IND|DOE, JANE|NYC|NY|10001|ACME|CEO|03152024|100|C00200|t|f|||sub-5"
    with pytest.raises(ValueError, match="recipient"):
        parse_contribution_line(line)


def test_rejects_unparseable_amount() -> None:
    line = "C00100|N|Q2|P|img|15|IND|DOE, JANE|NYC|NY|10001|ACME|CEO|03152024|N/A|C00200|t|f|||s"
    with pytest.raises(ValueError, match="amount"):
        parse_contribution_line(line)


def test_invalid_date_is_none() -> None:
    line = "C00100|N|Q2|P|img|15|IND|DOE, JANE|NYC|NY|10001|ACME|CEO|99999999|100|C00200|t|f|||s6"
    assert parse_contribution_line(line).transaction_date is None


def test_contribution_row_is_frozen() -> None:
    row = parse_contribution_line(_C2C)
    with pytest.raises(FrozenInstanceError):
        row.amount_cents = 0  # type: ignore[misc]
