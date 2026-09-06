"""Runtime entry point for member -> FEC candidate ID crosswalk loads."""

from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from src.core.files import sha256_file as _sha256_file
from src.db.repositories import ConnectionLike, fetch_all, rollback_if_available
from src.normalize.member_crosswalk import CrosswalkRecord, validate_one_to_one
from src.provenance.artifacts import create_source_artifact
from src.provenance.store import (
    ensure_data_source,
    fail_ingestion_run,
    finish_ingestion_run,
    start_ingestion_run,
)
from src.runtime.sources import MEMBER_FEC_CROSSWALK


@dataclass(frozen=True)
class MemberFecCrosswalkRow:
    bioguide_id: str
    fec_candidate_id: str


@dataclass(frozen=True)
class MemberTermCrosswalkRow:
    bioguide_id: str
    congress: int
    chamber: str
    state: str | None
    district: int | None
    start_date: dt.date
    end_date: dt.date | None
    is_current: bool


@dataclass(frozen=True)
class MemberFecCrosswalkLoadResult:
    data_source: dict[str, Any]
    run_id: int
    source_artifact: dict[str, Any]
    parsed_count: int
    parsed_member_term_count: int
    updated_count: int
    member_term_upserted_count: int
    skipped_bioguide_ids: list[str]
    skipped_member_term_bioguide_ids: list[str]


def load_member_fec_crosswalk_runtime(
    conn: ConnectionLike,
    path: Path,
    *,
    member_terms_path: Path | None = None,
    source_url: str | None = None,
) -> MemberFecCrosswalkLoadResult:
    """Load a local CSV mapping existing members to FEC candidate IDs."""
    if not path.is_file():
        raise FileNotFoundError(path)
    rows = _read_crosswalk_rows(path)
    member_term_rows = _read_member_term_rows(member_terms_path) if member_terms_path else []
    _validate_crosswalk_rows(rows)

    data_source = ensure_data_source(
        conn,
        slug=MEMBER_FEC_CROSSWALK.slug,
        name=MEMBER_FEC_CROSSWALK.name,
        source_kind=MEMBER_FEC_CROSSWALK.source_kind,
        base_url=MEMBER_FEC_CROSSWALK.base_url,
    )
    run_id = start_ingestion_run(
        conn,
        data_source_id=data_source["id"],
        run_type="ingest",
        parameters={
            "stage": "member_fec_crosswalk",
            "path": str(path),
            "member_terms_path": str(member_terms_path) if member_terms_path else None,
            "row_count": len(rows),
            "member_term_row_count": len(member_term_rows),
        },
    )

    try:
        artifact = _create_or_reuse_source_artifact(
            conn,
            data_source_id=data_source["id"],
            artifact_kind="csv",
            storage_uri=str(path.resolve()),
            sha256=_sha256_file(path),
            ingestion_run_id=run_id,
            source_url=source_url or MEMBER_FEC_CROSSWALK.base_url,
            mime_type="text/csv",
            source_record_id="member-fec-crosswalk",
        )
        updated_count, skipped = _update_member_fec_ids(conn, rows, _artifact_id(artifact))
        member_term_upserted_count, skipped_member_terms = _upsert_member_terms(
            conn,
            member_term_rows,
            _artifact_id(artifact),
        )
    except Exception as exc:
        rollback_if_available(conn)
        fail_ingestion_run(conn, run_id, str(exc))
        raise

    try:
        finish_ingestion_run(
            conn,
            run_id,
            record_count=updated_count + member_term_upserted_count,
        )
    except Exception:
        rollback_if_available(conn)
        raise

    return MemberFecCrosswalkLoadResult(
        data_source=data_source,
        run_id=run_id,
        source_artifact=artifact,
        parsed_count=len(rows),
        parsed_member_term_count=len(member_term_rows),
        updated_count=updated_count,
        member_term_upserted_count=member_term_upserted_count,
        skipped_bioguide_ids=skipped,
        skipped_member_term_bioguide_ids=skipped_member_terms,
    )


def _read_crosswalk_rows(path: Path) -> list[MemberFecCrosswalkRow]:
    rows: list[MemberFecCrosswalkRow] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError("member FEC crosswalk CSV is missing a header")
        if "bioguide_id" not in reader.fieldnames or "fec_candidate_id" not in reader.fieldnames:
            raise ValueError(
                "member FEC crosswalk requires bioguide_id and fec_candidate_id columns"
            )
        for idx, raw in enumerate(reader, start=2):
            bioguide_id = (raw.get("bioguide_id") or "").strip()
            fec_candidate_id = (raw.get("fec_candidate_id") or "").strip().upper()
            if not bioguide_id:
                raise ValueError(f"row {idx} is missing bioguide_id")
            if not fec_candidate_id:
                continue
            rows.append(
                MemberFecCrosswalkRow(
                    bioguide_id=bioguide_id,
                    fec_candidate_id=fec_candidate_id,
                )
            )
    return rows


def _validate_crosswalk_rows(rows: list[MemberFecCrosswalkRow]) -> None:
    records = [
        CrosswalkRecord(row.bioguide_id, fec_candidate_id=row.fec_candidate_id) for row in rows
    ]
    conflicts = validate_one_to_one(records)
    if conflicts:
        conflict = conflicts[0]
        raise ValueError(f"member FEC crosswalk has duplicate {conflict.field}: {conflict.value}")


def _read_member_term_rows(path: Path) -> list[MemberTermCrosswalkRow]:
    if not path.is_file():
        raise FileNotFoundError(path)
    rows: list[MemberTermCrosswalkRow] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        required = {"bioguide_id", "congress", "chamber", "start_date"}
        if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
            raise ValueError(
                "member terms CSV requires bioguide_id, congress, chamber, and start_date columns"
            )
        for idx, raw in enumerate(reader, start=2):
            bioguide_id = (raw.get("bioguide_id") or "").strip()
            if not bioguide_id:
                raise ValueError(f"member terms row {idx} is missing bioguide_id")
            rows.append(
                MemberTermCrosswalkRow(
                    bioguide_id=bioguide_id,
                    congress=_parse_positive_int(raw.get("congress"), f"row {idx} congress"),
                    chamber=_parse_chamber(raw.get("chamber"), idx),
                    state=_parse_state(raw.get("state")),
                    district=_parse_optional_positive_int(raw.get("district")),
                    start_date=_parse_date(raw.get("start_date"), f"row {idx} start_date"),
                    end_date=_parse_optional_date(raw.get("end_date")),
                    is_current=_parse_bool(raw.get("is_current")),
                )
            )
    return rows


def _parse_chamber(value: str | None, row_number: int) -> str:
    chamber = (value or "").strip().lower()
    if chamber not in {"house", "senate"}:
        raise ValueError(f"member terms row {row_number} has invalid chamber")
    return chamber


def _parse_state(value: str | None) -> str | None:
    state = (value or "").strip().upper()
    return state or None


def _parse_positive_int(value: str | None, field_name: str) -> int:
    parsed = int((value or "").strip())
    if parsed <= 0:
        raise ValueError(f"{field_name} must be positive")
    return parsed


def _parse_optional_positive_int(value: str | None) -> int | None:
    stripped = (value or "").strip()
    if not stripped:
        return None
    parsed = int(stripped)
    if parsed == 0:
        return None
    if parsed < 0:
        raise ValueError("district must be positive")
    return parsed


def _parse_date(value: str | None, field_name: str) -> dt.date:
    stripped = (value or "").strip()
    if not stripped:
        raise ValueError(f"{field_name} is required")
    return dt.date.fromisoformat(stripped)


def _parse_optional_date(value: str | None) -> dt.date | None:
    stripped = (value or "").strip()
    return dt.date.fromisoformat(stripped) if stripped else None


def _parse_bool(value: str | None) -> bool:
    return (value or "").strip().lower() == "true"


def _update_member_fec_ids(
    conn: ConnectionLike,
    rows: list[MemberFecCrosswalkRow],
    source_artifact_id: int,  # noqa: ARG001
) -> tuple[int, list[str]]:
    from psycopg.rows import dict_row

    updated = 0
    skipped: list[str] = []
    with conn.cursor(row_factory=dict_row) as raw_cur:
        cur = cast(Any, raw_cur)
        for row in rows:
            cur.execute(
                """
                UPDATE member
                   SET fec_candidate_id = %s,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE bioguide_id = %s
                RETURNING id
                """,
                (row.fec_candidate_id, row.bioguide_id),
            )
            result = cur.fetchone()
            if result is None:
                skipped.append(row.bioguide_id)
            else:
                updated += 1
    return updated, skipped


def _create_or_reuse_source_artifact(
    conn: ConnectionLike,
    *,
    data_source_id: int,
    artifact_kind: str,
    storage_uri: str,
    sha256: str,
    ingestion_run_id: int,
    source_url: str | None,
    mime_type: str,
    source_record_id: str,
) -> dict[str, Any]:
    existing = fetch_all(conn, "SELECT * FROM source_artifact WHERE sha256 = %s", (sha256,))
    if existing:
        return existing[0]
    return create_source_artifact(
        conn,
        data_source_id=data_source_id,
        artifact_kind=artifact_kind,
        storage_uri=storage_uri,
        sha256=sha256,
        ingestion_run_id=ingestion_run_id,
        source_url=source_url,
        mime_type=mime_type,
        source_record_id=source_record_id,
        commit=False,
    )


def _upsert_member_terms(
    conn: ConnectionLike,
    rows: list[MemberTermCrosswalkRow],
    source_artifact_id: int,
) -> tuple[int, list[str]]:
    from psycopg.rows import dict_row

    upserted = 0
    skipped: list[str] = []
    with conn.cursor(row_factory=dict_row) as raw_cur:
        cur = cast(Any, raw_cur)
        for row in rows:
            cur.execute(
                """
                INSERT INTO member_term (
                    member_id,
                    source_artifact_id,
                    source_record_id,
                    congress,
                    chamber,
                    state,
                    district,
                    start_date,
                    end_date,
                    is_current,
                    updated_at
                )
                SELECT m.id, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP
                  FROM member m
                 WHERE m.bioguide_id = %s
                ON CONFLICT (member_id, congress, chamber, start_date)
                DO UPDATE SET
                    source_artifact_id = EXCLUDED.source_artifact_id,
                    source_record_id = EXCLUDED.source_record_id,
                    state = EXCLUDED.state,
                    district = EXCLUDED.district,
                    end_date = EXCLUDED.end_date,
                    is_current = EXCLUDED.is_current,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id
                """,
                (
                    source_artifact_id,
                    f"{row.bioguide_id}:{row.congress}:{row.chamber}:{row.start_date}",
                    row.congress,
                    row.chamber,
                    row.state,
                    row.district,
                    row.start_date,
                    row.end_date,
                    row.is_current,
                    row.bioguide_id,
                ),
            )
            result = cur.fetchone()
            if result is None:
                skipped.append(row.bioguide_id)
            else:
                upserted += 1
    return upserted, skipped


def _artifact_id(row: dict[str, Any]) -> int:
    artifact_id = row.get("id")
    if isinstance(artifact_id, bool) or not isinstance(artifact_id, int):
        raise TypeError(f"expected source_artifact id to be int, got {artifact_id!r}")
    return artifact_id
