from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

import src.incident_lab.prototype.nm_reassociation_fixture as fixture_module
from src.incident_lab.prototype.nm_reassociation_fixture import (
    DEFAULT_SOURCE_ROOT,
    FIXTURE_ROOT,
    TARGET_ROLL_ID,
    audit_fixture,
    build_fixture,
)


def test_csv_filter_retains_exact_header_and_selected_record_bytes() -> None:
    payload = (
        b"id,note\r\n"
        b'1,"first line\r\nsecond line"\r\n'
        b'2,"caf\xc3\xa9, quoted"\n'
        b"3,last-without-newline"
    )
    table = fixture_module._parse_csv_records(payload, "synthetic.csv")
    selected, row_count = table.filter(lambda row: row.get("id") == "2")
    assert row_count == 1
    assert selected == b'id,note\r\n2,"caf\xc3\xa9, quoted"\n'


def test_committed_fixture_audits_as_measurement_evidence() -> None:
    report = audit_fixture()
    assert report == {
        "status": "PASS",
        "prototype_status": "measurement_fixture_only_not_task_schema",
        "canonical_extract_sha256": fixture_module.PINNED_CANONICAL_EXTRACT_SHA256,
        "extract_file_count": 18,
        "extract_byte_count": 19_259,
        "parent_archives": 2,
        "parents_verified": 0,
        "bills": 2,
        "bill_actions": 14,
        "bill_sources": 2,
        "rolls": 2,
        "vote_people": 84,
        "vote_counts": 10,
        "vote_sources": 2,
        "organizations": 5,
        "resolved_people": 39,
        "unresolved_vote_rows": 11,
        "shared_voter_keys_same_choice": 37,
        "session_only_voter_keys": 10,
    }
    assert report["extract_byte_count"] < 100_000
    receipt = (FIXTURE_ROOT / "SOURCE.json").read_text(encoding="utf-8")
    assert TARGET_ROLL_ID in receipt
    assert "DERIVED FIXTURE BYTES" in receipt
    assert "not a task schema" in receipt
    assert not tuple(FIXTURE_ROOT.rglob("*.zip"))
    assert not tuple(FIXTURE_ROOT.rglob("*.parquet"))
    assert not tuple(FIXTURE_ROOT.rglob("*.jsonl"))


def test_builder_reproduces_committed_bytes_from_pinned_parents(tmp_path: Path) -> None:
    required_sources = (
        DEFAULT_SOURCE_ROOT / "_manifest.json",
        DEFAULT_SOURCE_ROOT / "New_Mexico_2024_Regular_Session.zip",
        DEFAULT_SOURCE_ROOT / "New_Mexico_2024_First_Special_Session.zip",
    )
    if not all(path.is_file() for path in required_sources):
        pytest.skip("pinned read-only New Mexico parents are not mounted")
    rebuilt = tmp_path / "input"
    report = build_fixture(DEFAULT_SOURCE_ROOT, rebuilt)
    assert report["parents_verified"] == 2
    assert report["vote_people"] == 84
    committed = {
        path.relative_to(FIXTURE_ROOT).as_posix(): path.read_bytes()
        for path in FIXTURE_ROOT.rglob("*")
        if path.is_file()
    }
    generated = {
        path.relative_to(rebuilt).as_posix(): path.read_bytes()
        for path in rebuilt.rglob("*")
        if path.is_file()
    }
    assert generated == committed
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        build_fixture(DEFAULT_SOURCE_ROOT, rebuilt)


def test_builder_and_parent_verifier_work_without_private_archives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repack public records into synthetic parents, not claimed official ZIPs."""
    source = tmp_path / "parents"
    source.mkdir()
    parents = []
    for spec in fixture_module.PARENTS:
        archive_path = source / spec.archive_name
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("README", (FIXTURE_ROOT / spec.label / "README").read_bytes())
            for table in fixture_module.TABLE_NAMES:
                archive.writestr(
                    spec.member_path(table),
                    (FIXTURE_ROOT / spec.label / f"{table}.csv").read_bytes(),
                )
        parents.append(
            replace(
                spec,
                source_url=f"https://example.invalid/{spec.archive_name}",
                byte_count=archive_path.stat().st_size,
                sha256=hashlib.sha256(archive_path.read_bytes()).hexdigest(),
            )
        )
    manifest = json.dumps(
        [{"text": spec.manifest_text, "href": spec.source_url} for spec in parents]
    ).encode()
    (source / fixture_module.MANIFEST_NAME).write_bytes(manifest)
    monkeypatch.setattr(fixture_module, "PARENTS", tuple(parents))
    monkeypatch.setattr(fixture_module, "MANIFEST_BYTES", len(manifest))
    monkeypatch.setattr(fixture_module, "MANIFEST_SHA256", hashlib.sha256(manifest).hexdigest())

    output = tmp_path / "derived"
    report = build_fixture(source, output)
    assert report["status"] == "PASS" and report["parents_verified"] == 2
    assert report["canonical_extract_sha256"] == fixture_module.PINNED_CANONICAL_EXTRACT_SHA256
    for original in FIXTURE_ROOT.rglob("*"):
        if original.is_file() and original.name != fixture_module.RECEIPT_NAME:
            assert (
                output / original.relative_to(FIXTURE_ROOT)
            ).read_bytes() == original.read_bytes()
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        build_fixture(source, output)
    archive_path.write_bytes(archive_path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="parent ZIP size/hash mismatch"):
        audit_fixture(output, source_root=source)


def test_auditor_rejects_byte_tampering_and_extra_files(tmp_path: Path) -> None:
    tampered = tmp_path / "tampered"
    shutil.copytree(FIXTURE_ROOT, tampered)
    vote_path = tampered / "nm_2024_regular/votes.csv"
    vote_path.write_bytes(vote_path.read_bytes().replace(b"senate passage", b"senate defeat"))
    with pytest.raises(ValueError, match="source receipt differs"):
        audit_fixture(tampered)

    contaminated = tmp_path / "contaminated"
    shutil.copytree(FIXTURE_ROOT, contaminated)
    (contaminated / "expected.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="allow-list mismatch"):
        audit_fixture(contaminated)
