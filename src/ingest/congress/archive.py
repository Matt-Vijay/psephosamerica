"""Congress archive layout — typed source contracts and path helpers.

An archive is a directory tree of JSON/XML files mirroring Congress.gov API
responses and official vote feeds, produced by a prior fetch and intended
for offline / deterministic replay.

Expected layout::

    {root}/
      members.json
      committees.json
      bills.json
      member_details/
        {bioguide_id}.json
      bill_details/
        {congress}_{bill_type}_{bill_number}.json
      cosponsors/
        {congress}_{bill_type}_{bill_number}.json
      house_votes/
        {year}_{roll_call_number:04d}.xml          (optional)
      senate_votes/
        {congress}_{session}_{roll_call_number:05d}.xml  (optional)

No I/O is performed here; callers own all file reads.

Source types
------------
Each payload kind has a small immutable typed descriptor:

    MembersSource, CommitteesSource, BillsSource
    CosponsorsSource, MemberDetailSource, BillDetailSource
    HouseVoteSource, SenateVoteSource

A CongressArchiveManifest bundles all resolved source descriptors.
Build one with manifest_from_archive() or manifest_from_dict().
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from src.core.path_safety import require_confined_relative_path, safe_join_confined


# ---------------------------------------------------------------------------
# Immutable source descriptor types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MembersSource:
    """Resolved path to the members-list JSON payload."""

    path: Path
    congress: int


@dataclass(frozen=True, slots=True)
class CommitteesSource:
    """Resolved path to the committees-list JSON payload for one congress."""

    path: Path
    congress: int


@dataclass(frozen=True, slots=True)
class BillsSource:
    """Resolved path to the bills-list JSON payload for one congress."""

    path: Path
    congress: int


@dataclass(frozen=True, slots=True)
class CosponsorsSource:
    """Resolved path to the cosponsors JSON payload for one bill."""

    path: Path
    congress: int
    bill_type: str
    bill_number: int


@dataclass(frozen=True, slots=True)
class MemberDetailSource:
    """Resolved path to a single member-detail JSON payload."""

    path: Path
    bioguide_id: str


@dataclass(frozen=True, slots=True)
class BillDetailSource:
    """Resolved path to a single bill-detail JSON payload."""

    path: Path
    congress: int
    bill_type: str
    bill_number: int


@dataclass(frozen=True, slots=True)
class HouseVoteSource:
    """Resolved path to a House roll-call XML file."""

    path: Path
    year: int
    roll_call_number: int


@dataclass(frozen=True, slots=True)
class SenateVoteSource:
    """Resolved path to a Senate roll-call XML file."""

    path: Path
    congress: int
    session_number: int
    roll_call_number: int


# ---------------------------------------------------------------------------
# Archive manifest — immutable bundle of all resolved sources
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CongressArchiveManifest:
    """All typed payload sources for one Congress archive snapshot.

    Attributes:
        congress:       Congress number (e.g. 119).
        members:        Bulk members list source.
        committees:     Bulk committees list source.
        bills:          Bulk bills list source.
        cosponsors:     Per-bill cosponsor sources (may be empty).
        member_details: Per-member detail sources (may be empty).
        bill_details:   Per-bill detail sources (may be empty).
        house_votes:    House roll-call sources (optional; may be empty).
        senate_votes:   Senate roll-call sources (optional; may be empty).
    """

    congress: int
    members: MembersSource
    committees: CommitteesSource
    bills: BillsSource
    cosponsors: tuple[CosponsorsSource, ...]
    member_details: tuple[MemberDetailSource, ...]
    bill_details: tuple[BillDetailSource, ...]
    house_votes: tuple[HouseVoteSource, ...]
    senate_votes: tuple[SenateVoteSource, ...]


# ---------------------------------------------------------------------------
# CongressArchive — path resolver for conventional directory layout
# ---------------------------------------------------------------------------


class CongressArchive:
    """Resolve payload paths within a local Congress archive directory.

    All methods return typed source descriptors; no files are read.
    """

    MEMBER_DETAILS_DIR = "member_details"
    BILL_DETAILS_DIR = "bill_details"
    COSPONSORS_DIR = "cosponsors"
    HOUSE_VOTES_DIR = "house_votes"
    SENATE_VOTES_DIR = "senate_votes"

    def __init__(self, root: Path, congress: int = 0) -> None:
        self.root = root
        self.congress = congress

    # ------------------------------------------------------------------
    # List payload sources
    # ------------------------------------------------------------------

    def members_source(self) -> MembersSource:
        return MembersSource(path=self.root / "members.json", congress=self.congress)

    def committees_source(self) -> CommitteesSource:
        return CommitteesSource(path=self.root / "committees.json", congress=self.congress)

    def bills_source(self) -> BillsSource:
        return BillsSource(path=self.root / "bills.json", congress=self.congress)

    # ------------------------------------------------------------------
    # Detail and sub-resource payload sources
    # ------------------------------------------------------------------

    def member_detail_source(self, bioguide_id: str) -> MemberDetailSource:
        path = self.root / self.MEMBER_DETAILS_DIR / f"{bioguide_id}.json"
        return MemberDetailSource(path=path, bioguide_id=bioguide_id)

    def bill_detail_source(
        self, congress: int, bill_type: str, bill_number: int
    ) -> BillDetailSource:
        stem = _bill_stem(congress, bill_type, bill_number)
        path = self.root / self.BILL_DETAILS_DIR / f"{stem}.json"
        return BillDetailSource(
            path=path, congress=congress, bill_type=bill_type, bill_number=bill_number
        )

    def cosponsors_source(
        self, congress: int, bill_type: str, bill_number: int
    ) -> CosponsorsSource:
        stem = _bill_stem(congress, bill_type, bill_number)
        path = self.root / self.COSPONSORS_DIR / f"{stem}.json"
        return CosponsorsSource(
            path=path, congress=congress, bill_type=bill_type, bill_number=bill_number
        )

    def house_vote_source(self, year: int, roll_call_number: int) -> HouseVoteSource:
        filename = f"{year}_{roll_call_number:04d}.xml"
        path = self.root / self.HOUSE_VOTES_DIR / filename
        return HouseVoteSource(path=path, year=year, roll_call_number=roll_call_number)

    def senate_vote_source(
        self, congress: int, session_number: int, roll_call_number: int
    ) -> SenateVoteSource:
        filename = f"{congress}_{session_number}_{roll_call_number:05d}.xml"
        path = self.root / self.SENATE_VOTES_DIR / filename
        return SenateVoteSource(
            path=path,
            congress=congress,
            session_number=session_number,
            roll_call_number=roll_call_number,
        )

    # ------------------------------------------------------------------
    # Directory accessors (for scanning)
    # ------------------------------------------------------------------

    def member_details_dir(self) -> Path:
        return self.root / self.MEMBER_DETAILS_DIR

    def bill_details_dir(self) -> Path:
        return self.root / self.BILL_DETAILS_DIR

    def cosponsors_dir(self) -> Path:
        return self.root / self.COSPONSORS_DIR

    def house_votes_dir(self) -> Path:
        return self.root / self.HOUSE_VOTES_DIR

    def senate_votes_dir(self) -> Path:
        return self.root / self.SENATE_VOTES_DIR

    # ------------------------------------------------------------------
    # Legacy path helpers (kept for callers that just need a Path)
    # ------------------------------------------------------------------

    def members_path(self) -> Path:
        return self.root / "members.json"

    def committees_path(self) -> Path:
        return self.root / "committees.json"

    def bills_path(self) -> Path:
        return self.root / "bills.json"

    def member_detail_path(self, bioguide_id: str) -> Path:
        return self.root / self.MEMBER_DETAILS_DIR / f"{bioguide_id}.json"

    def bill_detail_path(self, congress: int, bill_type: str, bill_number: int) -> Path:
        stem = _bill_stem(congress, bill_type, bill_number)
        return self.root / self.BILL_DETAILS_DIR / f"{stem}.json"

    def cosponsors_path(self, congress: int, bill_type: str, bill_number: int) -> Path:
        stem = _bill_stem(congress, bill_type, bill_number)
        return self.root / self.COSPONSORS_DIR / f"{stem}.json"


# ---------------------------------------------------------------------------
# Manifest builders
# ---------------------------------------------------------------------------


def manifest_from_archive(
    archive: CongressArchive,
    *,
    bill_keys: list[tuple[int, str, int]] | None = None,
    bioguide_ids: list[str] | None = None,
    house_vote_keys: list[tuple[int, int]] | None = None,
    senate_vote_keys: list[tuple[int, int, int]] | None = None,
) -> CongressArchiveManifest:
    """Build a CongressArchiveManifest from a CongressArchive and key lists.

    Args:
        archive:           Path resolver for the archive directory.
        bill_keys:         List of (congress, bill_type, bill_number) tuples for
                           which cosponsor and bill-detail sources are included.
        bioguide_ids:      Bioguide IDs for which member-detail sources are included.
        house_vote_keys:   List of (year, roll_call_number) tuples.
        senate_vote_keys:  List of (congress, session_number, roll_call_number) tuples.

    Any omitted key list produces an empty tuple for that source group.
    """
    bill_keys = bill_keys or []
    bioguide_ids = bioguide_ids or []
    house_vote_keys = house_vote_keys or []
    senate_vote_keys = senate_vote_keys or []

    return CongressArchiveManifest(
        congress=archive.congress,
        members=archive.members_source(),
        committees=archive.committees_source(),
        bills=archive.bills_source(),
        cosponsors=tuple(archive.cosponsors_source(c, bt, bn) for c, bt, bn in bill_keys),
        member_details=tuple(archive.member_detail_source(bid) for bid in bioguide_ids),
        bill_details=tuple(archive.bill_detail_source(c, bt, bn) for c, bt, bn in bill_keys),
        house_votes=tuple(archive.house_vote_source(yr, rc) for yr, rc in house_vote_keys),
        senate_votes=tuple(archive.senate_vote_source(c, sn, rc) for c, sn, rc in senate_vote_keys),
    )


def manifest_from_existing_archive(archive: CongressArchive) -> CongressArchiveManifest:
    """Build a manifest by scanning the archive tree already present on disk."""
    return CongressArchiveManifest(
        congress=archive.congress,
        members=archive.members_source(),
        committees=archive.committees_source(),
        bills=archive.bills_source(),
        cosponsors=_scan_cosponsors_sources(archive),
        member_details=_scan_member_detail_sources(archive),
        bill_details=_scan_bill_detail_sources(archive),
        house_votes=_scan_house_vote_sources(archive),
        senate_votes=_scan_senate_vote_sources(archive),
    )


def congress_archive_manifest_to_dict(
    manifest: CongressArchiveManifest,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Serialize a manifest into the JSON shape consumed by ``load_manifest``."""
    return {
        "congress": manifest.congress,
        "members": _manifest_path_text(manifest.members.path, root=root),
        "committees": _manifest_path_text(manifest.committees.path, root=root),
        "bills": _manifest_path_text(manifest.bills.path, root=root),
        "cosponsors": [
            {
                "congress": source.congress,
                "bill_type": source.bill_type,
                "bill_number": source.bill_number,
                "path": _manifest_path_text(source.path, root=root),
            }
            for source in manifest.cosponsors
        ],
        "member_details": [
            {
                "bioguide_id": source.bioguide_id,
                "path": _manifest_path_text(source.path, root=root),
            }
            for source in manifest.member_details
        ],
        "bill_details": [
            {
                "congress": source.congress,
                "bill_type": source.bill_type,
                "bill_number": source.bill_number,
                "path": _manifest_path_text(source.path, root=root),
            }
            for source in manifest.bill_details
        ],
        "house_votes": [
            {
                "year": source.year,
                "roll_call_number": source.roll_call_number,
                "path": _manifest_path_text(source.path, root=root),
            }
            for source in manifest.house_votes
        ],
        "senate_votes": [
            {
                "congress": source.congress,
                "session_number": source.session_number,
                "roll_call_number": source.roll_call_number,
                "path": _manifest_path_text(source.path, root=root),
            }
            for source in manifest.senate_votes
        ],
    }


def manifest_from_dict(
    data: dict[str, Any],
    *,
    root: Path,
) -> CongressArchiveManifest:
    """Build a CongressArchiveManifest from a parsed manifest dict.

    All relative path strings in ``data`` are resolved against ``root``.
    ``house_votes`` and ``senate_votes`` keys may be omitted (treated as empty).

    Raises ValueError if any required key is absent or any entry is malformed.

    Expected shape::

        {
            "congress": 119,
            "members": "members.json",
            "committees": "committees.json",
            "bills": "bills.json",
            "cosponsors": [
                {"congress": 119, "bill_type": "hr", "bill_number": 1,
                 "path": "cosponsors/119_hr_1.json"}
            ],
            "member_details": [
                {"bioguide_id": "P000197", "path": "member_details/P000197.json"}
            ],
            "bill_details": [
                {"congress": 119, "bill_type": "hr", "bill_number": 1,
                 "path": "bill_details/119_hr_1.json"}
            ],
            "house_votes": [...],   // optional
            "senate_votes": [...]   // optional
        }
    """
    if not isinstance(data, dict):
        raise TypeError(f"manifest must be a dict, got {type(data).__name__}")

    congress = _int_field(data, "congress", "manifest")

    members = MembersSource(
        path=_path_from_field(root, data, "members", "manifest"),
        congress=congress,
    )
    committees = CommitteesSource(
        path=_path_from_field(root, data, "committees", "manifest"),
        congress=congress,
    )
    bills = BillsSource(
        path=_path_from_field(root, data, "bills", "manifest"),
        congress=congress,
    )

    cosponsors = tuple(
        _cosponsor_source_from_dict(entry, i, root)
        for i, entry in enumerate(_list_field(data, "cosponsors", "manifest"))
    )
    member_details = tuple(
        _member_detail_source_from_dict(entry, i, root)
        for i, entry in enumerate(_list_field(data, "member_details", "manifest"))
    )
    bill_details = tuple(
        _bill_detail_source_from_dict(entry, i, root)
        for i, entry in enumerate(_list_field(data, "bill_details", "manifest"))
    )
    house_votes = tuple(
        _house_vote_source_from_dict(entry, i, root)
        for i, entry in enumerate(_list_field(data, "house_votes", "manifest", default=[]))
    )
    senate_votes = tuple(
        _senate_vote_source_from_dict(entry, i, root)
        for i, entry in enumerate(_list_field(data, "senate_votes", "manifest", default=[]))
    )

    return CongressArchiveManifest(
        congress=congress,
        members=members,
        committees=committees,
        bills=bills,
        cosponsors=cosponsors,
        member_details=member_details,
        bill_details=bill_details,
        house_votes=house_votes,
        senate_votes=senate_votes,
    )


# ---------------------------------------------------------------------------
# Row constructors for manifest_from_dict (internal)
# ---------------------------------------------------------------------------


def _cosponsor_source_from_dict(raw: Any, idx: int, root: Path) -> CosponsorsSource:
    _require_dict(raw, "cosponsors", idx)
    return CosponsorsSource(
        path=_path_from_field(root, raw, "path", f"cosponsors[{idx}]"),
        congress=_int_field(raw, "congress", f"cosponsors[{idx}]"),
        bill_type=_str_field(raw, "bill_type", f"cosponsors[{idx}]"),
        bill_number=_int_field(raw, "bill_number", f"cosponsors[{idx}]"),
    )


def _member_detail_source_from_dict(raw: Any, idx: int, root: Path) -> MemberDetailSource:
    _require_dict(raw, "member_details", idx)
    return MemberDetailSource(
        path=_path_from_field(root, raw, "path", f"member_details[{idx}]"),
        bioguide_id=_str_field(raw, "bioguide_id", f"member_details[{idx}]"),
    )


def _bill_detail_source_from_dict(raw: Any, idx: int, root: Path) -> BillDetailSource:
    _require_dict(raw, "bill_details", idx)
    return BillDetailSource(
        path=_path_from_field(root, raw, "path", f"bill_details[{idx}]"),
        congress=_int_field(raw, "congress", f"bill_details[{idx}]"),
        bill_type=_str_field(raw, "bill_type", f"bill_details[{idx}]"),
        bill_number=_int_field(raw, "bill_number", f"bill_details[{idx}]"),
    )


def _house_vote_source_from_dict(raw: Any, idx: int, root: Path) -> HouseVoteSource:
    _require_dict(raw, "house_votes", idx)
    return HouseVoteSource(
        path=_path_from_field(root, raw, "path", f"house_votes[{idx}]"),
        year=_int_field(raw, "year", f"house_votes[{idx}]"),
        roll_call_number=_int_field(raw, "roll_call_number", f"house_votes[{idx}]"),
    )


def _senate_vote_source_from_dict(raw: Any, idx: int, root: Path) -> SenateVoteSource:
    _require_dict(raw, "senate_votes", idx)
    return SenateVoteSource(
        path=_path_from_field(root, raw, "path", f"senate_votes[{idx}]"),
        congress=_int_field(raw, "congress", f"senate_votes[{idx}]"),
        session_number=_int_field(raw, "session_number", f"senate_votes[{idx}]"),
        roll_call_number=_int_field(raw, "roll_call_number", f"senate_votes[{idx}]"),
    )


_HOUSE_LEGACY_RE = re.compile(r"^(?P<year>\d{4})_(?P<roll>\d+)\.xml$")
_HOUSE_ARCHIVE_RE = re.compile(r"^roll(?P<roll>\d+)\.xml$")
_SENATE_LEGACY_RE = re.compile(r"^(?P<congress>\d+)_(?P<session>\d+)_(?P<roll>\d+)\.xml$")
_SENATE_ARCHIVE_RE = re.compile(r"^vote_(?P<congress>\d+)_(?P<session>\d+)_(?P<roll>\d+)\.xml$")


def _scan_member_detail_sources(archive: CongressArchive) -> tuple[MemberDetailSource, ...]:
    detail_dir = archive.member_details_dir()
    if not detail_dir.is_dir():
        return ()
    return tuple(
        archive.member_detail_source(path.stem) for path in sorted(detail_dir.glob("*.json"))
    )


def _scan_bill_detail_sources(archive: CongressArchive) -> tuple[BillDetailSource, ...]:
    detail_dir = archive.bill_details_dir()
    if not detail_dir.is_dir():
        return ()
    sources: list[BillDetailSource] = []
    for path in sorted(detail_dir.glob("*.json")):
        try:
            congress, bill_type, bill_number = parse_bill_stem(path.stem)
        except ValueError:
            continue
        sources.append(
            BillDetailSource(
                path=path,
                congress=congress,
                bill_type=bill_type,
                bill_number=bill_number,
            )
        )
    return tuple(sources)


def _scan_cosponsors_sources(archive: CongressArchive) -> tuple[CosponsorsSource, ...]:
    cosponsor_dir = archive.cosponsors_dir()
    if not cosponsor_dir.is_dir():
        return ()
    sources: list[CosponsorsSource] = []
    for path in sorted(cosponsor_dir.glob("*.json")):
        try:
            congress, bill_type, bill_number = parse_bill_stem(path.stem)
        except ValueError:
            continue
        sources.append(
            CosponsorsSource(
                path=path,
                congress=congress,
                bill_type=bill_type,
                bill_number=bill_number,
            )
        )
    return tuple(sources)


def _scan_house_vote_sources(archive: CongressArchive) -> tuple[HouseVoteSource, ...]:
    sources: dict[tuple[int, int], HouseVoteSource] = {}

    legacy_dir = archive.house_votes_dir()
    if legacy_dir.is_dir():
        for path in sorted(legacy_dir.glob("*.xml")):
            match = _HOUSE_LEGACY_RE.fullmatch(path.name)
            if match is None:
                continue
            year = int(match.group("year"))
            roll = int(match.group("roll"))
            sources[(year, roll)] = HouseVoteSource(path=path, year=year, roll_call_number=roll)

    archive_dir = archive.root / "house"
    if archive_dir.is_dir():
        for year_dir in sorted(archive_dir.iterdir()):
            if not year_dir.is_dir():
                continue
            try:
                year = int(year_dir.name)
            except ValueError:
                continue
            for path in sorted(year_dir.glob("roll*.xml")):
                match = _HOUSE_ARCHIVE_RE.fullmatch(path.name)
                if match is None:
                    continue
                roll = int(match.group("roll"))
                sources[(year, roll)] = HouseVoteSource(
                    path=path,
                    year=year,
                    roll_call_number=roll,
                )

    return tuple(sources[key] for key in sorted(sources))


def _scan_senate_vote_sources(archive: CongressArchive) -> tuple[SenateVoteSource, ...]:
    sources: dict[tuple[int, int, int], SenateVoteSource] = {}

    legacy_dir = archive.senate_votes_dir()
    if legacy_dir.is_dir():
        for path in sorted(legacy_dir.glob("*.xml")):
            match = _SENATE_LEGACY_RE.fullmatch(path.name)
            if match is None:
                continue
            congress = int(match.group("congress"))
            session = int(match.group("session"))
            roll = int(match.group("roll"))
            sources[(congress, session, roll)] = SenateVoteSource(
                path=path,
                congress=congress,
                session_number=session,
                roll_call_number=roll,
            )

    archive_dir = archive.root / "senate"
    if archive_dir.is_dir():
        for session_dir in sorted(archive_dir.iterdir()):
            if not session_dir.is_dir():
                continue
            for path in sorted(session_dir.glob("vote_*.xml")):
                match = _SENATE_ARCHIVE_RE.fullmatch(path.name)
                if match is None:
                    continue
                congress = int(match.group("congress"))
                session = int(match.group("session"))
                roll = int(match.group("roll"))
                sources[(congress, session, roll)] = SenateVoteSource(
                    path=path,
                    congress=congress,
                    session_number=session,
                    roll_call_number=roll,
                )

    return tuple(sources[key] for key in sorted(sources))


def _manifest_path_text(path: Path, *, root: Path | None) -> str:
    if root is None:
        return str(path)
    return str(path.relative_to(root))


# ---------------------------------------------------------------------------
# Typed field extractors (internal)
# ---------------------------------------------------------------------------


def _require_dict(raw: Any, section: str, idx: int) -> None:
    if not isinstance(raw, dict):
        raise ValueError(f"{section}[{idx}] must be an object, got {type(raw).__name__}")


def _str_field(raw: dict[str, Any], field: str, location: str) -> str:
    if field not in raw:
        raise ValueError(f"{location} missing required field: {field!r}")
    v = raw[field]
    if not isinstance(v, str):
        raise ValueError(f"{location}.{field} must be a string, got {type(v).__name__}")
    return v


def _path_field(raw: dict[str, Any], field: str, location: str) -> str:
    value = _str_field(raw, field, location)
    return require_confined_relative_path(value, label=f"{location}.{field}")


def _path_from_field(root: Path, raw: dict[str, Any], field: str, location: str) -> Path:
    return safe_join_confined(root, _path_field(raw, field, location), label=f"{location}.{field}")


def _int_field(raw: dict[str, Any], field: str, location: str) -> int:
    if field not in raw:
        raise ValueError(f"{location} missing required field: {field!r}")
    v = raw[field]
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError(f"{location}.{field} must be an integer, got {type(v).__name__}")
    return int(v)


def _list_field(
    raw: dict[str, Any],
    field: str,
    location: str,
    *,
    default: list[Any] | None = None,
) -> list[Any]:
    if field not in raw:
        if default is not None:
            return default
        raise ValueError(f"{location} missing required field: {field!r}")
    v = raw[field]
    if not isinstance(v, list):
        raise ValueError(f"{location}.{field} must be an array, got {type(v).__name__}")
    return v


# ---------------------------------------------------------------------------
# Stem helpers (for conventional directory layout)
# ---------------------------------------------------------------------------


def _bill_stem(congress: int, bill_type: str, bill_number: int) -> str:
    return f"{congress}_{bill_type}_{bill_number}"


def parse_bill_stem(stem: str) -> tuple[int, str, int]:
    """Parse '{congress}_{bill_type}_{bill_number}' back to typed fields.

    Raises ValueError if the stem does not match the expected pattern.
    """
    parts = stem.split("_", 2)
    if len(parts) != 3:
        raise ValueError(f"unexpected bill stem: {stem!r}")
    congress_str, bill_type, bill_number_str = parts
    return int(congress_str), bill_type, int(bill_number_str)
