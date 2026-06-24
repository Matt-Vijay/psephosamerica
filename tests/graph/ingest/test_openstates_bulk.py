"""Tests for the OpenStates bulk session-CSV adapter + runner.

The adapter (:mod:`src.graph.ingest.openstates_bulk`) is pure and tested from
in-memory CSV-row streams; the runner (:mod:`src.graph.ingest.openstates_bulk_\
export`) is exercised end-to-end against synthetic in-memory ZIPs, including the
merge-with-API dedup and the resume checkpoint.
"""

from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from src.graph.ingest.openstates_bulk import (
    index_bills,
    index_vote_events,
    legislator_records_from_voters,
    parse_session,
    parse_vote_person_row,
    state_from_member_path,
    voter_provenance,
)
from src.graph.ingest.openstates_bulk_export import (
    COMBINED_EDGES_FILENAME,
    discover_zips,
    export_openstates_bulk,
)

_OBS = datetime(2026, 6, 24, tzinfo=UTC)
_JANE = "ocd-person/11111111-1111-1111-1111-111111111111"
_BOB = "ocd-person/22222222-2222-2222-2222-222222222222"
_BILL = "ocd-bill/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_VOTE = "ocd-vote/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _prov():
    return voter_provenance(
        state="al",
        source_url="https://openstates.org/al/",
        content_sha256="a" * 64,
        first_observed_at=_OBS,
    )


# ── path -> state ────────────────────────────────────────────────────────


def test_state_from_member_path_extracts_usps() -> None:
    assert state_from_member_path("AL/2023rs/AL_2023rs_votes.csv") == "al"
    assert state_from_member_path("DC/23/DC_23_votes.csv") == "dc"
    assert state_from_member_path("PR/2025-2028/PR_x.csv") == "pr"


def test_state_from_member_path_skips_federal_and_garbage() -> None:
    # US (federal Congress) is the transfer-test *source*, never a state target.
    assert state_from_member_path("US/119/US_119_votes.csv") is None
    assert state_from_member_path("README") is None
    assert state_from_member_path("XYZ/x.csv") is None


# ── lookups ───────────────────────────────────────────────────────────────


def test_index_bills_keeps_only_linkable_rows() -> None:
    rows = iter(
        [
            {
                "id": _BILL,
                "identifier": "HB 22",
                "title": "An act",
                "session_identifier": "2023rs",
                "organization_classification": "lower",
            },
            {"id": "not-a-bill", "identifier": "X"},  # bad id -> dropped
            {"id": _BILL.replace("a", "c"), "identifier": ""},  # blank identifier -> dropped
        ]
    )
    idx = index_bills(rows)
    assert set(idx) == {_BILL}
    assert idx[_BILL].identifier == "HB 22"
    assert idx[_BILL].chamber == "lower"


def test_index_vote_events_requires_date_and_bill() -> None:
    rows = iter(
        [
            {
                "id": _VOTE,
                "bill_id": _BILL,
                "motion_text": "Final passage",
                "motion_classification": "['passage']",
                "result": "pass",
                "start_date": "2023-03-23",
                "session_identifier": "2023rs",
            },
            {"id": _VOTE.replace("b", "c"), "bill_id": _BILL, "start_date": ""},  # no date
            {"id": _VOTE.replace("b", "d"), "bill_id": "", "start_date": "2023-01-01"},  # no bill
        ]
    )
    idx = index_vote_events(rows)
    assert set(idx) == {_VOTE}
    assert idx[_VOTE].motion_classification == "passage"
    assert idx[_VOTE].result == "pass"


def test_parse_vote_person_row_normalizes_and_skips() -> None:
    yes = parse_vote_person_row(
        {"voter_id": _JANE, "voter_name": "Jane", "option": "yes", "vote_event_id": _VOTE}
    )
    assert yes is not None and yes.choice == "yea"
    # "not voting" -> not_voting; "abstain" -> abstain.
    assert parse_vote_person_row({"voter_id": _BOB, "option": "not voting"}).choice == "not_voting"
    # No OCD voter id -> dropped (cannot resolve to a legislator).
    assert parse_vote_person_row({"voter_id": "", "option": "yes"}) is None
    # Unrecognized option -> dropped.
    assert parse_vote_person_row({"voter_id": _JANE, "option": "???"}) is None


# ── join ──────────────────────────────────────────────────────────────────


def _session_streams():
    bills = [
        {
            "id": _BILL,
            "identifier": "HB 22",
            "title": "An act",
            "session_identifier": "2023rs",
            "organization_classification": "lower",
        }
    ]
    votes = [
        {
            "id": _VOTE,
            "bill_id": _BILL,
            "motion_text": "Final passage",
            "motion_classification": "['passage']",
            "result": "pass",
            "start_date": "2023-03-23",
            "session_identifier": "2023rs",
        }
    ]
    people = [
        {"voter_id": _JANE, "voter_name": "Jane", "option": "yes", "vote_event_id": _VOTE},
        {"voter_id": _BOB, "voter_name": "Bob", "option": "no", "vote_event_id": _VOTE},
        # Row whose event is not in the votes file -> ignored by the join.
        {"voter_id": _JANE, "voter_name": "Jane", "option": "yes", "vote_event_id": "ocd-vote/zzz"},
    ]
    return iter(bills), iter(votes), iter(people)


def test_parse_session_joins_people_to_bill() -> None:
    bills_rows, votes_rows, people_rows = _session_streams()
    parsed = parse_session(
        state="al",
        bills_rows=bills_rows,
        votes_rows=votes_rows,
        vote_people_rows=people_rows,
        session_label="Alabama_2023_Regular_Session",
        source_url="https://openstates.org/al/",
    )
    assert len(parsed.bills) == 1
    bill = parsed.bills[0]
    assert bill.identifier == "HB 22"
    assert bill.session == "2023rs"
    assert len(bill.rollcalls) == 1
    assert {v.voter_ocd_id for v in bill.rollcalls[0].votes} == {_JANE, _BOB}
    assert parsed.voters == {_JANE: "Jane", _BOB: "Bob"}
    assert parsed.rollcalls == 1


def test_legislator_records_keyed_on_ocd() -> None:
    recs = legislator_records_from_voters(
        voters={_JANE: "Jane", _BOB: "Bob"}, state="al", provenance=_prov()
    )
    assert len(recs) == 2
    systems = {(e.system, e.value) for r in recs for e in r.external_ids}
    assert ("openstates", _JANE) in systems
    assert all(r.source_system == "openstates" for r in recs)


# ── recency ordering ───────────────────────────────────────────────────────


def test_discover_zips_recent_first(tmp_path: Path) -> None:
    for name in ("Foo_2008_Session.zip", "Foo_2023_Session.zip", "Foo_2019_Session.zip"):
        (tmp_path / name).write_bytes(b"")
    order = [j.label for j in discover_zips(tmp_path)]
    # 2015+ first (desc), then older.
    assert order == ["Foo_2023_Session", "Foo_2019_Session", "Foo_2008_Session"]


# ── end-to-end runner ──────────────────────────────────────────────────────


def _write_zip(path: Path, state_code: str, *, vote_id: str, bill_id: str, date: str) -> None:
    pfx = f"{state_code}/2023rs/{state_code}_2023rs"
    bills_csv = (
        "id,identifier,title,classification,subject,session_identifier,jurisdiction,"
        "organization_classification\n"
        f"{bill_id},HB 22,An act,['bill'],[],2023rs,Alabama,lower\n"
    )
    votes_csv = (
        "id,identifier,motion_text,motion_classification,start_date,result,organization_id,"
        "bill_id,bill_action_id,jurisdiction,session_identifier\n"
        f"{vote_id},,Final passage,['passage'],{date},pass,ocd-organization/x,{bill_id},,"
        "Alabama,2023rs\n"
    )
    people_csv = (
        "id,vote_event_id,option,voter_name,voter_id,note\n"
        f"row1,{vote_id},yes,Jane,{_JANE},\n"
        f"row2,{vote_id},no,Bob,{_BOB},\n"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{pfx}_bills.csv", bills_csv)
        zf.writestr(f"{pfx}_votes.csv", votes_csv)
        zf.writestr(f"{pfx}_vote_people.csv", people_csv)


def test_export_end_to_end_emits_edges(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    _write_zip(
        raw / "Alabama_2023_Regular_Session.zip",
        "AL",
        vote_id=_VOTE,
        bill_id=_BILL,
        date="2023-03-23",
    )

    report = export_openstates_bulk(raw_dir=raw, out_directory=out, as_of=_OBS)

    assert report.zips_processed == 1
    assert report.total_vote_edges == 2  # Jane + Bob
    assert report.bulk_vote_edges == 2
    assert report.distinct_states_with_votes == 1
    assert report.states_with_votes == ("al",)
    per_al = {c["state"]: c for c in report.per_state}["al"]
    assert per_al["legislators"] == 2
    assert per_al["bills"] == 1
    assert per_al["vote_edges"] == 2

    # Combined sidecar has the two edges with the vote-event external_key.
    lines = (out / COMBINED_EDGES_FILENAME).read_text().splitlines()
    assert len(lines) == 2
    edges = [json.loads(line) for line in lines]
    assert all(e["external_key"] == _VOTE for e in edges)
    assert {e["attributes"]["choice"] for e in edges} == {"yea", "nay"}


def test_export_merges_and_dedups_api_sidecar(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    _write_zip(
        raw / "Alabama_2023_Regular_Session.zip",
        "AL",
        vote_id=_VOTE,
        bill_id=_BILL,
        date="2023-03-23",
    )

    # Seed an API legislator sidecar mapping Jane's OCD id to a canonical id, and
    # an API edge for Jane on the SAME vote event (must dedup, not duplicate).
    recs = legislator_records_from_voters(voters={_JANE: "Jane"}, state="al", provenance=_prov())
    # Reproduce the canonical id the runner would assign for Jane.
    from src.graph.entity_resolution.canonical import build_canonical_entity
    from src.graph.entity_resolution.linker import resolve

    rbid = {r.record_id: r for r in recs}
    res = resolve(rbid.values())
    jane_cid = ""
    for cl in res.clusters:
        ent = build_canonical_entity(cl, rbid)
        jane_cid = ent.canonical_id  # type: ignore[union-attr]
    (out / "state_legislators.jsonl").write_text(
        json.dumps(
            {
                "canonical_id": jane_cid,
                "entity_type": "person",
                "display_name": "Jane",
                "external_ids": [f"openstates:{_JANE}"],
                "known_at": _OBS.isoformat(),
                "source_anchors": [],
                "enrichment_status": "pending",
            }
        )
        + "\n"
    )
    api_edge = {
        "edge_type": "vote",
        "src_id": jane_cid,
        "dst_id": "cb-fromapi",
        "attributes": {"choice": "yea", "source": "openstates"},
        "external_key": _VOTE,
        "provenance": {
            "source_url": "https://openstates.org/al/",
            "content_sha256": "c" * 64,
            "first_observed_at": _OBS.isoformat(),
            "valid_from": "2023-03-23",
            "valid_to": None,
            "known_at": "2023-03-23T00:00:00Z",
        },
    }
    (out / "state_vote_edges.jsonl").write_text(json.dumps(api_edge) + "\n")

    report = export_openstates_bulk(raw_dir=raw, out_directory=out, as_of=_OBS)

    # API edge carried in (1) + Bob's bulk edge (1) = 2; Jane's bulk edge is a dup.
    assert report.api_vote_edges == 1
    assert report.total_vote_edges == 2
    lines = (out / COMBINED_EDGES_FILENAME).read_text().splitlines()
    assert len(lines) == 2
    keys = {(json.loads(line)["external_key"], json.loads(line)["src_id"]) for line in lines}
    assert (_VOTE, jane_cid) in keys  # only once
    # The original API sidecar is untouched.
    assert (out / "state_vote_edges.jsonl").read_text().count("\n") == 1


def test_export_skips_federal_us_dump(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    _write_zip(
        raw / "United_States_119th_Congress.zip",
        "US",
        vote_id=_VOTE,
        bill_id=_BILL,
        date="2023-03-23",
    )
    report = export_openstates_bulk(raw_dir=raw, out_directory=out, as_of=_OBS)
    assert report.zips_processed == 1  # the ZIP is opened...
    assert report.total_vote_edges == 0  # ...but US rows are skipped (federal source).


def test_export_resume_skips_completed(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    _write_zip(
        raw / "Alabama_2023_Regular_Session.zip",
        "AL",
        vote_id=_VOTE,
        bill_id=_BILL,
        date="2023-03-23",
    )
    first = export_openstates_bulk(raw_dir=raw, out_directory=out, as_of=_OBS)
    assert first.total_vote_edges == 2
    # Second run resumes: the only ZIP is already completed, so no new edges are
    # written and the combined sidecar still holds exactly the two edges.
    second = export_openstates_bulk(raw_dir=raw, out_directory=out, as_of=_OBS)
    assert second.total_vote_edges == 2
    assert len((out / COMBINED_EDGES_FILENAME).read_text().splitlines()) == 2


def test_leakage_guard_skips_future_dated(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    # Vote dated after the observation instant -> dropped (no leakage).
    _write_zip(
        raw / "Alabama_2099_Regular_Session.zip",
        "AL",
        vote_id=_VOTE,
        bill_id=_BILL,
        date="2099-01-01",
    )
    report = export_openstates_bulk(raw_dir=raw, out_directory=out, as_of=_OBS)
    assert report.total_vote_edges == 0
