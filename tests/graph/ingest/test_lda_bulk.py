from __future__ import annotations

import json
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from src.graph.bills import BillRef
from src.graph.ingest.lda_bulk import (
    SOURCE_DESCRIPTION,
    BulkArchiveRef,
    bulk_filing_to_record,
    iter_bulk_records,
    merge_bulk_into_export,
)

_OBSERVED = datetime(2026, 6, 24, tzinfo=UTC)


def _filing_xml(
    *,
    uuid: str = "DADC6611-C6A8-4747-AE8B-F8991A03660A",
    year: str = "2020",
    received: str = "2020-10-25T07:57:33.583",
    amount: str = "100000",
    ftype: str = "THIRD QUARTER",
    registrant_id: str = "401104327",
    registrant_name: str = "Avenue Strategies",
    client_id: str = "164",
    client_name: str = "Acme Corp",
    client_state: str = "CALIFORNIA",
    issue_code: str = "MEDICAL/DISEASE RESEARCH/CLINICAL LABS",
    specific_issue: str | None = "Supported funding under H.R. 2471 appropriations.",
) -> str:
    issue = (
        f'<Issue Code="{issue_code}" SpecificIssue="{specific_issue}"/>'
        if specific_issue is not None
        else f'<Issue Code="{issue_code}" SpecificIssue=""/>'
    )
    return (
        f'<Filing ID="{uuid}" Year="{year}" Received="{received}" Amount="{amount}" '
        f'Type="{ftype}" Period="3rd Quarter">'
        f'<Registrant RegistrantID="{registrant_id}" RegistrantName="{registrant_name}" '
        f'RegistrantCountry="USA"/>'
        f'<Client ClientID="{client_id}" ClientName="{client_name}" '
        f'ClientState="{client_state}"/>'
        f"<Issues>{issue}</Issues>"
        f"</Filing>"
    )


def _public_filings(*filings: str) -> str:
    return "<PublicFilings>" + "".join(filings) + "</PublicFilings>"


def _write_zip(path: Path, *members: tuple[str, str]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in members:
            # The real archives are UTF-16; exercise that encoding here too.
            archive.writestr(name, body.encode("utf-16"))


def test_bulk_filing_to_record_maps_api_shape() -> None:
    elem = ET.fromstring(_filing_xml())
    record = bulk_filing_to_record(elem)
    assert record is not None
    assert record["filing_uuid"] == "dadc6611-c6a8-4747-ae8b-f8991a03660a"
    assert record["filing_year"] == "2020"
    assert record["filing_type_display"] == "THIRD QUARTER"
    # The naive bulk ``Received`` is stamped with its DST-aware US/Eastern offset
    # (October 2020 is EDT, -04:00) so ``known_at`` is timezone-aware.
    assert record["dt_posted"] == "2020-10-25T07:57:33.583000-04:00"
    assert record["income"] == "100000"
    assert record["expenses"] is None
    assert record["registrant"] == {"id": "401104327", "name": "Avenue Strategies", "state": None}
    assert record["client"]["id"] == "164"
    assert record["client"]["state"] == "CALIFORNIA"
    assert record["lobbying_activities"] == [
        {
            "general_issue_area_code": "MEDICAL/DISEASE RESEARCH/CLINICAL LABS",
            "description": "Supported funding under H.R. 2471 appropriations.",
        }
    ]


def test_bulk_filing_to_record_skips_missing_ids() -> None:
    no_uuid = ET.fromstring(_filing_xml(uuid=""))
    assert bulk_filing_to_record(no_uuid) is None
    no_client = ET.fromstring(_filing_xml(client_id=""))
    assert bulk_filing_to_record(no_client) is None
    no_registrant = ET.fromstring(_filing_xml(registrant_id=""))
    assert bulk_filing_to_record(no_registrant) is None


def test_bulk_filing_to_record_handles_empty_issue() -> None:
    elem = ET.fromstring(_filing_xml(specific_issue=None))
    record = bulk_filing_to_record(elem)
    assert record is not None
    # An empty SpecificIssue is preserved as a null description, never fabricated.
    assert record["lobbying_activities"] == [
        {"general_issue_area_code": "MEDICAL/DISEASE RESEARCH/CLINICAL LABS", "description": None}
    ]


def test_iter_bulk_records_streams_utf16_zip(tmp_path: Path) -> None:
    zip_path = tmp_path / "2020_4.zip"
    _write_zip(
        zip_path,
        (
            "2020_4_1_1.xml",
            _public_filings(
                _filing_xml(uuid="11111111-1111-1111-1111-111111111111"),
                _filing_xml(uuid="22222222-2222-2222-2222-222222222222", client_name="Beta LLC"),
            ),
        ),
        ("notes.txt", "ignored non-xml member"),
    )
    records = list(iter_bulk_records(zip_path))
    assert len(records) == 2
    assert {r["filing_uuid"] for r in records} == {
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    }


def _existing_export(tmp_path: Path) -> Path:
    """Seed an existing LDA export (corpus + one retention edge) to merge into."""
    from src.graph.export import write_contract_corpus
    from src.graph.ingest.lda_export import LOBBYING_EDGES_FILENAME, build_lobbying_graph

    existing_filing = {
        "filing_uuid": "99999999-9999-9999-9999-999999999999",
        "filing_year": 2025,
        "filing_type_display": "Q1",
        "dt_posted": "2025-04-20T10:00:00-04:00",
        "income": "5000.00",
        "expenses": None,
        "client": {"name": "Existing Client", "id": 700, "state": "NY"},
        "registrant": {"name": "Existing Lobby", "id": 800, "state": "DC"},
        "lobbying_activities": [{"general_issue_area_code": "TAX", "description": "no bill"}],
    }
    rows, edges, _s, _k = build_lobbying_graph([existing_filing], first_observed_at=_OBSERVED)
    write_contract_corpus(rows, directory=tmp_path, as_of=_OBSERVED)
    with (tmp_path / LOBBYING_EDGES_FILENAME).open("w", encoding="utf-8") as handle:
        for edge in edges:
            handle.write(edge.model_dump_json() + "\n")
    return tmp_path


def test_merge_bulk_dedups_and_writes_meta(tmp_path: Path) -> None:
    from src.graph.export import read_contract_corpus
    from src.graph.ingest.lda_export import INGEST_META_FILENAME, LOBBYING_EDGES_FILENAME

    out_dir = _existing_export(tmp_path)
    zip_path = tmp_path / "raw" / "2020_4.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    _write_zip(
        zip_path,
        (
            "2020_4_1_1.xml",
            _public_filings(
                _filing_xml(uuid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                # A filing whose UUID already exists -> must be counted as duplicate.
                _filing_xml(uuid="99999999-9999-9999-9999-999999999999"),
            ),
        ),
    )

    report = merge_bulk_into_export(
        zip_paths=[zip_path],
        out_directory=out_dir,
        as_of=_OBSERVED,
        archives_used=["2020_4.zip"],
    )

    assert report.source == SOURCE_DESCRIPTION
    assert report.bulk_filings_scanned == 2
    assert report.bulk_filings_new == 1
    assert report.bulk_filings_duplicate == 1
    assert report.retention_edges == 1
    assert report.bill_lobbying_edges == 1  # H.R. 2471 mined from the bulk SpecificIssue

    # Per-year breakdown present for 2020.
    years = {y["year"] for y in report.per_year}
    assert 2020 in years

    # Combined corpus carries both the pre-existing orgs and the new bulk orgs.
    corpus = read_contract_corpus(out_dir)
    names = {row.display_name.upper() for row in corpus}
    assert "EXISTING CLIENT" in names
    assert any("ACME" in n for n in names)

    # Combined sidecar keeps the existing edge and adds the bulk edges.
    edge_lines = (out_dir / LOBBYING_EDGES_FILENAME).read_text().strip().splitlines()
    keys = {json.loads(line)["external_key"].split(":", 1)[0] for line in edge_lines}
    assert "99999999-9999-9999-9999-999999999999" in keys  # existing preserved
    assert "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" in keys  # new bulk

    meta = json.loads((out_dir / INGEST_META_FILENAME).read_text())
    assert meta["bulk_filings_new"] == 1
    assert meta["archives_used"] == ["2020_4.zip"]

    bill_targets = {
        json.loads(line)["dst_id"]
        for line in edge_lines
        if json.loads(line)["edge_type"] == "lobbying_contact"
    }
    assert BillRef.for_congress(116, "H.R. 2471").canonical_id in bill_targets


def test_merge_bulk_idempotent_on_rerun(tmp_path: Path) -> None:
    from src.graph.ingest.lda_export import LOBBYING_EDGES_FILENAME

    out_dir = _existing_export(tmp_path)
    zip_path = tmp_path / "raw" / "2020_4.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    _write_zip(
        zip_path,
        (
            "2020_4_1_1.xml",
            _public_filings(_filing_xml(uuid="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")),
        ),
    )

    first = merge_bulk_into_export(
        zip_paths=[zip_path], out_directory=out_dir, as_of=_OBSERVED, archives_used=["2020_4.zip"]
    )
    edges_first = (out_dir / LOBBYING_EDGES_FILENAME).read_text()
    # Second run sees the now-ingested UUID as a duplicate; edge file is unchanged.
    second = merge_bulk_into_export(
        zip_paths=[zip_path], out_directory=out_dir, as_of=_OBSERVED, archives_used=["2020_4.zip"]
    )
    edges_second = (out_dir / LOBBYING_EDGES_FILENAME).read_text()
    assert first.bulk_filings_new == 1
    assert second.bulk_filings_new == 0
    assert second.bulk_filings_duplicate == 1
    assert edges_first == edges_second


def test_bulk_archive_ref_wayback_url() -> None:
    ref = BulkArchiveRef(year=2020, quarter=4, wayback_timestamp="20201124061045")
    assert ref.basename == "2020_4.zip"
    assert ref.wayback_url == (
        "https://web.archive.org/web/20201124061045id_/"
        "https://soprweb.senate.gov/downloads/2020_4.zip"
    )
