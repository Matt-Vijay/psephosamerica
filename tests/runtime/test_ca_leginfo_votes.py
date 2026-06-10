from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from src.runtime.ca_leginfo_votes import (
    DETAIL_VOTE_FILE,
    CaRollCall,
    HttpRangeReader,
    backfill_ca_votes,
    ca_rollcall_record,
    open_remote_zip,
    parse_ca_bill_id,
    parse_detail_rollcalls,
    pubinfo_zip_url,
)

# Two motions: an Assembly-floor pass on AB1 and a committee fail on SB9. Columns:
# bill_id, location, member, date, motion_seq, vote_code, motion_id, ...
_DETAIL = "\n".join(
    [
        "`202520260AB1`\t`AFLOOR`\t`Wicks`\t2025-05-23 00:00:00\t1\t`AYE`\t`100`\tx\tts\t1\td\tNULL",
        "`202520260AB1`\t`AFLOOR`\t`Sanchez`\t2025-05-23 00:00:00\t1\t`NOE`\t`100`\tx\tts\t2\td\tN",
        "`202520260AB1`\t`AFLOOR`\t`Lee`\t2025-05-23 00:00:00\t1\t`ABS`\t`100`\tx\tts\t3\td\tNULL",
        "`202520260SB9`\t`CX25`\t`Durazo`\t2025-04-10 00:00:00\t1\t`NOE`\t`200`\tx\tts\t1\td\tNULL",
        "`202520260SB9`\t`CX25`\t`Allen`\t2025-04-10 00:00:00\t1\t`NOE`\t`200`\tx\tts\t2\td\tNULL",
        "",  # blank line tolerated
        "`202520260AB1`\t`AFLOOR`\t`badrow`",  # too few columns -> skipped
        "`202520260AB1`\t`AFLOOR`\t`Skip`\t2025-05-23 00:00:00\t1\t`PRES`\t`100`\tx",  # bad code
        "`BADID`\t`AFLOOR`\t`Nobody`\t2025-05-23 00:00:00\t1\t`AYE`\t`999`\tx",  # bad bill id
    ]
)


def test_pubinfo_zip_url() -> None:
    assert pubinfo_zip_url(2025).endswith("/pubinfo_2025.zip")


def test_parse_ca_bill_id() -> None:
    assert parse_ca_bill_id("202520260AB1") == ("2025-2026", "AB1")
    assert parse_ca_bill_id("201520160SCR15") == ("2015-2016", "SCR15")


def test_parse_ca_bill_id_rejects_short() -> None:
    with pytest.raises(ValueError):
        parse_ca_bill_id("AB1")


def test_parse_detail_rollcalls_groups_and_tallies() -> None:
    rollcalls = parse_detail_rollcalls(_DETAIL)
    assert len(rollcalls) == 2  # AB1/AFLOOR/100 and SB9/CX25/200; bad-id row dropped its own group
    ab1 = next(r for r in rollcalls if r.measure == "AB1")
    assert ab1.chamber == "assembly" and ab1.session == "2025-2026"
    assert ab1.ayes == 1 and ab1.noes == 1 and ab1.other == 1
    assert ab1.result == "pass" if ab1.ayes > ab1.noes else "fail"  # 1 vs 1 -> fail
    assert ab1.result == "fail"
    assert ab1.date == "2025-05-23"
    assert ("Lee", "abstain") in ab1.votes
    sb9 = next(r for r in rollcalls if r.measure == "SB9")
    assert sb9.chamber == "committee" and sb9.noes == 2 and sb9.result == "fail"


def test_reused_motion_id_splits_by_timestamp() -> None:
    # CA reuses a motion_id (300) for two separate AFLOOR votes on AB7 at different
    # times -> they must become two roll-calls, not one double-counted member list.
    detail = "\n".join(
        [
            "`202520260AB7`\t`AFLOOR`\t`Lee`\t2025-08-29 09:39:51\t1\t`AYE`\t`300`\tx\tts\t1\td\tN",
            "`202520260AB7`\t`AFLOOR`\t`Bonta`\t2025-08-29 09:39:51\t1\t`AYE`\t`300`\tx\tts\t2\td\tN",
            "`202520260AB7`\t`AFLOOR`\t`Lee`\t2025-09-12 20:19:01\t1\t`NOE`\t`300`\tx\tts\t1\td\tN",
            "`202520260AB7`\t`AFLOOR`\t`Bonta`\t2025-09-12 20:19:01\t1\t`NOE`\t`300`\tx\tts\t2\td\tN",
        ]
    )
    rollcalls = parse_detail_rollcalls(detail)
    assert len(rollcalls) == 2  # split by vote timestamp despite the shared motion id
    assert {r.date for r in rollcalls} == {"2025-08-29", "2025-09-12"}
    assert all(len(r.votes) == 2 for r in rollcalls)  # neither overflows


def test_same_timestamp_different_motion_seq_splits() -> None:
    # Two motions on AB7 at the SAME second share motion_id 300 but differ in the
    # motion_seq (column 5: 1055 vs 1056) -> two roll-calls, not one merged 4-member.
    detail = "\n".join(
        [
            "`202520260AB7`\t`AFLOOR`\t`Lee`\t2025-05-23 09:24:35\t1055\t`AYE`\t`300`\tx\tts\t1\td\tN",
            "`202520260AB7`\t`AFLOOR`\t`Bonta`\t2025-05-23 09:24:35\t1055\t`AYE`\t`300`\tx\tts\t2\td\tN",
            "`202520260AB7`\t`AFLOOR`\t`Lee`\t2025-05-23 09:24:35\t1056\t`NOE`\t`300`\tx\tts\t1\td\tN",
            "`202520260AB7`\t`AFLOOR`\t`Bonta`\t2025-05-23 09:24:35\t1056\t`NOE`\t`300`\tx\tts\t2\td\tN",
        ]
    )
    rollcalls = parse_detail_rollcalls(detail)
    assert len(rollcalls) == 2 and all(len(r.votes) == 2 for r in rollcalls)


def test_floor_only_excludes_committee() -> None:
    rollcalls = parse_detail_rollcalls(_DETAIL, floor_only=True)
    assert {r.measure for r in rollcalls} == {"AB1"}  # SB9 committee vote excluded


def test_result_pass_when_ayes_exceed_noes() -> None:
    rc = CaRollCall(
        raw_bill_id="202520260AB2",
        session="2025-2026",
        measure="AB2",
        location_code="SFLOOR",
        motion_id="1",
        chamber="senate",
        date="2025-01-01",
        votes=(("A", "aye"), ("B", "aye"), ("C", "no")),
    )
    assert rc.result == "pass" and rc.ayes == 2 and rc.noes == 1 and rc.other == 0


def test_ca_rollcall_record_shape() -> None:
    rc = parse_detail_rollcalls(_DETAIL)[0]
    record = ca_rollcall_record(rc)
    assert record["bill_id"] == "ca:2025-2026:AB1"
    assert record["raw_bill_id"] == "202520260AB1"
    assert record["result"] == "fail"
    assert record["votes"][0] == ["Wicks", "aye"]
    assert set(record) == {
        "bill_id",
        "raw_bill_id",
        "date",
        "chamber",
        "location_code",
        "motion_id",
        "result",
        "ayes",
        "noes",
        "other",
        "votes",
    }


# --------------------------------------------------------------- range-zip extraction


def _zip_bytes(entries: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _range_client(blob: bytes) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Length": str(len(blob))})
        range_header = request.headers.get("Range", "")
        start_s, end_s = range_header.removeprefix("bytes=").split("-")
        start, end = int(start_s), int(end_s)
        return httpx.Response(206, content=blob[start : end + 1])

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_http_range_reader_protocol() -> None:
    blob = b"0123456789ABCDEF"
    reader = HttpRangeReader("https://h/f", client=_range_client(blob))
    assert reader.seekable() and reader.readable()
    assert reader.seek(4) == 4 and reader.tell() == 4
    assert reader.read(3) == b"456"  # advances pos to 7
    assert reader.seek(2, io.SEEK_CUR) == 9  # 7 + 2 (relative)
    assert reader.seek(-1, io.SEEK_END) == len(blob) - 1  # from end
    assert reader.read() == b"F"  # size<0 -> to EOF
    assert reader.read(0) == b""  # zero-length read short-circuits
    reader.seek(len(blob))
    assert reader.read(8) == b""  # at EOF -> empty


def test_open_remote_zip_extracts_single_entry() -> None:
    blob = _zip_bytes({DETAIL_VOTE_FILE: _DETAIL, "OTHER.dat": "x" * 5000})
    with open_remote_zip("https://leginfo/pubinfo_2025.zip", client=_range_client(blob)) as archive:
        with archive.open(DETAIL_VOTE_FILE) as handle:
            text = handle.read().decode("latin-1")
    assert "Wicks" in text and len(parse_detail_rollcalls(text)) == 2


def test_backfill_over_range_zip_writes_records(tmp_path: Path) -> None:
    blob = _zip_bytes({DETAIL_VOTE_FILE: _DETAIL})
    out = tmp_path / "ca.jsonl"
    progress = backfill_ca_votes(year=2025, out_path=out, client=_range_client(blob))
    assert progress.rollcalls_written == 2
    assert progress.members_total == 5  # 3 on AB1 + 2 on SB9
    first = json.loads(out.read_text().splitlines()[0])
    assert first["bill_id"] == "ca:2025-2026:AB1"


def test_backfill_with_detail_text_is_resumable(tmp_path: Path) -> None:
    out = tmp_path / "ca.jsonl"
    first = backfill_ca_votes(year=2025, out_path=out, detail_text=_DETAIL)
    assert first.rollcalls_written == 2
    second = backfill_ca_votes(year=2025, out_path=out, detail_text=_DETAIL)
    assert second.rollcalls_written == 0 and second.rollcalls_total == 2


def test_done_keys_ignores_blank_lines(tmp_path: Path) -> None:
    out = tmp_path / "ca.jsonl"
    # a blank line precedes a real record for AB1/AFLOOR/100 -> that roll-call is "done"
    record = ca_rollcall_record(
        next(r for r in parse_detail_rollcalls(_DETAIL) if r.measure == "AB1")
    )
    out.write_text("\n" + json.dumps(record) + "\n", encoding="utf-8")
    progress = backfill_ca_votes(year=2025, out_path=out, detail_text=_DETAIL)
    assert progress.rollcalls_written == 1  # only SB9 remained (AB1 already present)


def test_backfill_respects_max_rollcalls(tmp_path: Path) -> None:
    out = tmp_path / "ca.jsonl"
    progress = backfill_ca_votes(year=2025, out_path=out, detail_text=_DETAIL, max_rollcalls=1)
    assert progress.rollcalls_written == 1


def test_http_range_reader_default_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import src.runtime.ca_leginfo_votes as mod

    blob = _zip_bytes({DETAIL_VOTE_FILE: _DETAIL})
    real = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Length": str(len(blob))})
        start_s, end_s = request.headers["Range"].removeprefix("bytes=").split("-")
        return httpx.Response(206, content=blob[int(start_s) : int(end_s) + 1])

    monkeypatch.setattr(
        mod.httpx, "Client", lambda **_kw: real(transport=httpx.MockTransport(handler))
    )
    progress = backfill_ca_votes(year=2025, out_path=tmp_path / "ca.jsonl")
    assert progress.rollcalls_written == 2
