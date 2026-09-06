from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path
from unittest.mock import patch

import pytest

from src.runtime.member_fec_crosswalk_materialize import (
    materialize_member_fec_crosswalk,
)

_MODULE = "src.runtime.member_fec_crosswalk_materialize"


class _Response(io.BytesIO):
    def __init__(self, body: bytes, *, final_url: str | None = None) -> None:
        super().__init__(body)
        self._final_url = final_url

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def geturl(self) -> str:
        return self._final_url or ""


def _source(path: Path) -> Path:
    path.write_text(
        """
- id:
    bioguide: A000001
    fec:
      - H0MI00001
      - S0MI00001
  terms:
    - type: rep
      state: MI
      district: 1
      start: '2019-01-03'
      end: '2021-01-03'
- id:
    bioguide: B000002
    fec:
      - H0CA00002
      - S0CA00002
  terms:
    - type: rep
      congress: 117
      state: CA
      district: 2
      start: '2021-01-03'
      end: '2023-01-03'
    - type: sen
      congress: 118
      state: CA
      start: '2023-01-03'
- id:
    bioguide: C000003
  terms:
    - type: rep
""",
        encoding="utf-8",
    )
    return path


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_materialize_member_fec_crosswalk_from_local_source(tmp_path: Path) -> None:
    source = _source(tmp_path / "legislators-current.yaml")
    output = tmp_path / "member_fec.csv"

    result = materialize_member_fec_crosswalk(
        output_path=output,
        source_path=source,
    )

    assert _rows(output) == [
        {"bioguide_id": "A000001", "fec_candidate_id": "H0MI00001"},
        {"bioguide_id": "B000002", "fec_candidate_id": "S0CA00002"},
    ]
    assert result.row_count == 2
    assert result.skipped_count == 1
    assert result.output_sha256 == hashlib.sha256(output.read_bytes()).hexdigest()
    assert result.source_sha256 == hashlib.sha256(source.read_bytes()).hexdigest()


def test_materialize_member_fec_crosswalk_can_write_member_terms_companion_csv(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path / "legislators-current.yaml")
    output = tmp_path / "member_fec.csv"
    terms_output = tmp_path / "member_terms.csv"

    result = materialize_member_fec_crosswalk(
        output_path=output,
        terms_output_path=terms_output,
        source_path=source,
    )

    assert _rows(terms_output) == [
        {
            "bioguide_id": "A000001",
            "congress": "116",
            "chamber": "house",
            "state": "MI",
            "district": "1",
            "start_date": "2019-01-03",
            "end_date": "2021-01-03",
            "is_current": "false",
        },
        {
            "bioguide_id": "B000002",
            "congress": "117",
            "chamber": "house",
            "state": "CA",
            "district": "2",
            "start_date": "2021-01-03",
            "end_date": "2023-01-03",
            "is_current": "false",
        },
        {
            "bioguide_id": "B000002",
            "congress": "118",
            "chamber": "senate",
            "state": "CA",
            "district": "",
            "start_date": "2023-01-03",
            "end_date": "",
            "is_current": "true",
        },
    ]
    assert result.terms_output_path == str(terms_output)
    assert result.term_row_count == 3
    assert result.terms_output_sha256 == hashlib.sha256(terms_output.read_bytes()).hexdigest()


def test_materialize_member_fec_crosswalk_downloads_source(tmp_path: Path) -> None:
    body = _source(tmp_path / "source.yaml").read_bytes()

    def _urlopen(url: str, *, timeout: float, context: object) -> _Response:
        assert url == "https://example.test/legislators-current.yaml"
        assert timeout == 12.0
        assert context is not None
        return _Response(body)

    output = tmp_path / "member_fec.csv"
    with patch(f"{_MODULE}.urllib.request.urlopen", side_effect=_urlopen):
        result = materialize_member_fec_crosswalk(
            output_path=output,
            source_url="https://example.test/legislators-current.yaml",
            timeout=12.0,
        )

    assert result.row_count == 2
    assert _rows(output)[1] == {
        "bioguide_id": "B000002",
        "fec_candidate_id": "S0CA00002",
    }


def test_materialize_member_fec_crosswalk_dry_run_does_not_write(
    tmp_path: Path,
) -> None:
    output = tmp_path / "member_fec.csv"

    result = materialize_member_fec_crosswalk(
        output_path=output,
        dry_run=True,
    )

    assert result.dry_run is True
    assert result.output_path == str(output)
    assert result.row_count == 0
    assert not output.exists()


def test_materialize_member_fec_crosswalk_dry_run_counts_existing_output(
    tmp_path: Path,
) -> None:
    output = tmp_path / "member_fec.csv"
    output.write_text(
        "bioguide_id,fec_candidate_id\nA000001,H0MI00001\nB000002,S0CA00002\n",
        encoding="utf-8",
    )

    result = materialize_member_fec_crosswalk(
        output_path=output,
        dry_run=True,
    )

    assert result.row_count == 2
    assert result.output_sha256 == hashlib.sha256(output.read_bytes()).hexdigest()


def test_materialize_member_fec_crosswalk_refuses_existing_without_force(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path / "legislators-current.yaml")
    output = tmp_path / "member_fec.csv"
    output.write_text("existing\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        materialize_member_fec_crosswalk(output_path=output, source_path=source)


def test_materialize_member_fec_crosswalk_rejects_non_https_url(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="must be HTTPS"):
        materialize_member_fec_crosswalk(
            output_path=tmp_path / "member_fec.csv",
            source_url="http://example.test/legislators-current.yaml",
            dry_run=True,
        )


def test_materialize_member_fec_crosswalk_rejects_non_positive_timeout(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="timeout must be positive"):
        materialize_member_fec_crosswalk(
            output_path=tmp_path / "member_fec.csv",
            source_path=_source(tmp_path / "legislators-current.yaml"),
            timeout=0,
        )


def test_materialize_member_fec_crosswalk_rejects_oversized_download(
    tmp_path: Path,
) -> None:
    def _urlopen(url: str, *, timeout: float, context: object) -> _Response:
        return _Response(b"123456")

    with patch(f"{_MODULE}.urllib.request.urlopen", side_effect=_urlopen):
        with pytest.raises(ValueError, match="exceeds maximum size"):
            materialize_member_fec_crosswalk(
                output_path=tmp_path / "member_fec.csv",
                source_url="https://example.test/legislators-current.yaml",
                max_source_bytes=5,
            )


def test_materialize_member_fec_crosswalk_rejects_off_origin_response_url(
    tmp_path: Path,
) -> None:
    def _urlopen(url: str, *, timeout: float, context: object) -> _Response:
        return _Response(
            _source(tmp_path / "source.yaml").read_bytes(),
            final_url="https://example.com/legislators-current.yaml",
        )

    with patch(f"{_MODULE}.urllib.request.urlopen", side_effect=_urlopen):
        with pytest.raises(ValueError, match="off-origin member FEC crosswalk response URL"):
            materialize_member_fec_crosswalk(
                output_path=tmp_path / "member_fec.csv",
                source_url="https://example.test/legislators-current.yaml",
            )


def test_materialize_member_fec_crosswalk_uses_unique_temp_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output = tmp_path / "member_fec.csv"
    seen_temp_names: list[str] = []
    original_open = Path.open

    def guarded_open(path: Path, *args: object, **kwargs: object):
        if path.name == ".member_fec.csv.tmp":
            raise AssertionError("fixed temp filename used")
        if path.name.startswith(".member_fec.csv.") and path.name.endswith(".tmp"):
            seen_temp_names.append(path.name)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)

    materialize_member_fec_crosswalk(
        output_path=output,
        source_path=_source(tmp_path / "legislators-current.yaml"),
    )

    assert len(seen_temp_names) == 1
