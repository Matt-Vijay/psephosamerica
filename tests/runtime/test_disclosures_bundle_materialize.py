from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from src.parse.disclosures.house_index import HouseFilingKind, HouseIndexRow
from src.parse.disclosures.senate_index import SenateIndexRow
from src.runtime.disclosures_bundle import load_disclosures_bundle
from src.runtime.disclosures_bundle_materialize import materialize_disclosures_bundle
from src.runtime.sources import HOUSE_DISCLOSURES, SENATE_DISCLOSURES

_HOUSE_FETCH = "src.runtime.disclosures_bundle_materialize.fetch_house_index"
_SENATE_FETCH = "src.runtime.disclosures_bundle_materialize.fetch_senate_index"
_DOWNLOAD = "src.runtime.disclosures_bundle_materialize.download_artifact_bytes"


def _house_row(doc_id: str, *, filing_kind: HouseFilingKind) -> HouseIndexRow:
    return HouseIndexRow(
        last_name="Smith",
        first_name="Jane",
        suffix="",
        raw_filing_type="P" if filing_kind is HouseFilingKind.PTR else "O",
        state_dst="CA08",
        year=2024,
        filing_date=date(2024, 1, 15),
        doc_id=doc_id,
        filing_kind=filing_kind,
    )


def _senate_row(doc_id: str) -> SenateIndexRow:
    return SenateIndexRow(
        first_name="John",
        last_name="Doe",
        office="Senator, CA",
        report_type="Annual Report for CY2023",
        date_filed="01/15/2024",
        doc_id=doc_id,
        filing_year=2024,
    )


def test_materialize_disclosures_bundle_writes_bundle_and_artifacts(tmp_path: Path) -> None:
    bundle_path = tmp_path / "bundle.json"
    artifact_root = tmp_path / "artifacts"
    with (
        patch(
            _HOUSE_FETCH,
            side_effect=[
                [_house_row("HOUSE-ANNUAL", filing_kind=HouseFilingKind.ANNUAL)],
                [_house_row("HOUSE-PTR", filing_kind=HouseFilingKind.PTR)],
            ],
        ),
        patch(_SENATE_FETCH, return_value=[_senate_row("SENATE-001")]),
        patch(_DOWNLOAD, side_effect=lambda url: url.encode("utf-8")),
    ):
        result = materialize_disclosures_bundle(
            years=[2024],
            chamber="both",
            bundle_path=bundle_path,
            artifact_root=artifact_root,
        )

    assert result.artifact_count == 3
    assert result.house_count == 2
    assert result.senate_count == 1
    assert result.bundle_path == bundle_path
    bundle = load_disclosures_bundle(bundle_path)
    assert [entry.source_record_id for entry in bundle.artifacts] == [
        "HOUSE-ANNUAL",
        "HOUSE-PTR",
        "SENATE-001",
    ]
    assert [entry.source_slug for entry in bundle.artifacts] == [
        HOUSE_DISCLOSURES.slug,
        HOUSE_DISCLOSURES.slug,
        SENATE_DISCLOSURES.slug,
    ]
    assert (artifact_root / "house/2024/HOUSE-ANNUAL.pdf").exists()
    assert (artifact_root / "house/2024/HOUSE-PTR.pdf").exists()
    assert (artifact_root / "senate/2024/SENATE-001.pdf").exists()


def test_materialize_disclosures_bundle_honors_senate_scope(tmp_path: Path) -> None:
    bundle_path = tmp_path / "bundle.json"
    artifact_root = tmp_path / "artifacts"
    with (
        patch(_HOUSE_FETCH) as mock_house,
        patch(_SENATE_FETCH, return_value=[_senate_row("SENATE-001")]),
        patch(_DOWNLOAD, side_effect=lambda url: url.encode("utf-8")),
    ):
        result = materialize_disclosures_bundle(
            years=[2024],
            chamber="senate",
            bundle_path=bundle_path,
            artifact_root=artifact_root,
        )

    mock_house.assert_not_called()
    assert result.house_count == 0
    assert result.senate_count == 1
    bundle = load_disclosures_bundle(bundle_path)
    assert [entry.chamber for entry in bundle.artifacts] == ["senate"]


def test_materialize_disclosures_bundle_rejects_existing_bundle_path(
    tmp_path: Path,
) -> None:
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="bundle_path"):
        materialize_disclosures_bundle(
            years=[2024],
            chamber="house",
            bundle_path=bundle_path,
            artifact_root=tmp_path / "artifacts",
        )


def test_materialize_disclosures_bundle_rejects_non_empty_artifact_root(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    (artifact_root / "existing.pdf").write_text("old", encoding="utf-8")

    with pytest.raises(ValueError, match="artifact_root"):
        materialize_disclosures_bundle(
            years=[2024],
            chamber="house",
            bundle_path=tmp_path / "bundle.json",
            artifact_root=artifact_root,
        )


def test_materialize_disclosures_bundle_failure_leaves_no_partial_outputs(
    tmp_path: Path,
) -> None:
    bundle_path = tmp_path / "bundle.json"
    artifact_root = tmp_path / "artifacts"

    with (
        patch(
            _HOUSE_FETCH,
            side_effect=[
                [
                    _house_row("HOUSE-ANNUAL", filing_kind=HouseFilingKind.ANNUAL),
                    _house_row("HOUSE-ANNUAL-2", filing_kind=HouseFilingKind.ANNUAL),
                ],
                [],
            ],
        ),
        patch(_DOWNLOAD, side_effect=[b"first-pdf", RuntimeError("download failed")]),
    ):
        with pytest.raises(RuntimeError, match="download failed"):
            materialize_disclosures_bundle(
                years=[2024],
                chamber="house",
                bundle_path=bundle_path,
                artifact_root=artifact_root,
            )

    assert not bundle_path.exists()
    assert not artifact_root.exists()
