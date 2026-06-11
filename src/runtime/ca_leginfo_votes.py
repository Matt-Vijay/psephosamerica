"""Keyless California roll-call vote ingest via partial-ZIP range extraction.

California publishes its full legislative database only as a ~975 MB
``pubinfo_<year>.zip`` at ``downloads.leginfo.legislature.ca.gov`` -- no
per-vote endpoint, which is why the v5 #2 gate marked CA "keyless but
impractical". But the server honours HTTP range requests and a ZIP's central
directory sits at the *end*: backing :class:`zipfile.ZipFile` with a
range-request reader lets us pull only ``BILL_DETAIL_VOTE_TBL.dat`` (~3.6 MB
compressed) out of the 975 MB archive without downloading the rest.

``BILL_DETAIL_VOTE_TBL.dat`` *is* the roll-call: one tab-delimited, backtick-
quoted row per legislator per motion (bill id, location, member, date, motion
id, AYE/NOE/ABS). Grouping by ``(bill_id, location, motion_id)`` reconstructs
each roll-call; the tallies are counted directly from the member rows (more
reliable than joining the summary table). The result is Track B's rich
roll-call shape -- a separate ``data/real`` artifact, like the Senate backfill,
that the state-vote model consumes.

Public-record only, keyless, robots-respecting; a malformed row is skipped, never
fabricated.
"""

from __future__ import annotations

import io
import zipfile
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import httpx

from src.graph.ingest.ca_leginfo import ca_session_of
from src.runtime.http_client import client_or_default

LEGINFO_BASE = "https://downloads.leginfo.legislature.ca.gov"
DETAIL_VOTE_FILE = "BILL_DETAIL_VOTE_TBL.dat"
LEGISLATOR_FILE = "LEGISLATOR_TBL.dat"
STATE = "CA"
# location_code prefixes -> the chamber a vote was cast in.
_CHAMBER_BY_PREFIX = {"AFLOOR": "assembly", "SFLOOR": "senate"}
# Normalize to the universal yea/nay vocab the Senate/House rich roll-calls use,
# so Track B's existing loader consumes CA votes unchanged (an AYE *is* a yea).
_CHOICES = {"AYE": "yea", "NOE": "nay", "ABS": "abstain", "NVR": "abstain"}
# chamber name -> LEGISLATOR_TBL house letter (column 4), for the surname->party join.
_HOUSE_LETTER = {"assembly": "A", "senate": "S"}
_UNKNOWN_PARTY = "U"


def pubinfo_zip_url(year: int) -> str:
    """The keyless full-database snapshot URL for a session year."""
    return f"{LEGINFO_BASE}/pubinfo_{year}.zip"


class HttpRangeReader(io.RawIOBase):
    """A seekable read-only file object backed by HTTP range requests.

    Exposes just enough of the binary file protocol (``seek``/``read``/``tell``)
    for :class:`zipfile.ZipFile` to read the central directory and a single entry
    without downloading the whole archive.
    """

    def __init__(self, url: str, *, client: httpx.Client) -> None:
        self._url = url
        self._client = client
        self._pos = 0
        response = client.head(url, follow_redirects=True)
        response.raise_for_status()
        self._size = int(response.headers["content-length"])

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            self._pos = offset
        elif whence == io.SEEK_CUR:
            self._pos += offset
        else:  # io.SEEK_END
            self._pos = self._size + offset
        return self._pos

    def tell(self) -> int:
        return self._pos

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = self._size - self._pos
        end = min(self._pos + size, self._size) - 1
        if size == 0 or end < self._pos:
            return b""
        response = self._client.get(
            self._url, headers={"Range": f"bytes={self._pos}-{end}"}, follow_redirects=True
        )
        response.raise_for_status()
        data = response.content
        self._pos += len(data)
        return data


def open_remote_zip(url: str, *, client: httpx.Client) -> zipfile.ZipFile:
    """Open a remote ZIP for selective extraction over HTTP range requests."""
    return zipfile.ZipFile(HttpRangeReader(url, client=client))


def _unquote(field: str) -> str:
    """Strip CA's backtick quoting (some fields are ```value```, some bare)."""
    field = field.strip()
    if len(field) >= 2 and field.startswith("`") and field.endswith("`"):
        return field[1:-1]
    return field


def parse_ca_bill_id(raw: str) -> tuple[str, str]:
    """``202520260AB1`` -> (session ``"2025-2026"``, measure ``"AB1"``).

    Layout: 4-digit start year, 4-digit end year, 1-digit special-session flag,
    then the measure. Extraordinary sessions (flag != 0) get a ``-x<digit>``
    session suffix so their measure numbers, which restart at 1, never collide
    with the regular session's (``...1AB1`` -> ``2025-2026-x1``).
    """
    return ca_session_of(raw), raw[9:]


def _chamber_for(location_code: str) -> str:
    return _CHAMBER_BY_PREFIX.get(location_code.upper(), "committee")


@dataclass(frozen=True)
class CaRollCall:
    """One CA roll-call: every member's choice on a single motion."""

    raw_bill_id: str
    session: str
    measure: str
    location_code: str
    motion_id: str
    motion_seq: str
    chamber: str
    date: str
    votes: tuple[tuple[str, str], ...]  # (member, normalized choice)

    @property
    def ayes(self) -> int:
        return sum(1 for _, choice in self.votes if choice == "yea")

    @property
    def noes(self) -> int:
        return sum(1 for _, choice in self.votes if choice == "nay")

    @property
    def other(self) -> int:
        return sum(1 for _, choice in self.votes if choice not in ("yea", "nay"))

    @property
    def result(self) -> str:
        """Plurality outcome (ayes > noes). Not CA's true passage threshold (a
        floor measure often needs a majority of *elected* members), so consumers
        wanting official passage should apply the rule themselves; the per-member
        tallies are the ground truth."""
        return "pass" if self.ayes > self.noes else "fail"


def parse_detail_rollcalls(text: str, *, floor_only: bool = False) -> list[CaRollCall]:
    """Group ``BILL_DETAIL_VOTE_TBL`` member rows into roll-calls (insertion order).

    Key = ``(bill_id, location_code, motion_id, motion_seq, vote_datetime)``. CA
    *reuses* a ``motion_id`` across separate vote events -- both on different days
    (a reconsideration) and within the same second (consecutive motions differ
    only by the ``motion_seq`` in column 5). Grouping on the id alone merges two
    roll-calls and overflows the chamber size, so the per-motion sequence and the
    full timestamp both join the key. Rows with fewer than seven columns, an
    unparseable bill id, or an unknown vote code are skipped.
    """
    groups: OrderedDict[tuple[str, str, str, str, str], list[tuple[str, str, str]]] = OrderedDict()
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = [_unquote(part) for part in line.split("\t")]
        if len(fields) < 7:
            continue
        raw_bill_id, location_code, member, vote_date = fields[0], fields[1], fields[2], fields[3]
        motion_seq, vote_code, motion_id = fields[4], fields[5], fields[6]
        choice = _CHOICES.get(vote_code.upper())
        if choice is None or not member:
            continue
        if floor_only and location_code.upper() not in _CHAMBER_BY_PREFIX:
            continue
        groups.setdefault(
            (raw_bill_id, location_code, motion_id, motion_seq, vote_date), []
        ).append((member, choice, vote_date))

    rollcalls: list[CaRollCall] = []
    for (raw_bill_id, location_code, motion_id, motion_seq, _vote_dt), members in groups.items():
        try:
            session, measure = parse_ca_bill_id(raw_bill_id)
        except ValueError:
            continue
        rollcalls.append(
            CaRollCall(
                raw_bill_id=raw_bill_id,
                session=session,
                measure=measure,
                location_code=location_code,
                motion_id=motion_id,
                motion_seq=motion_seq,
                chamber=_chamber_for(location_code),
                date=members[0][2][:10],
                votes=tuple((member, choice) for member, choice, _ in members),
            )
        )
    return rollcalls


def parse_legislator_party(text: str) -> dict[tuple[str, str], str]:
    """``LEGISLATOR_TBL`` -> ``{(surname_lower, house_letter): party}``.

    The vote table identifies a member only by surname, so the party join is keyed
    by ``(last name, house)`` (house from column 4: ``S``/``A``). A surname that
    maps to more than one party within a house is left out (resolves to unknown).
    """
    parties: dict[tuple[str, str], str] = {}
    conflicts: set[tuple[str, str]] = set()
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = [_unquote(part) for part in line.split("\t")]
        if len(fields) < 12:
            continue
        house, last_name, party = fields[3].upper(), fields[4].strip().lower(), fields[11].strip()
        if not last_name or not party or house not in ("A", "S"):
            continue
        key = (last_name, house)
        if key in parties and parties[key] != party:
            conflicts.add(key)
        parties[key] = party
    for key in conflicts:
        del parties[key]
    return parties


def _party_of(member: str, chamber: str, parties: dict[tuple[str, str], str]) -> str:
    house = _HOUSE_LETTER.get(chamber)
    if house is None:
        return _UNKNOWN_PARTY
    return parties.get((member.strip().lower(), house), _UNKNOWN_PARTY)


def ca_rollcall_record(
    rollcall: CaRollCall, *, parties: dict[tuple[str, str], str] | None = None
) -> dict[str, object]:
    """A CA roll-call in Track B's rich-roll-call shape (state-vote model input).

    ``votes`` are 4-tuples ``[member, party, state, choice]`` matching the
    Senate/House rich format, so Track B's loader consumes them unchanged. Party
    is joined from ``LEGISLATOR_TBL`` by ``(surname, house)`` (``U`` if unresolved).
    """
    party_index = parties if parties is not None else {}
    return {
        "bill_id": f"ca:{rollcall.session}:{rollcall.measure}",
        "raw_bill_id": rollcall.raw_bill_id,
        "date": rollcall.date,
        "chamber": rollcall.chamber,
        "location_code": rollcall.location_code,
        "motion_id": rollcall.motion_id,
        "motion_seq": rollcall.motion_seq,
        "sectors": [],  # CA bills are not in the federal CRS corpus
        "result": rollcall.result,
        "ayes": rollcall.ayes,
        "noes": rollcall.noes,
        "other": rollcall.other,
        "votes": [
            [member, _party_of(member, rollcall.chamber, party_index), STATE, choice]
            for member, choice in rollcall.votes
        ],
    }


@dataclass(frozen=True)
class CaBackfillProgress:
    """Counts from one CA roll-call backfill pass."""

    rollcalls_written: int
    members_total: int
    rollcalls_total: int


def _done_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    import json

    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            keys.add(_rollcall_key(record))
    return keys


def _rollcall_key(record: dict[str, object]) -> str:
    return (
        f"{record['raw_bill_id']}|{record['location_code']}|"
        f"{record['motion_id']}|{record['motion_seq']}"
    )


def backfill_ca_votes(
    *,
    year: int,
    out_path: Path | str,
    client: httpx.Client | None = None,
    floor_only: bool = False,
    max_rollcalls: int | None = None,
    detail_text: str | None = None,
    legislator_text: str | None = None,
) -> CaBackfillProgress:
    """Range-extract the CA vote + legislator tables, parse roll-calls, append unseen.

    ``detail_text`` / ``legislator_text`` let a caller pass already-extracted
    ``.dat`` content (the network path is exercised in integration, not unit,
    tests). Party is joined from ``LEGISLATOR_TBL`` so each vote is a Track-B
    4-tuple. Resumable: a roll-call already in ``out_path`` is skipped (keyed by
    bill+location+motion id+seq).
    """
    import json

    out = Path(out_path)
    if detail_text is None:
        # 120s: a single ranged read of the compressed vote table is several MB.
        http, owns = client_or_default(client, timeout=120.0)
        try:
            with open_remote_zip(pubinfo_zip_url(year), client=http) as archive:
                with archive.open(DETAIL_VOTE_FILE) as handle:
                    detail_text = handle.read().decode("latin-1")
                with archive.open(LEGISLATOR_FILE) as legislators:
                    legislator_text = legislators.read().decode("latin-1")
        finally:
            if owns:
                http.close()

    parties = parse_legislator_party(legislator_text) if legislator_text else {}
    rollcalls = parse_detail_rollcalls(detail_text, floor_only=floor_only)
    done = _done_keys(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = (
        sum(1 for line in out.read_text().splitlines() if line.strip()) if out.exists() else 0
    )
    written = members = 0
    with out.open("a", encoding="utf-8") as handle:
        for rollcall in rollcalls:
            record = ca_rollcall_record(rollcall, parties=parties)
            if _rollcall_key(record) in done:
                continue
            if max_rollcalls is not None and written >= max_rollcalls:
                break
            handle.write(json.dumps(record) + "\n")
            written += 1
            members += len(rollcall.votes)
    return CaBackfillProgress(
        rollcalls_written=written, members_total=members, rollcalls_total=existing + written
    )


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI glue
    import argparse

    parser = argparse.ArgumentParser(description="Keyless CA roll-call vote backfill")
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--out", default="data/real/ca_votes_rich.jsonl")
    parser.add_argument("--floor-only", action="store_true")
    parser.add_argument("--max", type=int, default=None)
    args = parser.parse_args(argv)
    progress = backfill_ca_votes(
        year=args.year, out_path=args.out, floor_only=args.floor_only, max_rollcalls=args.max
    )
    print(
        f"rollcalls={progress.rollcalls_written} members={progress.members_total} "
        f"total={progress.rollcalls_total}",
        flush=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
