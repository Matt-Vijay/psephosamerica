from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.runtime.fec_bulk_materialize import materialize_fec_bulk_files

_MODULE = "src.runtime.fec_bulk_materialize"


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


def _zip_bytes(member_name: str, content: bytes) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr(member_name, content)
    return out.getvalue()


def test_materialize_fec_bulk_files_dry_run_plans_official_urls(tmp_path: Path) -> None:
    result = materialize_fec_bulk_files(cycle=2024, output_dir=tmp_path, dry_run=True)

    assert result.cycle == 2024
    assert result.dry_run is True
    assert [file.kind for file in result.files] == [
        "committee_master",
        "candidate_committee_linkage",
        "individual_contributions",
    ]
    assert result.files[0].source_url == ("https://www.fec.gov/files/bulk-downloads/2024/cm24.zip")
    assert result.files[1].source_url == ("https://www.fec.gov/files/bulk-downloads/2024/ccl24.zip")
    assert result.files[2].source_url == (
        "https://www.fec.gov/files/bulk-downloads/2024/indiv24.zip"
    )
    assert [Path(file.output_path).name for file in result.files] == [
        "cm.txt",
        "ccl.txt",
        "itcont.txt",
    ]
    assert all(file.action == "would_download" for file in result.files)
    assert result.files[2].filtered is True


def test_materialize_fec_bulk_files_filters_individual_contributions_to_linked_committees(
    tmp_path: Path,
) -> None:
    member_fec = tmp_path / "member_fec.csv"
    member_fec.write_text(
        "bioguide_id,fec_candidate_id\nA000001,H4MI00001\n",
        encoding="utf-8",
    )
    payloads = [
        _zip_bytes("cm.txt", b"committee\n"),
        _zip_bytes(
            "nested/ccl.txt",
            b"H4MI00001|2024|2024|C00431445|H|P|123\nH4MI99999|2024|2024|C99999999|H|P|124\n",
        ),
        _zip_bytes(
            "itcont.txt",
            b"C00431445|N|Q1|P|IMG|10|IND|SMITH, JANE|Detroit|MI|48201|Solar Co|Engineer|05012024|2500|||4073020241987654321|||4073020241987654321\n"
            b"C99999999|N|Q1|P|IMG|10|IND|SMITH, JOHN|Detroit|MI|48201|Coal Co|Engineer|05012024|2500|||4073020241987654322|||4073020241987654322\n",
        ),
    ]

    def _urlopen(url: str, *, timeout: float, context: object) -> _Response:
        assert url.startswith("https://www.fec.gov/files/bulk-downloads/2024/")
        assert timeout == 12.0
        assert context is not None
        return _Response(payloads.pop(0))

    with patch(f"{_MODULE}.urllib.request.urlopen", side_effect=_urlopen):
        result = materialize_fec_bulk_files(
            cycle=2024,
            output_dir=tmp_path,
            timeout=12.0,
            member_fec_crosswalk=member_fec,
        )

    assert (tmp_path / "cm.txt").read_bytes() == b"committee\n"
    assert (tmp_path / "ccl.txt").read_bytes() == (
        b"H4MI00001|2024|2024|C00431445|H|P|123\nH4MI99999|2024|2024|C99999999|H|P|124\n"
    )
    assert (tmp_path / "itcont.txt").read_bytes().startswith(b"C00431445|")
    assert b"C99999999" not in (tmp_path / "itcont.txt").read_bytes()
    assert [file.action for file in result.files] == [
        "downloaded",
        "downloaded",
        "downloaded",
    ]
    assert result.files[1].zip_member == "nested/ccl.txt"
    assert result.files[2].filtered is True
    assert result.files[2].filter_committee_count == 1
    assert result.files[2].filter_candidate_count == 1
    assert result.files[2].filtered_line_count == 1


def test_materialize_fec_bulk_files_preserves_existing_files_without_force(
    tmp_path: Path,
) -> None:
    existing = tmp_path / "cm.txt"
    existing.write_text("already here\n", encoding="utf-8")

    result = materialize_fec_bulk_files(
        cycle=2024,
        output_dir=tmp_path,
        dry_run=True,
    )

    assert result.files[0].action == "exists"
    assert result.files[0].sha256 == hashlib.sha256(b"already here\n").hexdigest()


def test_materialize_fec_bulk_files_rejects_non_https_override(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be HTTPS"):
        materialize_fec_bulk_files(
            cycle=2024,
            output_dir=tmp_path,
            committee_url="http://example.test/cm.zip",
            dry_run=True,
        )


def test_materialize_fec_bulk_files_rejects_non_positive_timeout(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="timeout must be positive"):
        materialize_fec_bulk_files(
            cycle=2024,
            output_dir=tmp_path,
            timeout=0,
            dry_run=True,
        )


def test_materialize_fec_bulk_files_rejects_oversized_download(
    tmp_path: Path,
) -> None:
    def _urlopen(url: str, *, timeout: float, context: object) -> _Response:
        return _Response(_zip_bytes("cm.txt", b"committee\n") + b"extra")

    with patch(f"{_MODULE}.urllib.request.urlopen", side_effect=_urlopen):
        with pytest.raises(ValueError, match="exceeds maximum size"):
            materialize_fec_bulk_files(
                cycle=2024,
                output_dir=tmp_path,
                max_download_bytes=5,
            )

    assert not (tmp_path / "cm.txt").exists()


def test_materialize_fec_bulk_files_rejects_oversized_extracted_member(
    tmp_path: Path,
) -> None:
    def _urlopen(url: str, *, timeout: float, context: object) -> _Response:
        return _Response(_zip_bytes("cm.txt", b"123456"))

    with patch(f"{_MODULE}.urllib.request.urlopen", side_effect=_urlopen):
        with pytest.raises(ValueError, match="extracted member exceeds maximum size"):
            materialize_fec_bulk_files(
                cycle=2024,
                output_dir=tmp_path,
                max_extracted_bytes=5,
            )

    assert not (tmp_path / "cm.txt").exists()


def test_materialize_fec_bulk_files_rejects_off_origin_response_url(
    tmp_path: Path,
) -> None:
    def _urlopen(url: str, *, timeout: float, context: object) -> _Response:
        return _Response(
            _zip_bytes("cm.txt", b"committee\n"),
            final_url="https://example.com/cm.zip",
        )

    with patch(f"{_MODULE}.urllib.request.urlopen", side_effect=_urlopen):
        with pytest.raises(ValueError, match="off-origin FEC bulk response URL"):
            materialize_fec_bulk_files(cycle=2024, output_dir=tmp_path)


def test_materialize_fec_bulk_files_allows_fec_govcloud_asset_redirect(
    tmp_path: Path,
) -> None:
    payloads = [
        _zip_bytes("cm.txt", b"committee\n"),
        _zip_bytes("ccl.txt", b"candidate\n"),
        _zip_bytes("itcont.txt", b"contribution\n"),
    ]

    def _urlopen(url: str, *, timeout: float, context: object) -> _Response:
        return _Response(
            payloads.pop(0),
            final_url=(
                "https://cg-519a459a-0ea3-42c2-b7bc-fa1143481f74."
                "s3-us-gov-west-1.amazonaws.com/bulk-downloads/2024/cm24.zip"
            ),
        )

    with patch(f"{_MODULE}.urllib.request.urlopen", side_effect=_urlopen):
        result = materialize_fec_bulk_files(
            cycle=2024,
            output_dir=tmp_path,
            filter_individual_contributions=False,
        )

    assert [file.action for file in result.files] == ["downloaded", "downloaded", "downloaded"]
    assert (tmp_path / "cm.txt").read_text(encoding="utf-8") == "committee\n"


def test_materialize_fec_bulk_files_uses_unique_temp_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    payloads = [
        _zip_bytes("cm.txt", b"committee\n"),
        _zip_bytes("ccl.txt", b"candidate\n"),
        _zip_bytes("itcont.txt", b"contribution\n"),
    ]

    def _urlopen(url: str, *, timeout: float, context: object) -> _Response:
        return _Response(payloads.pop(0))

    seen_temp_names: list[str] = []
    original_open = Path.open

    def guarded_open(path: Path, *args: object, **kwargs: object):
        if path.name in {".cm.txt.tmp", ".ccl.txt.tmp", ".itcont.txt.tmp"}:
            raise AssertionError("fixed temp filename used")
        if path.name.startswith(".cm.txt.") and path.name.endswith(".tmp"):
            seen_temp_names.append(path.name)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    with patch(f"{_MODULE}.urllib.request.urlopen", side_effect=_urlopen):
        materialize_fec_bulk_files(
            cycle=2024,
            output_dir=tmp_path,
            filter_individual_contributions=False,
        )

    assert len(seen_temp_names) == 1
