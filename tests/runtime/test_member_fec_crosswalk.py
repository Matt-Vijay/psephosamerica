from __future__ import annotations

import datetime as dt
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.runtime.member_fec_crosswalk import (
    MemberFecCrosswalkRow,
    MemberTermCrosswalkRow,
    _create_or_reuse_source_artifact,
    _parse_optional_positive_int,
    _read_crosswalk_rows,
    _update_member_fec_ids,
    _upsert_member_terms,
    load_member_fec_crosswalk_runtime,
)

_MODULE = "src.runtime.member_fec_crosswalk"


def _crosswalk(path: Path) -> Path:
    path.write_text(
        "bioguide_id,fec_candidate_id\nA000001,H4MI00001\nB000002,S4CA00001\n",
        encoding="utf-8",
    )
    return path


def _member_terms(path: Path) -> Path:
    path.write_text(
        "bioguide_id,congress,chamber,state,district,start_date,end_date,is_current\n"
        "A000001,116,house,MI,0,2019-01-03,2021-01-03,false\n"
        "B000002,118,senate,CA,,2023-01-03,,true\n",
        encoding="utf-8",
    )
    return path


def test_load_member_fec_crosswalk_runtime_stages_source_and_updates_rows(tmp_path: Path) -> None:
    path = _crosswalk(tmp_path / "member_fec.csv")
    conn = MagicMock()

    with (
        patch(
            f"{_MODULE}.ensure_data_source",
            return_value={"id": 5, "slug": "member-fec-crosswalk"},
        ) as ensure,
        patch(f"{_MODULE}.start_ingestion_run", return_value=77) as start,
        patch(f"{_MODULE}._create_or_reuse_source_artifact", return_value={"id": 10}) as create,
        patch(f"{_MODULE}._update_member_fec_ids", return_value=(2, [])) as update,
        patch(f"{_MODULE}._upsert_member_terms", return_value=(0, [])) as upsert_terms,
        patch(f"{_MODULE}.finish_ingestion_run") as finish,
    ):
        result = load_member_fec_crosswalk_runtime(conn, path)

    ensure.assert_called_once_with(
        conn,
        slug="member-fec-crosswalk",
        name="Member FEC Candidate Crosswalk",
        source_kind="supporting",
        base_url="https://github.com/unitedstates/congress-legislators",
    )
    assert start.call_args.kwargs["parameters"]["stage"] == "member_fec_crosswalk"
    assert create.call_args.kwargs["artifact_kind"] == "csv"
    assert create.call_args.kwargs["source_url"].startswith("https://github.com/unitedstates")
    rows = update.call_args.args[1]
    assert [(row.bioguide_id, row.fec_candidate_id) for row in rows] == [
        ("A000001", "H4MI00001"),
        ("B000002", "S4CA00001"),
    ]
    assert update.call_args.args[2] == 10
    upsert_terms.assert_called_once_with(conn, [], 10)
    finish.assert_called_once_with(conn, 77, record_count=2)
    assert result.updated_count == 2
    assert result.member_term_upserted_count == 0
    assert result.skipped_bioguide_ids == []


def test_load_member_fec_crosswalk_runtime_can_upsert_member_terms(tmp_path: Path) -> None:
    path = _crosswalk(tmp_path / "member_fec.csv")
    member_terms = _member_terms(tmp_path / "member_terms.csv")
    conn = MagicMock()

    with (
        patch(
            f"{_MODULE}.ensure_data_source",
            return_value={"id": 5, "slug": "member-fec-crosswalk"},
        ),
        patch(f"{_MODULE}.start_ingestion_run", return_value=77) as start,
        patch(f"{_MODULE}._create_or_reuse_source_artifact", return_value={"id": 10}),
        patch(f"{_MODULE}._update_member_fec_ids", return_value=(2, [])),
        patch(f"{_MODULE}._upsert_member_terms", return_value=(2, [])) as upsert_terms,
        patch(f"{_MODULE}.finish_ingestion_run") as finish,
    ):
        result = load_member_fec_crosswalk_runtime(
            conn,
            path,
            member_terms_path=member_terms,
        )

    assert start.call_args.kwargs["parameters"]["member_terms_path"] == str(member_terms)
    term_rows = upsert_terms.call_args.args[1]
    assert [(row.bioguide_id, row.congress, row.chamber) for row in term_rows] == [
        ("A000001", 116, "house"),
        ("B000002", 118, "senate"),
    ]
    assert term_rows[0].district is None
    assert term_rows[1].district is None
    finish.assert_called_once_with(conn, 77, record_count=4)
    assert result.parsed_member_term_count == 2
    assert result.member_term_upserted_count == 2


def test_load_member_fec_crosswalk_runtime_rejects_duplicate_fec_ids(tmp_path: Path) -> None:
    path = tmp_path / "member_fec.csv"
    path.write_text(
        "bioguide_id,fec_candidate_id\nA000001,H4MI00001\nB000002,H4MI00001\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="fec_candidate_id"):
        load_member_fec_crosswalk_runtime(MagicMock(), path)


def test_load_member_fec_crosswalk_runtime_marks_failed_on_update_error(tmp_path: Path) -> None:
    path = _crosswalk(tmp_path / "member_fec.csv")
    conn = MagicMock()

    with (
        patch(
            f"{_MODULE}.ensure_data_source", return_value={"id": 5, "slug": "member-fec-crosswalk"}
        ),
        patch(f"{_MODULE}.start_ingestion_run", return_value=77),
        patch(f"{_MODULE}._create_or_reuse_source_artifact", return_value={"id": 10}),
        patch(f"{_MODULE}._update_member_fec_ids", side_effect=RuntimeError("db failed")),
        patch(f"{_MODULE}.finish_ingestion_run") as finish,
        patch(f"{_MODULE}.fail_ingestion_run") as fail,
    ):
        with pytest.raises(RuntimeError, match="db failed"):
            load_member_fec_crosswalk_runtime(conn, path)

    conn.rollback.assert_called()
    fail.assert_called_once_with(conn, 77, "db failed")
    finish.assert_not_called()


def test_load_member_fec_crosswalk_runtime_rejects_boolean_source_artifact_id(
    tmp_path: Path,
) -> None:
    path = _crosswalk(tmp_path / "member_fec.csv")
    conn = MagicMock()

    with (
        patch(
            f"{_MODULE}.ensure_data_source", return_value={"id": 5, "slug": "member-fec-crosswalk"}
        ),
        patch(f"{_MODULE}.start_ingestion_run", return_value=77),
        patch(f"{_MODULE}._create_or_reuse_source_artifact", return_value={"id": True}),
        patch(f"{_MODULE}._update_member_fec_ids") as update,
        patch(f"{_MODULE}.finish_ingestion_run") as finish,
        patch(f"{_MODULE}.fail_ingestion_run") as fail,
    ):
        with pytest.raises(TypeError, match="source_artifact id"):
            load_member_fec_crosswalk_runtime(conn, path)

    update.assert_not_called()
    conn.rollback.assert_called()
    fail.assert_called_once_with(conn, 77, "expected source_artifact id to be int, got True")
    finish.assert_not_called()


# --- CSV validation / parser error branches (raise before any DB access) ---


def test_load_rejects_missing_crosswalk_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_member_fec_crosswalk_runtime(MagicMock(), tmp_path / "missing.csv")


def test_load_rejects_crosswalk_without_header(tmp_path: Path) -> None:
    path = tmp_path / "x.csv"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="missing a header"):
        load_member_fec_crosswalk_runtime(MagicMock(), path)


def test_load_rejects_crosswalk_missing_required_columns(tmp_path: Path) -> None:
    path = tmp_path / "x.csv"
    path.write_text("foo,bar\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="requires bioguide_id and fec_candidate_id"):
        load_member_fec_crosswalk_runtime(MagicMock(), path)


def test_load_rejects_crosswalk_row_missing_bioguide(tmp_path: Path) -> None:
    path = tmp_path / "x.csv"
    path.write_text("bioguide_id,fec_candidate_id\n,H4MI00001\n", encoding="utf-8")
    with pytest.raises(ValueError, match="row 2 is missing bioguide_id"):
        load_member_fec_crosswalk_runtime(MagicMock(), path)


def test_read_crosswalk_rows_skips_rows_without_fec_candidate_id(tmp_path: Path) -> None:
    path = tmp_path / "x.csv"
    path.write_text(
        "bioguide_id,fec_candidate_id\nA000001,\nB000002,h4mi00001\n",
        encoding="utf-8",
    )
    rows = _read_crosswalk_rows(path)
    assert [(r.bioguide_id, r.fec_candidate_id) for r in rows] == [("B000002", "H4MI00001")]


def test_load_rejects_missing_member_terms_file(tmp_path: Path) -> None:
    crosswalk = _crosswalk(tmp_path / "cw.csv")
    with pytest.raises(FileNotFoundError):
        load_member_fec_crosswalk_runtime(
            MagicMock(), crosswalk, member_terms_path=tmp_path / "nope.csv"
        )


def test_load_rejects_member_terms_missing_columns(tmp_path: Path) -> None:
    crosswalk = _crosswalk(tmp_path / "cw.csv")
    terms = tmp_path / "mt.csv"
    terms.write_text("bioguide_id,congress\nA000001,116\n", encoding="utf-8")
    with pytest.raises(ValueError, match="member terms CSV requires"):
        load_member_fec_crosswalk_runtime(MagicMock(), crosswalk, member_terms_path=terms)


def test_load_rejects_member_terms_row_missing_bioguide(tmp_path: Path) -> None:
    crosswalk = _crosswalk(tmp_path / "cw.csv")
    terms = tmp_path / "mt.csv"
    terms.write_text(
        "bioguide_id,congress,chamber,start_date\n,116,house,2019-01-03\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="member terms row 2 is missing bioguide_id"):
        load_member_fec_crosswalk_runtime(MagicMock(), crosswalk, member_terms_path=terms)


def test_load_rejects_member_terms_invalid_chamber(tmp_path: Path) -> None:
    crosswalk = _crosswalk(tmp_path / "cw.csv")
    terms = tmp_path / "mt.csv"
    terms.write_text(
        "bioguide_id,congress,chamber,start_date\nA000001,116,assembly,2019-01-03\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid chamber"):
        load_member_fec_crosswalk_runtime(MagicMock(), crosswalk, member_terms_path=terms)


def test_load_rejects_member_terms_non_positive_congress(tmp_path: Path) -> None:
    crosswalk = _crosswalk(tmp_path / "cw.csv")
    terms = tmp_path / "mt.csv"
    terms.write_text(
        "bioguide_id,congress,chamber,start_date\nA000001,0,house,2019-01-03\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="congress must be positive"):
        load_member_fec_crosswalk_runtime(MagicMock(), crosswalk, member_terms_path=terms)


def test_load_rejects_member_terms_blank_start_date(tmp_path: Path) -> None:
    crosswalk = _crosswalk(tmp_path / "cw.csv")
    terms = tmp_path / "mt.csv"
    terms.write_text(
        "bioguide_id,congress,chamber,start_date\nA000001,116,house,\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="start_date is required"):
        load_member_fec_crosswalk_runtime(MagicMock(), crosswalk, member_terms_path=terms)


def test_parse_optional_positive_int_handles_blank_zero_and_negative() -> None:
    assert _parse_optional_positive_int("") is None
    assert _parse_optional_positive_int("0") is None
    assert _parse_optional_positive_int("7") == 7
    with pytest.raises(ValueError, match="district must be positive"):
        _parse_optional_positive_int("-1")


# --- DB-write helpers driven by a mock connection ---


def test_update_member_fec_ids_counts_updated_and_skipped() -> None:
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect = [{"id": 1}, None]  # first updates, second matches no member
    rows = [
        MemberFecCrosswalkRow(bioguide_id="A000001", fec_candidate_id="H4MI00001"),
        MemberFecCrosswalkRow(bioguide_id="B000002", fec_candidate_id="S4CA00001"),
    ]
    updated, skipped = _update_member_fec_ids(conn, rows, 10)
    assert updated == 1
    assert skipped == ["B000002"]
    assert cur.execute.call_count == 2
    # Parameters are bound positionally: (fec_candidate_id, bioguide_id).
    assert cur.execute.call_args_list[0].args[1] == ("H4MI00001", "A000001")


def test_upsert_member_terms_counts_upserted_and_skipped() -> None:
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect = [{"id": 5}, None]
    rows = [
        MemberTermCrosswalkRow(
            bioguide_id="A000001",
            congress=116,
            chamber="house",
            state="MI",
            district=1,
            start_date=dt.date(2019, 1, 3),
            end_date=dt.date(2021, 1, 3),
            is_current=False,
        ),
        MemberTermCrosswalkRow(
            bioguide_id="B000002",
            congress=118,
            chamber="senate",
            state="CA",
            district=None,
            start_date=dt.date(2023, 1, 3),
            end_date=None,
            is_current=True,
        ),
    ]
    upserted, skipped = _upsert_member_terms(conn, rows, 10)
    assert upserted == 1
    assert skipped == ["B000002"]
    assert cur.execute.call_count == 2
    # source_record_id is composed deterministically from row identity.
    assert cur.execute.call_args_list[0].args[1][1] == "A000001:116:house:2019-01-03"


def test_create_or_reuse_source_artifact_returns_existing_on_sha_match() -> None:
    conn = MagicMock()
    with patch(f"{_MODULE}.fetch_all", return_value=[{"id": 99, "sha256": "abc"}]) as fetch:
        result = _create_or_reuse_source_artifact(
            conn,
            data_source_id=5,
            artifact_kind="csv",
            storage_uri="/x.csv",
            sha256="abc",
            ingestion_run_id=1,
            source_url=None,
            mime_type="text/csv",
            source_record_id="r",
        )
    assert result == {"id": 99, "sha256": "abc"}
    assert fetch.call_args.args[2] == ("abc",)


def test_create_or_reuse_source_artifact_creates_when_absent() -> None:
    conn = MagicMock()
    with (
        patch(f"{_MODULE}.fetch_all", return_value=[]),
        patch(f"{_MODULE}.create_source_artifact", return_value={"id": 100}) as create,
    ):
        result = _create_or_reuse_source_artifact(
            conn,
            data_source_id=5,
            artifact_kind="csv",
            storage_uri="/x.csv",
            sha256="def",
            ingestion_run_id=1,
            source_url="https://example.test",
            mime_type="text/csv",
            source_record_id="r",
        )
    assert result == {"id": 100}
    # New artifacts must not auto-commit inside the surrounding run transaction.
    assert create.call_args.kwargs["commit"] is False


def test_load_rolls_back_when_finish_ingestion_run_fails(tmp_path: Path) -> None:
    path = _crosswalk(tmp_path / "member_fec.csv")
    conn = MagicMock()

    with (
        patch(
            f"{_MODULE}.ensure_data_source", return_value={"id": 5, "slug": "member-fec-crosswalk"}
        ),
        patch(f"{_MODULE}.start_ingestion_run", return_value=77),
        patch(f"{_MODULE}._create_or_reuse_source_artifact", return_value={"id": 10}),
        patch(f"{_MODULE}._update_member_fec_ids", return_value=(2, [])),
        patch(f"{_MODULE}._upsert_member_terms", return_value=(0, [])),
        patch(f"{_MODULE}.finish_ingestion_run", side_effect=RuntimeError("finish failed")),
        patch(f"{_MODULE}.fail_ingestion_run") as fail,
    ):
        with pytest.raises(RuntimeError, match="finish failed"):
            load_member_fec_crosswalk_runtime(conn, path)

    conn.rollback.assert_called()
    # The work already succeeded, so the run is not separately marked failed here.
    fail.assert_not_called()
