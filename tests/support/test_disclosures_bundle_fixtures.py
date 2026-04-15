"""Tests for tests/support/disclosures_bundle_fixtures.py.

All assertions use real temp dirs via pytest's tmp_path fixture.
No network calls; no binary fixture blobs.
"""

from __future__ import annotations

import hashlib
import json

from tests.support.disclosures_bundle_fixtures import (
    BundleArtifactSpec,
    DisclosureBundleFixture,
    _placeholder_bytes,
    build_bundle_fixture,
    make_house_spec,
    make_senate_spec,
)


# ---------------------------------------------------------------------------
# _placeholder_bytes
# ---------------------------------------------------------------------------


class TestPlaceholderBytes:
    def test_returns_bytes(self):
        data = _placeholder_bytes("DOC001")
        assert isinstance(data, bytes)

    def test_contains_record_id(self):
        data = _placeholder_bytes("DOC001")
        assert b"DOC001" in data

    def test_different_ids_produce_different_bytes(self):
        assert _placeholder_bytes("DOC001") != _placeholder_bytes("DOC002")

    def test_deterministic(self):
        assert _placeholder_bytes("DOC001") == _placeholder_bytes("DOC001")

    def test_ascii_only(self):
        data = _placeholder_bytes("DOC999")
        data.decode("ascii")  # must not raise


# ---------------------------------------------------------------------------
# make_house_spec
# ---------------------------------------------------------------------------


class TestMakeHouseSpec:
    def test_returns_bundle_artifact_spec(self):
        spec = make_house_spec("12345")
        assert isinstance(spec, BundleArtifactSpec)

    def test_chamber_is_house(self):
        spec = make_house_spec("12345")
        assert spec.chamber == "house"

    def test_source_record_id_preserved(self):
        spec = make_house_spec("DOC123")
        assert spec.source_record_id == "DOC123"

    def test_storage_uri_contains_doc_id(self):
        spec = make_house_spec("DOC123", filing_year=2024)
        assert "DOC123" in spec.storage_uri
        assert "house" in spec.storage_uri
        assert "2024" in spec.storage_uri

    def test_index_row_has_required_house_fields(self):
        spec = make_house_spec("DOC123")
        row = spec.index_row
        for key in ("last_name", "first_name", "suffix", "raw_filing_type",
                    "state_dst", "filing_date", "doc_id", "filing_kind"):
            assert key in row, f"missing field: {key}"

    def test_index_row_doc_id_matches_spec(self):
        spec = make_house_spec("DOC123")
        assert spec.index_row["doc_id"] == "DOC123"

    def test_custom_overrides_applied(self):
        spec = make_house_spec(
            "DOC999",
            filing_year=2023,
            last_name="Jones",
            state_dst="NY10",
        )
        assert spec.filing_year == 2023
        assert spec.index_row["last_name"] == "Jones"
        assert spec.index_row["state_dst"] == "NY10"

    def test_artifact_kind_is_pdf(self):
        spec = make_house_spec("X")
        assert spec.artifact_kind == "pdf"


# ---------------------------------------------------------------------------
# make_senate_spec
# ---------------------------------------------------------------------------


class TestMakeSenateSpec:
    def test_returns_bundle_artifact_spec(self):
        spec = make_senate_spec("uuid-abc")
        assert isinstance(spec, BundleArtifactSpec)

    def test_chamber_is_senate(self):
        spec = make_senate_spec("uuid-abc")
        assert spec.chamber == "senate"

    def test_source_record_id_preserved(self):
        spec = make_senate_spec("uuid-xyz")
        assert spec.source_record_id == "uuid-xyz"

    def test_storage_uri_contains_doc_id(self):
        spec = make_senate_spec("uuid-xyz", filing_year=2024)
        assert "uuid-xyz" in spec.storage_uri
        assert "senate" in spec.storage_uri
        assert "2024" in spec.storage_uri

    def test_index_row_has_required_senate_fields(self):
        spec = make_senate_spec("uuid-xyz")
        row = spec.index_row
        for key in ("first_name", "last_name", "office", "report_type",
                    "date_filed", "doc_id"):
            assert key in row, f"missing field: {key}"

    def test_index_row_doc_id_matches_spec(self):
        spec = make_senate_spec("uuid-xyz")
        assert spec.index_row["doc_id"] == "uuid-xyz"

    def test_custom_overrides_applied(self):
        spec = make_senate_spec(
            "uuid-001",
            last_name="Williams",
            office="Senator, CA",
        )
        assert spec.index_row["last_name"] == "Williams"
        assert spec.index_row["office"] == "Senator, CA"

    def test_artifact_kind_is_pdf(self):
        spec = make_senate_spec("X")
        assert spec.artifact_kind == "pdf"


# ---------------------------------------------------------------------------
# build_bundle_fixture — empty bundle
# ---------------------------------------------------------------------------


class TestBuildBundleFixtureEmpty:
    def test_returns_fixture(self, tmp_path):
        result = build_bundle_fixture(tmp_path, [])
        assert isinstance(result, DisclosureBundleFixture)

    def test_bundle_json_written(self, tmp_path):
        result = build_bundle_fixture(tmp_path, [])
        assert result.bundle_json_path.exists()

    def test_bundle_json_is_valid_json(self, tmp_path):
        result = build_bundle_fixture(tmp_path, [])
        data = json.loads(result.bundle_json_path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_bundle_json_artifacts_key_empty_list(self, tmp_path):
        result = build_bundle_fixture(tmp_path, [])
        data = json.loads(result.bundle_json_path.read_text(encoding="utf-8"))
        assert data["artifacts"] == []

    def test_artifact_paths_empty(self, tmp_path):
        result = build_bundle_fixture(tmp_path, [])
        assert result.artifact_paths == {}

    def test_index_rows_empty(self, tmp_path):
        result = build_bundle_fixture(tmp_path, [])
        assert result.index_rows == []

    def test_local_root_is_tmp_path(self, tmp_path):
        result = build_bundle_fixture(tmp_path, [])
        assert result.local_root == tmp_path


# ---------------------------------------------------------------------------
# build_bundle_fixture — single house artifact
# ---------------------------------------------------------------------------


class TestBuildBundleFixtureSingleHouse:
    def _spec(self) -> BundleArtifactSpec:
        return make_house_spec("DOC100", filing_year=2024)

    def test_artifact_file_created(self, tmp_path):
        spec = self._spec()
        build_bundle_fixture(tmp_path, [spec])
        assert (tmp_path / spec.storage_uri).exists()

    def test_artifact_path_in_result(self, tmp_path):
        spec = self._spec()
        result = build_bundle_fixture(tmp_path, [spec])
        assert "DOC100" in result.artifact_paths
        assert result.artifact_paths["DOC100"].exists()

    def test_artifact_bytes_are_placeholder(self, tmp_path):
        spec = self._spec()
        result = build_bundle_fixture(tmp_path, [spec])
        data = result.artifact_paths["DOC100"].read_bytes()
        assert b"DOC100" in data

    def test_bundle_json_has_one_artifact(self, tmp_path):
        spec = self._spec()
        result = build_bundle_fixture(tmp_path, [spec])
        data = json.loads(result.bundle_json_path.read_text(encoding="utf-8"))
        assert len(data["artifacts"]) == 1

    def test_bundle_json_sha256_matches_file(self, tmp_path):
        spec = self._spec()
        result = build_bundle_fixture(tmp_path, [spec])
        file_bytes = result.artifact_paths["DOC100"].read_bytes()
        expected_sha256 = hashlib.sha256(file_bytes).hexdigest()
        data = json.loads(result.bundle_json_path.read_text(encoding="utf-8"))
        assert data["artifacts"][0]["sha256"] == expected_sha256

    def test_bundle_json_chamber_is_house(self, tmp_path):
        spec = self._spec()
        result = build_bundle_fixture(tmp_path, [spec])
        data = json.loads(result.bundle_json_path.read_text(encoding="utf-8"))
        assert data["artifacts"][0]["chamber"] == "house"

    def test_index_rows_contains_one_entry(self, tmp_path):
        spec = self._spec()
        result = build_bundle_fixture(tmp_path, [spec])
        assert len(result.index_rows) == 1
        assert result.index_rows[0]["doc_id"] == "DOC100"

    def test_bundle_json_loadable_via_runtime(self, tmp_path):
        """Bundle JSON produced by the fixture is accepted by load_disclosures_bundle."""
        from src.runtime.disclosures_bundle import load_disclosures_bundle

        spec = self._spec()
        result = build_bundle_fixture(tmp_path, [spec])
        bundle = load_disclosures_bundle(result.bundle_json_path)
        assert len(bundle.artifacts) == 1
        assert bundle.artifacts[0].source_record_id == "DOC100"
        assert bundle.artifacts[0].chamber == "house"


# ---------------------------------------------------------------------------
# build_bundle_fixture — single senate artifact
# ---------------------------------------------------------------------------


class TestBuildBundleFixtureSingleSenate:
    def _spec(self) -> BundleArtifactSpec:
        return make_senate_spec("uuid-sen1", filing_year=2023)

    def test_artifact_file_created(self, tmp_path):
        spec = self._spec()
        build_bundle_fixture(tmp_path, [spec])
        assert (tmp_path / spec.storage_uri).exists()

    def test_bundle_json_chamber_is_senate(self, tmp_path):
        spec = self._spec()
        result = build_bundle_fixture(tmp_path, [spec])
        data = json.loads(result.bundle_json_path.read_text(encoding="utf-8"))
        assert data["artifacts"][0]["chamber"] == "senate"

    def test_sha256_valid_for_senate(self, tmp_path):
        spec = self._spec()
        result = build_bundle_fixture(tmp_path, [spec])
        file_bytes = result.artifact_paths["uuid-sen1"].read_bytes()
        expected = hashlib.sha256(file_bytes).hexdigest()
        data = json.loads(result.bundle_json_path.read_text(encoding="utf-8"))
        assert data["artifacts"][0]["sha256"] == expected

    def test_bundle_json_loadable_via_runtime(self, tmp_path):
        from src.runtime.disclosures_bundle import load_disclosures_bundle

        spec = self._spec()
        result = build_bundle_fixture(tmp_path, [spec])
        bundle = load_disclosures_bundle(result.bundle_json_path)
        assert len(bundle.artifacts) == 1
        assert bundle.artifacts[0].chamber == "senate"


# ---------------------------------------------------------------------------
# build_bundle_fixture — mixed multi-artifact bundle
# ---------------------------------------------------------------------------


class TestBuildBundleFixtureMixed:
    def _specs(self) -> list[BundleArtifactSpec]:
        return [
            make_house_spec("HOUSE001", filing_year=2024),
            make_senate_spec("SEN001", filing_year=2024),
            make_house_spec("HOUSE002", filing_year=2023, state_dst="NY05"),
        ]

    def test_all_artifact_files_created(self, tmp_path):
        specs = self._specs()
        build_bundle_fixture(tmp_path, specs)
        for spec in specs:
            assert (tmp_path / spec.storage_uri).exists()

    def test_artifact_paths_keyed_by_record_id(self, tmp_path):
        result = build_bundle_fixture(tmp_path, self._specs())
        assert set(result.artifact_paths.keys()) == {"HOUSE001", "SEN001", "HOUSE002"}

    def test_bundle_json_has_three_artifacts(self, tmp_path):
        result = build_bundle_fixture(tmp_path, self._specs())
        data = json.loads(result.bundle_json_path.read_text(encoding="utf-8"))
        assert len(data["artifacts"]) == 3

    def test_index_rows_length_matches_specs(self, tmp_path):
        result = build_bundle_fixture(tmp_path, self._specs())
        assert len(result.index_rows) == 3

    def test_index_rows_order_preserved(self, tmp_path):
        result = build_bundle_fixture(tmp_path, self._specs())
        assert result.index_rows[0]["doc_id"] == "HOUSE001"
        assert result.index_rows[1]["doc_id"] == "SEN001"
        assert result.index_rows[2]["doc_id"] == "HOUSE002"

    def test_all_sha256s_valid(self, tmp_path):
        result = build_bundle_fixture(tmp_path, self._specs())
        data = json.loads(result.bundle_json_path.read_text(encoding="utf-8"))
        for entry in data["artifacts"]:
            file_path = tmp_path / entry["storage_uri"]
            expected = hashlib.sha256(file_path.read_bytes()).hexdigest()
            assert entry["sha256"] == expected

    def test_loadable_via_runtime_returns_all_entries(self, tmp_path):
        from src.runtime.disclosures_bundle import load_disclosures_bundle

        result = build_bundle_fixture(tmp_path, self._specs())
        bundle = load_disclosures_bundle(result.bundle_json_path)
        assert len(bundle.artifacts) == 3

    def test_runtime_files_module_reads_bytes(self, tmp_path):
        """entry_local_path and read_entry_bytes work with the fixture."""
        from src.runtime.disclosures_bundle import load_disclosures_bundle
        from src.runtime.disclosures_bundle_files import read_entry_bytes

        result = build_bundle_fixture(tmp_path, self._specs())
        bundle = load_disclosures_bundle(result.bundle_json_path)
        for entry in bundle.artifacts:
            data = read_entry_bytes(entry, result.local_root)
            assert entry.source_record_id.encode() in data

    def test_runtime_sha256_verification_passes(self, tmp_path):
        """verify_entry_sha256 does not raise for fixture-generated artifacts."""
        from src.runtime.disclosures_bundle import load_disclosures_bundle
        from src.runtime.disclosures_bundle_files import verify_entry_sha256

        result = build_bundle_fixture(tmp_path, self._specs())
        bundle = load_disclosures_bundle(result.bundle_json_path)
        for entry in bundle.artifacts:
            verify_entry_sha256(entry, result.local_root)  # must not raise


# ---------------------------------------------------------------------------
# build_bundle_fixture — isolation between calls
# ---------------------------------------------------------------------------


class TestBuildBundleFixtureIsolation:
    def test_separate_tmp_paths_do_not_share_state(self, tmp_path):
        sub_a = tmp_path / "a"
        sub_b = tmp_path / "b"
        sub_a.mkdir()
        sub_b.mkdir()

        spec_a = make_house_spec("A001")
        spec_b = make_house_spec("B001")

        result_a = build_bundle_fixture(sub_a, [spec_a])
        result_b = build_bundle_fixture(sub_b, [spec_b])

        assert "A001" in result_a.artifact_paths
        assert "B001" not in result_a.artifact_paths
        assert "B001" in result_b.artifact_paths
        assert "A001" not in result_b.artifact_paths
