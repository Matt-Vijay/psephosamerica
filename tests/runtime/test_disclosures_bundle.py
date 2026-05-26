"""Tests for src/runtime/disclosures_bundle.py — artifact bundle contract.

No network calls.  No DB.  All I/O is local tmp_path JSON writes.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest

from src.runtime.disclosures_bundle import (
    DisclosureArtifactEntry,
    DisclosuresBundle,
    HouseBundledIndexRow,
    SenateBundledIndexRow,
    disclosures_bundle_from_dict,
    disclosures_bundle_to_dict,
    load_disclosures_bundle,
    write_disclosures_bundle,
)
from src.runtime.sources import HOUSE_DISCLOSURES

_SHA256 = "a" * 64


# ---------------------------------------------------------------------------
# Canonical payload builders
# ---------------------------------------------------------------------------


def _house_entry_dict(
    source_record_id: str = "12345",
    filing_year: int = 2024,
    storage_uri: str = "house/2024/12345.pdf",
) -> dict:
    return {
        "source_record_id": source_record_id,
        "chamber": "house",
        "filing_year": filing_year,
        "storage_uri": storage_uri,
        "source_url": "https://disclosures.house.gov/public_disc/financial-pdfs/2024/12345.pdf",
        "source_slug": "house_disclosures",
        "artifact_kind": "pdf",
        "sha256": _SHA256,
        "index_row": {
            "last_name": "Smith",
            "first_name": "John",
            "suffix": "",
            "raw_filing_type": "O",
            "state_dst": "CA08",
            "filing_date": "2024-01-15",
            "doc_id": "12345",
            "filing_kind": "annual",
        },
    }


def _senate_entry_dict(
    source_record_id: str = "uuid-xyz",
    filing_year: int = 2024,
) -> dict:
    return {
        "source_record_id": source_record_id,
        "chamber": "senate",
        "filing_year": filing_year,
        "storage_uri": "senate/2024/uuid-xyz.pdf",
        "source_url": "https://efdsearch.senate.gov/search/view/paper/uuid-xyz/",
        "source_slug": "senate_disclosures",
        "artifact_kind": "pdf",
        "sha256": _SHA256,
        "index_row": {
            "first_name": "Jane",
            "last_name": "Doe",
            "office": "Senator, TX",
            "report_type": "Annual Report for CY2023",
            "date_filed": "01/15/2024",
            "doc_id": "uuid-xyz",
        },
    }


def _valid_dict(entries: list[dict] | None = None) -> dict:
    if entries is None:
        entries = [_house_entry_dict(), _senate_entry_dict()]
    return {"artifacts": entries}


# ---------------------------------------------------------------------------
# disclosures_bundle_from_dict — return types
# ---------------------------------------------------------------------------


class TestDisclosuresBundleFromDictReturnTypes:
    def test_returns_disclosures_bundle(self) -> None:
        result = disclosures_bundle_from_dict(_valid_dict())
        assert isinstance(result, DisclosuresBundle)

    def test_artifacts_is_tuple(self) -> None:
        result = disclosures_bundle_from_dict(_valid_dict())
        assert isinstance(result.artifacts, tuple)

    def test_artifact_count(self) -> None:
        result = disclosures_bundle_from_dict(_valid_dict())
        assert len(result.artifacts) == 2

    def test_entries_are_disclosure_artifact_entry(self) -> None:
        result = disclosures_bundle_from_dict(_valid_dict())
        assert all(isinstance(e, DisclosureArtifactEntry) for e in result.artifacts)

    def test_empty_artifacts_list(self) -> None:
        result = disclosures_bundle_from_dict({"artifacts": []})
        assert result.artifacts == ()


# ---------------------------------------------------------------------------
# House entry — field values
# ---------------------------------------------------------------------------


class TestHouseEntryFields:
    def setup_method(self) -> None:
        bundle = disclosures_bundle_from_dict(_valid_dict([_house_entry_dict()]))
        self.entry = bundle.artifacts[0]

    def test_source_record_id(self) -> None:
        assert self.entry.source_record_id == "12345"

    def test_chamber(self) -> None:
        assert self.entry.chamber == "house"

    def test_filing_year(self) -> None:
        assert self.entry.filing_year == 2024

    def test_storage_uri(self) -> None:
        assert self.entry.storage_uri == "house/2024/12345.pdf"

    def test_source_slug(self) -> None:
        assert self.entry.source_slug == HOUSE_DISCLOSURES.slug

    def test_legacy_source_slug_is_normalized(self) -> None:
        bundle = disclosures_bundle_from_dict(_valid_dict([_house_entry_dict()]))
        assert bundle.artifacts[0].source_slug == HOUSE_DISCLOSURES.slug

    def test_artifact_kind(self) -> None:
        assert self.entry.artifact_kind == "pdf"

    def test_sha256(self) -> None:
        assert self.entry.sha256 == _SHA256

    def test_index_row_is_house_type(self) -> None:
        assert isinstance(self.entry.index_row, HouseBundledIndexRow)

    def test_index_row_last_name(self) -> None:
        assert self.entry.index_row.last_name == "Smith"

    def test_index_row_state_dst(self) -> None:
        assert self.entry.index_row.state_dst == "CA08"

    def test_index_row_filing_kind(self) -> None:
        assert self.entry.index_row.filing_kind == "annual"

    def test_index_row_doc_id(self) -> None:
        assert self.entry.index_row.doc_id == "12345"

    def test_index_row_suffix_default_empty(self) -> None:
        d = _house_entry_dict()
        del d["index_row"]["suffix"]
        bundle = disclosures_bundle_from_dict({"artifacts": [d]})
        assert bundle.artifacts[0].index_row.suffix == ""


# ---------------------------------------------------------------------------
# Senate entry — field values
# ---------------------------------------------------------------------------


class TestSenateEntryFields:
    def setup_method(self) -> None:
        bundle = disclosures_bundle_from_dict(_valid_dict([_senate_entry_dict()]))
        self.entry = bundle.artifacts[0]

    def test_chamber(self) -> None:
        assert self.entry.chamber == "senate"

    def test_filing_year(self) -> None:
        assert self.entry.filing_year == 2024

    def test_storage_uri(self) -> None:
        assert self.entry.storage_uri == "senate/2024/uuid-xyz.pdf"

    def test_index_row_is_senate_type(self) -> None:
        assert isinstance(self.entry.index_row, SenateBundledIndexRow)

    def test_index_row_office(self) -> None:
        assert self.entry.index_row.office == "Senator, TX"

    def test_index_row_doc_id(self) -> None:
        assert self.entry.index_row.doc_id == "uuid-xyz"

    def test_index_row_last_name(self) -> None:
        assert self.entry.index_row.last_name == "Doe"

    def test_index_row_date_filed(self) -> None:
        assert self.entry.index_row.date_filed == "01/15/2024"


# ---------------------------------------------------------------------------
# Mixed house + senate entries
# ---------------------------------------------------------------------------


class TestMixedEntries:
    def test_two_entries_distinct_types(self) -> None:
        bundle = disclosures_bundle_from_dict(_valid_dict())
        assert isinstance(bundle.artifacts[0].index_row, HouseBundledIndexRow)
        assert isinstance(bundle.artifacts[1].index_row, SenateBundledIndexRow)

    def test_ordering_preserved(self) -> None:
        entries = [
            _house_entry_dict(source_record_id="A"),
            _senate_entry_dict(source_record_id="B"),
            _house_entry_dict(source_record_id="C", storage_uri="house/2024/C.pdf"),
        ]
        bundle = disclosures_bundle_from_dict({"artifacts": entries})
        assert [e.source_record_id for e in bundle.artifacts] == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# load_disclosures_bundle — file I/O
# ---------------------------------------------------------------------------


class TestLoadDisclosuresBundle:
    def test_returns_disclosures_bundle(self, tmp_path: Path) -> None:
        p = tmp_path / "bundle.json"
        p.write_text(json.dumps(_valid_dict()), encoding="utf-8")
        result = load_disclosures_bundle(p)
        assert isinstance(result, DisclosuresBundle)

    def test_artifact_count_from_file(self, tmp_path: Path) -> None:
        p = tmp_path / "bundle.json"
        p.write_text(json.dumps(_valid_dict()), encoding="utf-8")
        result = load_disclosures_bundle(p)
        assert len(result.artifacts) == 2

    def test_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_disclosures_bundle(tmp_path / "missing.json")

    def test_invalid_json(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("not json", encoding="utf-8")
        import json as _json

        with pytest.raises(_json.JSONDecodeError):
            load_disclosures_bundle(p)

    def test_json_array_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "array.json"
        p.write_text("[]", encoding="utf-8")
        with pytest.raises(ValueError, match="JSON object"):
            load_disclosures_bundle(p)


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


class TestDisclosuresBundleSerialization:
    def test_to_dict_roundtrips_through_parser(self) -> None:
        bundle = disclosures_bundle_from_dict(_valid_dict())
        dumped = disclosures_bundle_to_dict(bundle)
        reparsed = disclosures_bundle_from_dict(dumped)
        assert reparsed == bundle

    def test_to_dict_preserves_entry_order(self) -> None:
        bundle = disclosures_bundle_from_dict(
            {
                "artifacts": [
                    _house_entry_dict(source_record_id="A"),
                    _senate_entry_dict(source_record_id="B"),
                    _house_entry_dict(source_record_id="C", storage_uri="house/2024/C.pdf"),
                ]
            }
        )
        dumped = disclosures_bundle_to_dict(bundle)
        assert [entry["source_record_id"] for entry in dumped["artifacts"]] == ["A", "B", "C"]

    def test_write_disclosures_bundle_writes_loadable_json(self, tmp_path: Path) -> None:
        bundle = disclosures_bundle_from_dict(_valid_dict())
        path = tmp_path / "bundle.json"
        written = write_disclosures_bundle(path, bundle)
        assert written == path
        assert load_disclosures_bundle(path) == bundle

    def test_write_disclosures_bundle_uses_unique_temp_file_before_replace(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bundle = disclosures_bundle_from_dict(_valid_dict())
        path = tmp_path / "bundle.json"
        seen_temp_names: list[str] = []
        original_open = Path.open

        def guarded_open(target: Path, *args: object, **kwargs: object):
            mode = str(args[0]) if args else str(kwargs.get("mode", "r"))
            if target == path and any(flag in mode for flag in ("w", "a", "x", "+")):
                raise AssertionError("direct final-path write")
            if target.name == ".bundle.json.tmp":
                raise AssertionError("fixed temp filename used")
            if target.name.startswith(".bundle.json.") and target.name.endswith(".tmp"):
                token = target.name.removeprefix(".bundle.json.").removesuffix(".tmp")
                UUID(token)
                seen_temp_names.append(target.name)
            return original_open(target, *args, **kwargs)

        monkeypatch.setattr(Path, "open", guarded_open)

        written = write_disclosures_bundle(path, bundle)

        assert written == path
        assert len(seen_temp_names) == 1
        assert load_disclosures_bundle(path) == bundle


# ---------------------------------------------------------------------------
# Validation — top-level structure
# ---------------------------------------------------------------------------


class TestTopLevelValidation:
    def test_missing_artifacts_key(self) -> None:
        with pytest.raises(ValueError, match="'artifacts'"):
            disclosures_bundle_from_dict({})

    def test_artifacts_not_list(self) -> None:
        with pytest.raises(ValueError, match="'artifacts' must be a list"):
            disclosures_bundle_from_dict({"artifacts": {}})

    def test_entry_not_dict(self) -> None:
        with pytest.raises(ValueError, match="artifacts\\[0\\] must be an object"):
            disclosures_bundle_from_dict({"artifacts": ["bad"]})


# ---------------------------------------------------------------------------
# Validation — entry-level field errors
# ---------------------------------------------------------------------------


class TestEntryFieldValidation:
    def _mutate(self, key: str, value: object) -> dict:
        d = _valid_dict([_house_entry_dict()])
        d["artifacts"][0][key] = value
        return d

    def _drop(self, key: str) -> dict:
        d = _valid_dict([_house_entry_dict()])
        del d["artifacts"][0][key]
        return d

    def test_missing_source_record_id(self) -> None:
        with pytest.raises(ValueError, match="source_record_id"):
            disclosures_bundle_from_dict(self._drop("source_record_id"))

    def test_missing_chamber(self) -> None:
        with pytest.raises(ValueError, match="chamber"):
            disclosures_bundle_from_dict(self._drop("chamber"))

    def test_invalid_chamber(self) -> None:
        with pytest.raises(ValueError, match="'joint'"):
            disclosures_bundle_from_dict(self._mutate("chamber", "joint"))

    def test_missing_filing_year(self) -> None:
        with pytest.raises(ValueError, match="filing_year"):
            disclosures_bundle_from_dict(self._drop("filing_year"))

    def test_filing_year_float_rejected(self) -> None:
        with pytest.raises(ValueError, match="integer"):
            disclosures_bundle_from_dict(self._mutate("filing_year", 2024.0))

    def test_filing_year_bool_rejected(self) -> None:
        with pytest.raises(ValueError, match="integer"):
            disclosures_bundle_from_dict(self._mutate("filing_year", True))

    def test_missing_storage_uri(self) -> None:
        with pytest.raises(ValueError, match="storage_uri"):
            disclosures_bundle_from_dict(self._drop("storage_uri"))

    def test_absolute_storage_uri_rejected(self) -> None:
        with pytest.raises(ValueError, match="storage_uri"):
            disclosures_bundle_from_dict(self._mutate("storage_uri", "/tmp/outside.pdf"))

    def test_path_traversal_storage_uri_rejected(self) -> None:
        with pytest.raises(ValueError, match="storage_uri"):
            disclosures_bundle_from_dict(self._mutate("storage_uri", "../outside.pdf"))

    def test_missing_source_url(self) -> None:
        with pytest.raises(ValueError, match="source_url"):
            disclosures_bundle_from_dict(self._drop("source_url"))

    @pytest.mark.parametrize(
        "source_url",
        [
            "http://disclosures.house.gov/public_disc/ptr-pdfs/2024/12345.pdf",
            "https://evil.example/public_disc/ptr-pdfs/2024/12345.pdf",
            "https://disclosures.house.gov.evil.example/public_disc/ptr-pdfs/2024/12345.pdf",
            "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/../12345.pdf",
            "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/12345.pdf?download=1",
            "https://disclosures.house.gov/search/view/paper/12345/",
            "https://efdsearch.senate.gov/search/view/paper/12345/",
        ],
    )
    def test_house_source_url_must_be_official_house_artifact_url(
        self,
        source_url: str,
    ) -> None:
        with pytest.raises(ValueError, match=r"artifacts\[0\]\.source_url"):
            disclosures_bundle_from_dict(self._mutate("source_url", source_url))

    @pytest.mark.parametrize(
        "source_url",
        [
            "http://efdsearch.senate.gov/search/view/paper/uuid-xyz/",
            "https://evil.example/search/view/paper/uuid-xyz/",
            "https://efdsearch.senate.gov.evil.example/search/view/paper/uuid-xyz/",
            "https://efdsearch.senate.gov/search/view/paper/../uuid-xyz/",
            "https://efdsearch.senate.gov/search/view/paper/uuid-xyz/?download=1",
            "https://efdsearch.senate.gov/public_disc/ptr-pdfs/2024/uuid-xyz.pdf",
            "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/12345.pdf",
        ],
    )
    def test_senate_source_url_must_be_official_senate_artifact_url(
        self,
        source_url: str,
    ) -> None:
        d = _valid_dict([_senate_entry_dict()])
        d["artifacts"][0]["source_url"] = source_url
        with pytest.raises(ValueError, match=r"artifacts\[0\]\.source_url"):
            disclosures_bundle_from_dict(d)

    def test_missing_source_slug(self) -> None:
        with pytest.raises(ValueError, match="source_slug"):
            disclosures_bundle_from_dict(self._drop("source_slug"))

    def test_invalid_artifact_kind(self) -> None:
        with pytest.raises(ValueError, match="artifact_kind"):
            disclosures_bundle_from_dict(self._mutate("artifact_kind", "docx"))

    def test_missing_sha256(self) -> None:
        with pytest.raises(ValueError, match="sha256"):
            disclosures_bundle_from_dict(self._drop("sha256"))

    def test_sha256_wrong_length(self) -> None:
        with pytest.raises(ValueError, match="64-char"):
            disclosures_bundle_from_dict(self._mutate("sha256", "abc"))

    def test_sha256_must_be_hex(self) -> None:
        with pytest.raises(ValueError, match="64-char hex"):
            disclosures_bundle_from_dict(self._mutate("sha256", "z" * 64))

    def test_missing_index_row(self) -> None:
        with pytest.raises(ValueError, match="index_row"):
            disclosures_bundle_from_dict(self._drop("index_row"))

    def test_index_row_not_dict(self) -> None:
        with pytest.raises(ValueError, match="index_row.*object"):
            disclosures_bundle_from_dict(self._mutate("index_row", "bad"))


# ---------------------------------------------------------------------------
# Validation — house index_row field errors
# ---------------------------------------------------------------------------


class TestHouseIndexRowValidation:
    def _drop_index_field(self, field: str) -> dict:
        d = _valid_dict([_house_entry_dict()])
        del d["artifacts"][0]["index_row"][field]
        return d

    def test_missing_last_name(self) -> None:
        with pytest.raises(ValueError, match="last_name"):
            disclosures_bundle_from_dict(self._drop_index_field("last_name"))

    def test_missing_first_name(self) -> None:
        with pytest.raises(ValueError, match="first_name"):
            disclosures_bundle_from_dict(self._drop_index_field("first_name"))

    def test_missing_raw_filing_type(self) -> None:
        with pytest.raises(ValueError, match="raw_filing_type"):
            disclosures_bundle_from_dict(self._drop_index_field("raw_filing_type"))

    def test_missing_state_dst(self) -> None:
        with pytest.raises(ValueError, match="state_dst"):
            disclosures_bundle_from_dict(self._drop_index_field("state_dst"))

    def test_missing_filing_date(self) -> None:
        with pytest.raises(ValueError, match="filing_date"):
            disclosures_bundle_from_dict(self._drop_index_field("filing_date"))

    def test_missing_doc_id(self) -> None:
        with pytest.raises(ValueError, match="doc_id"):
            disclosures_bundle_from_dict(self._drop_index_field("doc_id"))

    def test_missing_filing_kind(self) -> None:
        with pytest.raises(ValueError, match="filing_kind"):
            disclosures_bundle_from_dict(self._drop_index_field("filing_kind"))

    def test_extra_keys_ignored(self) -> None:
        d = _valid_dict([_house_entry_dict()])
        d["artifacts"][0]["index_row"]["unknown_key"] = "extra"
        bundle = disclosures_bundle_from_dict(d)
        assert isinstance(bundle.artifacts[0].index_row, HouseBundledIndexRow)


# ---------------------------------------------------------------------------
# Validation — senate index_row field errors
# ---------------------------------------------------------------------------


class TestSenateIndexRowValidation:
    def _drop_index_field(self, field: str) -> dict:
        d = _valid_dict([_senate_entry_dict()])
        del d["artifacts"][0]["index_row"][field]
        return d

    def test_missing_first_name(self) -> None:
        with pytest.raises(ValueError, match="first_name"):
            disclosures_bundle_from_dict(self._drop_index_field("first_name"))

    def test_missing_last_name(self) -> None:
        with pytest.raises(ValueError, match="last_name"):
            disclosures_bundle_from_dict(self._drop_index_field("last_name"))

    def test_missing_office(self) -> None:
        with pytest.raises(ValueError, match="office"):
            disclosures_bundle_from_dict(self._drop_index_field("office"))

    def test_missing_report_type(self) -> None:
        with pytest.raises(ValueError, match="report_type"):
            disclosures_bundle_from_dict(self._drop_index_field("report_type"))

    def test_missing_date_filed(self) -> None:
        with pytest.raises(ValueError, match="date_filed"):
            disclosures_bundle_from_dict(self._drop_index_field("date_filed"))

    def test_missing_doc_id(self) -> None:
        with pytest.raises(ValueError, match="doc_id"):
            disclosures_bundle_from_dict(self._drop_index_field("doc_id"))

    def test_extra_keys_ignored(self) -> None:
        d = _valid_dict([_senate_entry_dict()])
        d["artifacts"][0]["index_row"]["bogus"] = 999
        bundle = disclosures_bundle_from_dict(d)
        assert isinstance(bundle.artifacts[0].index_row, SenateBundledIndexRow)


# ---------------------------------------------------------------------------
# Frozen / immutability checks
# ---------------------------------------------------------------------------


class TestFrozenContracts:
    def test_bundle_is_frozen(self) -> None:
        bundle = disclosures_bundle_from_dict(_valid_dict())
        with pytest.raises(Exception):
            bundle.artifacts = ()  # type: ignore[misc]

    def test_entry_is_frozen(self) -> None:
        bundle = disclosures_bundle_from_dict(_valid_dict())
        entry = bundle.artifacts[0]
        with pytest.raises(Exception):
            entry.chamber = "senate"  # type: ignore[misc]

    def test_house_index_row_is_frozen(self) -> None:
        bundle = disclosures_bundle_from_dict(_valid_dict([_house_entry_dict()]))
        row = bundle.artifacts[0].index_row
        with pytest.raises(Exception):
            row.last_name = "changed"  # type: ignore[misc]

    def test_senate_index_row_is_frozen(self) -> None:
        bundle = disclosures_bundle_from_dict(_valid_dict([_senate_entry_dict()]))
        row = bundle.artifacts[0].index_row
        with pytest.raises(Exception):
            row.office = "changed"  # type: ignore[misc]
