"""Canonical table schemas and tiny Parquet-writing utilities.

The time machine deliberately has no ORM and no generic graph model.  These
schemas are the contract.  Every evidence row separates three clocks:

``event_at``
    When the legislative event happened.
``available_at``
    Earliest defensible time the source made the fact public/knowable.
``observed_at``
    When this local project first acquired and hashed the source artifact.
``valid_from`` / ``valid_to``
    The legal/effective interval when the row represents state rather than an
    immutable event.
Point-in-time DuckDB macros apply public availability and event/legal time;
companion ``*_as_observed`` macros also apply the local observation clock.
Callers therefore cannot get a future action merely because it appeared in a
later snapshot, and can separately reproduce what this installation had seen.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

UTC_TS = pa.timestamp("us", tz="UTC")


def _schema(fields: list[tuple[str, pa.DataType]]) -> pa.Schema:
    return pa.schema(fields)


PROVENANCE_FIELDS: list[tuple[str, pa.DataType]] = [
    ("event_at", UTC_TS),
    ("available_at", UTC_TS),
    ("availability_basis", pa.string()),
    ("observed_at", UTC_TS),
    ("valid_from", UTC_TS),
    ("valid_to", UTC_TS),
    ("source_artifact_id", pa.string()),
    ("source_family", pa.string()),
    ("source_url", pa.string()),
    ("content_sha256", pa.string()),
]


TABLE_SCHEMAS: dict[str, pa.Schema] = {
    "source_artifacts": _schema(
        [
            ("source_artifact_id", pa.string()),
            ("source_family", pa.string()),
            ("relative_path", pa.string()),
            ("source_url", pa.string()),
            ("media_type", pa.string()),
            ("content_sha256", pa.string()),
            ("byte_count", pa.int64()),
            ("retained", pa.bool_()),
            ("modified_at", UTC_TS),
            ("published_at", UTC_TS),
            ("available_at", UTC_TS),
            ("availability_basis", pa.string()),
            ("observed_at", UTC_TS),
            ("inventoried_at", UTC_TS),
        ]
    ),
    "people": _schema(
        [
            ("person_id", pa.string()),
            ("display_name", pa.string()),
            ("jurisdiction_id", pa.string()),
            ("birth_date", pa.date32()),
            ("gender", pa.string()),
            *PROVENANCE_FIELDS,
        ]
    ),
    "person_ids": _schema(
        [
            ("person_id", pa.string()),
            ("id_scheme", pa.string()),
            ("id_value", pa.string()),
            ("is_primary", pa.bool_()),
            *PROVENANCE_FIELDS,
        ]
    ),
    "terms": _schema(
        [
            ("term_id", pa.string()),
            ("person_id", pa.string()),
            ("jurisdiction_id", pa.string()),
            ("chamber", pa.string()),
            ("state", pa.string()),
            ("district", pa.string()),
            ("party", pa.string()),
            ("start_date", pa.date32()),
            ("end_date", pa.date32()),
            *PROVENANCE_FIELDS,
        ]
    ),
    "sessions": _schema(
        [
            ("session_id", pa.string()),
            ("jurisdiction_id", pa.string()),
            ("identifier", pa.string()),
            ("name", pa.string()),
            ("start_date", pa.date32()),
            ("end_date", pa.date32()),
            *PROVENANCE_FIELDS,
        ]
    ),
    "bills": _schema(
        [
            ("bill_id", pa.string()),
            ("jurisdiction_id", pa.string()),
            ("session_id", pa.string()),
            ("source_bill_id", pa.string()),
            ("identifier", pa.string()),
            ("classification", pa.string()),
            ("title", pa.string()),
            ("introduced_date", pa.date32()),
            ("policy_area", pa.string()),
            ("subjects", pa.list_(pa.string())),
            ("summary_text", pa.string()),
            *PROVENANCE_FIELDS,
        ]
    ),
    "bill_text_versions": _schema(
        [
            ("text_version_id", pa.string()),
            ("bill_id", pa.string()),
            ("version_code", pa.string()),
            ("version_name", pa.string()),
            ("issued_date", pa.date32()),
            ("media_type", pa.string()),
            ("content_path", pa.string()),
            ("text_content", pa.large_string()),
            ("is_full_text", pa.bool_()),
            *PROVENANCE_FIELDS,
        ]
    ),
    "actions": _schema(
        [
            ("action_id", pa.string()),
            ("bill_id", pa.string()),
            ("organization_id", pa.string()),
            ("description", pa.string()),
            ("classification", pa.string()),
            ("action_date", pa.date32()),
            *PROVENANCE_FIELDS,
        ]
    ),
    "amendments": _schema(
        [
            ("amendment_id", pa.string()),
            ("bill_id", pa.string()),
            ("identifier", pa.string()),
            ("description", pa.string()),
            ("proposed_date", pa.date32()),
            ("text_version_id", pa.string()),
            *PROVENANCE_FIELDS,
        ]
    ),
    "law_links": _schema(
        [
            ("law_link_id", pa.string()),
            ("bill_id", pa.string()),
            ("law_id", pa.string()),
            ("law_type", pa.string()),
            ("law_number", pa.string()),
            ("statute_citation", pa.string()),
            ("enacted_date", pa.date32()),
            *PROVENANCE_FIELDS,
        ]
    ),
    "roll_calls": _schema(
        [
            ("roll_call_id", pa.string()),
            ("jurisdiction_id", pa.string()),
            ("session_id", pa.string()),
            ("chamber", pa.string()),
            ("identifier", pa.string()),
            ("source_roll_call_id", pa.string()),
            ("bill_id", pa.string()),
            ("source_bill_id", pa.string()),
            ("motion", pa.string()),
            ("result", pa.string()),
            ("roll_call_date", pa.date32()),
            *PROVENANCE_FIELDS,
        ]
    ),
    "member_votes": _schema(
        [
            ("member_vote_id", pa.string()),
            ("roll_call_id", pa.string()),
            ("person_id", pa.string()),
            ("source_person_id", pa.string()),
            ("member_name", pa.string()),
            ("choice", pa.string()),
            *PROVENANCE_FIELDS,
        ]
    ),
}


TABLE_KEYS: dict[str, tuple[str, ...]] = {
    "source_artifacts": ("source_artifact_id",),
    "people": ("person_id",),
    "person_ids": ("id_scheme", "id_value"),
    "terms": ("term_id",),
    "sessions": ("session_id",),
    "bills": ("bill_id",),
    "bill_text_versions": ("text_version_id",),
    "actions": ("action_id",),
    "amendments": ("amendment_id",),
    "law_links": ("law_link_id",),
    "roll_calls": ("roll_call_id",),
    "member_votes": ("member_vote_id",),
}


def utc_datetime(value: datetime | date | str | None) -> datetime | None:
    """Coerce a source timestamp/date to an aware UTC datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, str):
        raw = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            parsed_date = date.fromisoformat(raw[:10])
            return datetime(parsed_date.year, parsed_date.month, parsed_date.day, tzinfo=UTC)
    else:
        parsed = value
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def iso_date(value: str | date | None) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def sha256_file(path: Path, *, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_id(content_sha256: str) -> str:
    return f"artifact:sha256:{content_sha256}"


def stable_id(namespace: str, *parts: object) -> str:
    """Readable namespace plus a stable digest for source tuples."""
    payload = "\x1f".join(str(part) for part in parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"{namespace}:{digest}"


def federal_person_id(bioguide_id: str) -> str:
    return f"person:bioguide:{bioguide_id.upper()}"


def openstates_person_id(ocd_id: str) -> str:
    return f"person:openstates:{ocd_id.removeprefix('openstates:')}"


def federal_bill_id(congress: int, bill_type: str, number: int | str) -> str:
    return f"bill:us-congress:{congress}:{bill_type.lower()}:{int(number)}"


def openstates_bill_id(ocd_id: str) -> str:
    return f"bill:openstates:{ocd_id}"


def provenance(
    *,
    event_at: datetime | date | str | None,
    available_at: datetime | date | str | None,
    availability_basis: str,
    observed_at: datetime | date | str,
    source_artifact_id: str,
    source_family: str,
    source_url: str | None,
    content_sha256: str | None,
    valid_from: datetime | date | str | None = None,
    valid_to: datetime | date | str | None = None,
) -> dict[str, Any]:
    """Build the shared bitemporal/provenance columns for a canonical row."""
    return {
        "event_at": utc_datetime(event_at),
        "available_at": utc_datetime(available_at),
        "availability_basis": availability_basis,
        "observed_at": utc_datetime(observed_at),
        "valid_from": utc_datetime(valid_from),
        "valid_to": utc_datetime(valid_to),
        "source_artifact_id": source_artifact_id,
        "source_family": source_family,
        "source_url": source_url,
        "content_sha256": content_sha256,
    }


class ParquetSink:
    """Bounded-memory, deterministic row-group writer for one canonical table."""

    def __init__(
        self,
        path: Path,
        schema: pa.Schema,
        *,
        batch_size: int = 50_000,
    ) -> None:
        self.path = path
        self.schema = schema
        self.batch_size = batch_size
        self._buffer: list[Mapping[str, Any]] = []
        self._writer: pq.ParquetWriter | None = None
        self.row_count = 0

    def write(self, row: Mapping[str, Any]) -> None:
        self._buffer.append(row)
        if len(self._buffer) >= self.batch_size:
            self.flush()

    def write_many(self, rows: Iterable[Mapping[str, Any]]) -> None:
        for row in rows:
            self.write(row)

    def flush(self) -> None:
        if not self._buffer:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pylist(list(self._buffer), schema=self.schema)
        if self._writer is None:
            self._writer = pq.ParquetWriter(
                self.path,
                self.schema,
                compression="zstd",
                use_dictionary=True,
                write_statistics=True,
            )
        self._writer.write_table(table)
        self.row_count += len(self._buffer)
        self._buffer.clear()

    def close(self) -> None:
        self.flush()
        if self._writer is not None:
            self._writer.close()
            self._writer = None
        elif not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_pylist([], schema=self.schema), self.path)

    def __enter__(self) -> ParquetSink:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc_type is None:
            self.close()
        elif self._writer is not None:
            self._writer.close()
