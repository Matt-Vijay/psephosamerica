"""Whole local build contract on small, explicitly synthetic source fixtures."""

from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.time_machine import cli
from src.time_machine.catalog import query
from src.time_machine.govinfo_text import cache_bill_text_bytes
from src.transfer_eval import cases
from src.transfer_eval.reference import validate_oracle
from tests.test_time_machine_govinfo import USLM_XML
from tests.test_time_machine_states import _write_fixture


def _write(root: Path, name: str, records: list[dict[str, object]]) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in records))


def test_inventory_build_query_and_resume_are_one_local_workflow(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "sources"
    output = tmp_path / "reader's catalog"
    roster = source / "data/raw/acquisitions/congress_legislators/repo"
    roster.mkdir(parents=True)
    person = {
        "id": {"bioguide": "X000001", "lis": "S999", "other": ["one", "two", None]},
        "name": {"first": "Fixture", "last": "Member"},
        "terms": [
            {
                "type": "rep",
                "start": "2023-01-03",
                "end": "2025-01-03",
                "state": "MI",
                "district": 1,
            }
        ],
    }
    (roster / "legislators-current.yaml").write_text(json.dumps([person]))
    (roster / "legislators-historical.yaml").write_text(json.dumps([person, {"id": {}}]))
    govinfo = "data/exports/govinfo_bills/"
    _write(
        source,
        "data/exports/contract_records/records.jsonl",
        [
            {
                "canonical_id": "legacy-person",
                "entity_type": "person",
                "external_ids": ["bioguide:X000001"],
                "known_at": "2025-01-01",
            },
        ],
    )
    _write(
        source,
        govinfo + "records.jsonl",
        [
            {"canonical_id": "legacy-bill", "source_anchors": [], "dossier_json": {}},
        ],
    )
    _write(
        source,
        govinfo + "bill_content_full.jsonl",
        [
            {
                "canonical_id": "legacy-bill",
                "congress": 118,
                "bill_type": "s",
                "number": 1325,
                "introduced_date": "2024-01-01",
                "title": "Fixture bill",
                "subjects": ["Fixture"],
                "summary_text": "Metadata, not full text.",
            },
        ],
    )
    _write(
        source,
        govinfo + "house_vote_edges.jsonl",
        [
            {
                "external_key": "house-118-2-1",
                "src_id": "legacy-person",
                "dst_id": "legacy-bill",
                "attributes": {"choice": "yea"},
                "provenance": {"valid_from": "2024-05-01"},
            },
        ],
    )
    _write(
        source,
        "data/real/senate_113_119_rich.jsonl",
        [
            {
                "vote_id": "senate-118-2-1",
                "bill_id": "us_congress:118:s-1325",
                "date": "2024-05-02",
                "votes": [["S999", "D", "MI", "nay"]],
            },
        ],
    )
    state = source / "data/raw/openstates_bulk/Michigan_2025_2026.zip"
    _write(source, "data/real/senate_113_119_rich_v2.jsonl", [])
    state.parent.mkdir(parents=True)
    _write_fixture(state)
    (state.parent / "_manifest.json").write_text(
        json.dumps(
            [{"text": "Michigan 2025 2026", "href": "https://data.openstates.org/csv/fixture.zip"}]
        )
    )
    with zipfile.ZipFile(state, "a") as archive:
        archive.writestr("MI/fixture_vote_counts.csv", "id,vote_event_id,option,value\n")
    common = ["--source-root", str(source), "--output", str(output)]

    assert cli.main([*common, "inventory"]) == 0
    first_inventory = json.loads((output / "inventory.json").read_text())
    assert cli.main([*common, "inventory"]) == 0
    assert (
        json.loads((output / "inventory.json").read_text())["artifacts"]
        == first_inventory["artifacts"]
    )
    cache_bill_text_bytes(
        "BILLS-118s1325rs", output, USLM_XML, observed_at=datetime(2025, 1, 1, tzinfo=UTC)
    )
    assert cli.main([*common, "inventory"]) == 0
    assert cli.main([*common, "build"]) == 0
    build = json.loads((output / "build.json").read_text())
    assert build["status"] == "complete" and build["govinfo_text_versions"] == 1
    assert build["federal"]["people"] == 1 and build["states"]["archives"] == 1
    assert query(
        output,
        "SELECT choice FROM tm.member_votes WHERE roll_call_id LIKE 'rollcall:us-congress:%' ORDER BY choice",
    )[1] == [("nay",), ("yea",)]
    assert query(
        output,
        "SELECT count(*) FROM tm.bill_text_versions_as_of('2024-05-02'::TIMESTAMPTZ) WHERE is_full_text",
    )[1] == [(0,)]
    assert query(
        output,
        "SELECT count(*) FROM tm.bill_text_versions_as_of('2024-05-03'::TIMESTAMPTZ) WHERE is_full_text",
    )[1] == [(1,)]
    assert (
        query(output, "SELECT text_content FROM tm.bill_text_versions WHERE is_full_text")[1][0][
            0
        ].find("CRS metadata")
        == -1
    )
    assert cli.main([*common, "build"]) == 0
    assert json.loads((output / "build.json").read_text()) == build
    assert cli.main([*common, "status"]) == 0
    assert cli.main([*common, "integrity"]) == 0
    capsys.readouterr()
    assert cli.main([*common, "query", "SELECT 1 AS count", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out) == [{"count": 1}]
    sql = tmp_path / "read.sql"
    sql.write_text("SELECT 1 AS count")
    assert cli.main([*common, "query", "--file", str(sql)]) == 0
    assert capsys.readouterr().out == "count\n1\n"
    with pytest.raises(ValueError, match="provide SQL"):
        cli.main([*common, "query"])

    # The independent CSV reference must agree with the newly built DuckDB, not
    # merely with a precomputed answer or a private database unavailable in CI.
    fixture_case = cases.PilotCase(
        "fixture-roundtrip",
        "2999-01-01T00:00:00Z",
        "public",
        (cases.ArtifactSelection(state.name, "mi"),),
    )
    monkeypatch.setattr(cases, "PILOT_CASES", (fixture_case,))
    (case_dir,) = cases.build_cases(output / "inventory.json", tmp_path / "cases")
    assert validate_oracle(case_dir, output)["status"] == "PASS"
    request = json.loads((case_dir / "request.json").read_text())
    request["cutoff"] = "2000-01-01T00:00:00Z"
    (case_dir / "request.json").write_text(json.dumps(request))
    assert validate_oracle(case_dir, output)["status"] == "PASS"
