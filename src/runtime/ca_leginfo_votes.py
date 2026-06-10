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

LEGINFO_BASE = "https://downloads.leginfo.legislature.ca.gov"
DETAIL_VOTE_FILE = "BILL_DETAIL_VOTE_TBL.dat"
# location_code prefixes -> the chamber a vote was cast in.
_CHAMBER_BY_PREFIX = {"AFLOOR": "assembly", "SFLOOR": "senate"}
_CHOICES = {"AYE": "aye", "NOE": "no", "ABS": "abstain", "NVR": "abstain"}


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
    then the measure (type + number).
    """
    if len(raw) < 10 or not raw[:9].isdigit():
        raise ValueError(f"unparseable CA bill id: {raw!r}")
    session = f"{raw[:4]}-{raw[4:8]}"
    return session, raw[9:]


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
    chamber: str
    date: str
    votes: tuple[tuple[str, str], ...]  # (member, normalized choice)

    @property
    def ayes(self) -> int:
        return sum(1 for _, choice in self.votes if choice == "aye")

    @property
    def noes(self) -> int:
        return sum(1 for _, choice in self.votes if choice == "no")

    @property
    def other(self) -> int:
        return sum(1 for _, choice in self.votes if choice not in ("aye", "no"))

    @property
    def result(self) -> str:
        return "pass" if self.ayes > self.noes else "fail"


def parse_detail_rollcalls(text: str, *, floor_only: bool = False) -> list[CaRollCall]:
    """Group ``BILL_DETAIL_VOTE_TBL`` member rows into roll-calls (insertion order).

    Key = ``(bill_id, location_code, motion_id)``. Rows with fewer than seven
    columns, an unparseable bill id, or an unknown vote code are skipped.
    """
    groups: OrderedDict[tuple[str, str, str], list[tuple[str, str, str]]] = OrderedDict()
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = [_unquote(part) for part in line.split("\t")]
        if len(fields) < 7:
            continue
        raw_bill_id, location_code, member, vote_date = fields[0], fields[1], fields[2], fields[3]
        vote_code, motion_id = fields[5], fields[6]
        choice = _CHOICES.get(vote_code.upper())
        if choice is None or not member:
            continue
        if floor_only and location_code.upper() not in _CHAMBER_BY_PREFIX:
            continue
        groups.setdefault((raw_bill_id, location_code, motion_id), []).append(
            (member, choice, vote_date)
        )

    rollcalls: list[CaRollCall] = []
    for (raw_bill_id, location_code, motion_id), members in groups.items():
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
                chamber=_chamber_for(location_code),
                date=members[0][2][:10],
                votes=tuple((member, choice) for member, choice, _ in members),
            )
        )
    return rollcalls


def ca_rollcall_record(rollcall: CaRollCall) -> dict[str, object]:
    """A CA roll-call as Track B's rich-roll-call dict (state-vote model input)."""
    return {
        "bill_id": f"ca:{rollcall.session}:{rollcall.measure}",
        "raw_bill_id": rollcall.raw_bill_id,
        "date": rollcall.date,
        "chamber": rollcall.chamber,
        "location_code": rollcall.location_code,
        "motion_id": rollcall.motion_id,
        "result": rollcall.result,
        "ayes": rollcall.ayes,
        "noes": rollcall.noes,
        "other": rollcall.other,
        "votes": [[member, choice] for member, choice in rollcall.votes],
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
            keys.add(f"{record['raw_bill_id']}|{record['location_code']}|{record['motion_id']}")
    return keys


def backfill_ca_votes(
    *,
    year: int,
    out_path: Path | str,
    client: httpx.Client | None = None,
    floor_only: bool = False,
    max_rollcalls: int | None = None,
    detail_text: str | None = None,
) -> CaBackfillProgress:
    """Range-extract the CA vote table, parse roll-calls, append unseen ones.

    ``detail_text`` lets a caller pass already-extracted ``.dat`` content (the
    network path is exercised in integration, not unit, tests). Resumable: a
    roll-call already in ``out_path`` (by bill+location+motion) is skipped.
    """
    import json

    out = Path(out_path)
    if detail_text is None:
        http, owns = _client_for(client)
        try:
            with open_remote_zip(pubinfo_zip_url(year), client=http) as archive:
                with archive.open(DETAIL_VOTE_FILE) as handle:
                    detail_text = handle.read().decode("latin-1")
        finally:
            if owns:
                http.close()

    rollcalls = parse_detail_rollcalls(detail_text, floor_only=floor_only)
    done = _done_keys(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = (
        sum(1 for line in out.read_text().splitlines() if line.strip()) if out.exists() else 0
    )
    written = members = 0
    with out.open("a", encoding="utf-8") as handle:
        for rollcall in rollcalls:
            key = f"{rollcall.raw_bill_id}|{rollcall.location_code}|{rollcall.motion_id}"
            if key in done:
                continue
            if max_rollcalls is not None and written >= max_rollcalls:
                break
            handle.write(json.dumps(ca_rollcall_record(rollcall)) + "\n")
            written += 1
            members += len(rollcall.votes)
    return CaBackfillProgress(
        rollcalls_written=written, members_total=members, rollcalls_total=existing + written
    )


def _client_for(client: httpx.Client | None) -> tuple[httpx.Client, bool]:
    if client is not None:
        return client, False
    return httpx.Client(timeout=120.0), True


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
