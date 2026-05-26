"""Materialize a member -> FEC candidate crosswalk from public legislator IDs."""

from __future__ import annotations

import csv
import hashlib
import ssl
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, IO
from urllib.parse import urlparse
from uuid import uuid4

import yaml  # type: ignore[import-untyped]

_DEFAULT_LEGISLATORS_URL = (
    "https://raw.githubusercontent.com/unitedstates/congress-legislators/"
    "main/legislators-current.yaml"
)
_CHUNK_SIZE = 1024 * 1024
_DEFAULT_MAX_SOURCE_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class MemberFecCrosswalkMaterializeResult:
    source_url: str
    source_path: str | None
    output_path: str
    terms_output_path: str | None
    dry_run: bool
    force: bool
    row_count: int
    term_row_count: int
    skipped_count: int
    output_sha256: str | None
    terms_output_sha256: str | None
    source_sha256: str | None


def materialize_member_fec_crosswalk(
    *,
    output_path: Path,
    terms_output_path: Path | None = None,
    source_path: Path | None = None,
    source_url: str = _DEFAULT_LEGISLATORS_URL,
    dry_run: bool = False,
    force: bool = False,
    timeout: float = 60.0,
    max_source_bytes: int = _DEFAULT_MAX_SOURCE_BYTES,
) -> MemberFecCrosswalkMaterializeResult:
    """Write loader-ready ``bioguide_id,fec_candidate_id`` rows."""
    _validate_https_url(source_url)
    _validate_options(timeout=timeout, max_source_bytes=max_source_bytes)
    if dry_run:
        output_exists = output_path.is_file()
        return MemberFecCrosswalkMaterializeResult(
            source_url=source_url,
            source_path=str(source_path) if source_path is not None else None,
            output_path=str(output_path),
            terms_output_path=str(terms_output_path) if terms_output_path is not None else None,
            dry_run=True,
            force=force,
            row_count=_crosswalk_output_row_count(output_path) if output_exists else 0,
            term_row_count=_crosswalk_output_row_count(terms_output_path)
            if terms_output_path is not None and terms_output_path.is_file()
            else 0,
            skipped_count=0,
            output_sha256=_sha256_file(output_path) if output_exists else None,
            terms_output_sha256=_sha256_file(terms_output_path)
            if terms_output_path is not None and terms_output_path.is_file()
            else None,
            source_sha256=_sha256_file(source_path)
            if source_path is not None and source_path.is_file()
            else None,
        )
    if output_path.is_file() and not force:
        raise FileExistsError(output_path)
    if terms_output_path is not None and terms_output_path.is_file() and not force:
        raise FileExistsError(terms_output_path)

    materialized_source_path: Path | None = None
    source = source_path
    if source is None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        materialized_source_path = _download_to_temp_file(
            source_url,
            output_path.parent,
            timeout=timeout,
            max_bytes=max_source_bytes,
        )
        source = materialized_source_path
    if not source.is_file():
        raise FileNotFoundError(source)

    try:
        rows, term_rows, skipped_count = _crosswalk_rows_from_source(source)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_output = output_path.with_name(f".{output_path.name}.{uuid4().hex}.tmp")
        temp_terms_output: Path | None = None
        if terms_output_path is not None:
            terms_output_path.parent.mkdir(parents=True, exist_ok=True)
            temp_terms_output = terms_output_path.with_name(
                f".{terms_output_path.name}.{uuid4().hex}.tmp"
            )
        try:
            _write_crosswalk_csv(temp_output, rows)
            if temp_terms_output is not None:
                _write_member_terms_csv(temp_terms_output, term_rows)
            temp_output.replace(output_path)
            if temp_terms_output is not None and terms_output_path is not None:
                temp_terms_output.replace(terms_output_path)
        finally:
            _unlink_if_exists(temp_output)
            if temp_terms_output is not None:
                _unlink_if_exists(temp_terms_output)
    finally:
        if materialized_source_path is not None:
            _unlink_if_exists(materialized_source_path)

    return MemberFecCrosswalkMaterializeResult(
        source_url=source_url,
        source_path=str(source_path) if source_path is not None else None,
        output_path=str(output_path),
        terms_output_path=str(terms_output_path) if terms_output_path is not None else None,
        dry_run=False,
        force=force,
        row_count=len(rows),
        term_row_count=len(term_rows) if terms_output_path is not None else 0,
        skipped_count=skipped_count,
        output_sha256=_sha256_file(output_path),
        terms_output_sha256=_sha256_file(terms_output_path)
        if terms_output_path is not None
        else None,
        source_sha256=_sha256_file(source) if source_path is not None else None,
    )


def _crosswalk_rows_from_source(
    path: Path,
) -> tuple[list[dict[str, str]], list[dict[str, str]], int]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("legislator source must be a YAML list")
    rows: list[dict[str, str]] = []
    term_rows: list[dict[str, str]] = []
    skipped_count = 0
    for item in raw:
        if not isinstance(item, dict):
            skipped_count += 1
            continue
        ids = item.get("id")
        if not isinstance(ids, dict):
            skipped_count += 1
            continue
        bioguide_id = _clean_id(ids.get("bioguide"))
        fec_ids = _fec_ids(ids.get("fec"))
        if bioguide_id is None or not fec_ids:
            skipped_count += 1
            continue
        selected = _select_fec_id(fec_ids, latest_term_type=_latest_term_type(item))
        if selected is None:
            skipped_count += 1
            continue
        rows.append({"bioguide_id": bioguide_id, "fec_candidate_id": selected})
        term_rows.extend(_term_rows_from_item(item, bioguide_id=bioguide_id))
    rows.sort(key=lambda row: row["bioguide_id"])
    term_rows.sort(key=lambda row: (row["bioguide_id"], row["start_date"], row["congress"]))
    return rows, term_rows, skipped_count


def _clean_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip().upper()
    return stripped or None


def _fec_ids(value: Any) -> list[str]:
    if isinstance(value, str):
        return [_cleaned for _cleaned in [_clean_id(value)] if _cleaned is not None]
    if not isinstance(value, list):
        return []
    ids: list[str] = []
    for item in value:
        cleaned = _clean_id(item)
        if cleaned is not None and cleaned not in ids:
            ids.append(cleaned)
    return ids


def _latest_term_type(item: dict[str, Any]) -> str | None:
    terms = item.get("terms")
    if not isinstance(terms, list) or not terms:
        return None
    latest = terms[-1]
    if not isinstance(latest, dict):
        return None
    term_type = latest.get("type")
    return term_type.strip().lower() if isinstance(term_type, str) else None


def _term_rows_from_item(item: dict[str, Any], *, bioguide_id: str) -> list[dict[str, str]]:
    terms = item.get("terms")
    if not isinstance(terms, list):
        return []
    rows: list[dict[str, str]] = []
    for raw_term in terms:
        if not isinstance(raw_term, dict):
            continue
        start_date = _term_date(raw_term.get("start"))
        if start_date is None:
            continue
        congress = raw_term.get("congress") or _congress_from_start_date(start_date)
        term_type = str(raw_term.get("type") or "").strip().lower()
        chamber = {"rep": "house", "sen": "senate"}.get(term_type, term_type)
        if chamber not in {"house", "senate"}:
            continue
        end_date = _term_date(raw_term.get("end"))
        state = _clean_state(raw_term.get("state"))
        district = "" if chamber == "senate" else _clean_optional_int(raw_term.get("district"))
        rows.append(
            {
                "bioguide_id": bioguide_id,
                "congress": str(congress),
                "chamber": chamber,
                "state": state or "",
                "district": district,
                "start_date": start_date,
                "end_date": end_date or "",
                "is_current": "true" if end_date is None else "false",
            }
        )
    return rows


def _congress_from_start_date(start_date: str) -> int:
    year = int(start_date[:4])
    return ((year - 1789) // 2) + 1


def _term_date(value: Any) -> str | None:
    cleaned = _clean_id(value)
    if cleaned is None:
        return None
    if len(cleaned) == 4 and cleaned.isdigit():
        return f"{cleaned}-01-03"
    return cleaned[:10]


def _clean_state(value: Any) -> str | None:
    cleaned = _clean_id(value)
    return cleaned if cleaned is not None and len(cleaned) == 2 else None


def _clean_optional_int(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return ""
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return ""


def _select_fec_id(
    fec_ids: list[str],
    *,
    latest_term_type: str | None,
) -> str | None:
    preferred_prefix = {"rep": "H", "sen": "S"}.get(latest_term_type or "")
    if preferred_prefix is not None:
        for fec_id in fec_ids:
            if fec_id.startswith(preferred_prefix):
                return fec_id
    return fec_ids[0] if fec_ids else None


def _write_crosswalk_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["bioguide_id", "fec_candidate_id"])
        writer.writeheader()
        writer.writerows(rows)


def _write_member_terms_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "bioguide_id",
                "congress",
                "chamber",
                "state",
                "district",
                "start_date",
                "end_date",
                "is_current",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def _crosswalk_output_row_count(path: Path) -> int:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return sum(1 for _ in csv.DictReader(fh))


def _download_to_temp_file(
    url: str,
    output_dir: Path,
    *,
    timeout: float,
    max_bytes: int,
) -> Path:
    _validate_https_url(url)
    handle = tempfile.NamedTemporaryFile(
        prefix="member-fec-crosswalk-",
        suffix=".yaml",
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_https_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"member FEC crosswalk source URL must be HTTPS: {url}")


def _validate_options(*, timeout: float, max_source_bytes: int) -> None:
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if max_source_bytes <= 0:
        raise ValueError("max_source_bytes must be positive")


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
        raise ValueError("member FEC crosswalk response URL must be HTTPS")
    if requested_host is None or final_host is None:
        raise ValueError("member FEC crosswalk response URL must include a host")
    if final_host.lower() != requested_host.lower():
        raise ValueError("off-origin member FEC crosswalk response URL")


def _copy_bounded_response(src: IO[bytes], dst: IO[bytes], *, max_bytes: int) -> None:
    total = 0
    for chunk in iter(lambda: src.read(_CHUNK_SIZE), b""):
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(
                f"member FEC crosswalk response exceeds maximum size of {max_bytes} bytes"
            )
        dst.write(chunk)


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _unlink_if_exists(path: Path) -> None:
    if path.exists():
        path.unlink()
