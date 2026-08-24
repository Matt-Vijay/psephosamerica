"""Build and audit the bounded New Mexico roll-reassociation prototype fixture.

The fixture is evidence, not a repaired output or a task schema.  Its CSV files
contain an original header record followed by selected original data records.
No CSV record is reserialized, so quoting, field bytes, record order, and line
endings remain those of the pinned parent ZIP member.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import stat
import tempfile
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TARGET_ROLL_ID = "ocd-vote/a5a38c48-675e-4dc9-bb67-c06337a2bce8"
DEFAULT_SOURCE_ROOT = Path(
    "/Users/matthew/Documents/Coding/psephosamerica/data/raw/openstates_bulk"
)
FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "pilot/cases/prototype/nm_reassociation/input"
RECEIPT_NAME = "SOURCE.json"
RECEIPT_FORMAT = "openstates-csv-verbatim-record-filter-prototype-v1"
FIXTURE_BYTES_LABEL = (
    "DERIVED FIXTURE BYTES: selected verbatim member records; not parent ZIP bytes, "
    "not a repaired output, and not a task schema."
)
CANONICAL_DIGEST_ALGORITHM = (
    "SHA-256 over each non-receipt file in lexical POSIX-path order: "
    "UTF-8 path, NUL, ASCII byte count, NUL, file bytes, NUL"
)

MANIFEST_NAME = "_manifest.json"
MANIFEST_PATH_NAME = "data/raw/openstates_bulk/_manifest.json"
MANIFEST_BYTES = 105_538
MANIFEST_SHA256 = "ea808f161867ec5e0afb34ee14d63a022703f766fe8b1227cf4a0e33f6cd1829"

# Filled from the first build against both hash-verified parents and then treated
# as a public measurement.  The builder never derives this value from a repaired
# or normalized output.
PINNED_CANONICAL_EXTRACT_SHA256 = "c54606d1adc5ce8bef87a302641b14ed0766e71eb679fe6d093d1df152917136"

TABLE_NAMES = (
    "bills",
    "bill_actions",
    "bill_sources",
    "votes",
    "vote_people",
    "vote_counts",
    "vote_sources",
    "organizations",
)


@dataclass(frozen=True)
class ParentSpec:
    label: str
    archive_name: str
    manifest_text: str
    source_url: str
    byte_count: int
    sha256: str
    generated_at: str
    member_prefix: str
    session: str
    bill_id: str
    bill_title: str
    expected_rows: tuple[tuple[str, int], ...]

    @property
    def path_name(self) -> str:
        return f"data/raw/openstates_bulk/{self.archive_name}"

    def member_path(self, table: str) -> str:
        return f"{self.member_prefix}_{table}.csv"

    def row_count(self, table: str) -> int:
        return dict(self.expected_rows)[table]


PARENTS = (
    ParentSpec(
        label="nm_2024_regular",
        archive_name="New_Mexico_2024_Regular_Session.zip",
        manifest_text="New Mexico 2024 Regular Session",
        source_url=(
            "https://data.openstates.org/csv/latest/NM_2024_csv_3TWIIK8DDER0TzmpIA7aiv.zip"
        ),
        byte_count=3_865_019,
        sha256="494f1680b245747df91af34504bcfdb08af080d22136dd977d34be6e205564f3",
        generated_at="2024-03-25 23:07:21.567504",
        member_prefix="NM/2024/NM_2024",
        session="2024",
        bill_id="ocd-bill/a2f5794e-0059-4762-9f12-a0d65f604a4c",
        bill_title="FIREARMS NEAR POLLING PLACES",
        expected_rows=(
            ("bills", 1),
            ("bill_actions", 12),
            ("bill_sources", 1),
            ("votes", 1),
            ("vote_people", 42),
            ("vote_counts", 5),
            ("vote_sources", 1),
            ("organizations", 3),
        ),
    ),
    ParentSpec(
        label="nm_2024_first_special",
        archive_name="New_Mexico_2024_First_Special_Session.zip",
        manifest_text="New Mexico 2024 First Special Session",
        source_url=(
            "https://data.openstates.org/csv/latest/NM_2024S1_csv_1vq29jAbcJWagcywRzt40Q.zip"
        ),
        byte_count=179_822,
        sha256="00453d1dee1aa34ad5100d635692e57fbfef45af79ce8cfbcb7ed5b5c45e7758",
        generated_at="2024-09-20 23:08:39.074368",
        member_prefix="NM/2024S1/NM_2024S1",
        session="2024S1",
        bill_id="ocd-bill/e5bb73d2-9345-4673-b547-598b215562e9",
        bill_title="ADD RACKETEERING DEFINITIONS",
        expected_rows=(
            ("bills", 1),
            ("bill_actions", 2),
            ("bill_sources", 1),
            ("votes", 1),
            ("vote_people", 42),
            ("vote_counts", 5),
            ("vote_sources", 1),
            ("organizations", 2),
        ),
    ),
)


@dataclass(frozen=True)
class CsvRecord:
    values: Mapping[str, str]
    raw: bytes


@dataclass(frozen=True)
class CsvTable:
    fields: tuple[str, ...]
    header_raw: bytes
    records: tuple[CsvRecord, ...]

    def filter(self, predicate: Callable[[Mapping[str, str]], bool]) -> tuple[bytes, int]:
        chosen = tuple(record.raw for record in self.records if predicate(record.values))
        return self.header_raw + b"".join(chosen), len(chosen)


@dataclass(frozen=True)
class DerivedFile:
    payload: bytes
    source_member: str
    selected_data_rows: int | None
    extraction: str


def _physical_lines_bytes(payload: bytes) -> list[bytes]:
    lines: list[bytes] = []
    start = 0
    cursor = 0
    while cursor < len(payload):
        if payload[cursor] == 10:  # LF
            lines.append(payload[start : cursor + 1])
            cursor += 1
            start = cursor
            continue
        if payload[cursor] == 13:  # CR or CRLF
            end = (
                cursor + 2
                if cursor + 1 < len(payload) and payload[cursor + 1] == 10
                else cursor + 1
            )
            lines.append(payload[start:end])
            cursor = end
            start = cursor
            continue
        cursor += 1
    if start < len(payload):
        lines.append(payload[start:])
    return lines


def _physical_lines_text(payload: str) -> list[str]:
    lines: list[str] = []
    start = 0
    cursor = 0
    while cursor < len(payload):
        if payload[cursor] == "\n":
            lines.append(payload[start : cursor + 1])
            cursor += 1
            start = cursor
            continue
        if payload[cursor] == "\r":
            end = (
                cursor + 2
                if cursor + 1 < len(payload) and payload[cursor + 1] == "\n"
                else cursor + 1
            )
            lines.append(payload[start:end])
            cursor = end
            start = cursor
            continue
        cursor += 1
    if start < len(payload):
        lines.append(payload[start:])
    return lines


def _parse_csv_records(payload: bytes, source_name: str) -> CsvTable:
    """Parse values while retaining the exact byte span of every CSV record."""
    try:
        decoded = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"CSV is not UTF-8: {source_name}") from exc
    raw_lines = _physical_lines_bytes(payload)
    text_lines = _physical_lines_text(decoded)
    if not raw_lines or len(raw_lines) != len(text_lines):
        raise ValueError(f"cannot align CSV physical lines: {source_name}")
    reader = csv.reader(text_lines, strict=True)
    try:
        header = next(reader)
    except (StopIteration, csv.Error) as exc:
        raise ValueError(f"CSV lacks a valid header: {source_name}") from exc
    if not header or len(header) != len(set(header)):
        raise ValueError(f"CSV header is empty or duplicated: {source_name}")
    header_end = reader.line_num
    header_raw = b"".join(raw_lines[:header_end])
    start_line = header_end
    records: list[CsvRecord] = []
    try:
        for values in reader:
            end_line = reader.line_num
            raw = b"".join(raw_lines[start_line:end_line])
            start_line = end_line
            if not values:
                continue
            if len(values) != len(header):
                raise ValueError(
                    f"CSV row has {len(values)} fields, expected {len(header)}: {source_name}"
                )
            records.append(CsvRecord(dict(zip(header, values, strict=True)), raw))
    except csv.Error as exc:
        raise ValueError(f"invalid CSV record: {source_name}") from exc
    if start_line != len(raw_lines):
        raise ValueError(f"CSV parser did not consume all physical lines: {source_name}")
    return CsvTable(tuple(header), header_raw, tuple(records))


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_regular_file(path: Path, description: str) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        raise ValueError(f"missing {description}: {path}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ValueError(f"{description} must be a non-symlink regular file: {path}")


def _verify_source_root(source_root: Path) -> None:
    manifest_path = source_root / MANIFEST_NAME
    _require_regular_file(manifest_path, "OpenStates manifest")
    manifest_payload = manifest_path.read_bytes()
    if len(manifest_payload) != MANIFEST_BYTES or _sha256(manifest_payload) != MANIFEST_SHA256:
        raise ValueError(f"OpenStates manifest size/hash mismatch: {manifest_path}")
    manifest = json.loads(manifest_payload)
    if not isinstance(manifest, list):
        raise TypeError(f"OpenStates manifest must contain a list: {manifest_path}")
    urls = {
        str(item.get("text")): str(item.get("href")) for item in manifest if isinstance(item, dict)
    }
    for spec in PARENTS:
        if urls.get(spec.manifest_text) != spec.source_url:
            raise ValueError(f"manifest URL mismatch for {spec.manifest_text}")
        parent_path = source_root / spec.archive_name
        _require_regular_file(parent_path, "parent ZIP")
        if (
            parent_path.stat().st_size != spec.byte_count
            or _sha256_file(parent_path) != spec.sha256
        ):
            raise ValueError(f"parent ZIP size/hash mismatch: {parent_path}")


def _readme_generated_at(payload: bytes, source_name: str) -> str:
    text = payload.decode("utf-8", errors="strict")
    for line in text.splitlines():
        key, marker, value = line.partition(":")
        if marker and key.strip().lower() == "generated at":
            return value.strip()
    raise ValueError(f"README lacks Generated At: {source_name}")


def _one(records: Sequence[CsvRecord], description: str) -> CsvRecord:
    if len(records) != 1:
        raise ValueError(f"expected one {description}, found {len(records)}")
    return records[0]


def _derive_parent(source_root: Path, spec: ParentSpec) -> dict[str, DerivedFile]:
    parent_path = source_root / spec.archive_name
    with zipfile.ZipFile(parent_path) as archive:
        required_members = {"README", *(spec.member_path(table) for table in TABLE_NAMES)}
        names = set(archive.namelist())
        missing = sorted(required_members - names)
        if missing:
            raise ValueError(f"parent ZIP lacks selected members {missing}: {parent_path}")

        readme = archive.read("README")
        if _readme_generated_at(readme, spec.archive_name) != spec.generated_at:
            raise ValueError(f"README generation timestamp mismatch: {parent_path}")
        tables = {
            table: _parse_csv_records(
                archive.read(spec.member_path(table)), spec.member_path(table)
            )
            for table in TABLE_NAMES
        }

    target_vote = _one(
        tuple(
            record
            for record in tables["votes"].records
            if record.values.get("id") == TARGET_ROLL_ID
        ),
        f"target vote in {spec.archive_name}",
    )
    if target_vote.values.get("bill_id") != spec.bill_id:
        raise ValueError(f"target vote bill link changed in {spec.archive_name}")

    selected_actions = tuple(
        record
        for record in tables["bill_actions"].records
        if record.values.get("bill_id") == spec.bill_id
    )
    organization_ids = {
        target_vote.values.get("organization_id", ""),
        *(record.values.get("organization_id", "") for record in selected_actions),
    }
    organization_ids.discard("")

    predicates: dict[str, Callable[[Mapping[str, str]], bool]] = {
        "bills": lambda row: row.get("id") == spec.bill_id,
        "bill_actions": lambda row: row.get("bill_id") == spec.bill_id,
        "bill_sources": lambda row: row.get("bill_id") == spec.bill_id,
        "votes": lambda row: row.get("id") == TARGET_ROLL_ID,
        "vote_people": lambda row: row.get("vote_event_id") == TARGET_ROLL_ID,
        "vote_counts": lambda row: row.get("vote_event_id") == TARGET_ROLL_ID,
        "vote_sources": lambda row: row.get("vote_event_id") == TARGET_ROLL_ID,
        "organizations": lambda row: row.get("id") in organization_ids,
    }

    derived: dict[str, DerivedFile] = {
        "README": DerivedFile(readme, "README", None, "verbatim_member")
    }
    for table in TABLE_NAMES:
        payload, row_count = tables[table].filter(predicates[table])
        expected = spec.row_count(table)
        if row_count != expected:
            raise ValueError(
                f"selected {row_count} {table} rows, expected {expected}: {spec.archive_name}"
            )
        derived[f"{table}.csv"] = DerivedFile(
            payload,
            spec.member_path(table),
            row_count,
            "verbatim_header_and_selected_records",
        )
    return derived


def _expected_relative_paths() -> tuple[str, ...]:
    paths = [RECEIPT_NAME]
    for spec in PARENTS:
        paths.append(f"{spec.label}/README")
        paths.extend(f"{spec.label}/{table}.csv" for table in TABLE_NAMES)
    return tuple(sorted(paths))


def _canonical_extract(files: Mapping[str, bytes]) -> tuple[str, int]:
    digest = hashlib.sha256()
    byte_count = 0
    for relative_path in sorted(files):
        payload = files[relative_path]
        encoded_path = relative_path.encode("utf-8")
        encoded_size = str(len(payload)).encode("ascii")
        digest.update(encoded_path)
        digest.update(b"\0")
        digest.update(encoded_size)
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
        byte_count += len(payload)
    return digest.hexdigest(), byte_count


def _receipt(extract_files: Mapping[str, bytes]) -> dict[str, Any]:
    extract_sha256, extract_bytes = _canonical_extract(extract_files)
    file_rows = [
        {
            "path": path,
            "byte_count": len(extract_files[path]),
            "sha256": _sha256(extract_files[path]),
        }
        for path in sorted(extract_files)
    ]
    parent_rows: list[dict[str, Any]] = []
    for spec in PARENTS:
        selected_members: list[dict[str, Any]] = []
        selections = (("README", "README", None, "verbatim_member"),) + tuple(
            (
                spec.member_path(table),
                f"{table}.csv",
                spec.row_count(table),
                "verbatim_header_and_selected_records",
            )
            for table in TABLE_NAMES
        )
        for member, fixture_name, selected_rows, extraction in selections:
            fixture_path = f"{spec.label}/{fixture_name}"
            selected_members.append(
                {
                    "member_path": member,
                    "fixture_path": fixture_path,
                    "extraction": extraction,
                    "selected_data_rows": selected_rows,
                    "byte_count": len(extract_files[fixture_path]),
                    "sha256": _sha256(extract_files[fixture_path]),
                }
            )
        parent_rows.append(
            {
                "label": spec.label,
                "archive_name": spec.archive_name,
                "path_name": spec.path_name,
                "source_url": spec.source_url,
                "byte_count": spec.byte_count,
                "sha256": spec.sha256,
                "generated_at": spec.generated_at,
                "selected_members": selected_members,
            }
        )
    return {
        "receipt_version": 1,
        "format": RECEIPT_FORMAT,
        "prototype_status": "measurement_fixture_only_not_task_schema",
        "fixture_bytes_label": FIXTURE_BYTES_LABEL,
        "target_roll_id": TARGET_ROLL_ID,
        "selection_policy": {
            "bills": "the bill_id linked from the target vote in each parent",
            "bill_actions": "all rows linked to the selected bill",
            "bill_sources": "all rows linked to the selected bill",
            "votes": "the target roll ID",
            "vote_people": "all rows linked to the target roll ID",
            "vote_counts": "all rows linked to the target roll ID",
            "vote_sources": "all rows linked to the target roll ID",
            "organizations": "rows directly referenced by selected actions or votes",
            "record_bytes": "original header and selected data records in original order",
        },
        "url_evidence": {
            "path_name": MANIFEST_PATH_NAME,
            "byte_count": MANIFEST_BYTES,
            "sha256": MANIFEST_SHA256,
        },
        "parents": parent_rows,
        "canonical_extract": {
            "algorithm": CANONICAL_DIGEST_ALGORITHM,
            "sha256": extract_sha256,
            "file_count": len(extract_files),
            "byte_count": extract_bytes,
            "files": file_rows,
        },
    }


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _files_from_directory(root: Path) -> dict[str, bytes]:
    try:
        root_mode = root.lstat().st_mode
    except FileNotFoundError as exc:
        raise ValueError(f"fixture root does not exist: {root}") from exc
    if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode):
        raise ValueError(f"fixture root must be a non-symlink directory: {root}")
    files: dict[str, bytes] = {}
    for path in root.rglob("*"):
        mode = path.lstat().st_mode
        relative = path.relative_to(root).as_posix()
        if stat.S_ISLNK(mode) or (not stat.S_ISREG(mode) and not stat.S_ISDIR(mode)):
            raise ValueError(f"fixture contains a symlink or special path: {relative}")
        if stat.S_ISREG(mode):
            files[relative] = path.read_bytes()
    expected = set(_expected_relative_paths())
    if set(files) != expected:
        raise ValueError(f"fixture allow-list mismatch: {sorted(set(files) ^ expected)}")
    return files


def _semantic_measurements(extract_files: Mapping[str, bytes]) -> dict[str, int]:
    tables_by_label: dict[str, dict[str, CsvTable]] = {}
    for spec in PARENTS:
        tables_by_label[spec.label] = {
            table: _parse_csv_records(
                extract_files[f"{spec.label}/{table}.csv"],
                f"{spec.label}/{table}.csv",
            )
            for table in TABLE_NAMES
        }

    all_people: list[CsvRecord] = []
    roster_maps: list[dict[str, str]] = []
    action_count = 0
    organization_count = 0
    for spec in PARENTS:
        tables = tables_by_label[spec.label]
        bills = tables["bills"].records
        votes = tables["votes"].records
        bill = _one(bills, f"fixture bill for {spec.label}")
        vote = _one(votes, f"fixture vote for {spec.label}")
        if (
            bill.values.get("id") != spec.bill_id
            or bill.values.get("title") != spec.bill_title
            or bill.values.get("session_identifier") != spec.session
        ):
            raise ValueError(f"fixture bill identity mismatch: {spec.label}")
        if (
            vote.values.get("id") != TARGET_ROLL_ID
            or vote.values.get("bill_id") != spec.bill_id
            or vote.values.get("session_identifier") != spec.session
            or vote.values.get("start_date") != "2024-01-30"
            or vote.values.get("motion_text") != "senate passage"
            or vote.values.get("result") != "pass"
        ):
            raise ValueError(f"fixture target vote identity mismatch: {spec.label}")

        for table, link_field, link_id in (
            ("bill_actions", "bill_id", spec.bill_id),
            ("bill_sources", "bill_id", spec.bill_id),
            ("vote_people", "vote_event_id", TARGET_ROLL_ID),
            ("vote_counts", "vote_event_id", TARGET_ROLL_ID),
            ("vote_sources", "vote_event_id", TARGET_ROLL_ID),
        ):
            records = tables[table].records
            if len(records) != spec.row_count(table) or any(
                record.values.get(link_field) != link_id for record in records
            ):
                raise ValueError(f"fixture relationship mismatch in {spec.label}/{table}.csv")

        action_orgs = {
            record.values.get("organization_id", "") for record in tables["bill_actions"].records
        }
        direct_orgs = action_orgs | {vote.values.get("organization_id", "")}
        direct_orgs.discard("")
        retained_orgs = {record.values.get("id", "") for record in tables["organizations"].records}
        if retained_orgs != direct_orgs or len(retained_orgs) != spec.row_count("organizations"):
            raise ValueError(f"fixture organization selection mismatch: {spec.label}")

        people = list(tables["vote_people"].records)
        all_people.extend(people)
        roster = {
            record.values.get("voter_id") or record.values.get("voter_name", ""): record.values.get(
                "option", ""
            )
            for record in people
        }
        if len(roster) != 42:
            raise ValueError(f"fixture voter keys are not unique: {spec.label}")
        roster_maps.append(roster)
        if (
            sum(int(record.values.get("value", "-1")) for record in tables["vote_counts"].records)
            != 42
        ):
            raise ValueError(f"fixture vote counts do not total 42: {spec.label}")
        action_count += len(tables["bill_actions"].records)
        organization_count += len(tables["organizations"].records)

    overlap = set(roster_maps[0]) & set(roster_maps[1])
    if len(overlap) != 37 or any(roster_maps[0][key] != roster_maps[1][key] for key in overlap):
        raise ValueError("cross-session voter overlap measurement changed")
    if len(set(roster_maps[0]) ^ set(roster_maps[1])) != 10:
        raise ValueError("cross-session session-only voter measurement changed")
    resolved_people = {
        record.values.get("voter_id", "") for record in all_people if record.values.get("voter_id")
    }
    unresolved = sum(not record.values.get("voter_id") for record in all_people)
    if len(resolved_people) != 39 or unresolved != 11:
        raise ValueError("resolved/unresolved voter measurement changed")

    return {
        "bills": 2,
        "bill_actions": action_count,
        "bill_sources": 2,
        "rolls": 2,
        "vote_people": len(all_people),
        "vote_counts": 10,
        "vote_sources": 2,
        "organizations": organization_count,
        "resolved_people": len(resolved_people),
        "unresolved_vote_rows": unresolved,
        "shared_voter_keys_same_choice": len(overlap),
        "session_only_voter_keys": len(set(roster_maps[0]) ^ set(roster_maps[1])),
    }


def audit_fixture(root: Path = FIXTURE_ROOT, source_root: Path | None = None) -> dict[str, Any]:
    """Audit the bounded allow-list, receipt, digest, graph, and optional parents."""
    files = _files_from_directory(root)
    receipt_payload = files.pop(RECEIPT_NAME)
    try:
        actual_receipt = json.loads(receipt_payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid fixture source receipt: {root / RECEIPT_NAME}") from exc
    expected_receipt = _receipt(files)
    if actual_receipt != expected_receipt:
        raise ValueError(f"source receipt differs from extracted fixture bytes: {root}")
    extract_sha256, extract_bytes = _canonical_extract(files)
    if (
        PINNED_CANONICAL_EXTRACT_SHA256 != "PENDING_FIRST_BUILD"
        and extract_sha256 != PINNED_CANONICAL_EXTRACT_SHA256
    ):
        raise ValueError(f"canonical extract digest mismatch: {root}")

    parents_verified = 0
    if source_root is not None:
        _verify_source_root(source_root)
        for spec in PARENTS:
            expected = _derive_parent(source_root, spec)
            for fixture_name, derived in expected.items():
                relative = f"{spec.label}/{fixture_name}"
                if files[relative] != derived.payload:
                    raise ValueError(
                        f"fixture bytes differ from selected parent records: {relative}"
                    )
            parents_verified += 1

    measurements = _semantic_measurements(files)
    return {
        "status": "PASS",
        "prototype_status": "measurement_fixture_only_not_task_schema",
        "canonical_extract_sha256": extract_sha256,
        "extract_file_count": len(files),
        "extract_byte_count": extract_bytes,
        "parent_archives": len(PARENTS),
        "parents_verified": parents_verified,
        **measurements,
    }


def build_fixture(
    source_root: Path = DEFAULT_SOURCE_ROOT, output: Path = FIXTURE_ROOT
) -> dict[str, Any]:
    """Build once from the two pinned parents; never overwrite an existing fixture."""
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"refusing to overwrite fixture destination: {output}")
    _verify_source_root(source_root)
    derived_by_parent = {spec.label: _derive_parent(source_root, spec) for spec in PARENTS}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        stage = Path(temporary) / "fixture"
        stage.mkdir()
        extract_files: dict[str, bytes] = {}
        for spec in PARENTS:
            artifact_dir = stage / spec.label
            artifact_dir.mkdir()
            for fixture_name, derived in derived_by_parent[spec.label].items():
                relative = f"{spec.label}/{fixture_name}"
                extract_files[relative] = derived.payload
                (stage / relative).write_bytes(derived.payload)
        (stage / RECEIPT_NAME).write_bytes(_json_bytes(_receipt(extract_files)))
        result = audit_fixture(stage, source_root=source_root)
        os.replace(stage, output)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="build the pinned prototype fixture")
    build.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    build.add_argument("--output", type=Path, default=FIXTURE_ROOT)
    audit = subparsers.add_parser("audit", help="audit an existing prototype fixture")
    audit.add_argument("--input", type=Path, default=FIXTURE_ROOT)
    audit.add_argument("--source-root", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "build":
        result = build_fixture(args.source_root, args.output)
    else:
        result = audit_fixture(args.input, args.source_root)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_SOURCE_ROOT",
    "FIXTURE_ROOT",
    "PINNED_CANONICAL_EXTRACT_SHA256",
    "TARGET_ROLL_ID",
    "audit_fixture",
    "build_fixture",
    "main",
]
