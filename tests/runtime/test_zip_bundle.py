"""Tests for src/runtime/zip_bundle.py.

No network calls; no live I/O beyond tmp_path filesystem writes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.pipeline.publish_snapshot_run import ZipBundleInputs
from src.runtime.zip_bundle import load_zip_bundle, zip_bundle_from_dict
from src.zip.resolve import DistrictMemberRow, SenatorRow, ZipDistrictRow


# ---------------------------------------------------------------------------
# Canonical valid payload
# ---------------------------------------------------------------------------


def _valid_dict() -> dict:
    return {
        "zip5_codes": ["90210", "10001"],
        "zip_district_rows": [
            {"zip5": "90210", "state": "CA", "district": 30, "population_share": 1.0},
            {"zip5": "10001", "state": "NY", "district": 12, "population_share": 0.8},
            {"zip5": "10001", "state": "NY", "district": 13, "population_share": 0.2},
        ],
        "district_member_rows": [
            {
                "state": "CA",
                "district": 30,
                "bioguide_id": "B001",
                "full_name": "Alice Smith",
                "party": "D",
                "slug": "alice-smith",
            },
            {
                "state": "NY",
                "district": 12,
                "bioguide_id": "B002",
                "full_name": "Bob Jones",
                "party": "R",
                "slug": "bob-jones",
            },
        ],
        "senator_rows": [
            {
                "state": "CA",
                "bioguide_id": "S001",
                "full_name": "Sen One",
                "party": "D",
                "slug": "sen-one",
                "seat": 1,
            },
            {
                "state": "CA",
                "bioguide_id": "S002",
                "full_name": "Sen Two",
                "party": "D",
                "slug": "sen-two",
                "seat": 2,
            },
            {
                "state": "NY",
                "bioguide_id": "S003",
                "full_name": "Sen Three",
                "party": "R",
                "slug": "sen-three",
                "seat": 1,
            },
        ],
    }


# ---------------------------------------------------------------------------
# zip_bundle_from_dict — happy path
# ---------------------------------------------------------------------------


class TestZipBundleFromDictHappyPath:
    def test_returns_zip_bundle_inputs(self) -> None:
        result = zip_bundle_from_dict(_valid_dict())
        assert isinstance(result, ZipBundleInputs)

    def test_zip5_codes_preserved(self) -> None:
        result = zip_bundle_from_dict(_valid_dict())
        assert result.zip5_codes == ["90210", "10001"]

    def test_zip_district_rows_count(self) -> None:
        result = zip_bundle_from_dict(_valid_dict())
        assert len(result.zip_district_rows) == 3

    def test_zip_district_row_type(self) -> None:
        result = zip_bundle_from_dict(_valid_dict())
        assert all(isinstance(r, ZipDistrictRow) for r in result.zip_district_rows)

    def test_zip_district_row_fields(self) -> None:
        result = zip_bundle_from_dict(_valid_dict())
        row = result.zip_district_rows[0]
        assert row.zip5 == "90210"
        assert row.state == "CA"
        assert row.district == 30
        assert row.population_share == 1.0

    def test_district_member_rows_count(self) -> None:
        result = zip_bundle_from_dict(_valid_dict())
        assert len(result.district_member_rows) == 2

    def test_district_member_row_type(self) -> None:
        result = zip_bundle_from_dict(_valid_dict())
        assert all(isinstance(r, DistrictMemberRow) for r in result.district_member_rows)

    def test_district_member_row_fields(self) -> None:
        result = zip_bundle_from_dict(_valid_dict())
        row = result.district_member_rows[0]
        assert row.state == "CA"
        assert row.district == 30
        assert row.bioguide_id == "B001"
        assert row.full_name == "Alice Smith"
        assert row.party == "D"
        assert row.slug == "alice-smith"

    def test_senator_rows_count(self) -> None:
        result = zip_bundle_from_dict(_valid_dict())
        assert len(result.senator_rows) == 3

    def test_senator_row_type(self) -> None:
        result = zip_bundle_from_dict(_valid_dict())
        assert all(isinstance(r, SenatorRow) for r in result.senator_rows)

    def test_senator_row_fields(self) -> None:
        result = zip_bundle_from_dict(_valid_dict())
        row = result.senator_rows[0]
        assert row.state == "CA"
        assert row.bioguide_id == "S001"
        assert row.full_name == "Sen One"
        assert row.party == "D"
        assert row.slug == "sen-one"
        assert row.seat == 1

    def test_extra_keys_ignored(self) -> None:
        data = _valid_dict()
        data["unexpected_key"] = "should not raise"
        result = zip_bundle_from_dict(data)
        assert isinstance(result, ZipBundleInputs)

    def test_integer_population_share_coerced_to_float(self) -> None:
        data = _valid_dict()
        data["zip_district_rows"][0]["population_share"] = 1  # int, not float
        result = zip_bundle_from_dict(data)
        assert isinstance(result.zip_district_rows[0].population_share, float)

    def test_empty_lists_are_valid(self) -> None:
        result = zip_bundle_from_dict(
            {
                "zip5_codes": [],
                "zip_district_rows": [],
                "district_member_rows": [],
                "senator_rows": [],
            }
        )
        assert result.zip5_codes == []
        assert result.zip_district_rows == []
        assert result.district_member_rows == []
        assert result.senator_rows == []

    def test_result_is_frozen(self) -> None:
        result = zip_bundle_from_dict(_valid_dict())
        with pytest.raises((AttributeError, TypeError)):
            result.zip5_codes = []  # type: ignore[misc]


# ---------------------------------------------------------------------------
# zip_bundle_from_dict — missing top-level keys
# ---------------------------------------------------------------------------


class TestZipBundleFromDictMissingKeys:
    @pytest.mark.parametrize(
        "missing_key",
        [
            "zip5_codes",
            "zip_district_rows",
            "district_member_rows",
            "senator_rows",
        ],
    )
    def test_missing_top_level_key_raises(self, missing_key: str) -> None:
        data = _valid_dict()
        del data[missing_key]
        with pytest.raises(ValueError, match=missing_key):
            zip_bundle_from_dict(data)

    def test_non_dict_input_raises(self) -> None:
        with pytest.raises((ValueError, AttributeError)):
            zip_bundle_from_dict([])  # type: ignore[arg-type]

    def test_zip5_codes_not_list_raises(self) -> None:
        data = _valid_dict()
        data["zip5_codes"] = "90210"
        with pytest.raises(ValueError):
            zip_bundle_from_dict(data)

    def test_zip5_code_not_string_raises(self) -> None:
        data = _valid_dict()
        data["zip5_codes"] = [90210]
        with pytest.raises(ValueError, match="zip5_codes"):
            zip_bundle_from_dict(data)


# ---------------------------------------------------------------------------
# zip_bundle_from_dict — malformed row fields
# ---------------------------------------------------------------------------


class TestZipBundleFromDictBadRows:
    def test_zip_district_row_missing_zip5_raises(self) -> None:
        data = _valid_dict()
        del data["zip_district_rows"][0]["zip5"]
        with pytest.raises(ValueError, match="zip5"):
            zip_bundle_from_dict(data)

    def test_zip_district_row_non_int_district_raises(self) -> None:
        data = _valid_dict()
        data["zip_district_rows"][0]["district"] = "30"
        with pytest.raises(ValueError, match="district"):
            zip_bundle_from_dict(data)

    def test_zip_district_row_bool_district_raises(self) -> None:
        # bool is a subclass of int in Python; must be rejected explicitly
        data = _valid_dict()
        data["zip_district_rows"][0]["district"] = True
        with pytest.raises(ValueError, match="district"):
            zip_bundle_from_dict(data)

    def test_zip_district_row_non_number_share_raises(self) -> None:
        data = _valid_dict()
        data["zip_district_rows"][0]["population_share"] = "1.0"
        with pytest.raises(ValueError, match="population_share"):
            zip_bundle_from_dict(data)

    def test_zip_district_row_bool_share_raises(self) -> None:
        data = _valid_dict()
        data["zip_district_rows"][0]["population_share"] = True
        with pytest.raises(ValueError, match="population_share"):
            zip_bundle_from_dict(data)

    def test_zip_district_row_not_dict_raises(self) -> None:
        data = _valid_dict()
        data["zip_district_rows"][0] = "not a dict"
        with pytest.raises(ValueError, match="zip_district_rows"):
            zip_bundle_from_dict(data)

    def test_district_member_row_missing_bioguide_raises(self) -> None:
        data = _valid_dict()
        del data["district_member_rows"][0]["bioguide_id"]
        with pytest.raises(ValueError, match="bioguide_id"):
            zip_bundle_from_dict(data)

    def test_district_member_row_non_int_district_raises(self) -> None:
        data = _valid_dict()
        data["district_member_rows"][0]["district"] = 30.0
        with pytest.raises(ValueError, match="district"):
            zip_bundle_from_dict(data)

    def test_senator_row_missing_seat_raises(self) -> None:
        data = _valid_dict()
        del data["senator_rows"][0]["seat"]
        with pytest.raises(ValueError, match="seat"):
            zip_bundle_from_dict(data)

    def test_senator_row_non_int_seat_raises(self) -> None:
        data = _valid_dict()
        data["senator_rows"][0]["seat"] = "1"
        with pytest.raises(ValueError, match="seat"):
            zip_bundle_from_dict(data)

    def test_senator_row_missing_state_raises(self) -> None:
        data = _valid_dict()
        del data["senator_rows"][0]["state"]
        with pytest.raises(ValueError, match="state"):
            zip_bundle_from_dict(data)

    def test_senator_row_not_dict_raises(self) -> None:
        data = _valid_dict()
        data["senator_rows"][0] = 42
        with pytest.raises(ValueError, match="senator_rows"):
            zip_bundle_from_dict(data)


# ---------------------------------------------------------------------------
# load_zip_bundle — filesystem round-trip
# ---------------------------------------------------------------------------


class TestLoadZipBundle:
    def test_round_trip(self, tmp_path: Path) -> None:
        p = tmp_path / "bundle.json"
        p.write_text(json.dumps(_valid_dict()), encoding="utf-8")
        result = load_zip_bundle(p)
        assert isinstance(result, ZipBundleInputs)
        assert result.zip5_codes == ["90210", "10001"]

    def test_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_zip_bundle(tmp_path / "nonexistent.json")

    def test_invalid_json_raises_json_decode_error(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("{not valid json}", encoding="utf-8")
        with pytest.raises(Exception):  # json.JSONDecodeError is a subclass of ValueError
            load_zip_bundle(p)

    def test_json_array_at_root_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "array.json"
        p.write_text(json.dumps([]), encoding="utf-8")
        with pytest.raises(ValueError):
            load_zip_bundle(p)

    def test_missing_key_in_file_raises(self, tmp_path: Path) -> None:
        data = _valid_dict()
        del data["senator_rows"]
        p = tmp_path / "partial.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError, match="senator_rows"):
            load_zip_bundle(p)

    def test_empty_bundle_file(self, tmp_path: Path) -> None:
        data = {
            "zip5_codes": [],
            "zip_district_rows": [],
            "district_member_rows": [],
            "senator_rows": [],
        }
        p = tmp_path / "empty.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        result = load_zip_bundle(p)
        assert result.zip5_codes == []

    def test_unicode_names_preserved(self, tmp_path: Path) -> None:
        data = _valid_dict()
        data["district_member_rows"][0]["full_name"] = "Ángel García"
        p = tmp_path / "unicode.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = load_zip_bundle(p)
        assert result.district_member_rows[0].full_name == "Ángel García"
