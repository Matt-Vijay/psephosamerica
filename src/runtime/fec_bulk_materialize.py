"""Materialize official FEC bulk ZIP files into local loader inputs."""

from __future__ import annotations

import ssl
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO
from urllib.parse import urlparse
from uuid import uuid4

from src.core.files import sha256_file as _sha256_file

_FEC_BULK_URL_PREFIX = "https://www.fec.gov/files/bulk-downloads"
_CHUNK_SIZE = 1024 * 1024
_DEFAULT_MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024 * 1024
_DEFAULT_MAX_EXTRACTED_BYTES = 4 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class FecBulkMaterializedFile:
    kind: str
    source_url: str
    output_path: str
    zip_member: str | None
    action: str
    sha256: str | None
    size_bytes: int | None
    filtered: bool = False
    filter_committee_count: int | None = None
    filter_candidate_count: int | None = None
    filtered_line_count: int | None = None


@dataclass(frozen=True)
class FecBulkMaterializeResult:
    cycle: int
    output_dir: str
    dry_run: bool
    force: bool
    files: tuple[FecBulkMaterializedFile, ...]


@dataclass(frozen=True)
class _FecBulkSpec:
    kind: str
    source_url: str
    output_name: str
    member_names: tuple[str, ...]


def materialize_fec_bulk_files(
    *,
    cycle: int,
    output_dir: Path,
    dry_run: bool = False,
    force: bool = False,
    timeout: float = 60.0,
    committee_url: str | None = None,
    linkage_url: str | None = None,
    contribution_url: str | None = None,
    filter_individual_contributions: bool = True,
    member_fec_crosswalk: Path | None = Path("data/crosswalks/member_fec.csv"),
    max_download_bytes: int = _DEFAULT_MAX_DOWNLOAD_BYTES,
    max_extracted_bytes: int = _DEFAULT_MAX_EXTRACTED_BYTES,
) -> FecBulkMaterializeResult:
    """Download and extract official FEC bulk files into loader-ready paths."""
    _validate_options(
        timeout=timeout,
        max_download_bytes=max_download_bytes,
        max_extracted_bytes=max_extracted_bytes,
    )
    specs = _fec_bulk_specs(
        cycle=cycle,
        committee_url=committee_url,
        linkage_url=linkage_url,
        contribution_url=contribution_url,
    )
    files = tuple(
        _materialize_spec(
            spec,
            output_dir=output_dir,
            dry_run=dry_run,
            force=force,
            timeout=timeout,
            filter_individual_contributions=filter_individual_contributions,
            member_fec_crosswalk=member_fec_crosswalk,
            max_download_bytes=max_download_bytes,
            max_extracted_bytes=max_extracted_bytes,
        )
        for spec in specs
    )
    return FecBulkMaterializeResult(
        cycle=cycle,
        output_dir=str(output_dir),
        dry_run=dry_run,
        force=force,
        files=files,
    )


def _fec_bulk_specs(
    *,
    cycle: int,
    committee_url: str | None,
    linkage_url: str | None,
    contribution_url: str | None,
) -> tuple[_FecBulkSpec, ...]:
    suffix = f"{cycle % 100:02d}"
    return (
        _FecBulkSpec(
            kind="committee_master",
            source_url=committee_url or f"{_FEC_BULK_URL_PREFIX}/{cycle}/cm{suffix}.zip",
            output_name="cm.txt",
            member_names=("cm.txt",),
        ),
        _FecBulkSpec(
            kind="candidate_committee_linkage",
            source_url=linkage_url or f"{_FEC_BULK_URL_PREFIX}/{cycle}/ccl{suffix}.zip",
            output_name="ccl.txt",
            member_names=("ccl.txt",),
        ),
        _FecBulkSpec(
            kind="individual_contributions",
            source_url=contribution_url or f"{_FEC_BULK_URL_PREFIX}/{cycle}/indiv{suffix}.zip",
            output_name="itcont.txt",
            member_names=("itcont.txt", "indiv.txt"),
        ),
    )


def _materialize_spec(
    spec: _FecBulkSpec,
    *,
    output_dir: Path,
    dry_run: bool,
    force: bool,
    timeout: float,
    filter_individual_contributions: bool,
    member_fec_crosswalk: Path | None,
    max_download_bytes: int,
    max_extracted_bytes: int,
) -> FecBulkMaterializedFile:
    _validate_https_url(spec.source_url)
    output_path = output_dir / spec.output_name
    if dry_run:
        action = "would_download" if force or not output_path.is_file() else "exists"
        return _file_status(
            spec,
            output_path=output_path,
            action=action,
            zip_member=None,
            filtered=spec.kind == "individual_contributions" and filter_individual_contributions,
            filter_committee_count=None,
            filter_candidate_count=None,
            filtered_line_count=None,
        )
    if output_path.is_file() and not force:
        return _file_status(
            spec,
            output_path=output_path,
            action="exists",
            zip_member=None,
            filtered=spec.kind == "individual_contributions" and filter_individual_contributions,
            filter_committee_count=None,
            filter_candidate_count=None,
            filtered_line_count=None,
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = _download_to_temp_file(
        spec.source_url,
        output_dir,
        timeout=timeout,
        max_bytes=max_download_bytes,
    )
    temp_output = output_path.with_name(f".{output_path.name}.{uuid4().hex}.tmp")
    filter_committee_count: int | None = None
    filter_candidate_count: int | None = None
    filtered_line_count: int | None = None
    try:
        if spec.kind == "individual_contributions" and filter_individual_contributions:
            candidate_ids = _member_fec_candidate_ids(member_fec_crosswalk)
            filter_candidate_count = len(candidate_ids) if candidate_ids else None
            committee_ids = _candidate_committee_ids(
                output_dir / "ccl.txt",
                candidate_ids=candidate_ids,
            )
            filter_committee_count = len(committee_ids)
            zip_member, filtered_line_count = _extract_filtered_individual_contributions(
                zip_path,
                temp_output,
                member_names=spec.member_names,
                committee_ids=committee_ids,
                max_bytes=max_extracted_bytes,
            )
        else:
            zip_member = _extract_zip_member(
                zip_path,
                temp_output,
                member_names=spec.member_names,
                max_bytes=max_extracted_bytes,
            )
        temp_output.replace(output_path)
    finally:
        _unlink_if_exists(zip_path)
        _unlink_if_exists(temp_output)
    return _file_status(
        spec,
        output_path=output_path,
        action="downloaded",
        zip_member=zip_member,
        filtered=spec.kind == "individual_contributions" and filter_individual_contributions,
        filter_committee_count=filter_committee_count,
        filter_candidate_count=filter_candidate_count,
        filtered_line_count=filtered_line_count,
    )


def _download_to_temp_file(
    url: str,
    output_dir: Path,
    *,
    timeout: float,
    max_bytes: int,
) -> Path:
    _validate_https_url(url)
    handle = tempfile.NamedTemporaryFile(
        prefix="fec-bulk-",
        suffix=".zip",
        dir=output_dir,
        delete=False,
    )
    path = Path(handle.name)
    try:
        with handle:
            with urllib.request.urlopen(  # nosec B310
                url,
                timeout=timeout,
                context=_ssl_context(),
            ) as response:
                _validate_response_url(url, response)
                _copy_bounded_response(response, handle, max_bytes=max_bytes)
    except Exception:
        _unlink_if_exists(path)
        raise
    return path


def _extract_zip_member(
    zip_path: Path,
    output_path: Path,
    *,
    member_names: tuple[str, ...],
    max_bytes: int,
) -> str:
    with zipfile.ZipFile(zip_path) as archive:
        member = _select_zip_member(archive, member_names=member_names)
        with archive.open(member) as src, output_path.open("wb") as dst:
            _copy_stream(src, dst, max_bytes=max_bytes)
        return member


def _extract_filtered_individual_contributions(
    zip_path: Path,
    output_path: Path,
    *,
    member_names: tuple[str, ...],
    committee_ids: set[str],
    max_bytes: int,
) -> tuple[str, int]:
    filtered = 0
    total = 0
    with zipfile.ZipFile(zip_path) as archive:
        member = _select_zip_member(archive, member_names=member_names)
        with archive.open(member) as src, output_path.open("wb") as dst:
            for line in src:
                committee_id = (
                    line.split(b"|", 1)[0]
                    .strip()
                    .decode(
                        "latin-1",
                        errors="ignore",
                    )
                )
                if committee_id in committee_ids:
                    total += len(line)
                    if total > max_bytes:
                        raise ValueError(
                            f"FEC extracted member exceeds maximum size of {max_bytes} bytes"
                        )
                    dst.write(line)
                    filtered += 1
        return member, filtered


def _select_zip_member(
    archive: zipfile.ZipFile,
    *,
    member_names: tuple[str, ...],
) -> str:
    members = [
        name
        for name in archive.namelist()
        if name and not name.endswith("/") and PurePosixPath(name).name in member_names
    ]
    if not members:
        raise ValueError(
            "FEC ZIP did not contain one of expected members: " + ", ".join(member_names)
        )
    return members[0]


def _copy_stream(src: IO[bytes], dst: IO[bytes], *, max_bytes: int) -> None:
    total = 0
    for chunk in iter(lambda: src.read(_CHUNK_SIZE), b""):
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"FEC extracted member exceeds maximum size of {max_bytes} bytes")
        dst.write(chunk)


def _copy_bounded_response(src: IO[bytes], dst: IO[bytes], *, max_bytes: int) -> None:
    total = 0
    for chunk in iter(lambda: src.read(_CHUNK_SIZE), b""):
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"FEC bulk response exceeds maximum size of {max_bytes} bytes")
        dst.write(chunk)


def _file_status(
    spec: _FecBulkSpec,
    *,
    output_path: Path,
    action: str,
    zip_member: str | None,
    filtered: bool = False,
    filter_committee_count: int | None = None,
    filter_candidate_count: int | None = None,
    filtered_line_count: int | None = None,
) -> FecBulkMaterializedFile:
    return FecBulkMaterializedFile(
        kind=spec.kind,
        source_url=spec.source_url,
        output_path=str(output_path),
        zip_member=zip_member,
        action=action,
        sha256=_sha256_file(output_path) if output_path.is_file() else None,
        size_bytes=output_path.stat().st_size if output_path.is_file() else None,
        filtered=filtered,
        filter_committee_count=filter_committee_count,
        filter_candidate_count=filter_candidate_count,
        filtered_line_count=filtered_line_count,
    )


def _candidate_committee_ids(
    path: Path,
    *,
    candidate_ids: set[str] | None = None,
) -> set[str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    committee_ids: set[str] = set()
    with path.open("r", encoding="latin-1", newline="") as fh:
        for line in fh:
            columns = line.rstrip("\n").split("|")
            if len(columns) <= 3:
                continue
            candidate_id = columns[0].strip()
            committee_id = columns[3].strip()
            if candidate_ids is not None and candidate_id not in candidate_ids:
                continue
            if committee_id:
                committee_ids.add(committee_id)
    return committee_ids


def _member_fec_candidate_ids(path: Path | None) -> set[str] | None:
    if path is None or not path.is_file():
        return None
    candidate_ids: set[str] = set()
    with path.open("r", encoding="utf-8", newline="") as fh:
        header = fh.readline().strip().split(",")
        try:
            idx = header.index("fec_candidate_id")
        except ValueError:
            return None
        for line in fh:
            columns = line.rstrip("\n").split(",")
            if len(columns) > idx and columns[idx].strip():
                candidate_ids.add(columns[idx].strip().upper())
    return candidate_ids


def _validate_https_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"FEC bulk source URL must be HTTPS: {url}")


def _validate_options(
    *,
    timeout: float,
    max_download_bytes: int,
    max_extracted_bytes: int,
) -> None:
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if max_download_bytes <= 0:
        raise ValueError("max_download_bytes must be positive")
    if max_extracted_bytes <= 0:
        raise ValueError("max_extracted_bytes must be positive")


def _validate_response_url(requested_url: str, response: object) -> None:
    geturl = getattr(response, "geturl", None)
    if not callable(geturl):
        return
    final_url = geturl()
    if not isinstance(final_url, str) or not final_url:
        return
    requested = urlparse(requested_url)
    final = urlparse(final_url)
    requested_host = requested.hostname
    final_host = final.hostname
    if final.scheme != "https":
        raise ValueError("FEC bulk response URL must be HTTPS")
    if requested_host is None or final_host is None:
        raise ValueError("FEC bulk response URL must include a host")
    if final_host.lower() != requested_host.lower() and not _is_allowed_fec_asset_redirect(
        requested_host,
        final_host,
    ):
        raise ValueError("off-origin FEC bulk response URL")


def _is_allowed_fec_asset_redirect(requested_host: str, final_host: str) -> bool:
    """FEC bulk ZIPs are served from fec.gov but redirect to AWS GovCloud S3."""
    requested = requested_host.lower()
    final = final_host.lower()
    return requested in {"fec.gov", "www.fec.gov"} and final.endswith(
        ".s3-us-gov-west-1.amazonaws.com"
    )


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _unlink_if_exists(path: Path) -> None:
    if path.exists():
        path.unlink()
