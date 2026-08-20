from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from src.time_machine.catalog import create_catalog, query
from src.time_machine.integrity import generate_integrity_report
from src.time_machine.model import TABLE_SCHEMAS, ParquetSink, provenance


def _empty_catalog(root: Path, bill_rows: list[dict[str, object]]) -> None:
    for table, schema in TABLE_SCHEMAS.items():
        with ParquetSink(root / "parquet" / f"{table}.parquet", schema) as sink:
            if table == "source_artifacts":
                sink.write(
                    {
                        "source_artifact_id": "artifact:sha256:" + "a" * 64,
                        "source_family": "fixture",
                        "relative_path": "fixture.json",
                        "source_url": "https://example.gov/source",
                        "media_type": "application/json",
                        "content_sha256": "a" * 64,
                        "byte_count": 1,
                        "retained": True,
                        "modified_at": datetime(2022, 1, 1, tzinfo=UTC),
                        "published_at": None,
                        "available_at": datetime(2022, 1, 1, tzinfo=UTC),
                        "availability_basis": "fixture",
                        "observed_at": datetime(2022, 1, 1, tzinfo=UTC),
                        "inventoried_at": datetime(2022, 1, 1, tzinfo=UTC),
                    }
                )
            elif table == "bills":
                sink.write_many(bill_rows)
    create_catalog(root)


def _bill(
    bill_id: str,
    *,
    event_at: str,
    available_at: str | None,
    observed_at: str,
) -> dict[str, object]:
    return {
        "bill_id": bill_id,
        "jurisdiction_id": "us-congress",
        "session_id": "session:us-congress:116",
        "source_bill_id": bill_id,
        "identifier": bill_id,
        "classification": "hr",
        "title": bill_id,
        "introduced_date": None,
        "policy_area": None,
        "subjects": [],
        "summary_text": None,
        **provenance(
            event_at=event_at,
            available_at=available_at,
            availability_basis="fixture",
            observed_at=observed_at,
            source_artifact_id="artifact:sha256:" + "a" * 64,
            source_family="fixture",
            source_url="https://example.gov/source",
            content_sha256="a" * 64,
        ),
    }


def test_as_of_separates_event_availability_and_local_observation(tmp_path: Path) -> None:
    rows = [
        _bill(
            "past",
            event_at="2020-01-01",
            available_at="2020-01-02",
            observed_at="2022-01-01",
        ),
        _bill(
            "preannounced-future",
            event_at="2025-01-01",
            available_at="2022-01-01",
            observed_at="2022-01-01",
        ),
        _bill(
            "late-publication",
            event_at="2020-01-01",
            available_at="2024-01-01",
            observed_at="2024-02-01",
        ),
        _bill(
            "unknown-availability",
            event_at="2020-01-01",
            available_at=None,
            observed_at="2022-01-01",
        ),
    ]
    _empty_catalog(tmp_path, rows)

    _columns, public_rows = query(
        tmp_path,
        "SELECT bill_id FROM tm.bills_as_of(TIMESTAMPTZ '2023-01-01') ORDER BY bill_id",
    )
    assert public_rows == [("past",)]

    _columns, local_rows = query(
        tmp_path,
        "SELECT bill_id FROM tm.bills_as_observed(TIMESTAMPTZ '2021-01-01')",
    )
    assert local_rows == []

    _columns, index_rows = query(
        tmp_path,
        "SELECT record_id FROM tm.as_of(TIMESTAMPTZ '2026-01-01') ORDER BY record_id",
    )
    assert index_rows == [("late-publication",), ("past",), ("preannounced-future",)]


def test_integrity_finds_duplicate_keys_and_bad_provenance(tmp_path: Path) -> None:
    bad = _bill(
        "duplicate",
        event_at="2020-01-01",
        available_at="2023-01-01",
        observed_at="2022-01-01",
    )
    bad["content_sha256"] = "not-a-hash"
    _empty_catalog(tmp_path, [bad, dict(bad)])
    report = generate_integrity_report(tmp_path, write=False)
    assert report["status"] == "fail"
    assert report["tables"]["bills"]["duplicate_keys"] == 1
    assert report["tables"]["bills"]["missing_or_bad_hashes"] == 2
    assert report["hard_failures"]["temporal_violations"] == 2
    assert report["hard_failures"]["artifact_hash_mismatches"] == 2


def test_query_surface_rejects_mutation(tmp_path: Path) -> None:
    _empty_catalog(tmp_path, [])
    try:
        query(tmp_path, "DELETE FROM tm.bills")
    except ValueError as exc:
        assert "read-only" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("mutation was accepted")


def test_provenance_datetimes_are_utc() -> None:
    row = provenance(
        event_at="2020-01-01",
        available_at="2020-01-02",
        availability_basis="fixture",
        observed_at=datetime(2020, 1, 3, tzinfo=UTC),
        source_artifact_id="artifact:sha256:" + "a" * 64,
        source_family="fixture",
        source_url="https://example.gov",
        content_sha256="a" * 64,
    )
    assert row["event_at"].tzinfo is UTC
    assert row["available_at"].tzinfo is UTC
    assert row["observed_at"].tzinfo is UTC
