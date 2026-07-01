"""Ingest real Senate roll-call votes from senate.gov (v5 #3 — cross-chamber).

The contract carries Senate persons (lis ids) but not their votes; senate.gov
publishes roll-calls as public XML, so we ingest them ourselves (the only network
boundary), mirroring the House clerk ingest. Each Senate vote yields the same rich
shape the defection experiments consume -- ``{bill_id, date, congress, sectors,
votes:[[member, party, state, choice], ...]}`` -- with the member key = lis_member_id,
the bill id slugged from ``document_name`` (e.g. "S. 5" -> us_congress:119:s-5), and
sectors keyword-tagged from ``vote_document_text``. Enables House→Senate zero-shot /
joint transfer via the existing ``chamber_transfer`` harness.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
from defusedxml import ElementTree as DefusedET

from src.prediction.bill_sector import tag_bill_sectors

_CHOICE = {"yea": "yea", "guilty": "yea", "nay": "nay", "not guilty": "nay"}
_MENU = "https://www.senate.gov/legislative/LIS/roll_call_lists/vote_menu_{congress}_{session}.xml"
_VOTE = "https://www.senate.gov/legislative/LIS/roll_call_votes/vote{congress}{session}/vote_{congress}_{session}_{number:05d}.xml"


def _slug_bill(document_name: str, congress: int) -> str:
    parts = document_name.strip().lower().replace(".", "").split()
    return (
        f"us_congress:{congress}:" + "-".join(parts) if parts else f"us_congress:{congress}:unknown"
    )


def parse_senate_vote_xml(xml: str) -> dict[str, object] | None:
    """Parse one senate.gov roll-call XML into the rich vote-row shape."""
    root = DefusedET.fromstring(xml)

    def _text(tag: str) -> str:
        node = root.find(tag)
        return (node.text or "").strip() if node is not None else ""

    congress = int(_text("congress") or 0)
    date_raw = _text("vote_date")  # e.g. "January 9, 2025, 05:30 PM"
    try:
        from datetime import datetime

        vote_date = datetime.strptime(
            date_raw.split(",")[0] + "," + date_raw.split(",")[1], "%B %d, %Y"
        ).date()
    except (ValueError, IndexError):
        return None
    # bill name lives under <document><document_name>; amendments under <amendment>
    document = (
        root.findtext("document/document_name")
        or root.findtext(".//document_name")
        or root.findtext("amendment/amendment_number")
        or ""
    ).strip()
    description = _text("vote_document_text") or _text("vote_question_text")
    votes = []
    for m in root.findall(".//member"):
        choice_raw = (m.findtext("vote_cast") or "").strip().lower()
        choice = _CHOICE.get(choice_raw, choice_raw)
        member = (m.findtext("lis_member_id") or m.findtext("last_name") or "").strip()
        if member:
            votes.append(
                [
                    member,
                    (m.findtext("party") or "").strip(),
                    (m.findtext("state") or "").strip(),
                    choice,
                ]
            )
    if not votes:
        return None
    return {
        "bill_id": _slug_bill(document, congress),
        "date": vote_date.isoformat(),
        "congress": congress,
        "sectors": sorted(tag_bill_sectors(description)),
        "votes": votes,
    }


def fetch_senate_rollcalls(
    congress: int, session: int, *, client: httpx.Client | None = None
) -> list[dict[str, object]]:
    """Fetch all roll-calls for a (congress, session) via the vote menu."""
    owns = client is None
    http = client or httpx.Client(
        timeout=30.0, headers={"User-Agent": "psephosamerica-research/0.1 (public-record)"}
    )
    rows: list[dict[str, object]] = []
    try:
        menu = http.get(_MENU.format(congress=congress, session=session))
        if menu.status_code != 200:
            return rows
        numbers = sorted(
            {int(n) for n in re.findall(r"<vote_number>(\d+)</vote_number>", menu.text)}
        )
        for number in numbers:
            resp = http.get(_VOTE.format(congress=congress, session=session, number=number))
            if resp.status_code != 200:
                continue
            row = parse_senate_vote_xml(resp.text)
            if row is not None:
                rows.append(row)
    finally:
        if owns:
            http.close()
    return rows


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Ingest Senate roll-calls")
    parser.add_argument("--congress", type=int, default=119)
    parser.add_argument("--session", type=int, default=1)
    parser.add_argument("--out", default="data/real/senate_119_rich.jsonl")
    args = parser.parse_args(argv)

    rows = fetch_senate_rollcalls(args.congress, args.session)
    real = sum(1 for r in rows if not str(r["bill_id"]).endswith((":unknown", ":motion")))
    Path(args.out).write_text(
        "\n".join(json.dumps(r) for r in rows) + ("\n" if rows else ""), encoding="utf-8"
    )
    print(f"wrote {args.out}: {len(rows)} roll-calls, {real} with a named bill")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
