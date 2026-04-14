"""Helpers for reading FEC bulk-data files.

FEC distributes data as pipe-delimited text files with no header row.
Column positions are fixed per file type and documented in the FEC bulk
data guide.  This module provides typed row parsers for the three file
types needed at launch:

  - Committee Master      (cm.txt)
  - Candidate-Committee   (ccl.txt)
  - Individual Contribs   (indiv.txt)

No network calls.  Each parser is a generator that yields typed records
from an already-downloaded file path.
"""

from __future__ import annotations

import csv
import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Generator, TextIO, Union

from .models import CandidateCommitteeLinkage, CommitteeRecord, ContributionRecord

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_PIPE = "|"


def _open_bulk(path: Union[str, Path]) -> TextIO:
    """Open an FEC bulk file with the expected encoding."""
    return open(path, "r", encoding="latin-1", newline="")


def _field(row: list[str], idx: int) -> str | None:
    """Return stripped field or None if missing / empty."""
    if idx >= len(row):
        return None
    val = row[idx].strip()
    return val if val else None


def _parse_fec_date(raw: str | None) -> datetime.date | None:
    """Parse MM/DD/YYYY or MMDDYYYY date strings used in FEC files."""
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%m%d%Y", "%m/%d/%Y"):
        try:
            return datetime.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _parse_amount(raw: str | None) -> Decimal:
    """Parse a contribution amount, defaulting to zero on failure."""
    if not raw:
        return Decimal(0)
    try:
        return Decimal(raw.strip())
    except InvalidOperation:
        return Decimal(0)


# ---------------------------------------------------------------------------
# Committee Master  (cm.txt)
# Column layout per FEC bulk-data guide
# 0  CMTE_ID
# 1  CMTE_NM
# 2  TRES_NM
# 3  CMTE_ST1
# 4  CMTE_ST2
# 5  CMTE_CITY
# 6  CMTE_ST
# 7  CMTE_ZIP
# 8  CMTE_DSGN
# 9  CMTE_TP
# 10 CMTE_PTY_AFFILIATION
# 11 CMTE_FILING_FREQ
# 12 ORG_TP
# 13 CONNECTED_ORG_NM
# 14 CAND_ID
# ---------------------------------------------------------------------------

def iter_committee_master(path: Union[str, Path]) -> Generator[CommitteeRecord, None, None]:
    """Yield CommitteeRecord from a committee-master bulk file."""
    with _open_bulk(path) as fh:
        reader = csv.reader(fh, delimiter=_PIPE, quoting=csv.QUOTE_NONE)
        for row in reader:
            cmte_id = _field(row, 0)
            cmte_name = _field(row, 1)
            if not cmte_id or not cmte_name:
                continue
            yield CommitteeRecord(
                fec_committee_id=cmte_id,
                committee_name=cmte_name,
                treasurer_name=_field(row, 2),
                city=_field(row, 5),
                state=_field(row, 6),
                designation_code=_field(row, 8),
                committee_type=_field(row, 9),
                party=_field(row, 10),
                filing_frequency=_field(row, 11),
            )


# ---------------------------------------------------------------------------
# Candidate-Committee Linkage  (ccl.txt)
# 0  CAND_ID
# 1  CAND_ELECTION_YR
# 2  FEC_ELECTION_YR
# 3  CMTE_ID
# 4  CMTE_TP
# 5  CMTE_DSGN
# 6  LINKAGE_ID
# ---------------------------------------------------------------------------

def iter_candidate_committee_linkage(
    path: Union[str, Path],
) -> Generator[CandidateCommitteeLinkage, None, None]:
    """Yield CandidateCommitteeLinkage from a ccl bulk file."""
    with _open_bulk(path) as fh:
        reader = csv.reader(fh, delimiter=_PIPE, quoting=csv.QUOTE_NONE)
        for row in reader:
            cand_id = _field(row, 0)
            cmte_id = _field(row, 3)
            if not cand_id or not cmte_id:
                continue
            election_yr_raw = _field(row, 1)
            election_yr: int | None = None
            if election_yr_raw:
                try:
                    election_yr = int(election_yr_raw)
                except ValueError:
                    pass
            yield CandidateCommitteeLinkage(
                fec_candidate_id=cand_id,
                fec_committee_id=cmte_id,
                linkage_type=_field(row, 5) or "U",
                election_year=election_yr,
            )


# ---------------------------------------------------------------------------
# Individual Contributions  (itcont.txt / indiv.txt)
# 0  CMTE_ID
# 1  AMNDT_IND
# 2  RPT_TP
# 3  TRANSACTION_PGI
# 4  IMAGE_NUM
# 5  TRANSACTION_TP
# 6  ENTITY_TP
# 7  NAME
# 8  CITY
# 9  STATE
# 10 ZIP_CODE
# 11 EMPLOYER
# 12 OCCUPATION
# 13 TRANSACTION_DT
# 14 TRANSACTION_AMT
# 15 OTHER_ID
# 16 TRAN_ID
# 17 FILE_NUM
# 18 MEMO_CD
# 19 MEMO_TEXT
# 20 SUB_ID
# ---------------------------------------------------------------------------

def iter_individual_contributions(
    path: Union[str, Path],
) -> Generator[ContributionRecord, None, None]:
    """Yield ContributionRecord from an individual-contributions bulk file."""
    with _open_bulk(path) as fh:
        reader = csv.reader(fh, delimiter=_PIPE, quoting=csv.QUOTE_NONE)
        for row in reader:
            cmte_id = _field(row, 0)
            name = _field(row, 7)
            if not cmte_id or not name:
                continue
            amt = _parse_amount(_field(row, 14))
            if amt <= 0:
                continue
            yield ContributionRecord(
                fec_committee_id=cmte_id,
                donor_name=name,
                city=_field(row, 8),
                state=_field(row, 9),
                zip_code=_field(row, 10),
                employer=_field(row, 11),
                occupation=_field(row, 12),
                contribution_date=_parse_fec_date(_field(row, 13)),
                amount=amt,
                transaction_type=_field(row, 5),
                memo_text=_field(row, 19),
                sub_id=_field(row, 20),
                entity_type=_field(row, 6),
            )
