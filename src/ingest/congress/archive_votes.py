"""Load House and Senate vote records from a local archive directory.

Archive layout mirrors the upstream URL structure so that offline bundles
can be created by mirroring the remote paths verbatim:

  House:
    {archive_dir}/house/{year}/index.xml
    {archive_dir}/house/{year}/roll{number:03d}.xml

  Senate:
    {archive_dir}/senate/vote{congress}{session}/vote_summary.xml
    {archive_dir}/senate/vote{congress}{session}/vote_{congress}_{session}_{number:05d}.xml

Public interface
----------------
load_house_vote_records(archive_dir, year)
    -> tuple[list[VoteEventRecord], list[VoteCastRecord]]

load_senate_vote_records(archive_dir, congress, session)
    -> tuple[list[VoteEventRecord], list[VoteCastRecord]]

No network calls are made.  Missing index files raise FileNotFoundError.
Missing individual vote files are skipped and the roll-call number is
collected in a warnings list that callers may inspect.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .house_vote_index import parse_house_vote_index
from .house_votes import parse_house_vote_xml
from .models import VoteCastRecord, VoteEventRecord
from .senate_vote_index import parse_senate_vote_index
from .senate_votes import parse_senate_vote_xml


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VoteArchiveResult:
    """Parsed records from one archive index + its individual vote files."""

    events: list[VoteEventRecord]
    casts: list[VoteCastRecord]
    # Roll-call numbers whose XML file was absent in the archive.
    missing_roll_calls: list[int]


# ---------------------------------------------------------------------------
# Path helpers (pure functions; no I/O)
# ---------------------------------------------------------------------------


def _house_index_path(archive_dir: Path, year: int) -> Path:
    return archive_dir / "house" / str(year) / "index.xml"


def _house_vote_path(archive_dir: Path, year: int, roll_call_number: int) -> Path:
    return archive_dir / "house" / str(year) / f"roll{roll_call_number:03d}.xml"


def _senate_index_path(archive_dir: Path, congress: int, session: int) -> Path:
    prefix = f"vote{congress}{session}"
    return archive_dir / "senate" / prefix / "vote_summary.xml"


def _senate_vote_path(
    archive_dir: Path, congress: int, session: int, vote_number: int
) -> Path:
    prefix = f"vote{congress}{session}"
    filename = f"vote_{congress}_{session}_{vote_number:05d}.xml"
    return archive_dir / "senate" / prefix / filename


# ---------------------------------------------------------------------------
# Public loaders
# ---------------------------------------------------------------------------


def load_house_vote_records(
    archive_dir: Path | str,
    year: int,
) -> VoteArchiveResult:
    """Load all House vote events and casts for *year* from local files.

    Reads the index XML first to enumerate roll-call numbers, then reads
    each individual vote XML.  Individual files that are absent are skipped;
    their roll-call numbers appear in VoteArchiveResult.missing_roll_calls.

    Raises FileNotFoundError if the index file is absent.
    """
    archive_dir = Path(archive_dir)
    index_path = _house_index_path(archive_dir, year)
    index_xml = index_path.read_text(encoding="utf-8")
    index_rows = parse_house_vote_index(index_xml)

    events: list[VoteEventRecord] = []
    casts: list[VoteCastRecord] = []
    missing: list[int] = []

    for row in index_rows:
        vote_path = _house_vote_path(archive_dir, year, row.roll_call_number)
        if not vote_path.exists():
            missing.append(row.roll_call_number)
            continue
        vote_xml = vote_path.read_text(encoding="utf-8")
        event, vote_casts = parse_house_vote_xml(vote_xml)
        events.append(event)
        casts.extend(vote_casts)

    return VoteArchiveResult(events=events, casts=casts, missing_roll_calls=missing)


def load_senate_vote_records(
    archive_dir: Path | str,
    congress: int,
    session: int,
) -> VoteArchiveResult:
    """Load all Senate vote events and casts for *congress*/*session* from local files.

    Reads the vote_summary.xml index first to enumerate vote numbers, then
    reads each individual vote XML.  Individual files that are absent are
    skipped; their vote numbers appear in VoteArchiveResult.missing_roll_calls.

    Raises FileNotFoundError if the index file is absent.
    """
    archive_dir = Path(archive_dir)
    index_path = _senate_index_path(archive_dir, congress, session)
    index_xml = index_path.read_text(encoding="utf-8")
    index_rows = parse_senate_vote_index(index_xml, congress=congress, session=session)

    events: list[VoteEventRecord] = []
    casts: list[VoteCastRecord] = []
    missing: list[int] = []

    for row in index_rows:
        vote_path = _senate_vote_path(archive_dir, congress, session, row.vote_number)
        if not vote_path.exists():
            missing.append(row.vote_number)
            continue
        vote_xml = vote_path.read_text(encoding="utf-8")
        event, vote_casts = parse_senate_vote_xml(vote_xml)
        events.append(event)
        casts.extend(vote_casts)

    return VoteArchiveResult(events=events, casts=casts, missing_roll_calls=missing)
