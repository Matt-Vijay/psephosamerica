"""Materialize prepared public-statement rows from official member statements."""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml  # type: ignore[import-untyped]

from src.evidence.source_anchor_policy import is_official_source_url


@dataclass(frozen=True)
class PublicStatementRowsMaterializeResult:
    input_path: str
    output_path: str
    taxonomy_path: str
    dry_run: bool
    force: bool
    row_count: int
    source_statement_count: int
    skipped_count: int
    skipped_reasons: dict[str, int]
    derived_source_id_count: int
    sector_count: int
    member_count: int
    output_sha256: str | None
    input_sha256: str | None
    taxonomy_sha256: str | None


def materialize_public_statement_rows(
    *,
    input_path: Path,
    output_path: Path,
    taxonomy_path: Path = Path("data/taxonomy/sectors.yaml"),
    dry_run: bool = False,
    force: bool = False,
) -> PublicStatementRowsMaterializeResult:
    """Write verifier/recompute-ready member-sector statement rows."""
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    if not taxonomy_path.is_file():
        raise FileNotFoundError(taxonomy_path)
    if output_path.is_file() and not force and not dry_run:
        raise FileExistsError(output_path)

    taxonomy = _load_taxonomy(taxonomy_path)
    source_rows = _load_raw_statement_rows(input_path)
    materialized, skipped_reasons, derived_source_id_count = _materialized_rows(
        source_rows,
        taxonomy=taxonomy,
    )

    output_sha256: str | None = _sha256_file(output_path) if output_path.is_file() else None
    if not dry_run:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_output = output_path.with_name(f".{output_path.name}.{uuid4()}.tmp")
        try:
            _write_jsonl(temp_output, materialized)
            temp_output.replace(output_path)
        finally:
            _unlink_if_exists(temp_output)
        output_sha256 = _sha256_file(output_path)

    return PublicStatementRowsMaterializeResult(
        input_path=str(input_path),
        output_path=str(output_path),
        taxonomy_path=str(taxonomy_path),
        dry_run=dry_run,
        force=force,
        row_count=len(materialized),
        source_statement_count=len(source_rows),
        skipped_count=sum(skipped_reasons.values()),
        skipped_reasons=dict(sorted(skipped_reasons.items())),
        derived_source_id_count=derived_source_id_count,
        sector_count=len({row["sector"] for row in materialized}),
        member_count=len({row["member_bioguide_id"] for row in materialized}),
        output_sha256=output_sha256,
        input_sha256=_sha256_file(input_path),
        taxonomy_sha256=_sha256_file(taxonomy_path),
    )


def _load_raw_statement_rows(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"statement row {line_number} must be an object")
            rows.append(value)
        return rows
    if suffix == ".json":
        value = json.loads(path.read_text(encoding="utf-8"))
        raw_rows: Any = value.get("statements") if isinstance(value, dict) else value
        if not isinstance(raw_rows, list):
            raise ValueError("statement JSON must be a list or object with statements")
        if not all(isinstance(row, dict) for row in raw_rows):
            raise ValueError("statement JSON rows must be objects")
        return [row for row in raw_rows if isinstance(row, dict)]
    if suffix == ".csv":
        with path.open("r", encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))
    raise ValueError("statement input must be a .json, .jsonl, or .csv file")


def _materialized_rows(
    source_rows: list[dict[str, Any]],
    *,
    taxonomy: _Taxonomy,
) -> tuple[list[dict[str, Any]], Counter[str], int]:
    rows: list[dict[str, Any]] = []
    skipped_reasons: Counter[str] = Counter()
    derived_source_id_count = 0
    seen: set[tuple[str, str]] = set()
    for source in source_rows:
        member_id = _first_string(source, "member_bioguide_id", "bioguide_id", "member_id")
        date_value = _first_string(source, "statement_date", "date", "published_at")
        url = _first_string(source, "statement_source_url", "source_url", "url")
        if member_id is None or date_value is None or url is None:
            skipped_reasons["missing_required_fields"] += 1
            continue
        if not is_official_source_url("public_statement", url):
            skipped_reasons["unofficial_source_url"] += 1
            continue
        statement_date = _statement_date(date_value)
        if statement_date is None:
            skipped_reasons["invalid_statement_date"] += 1
            continue
        source_id = _first_string(source, "statement_id", "source_record_id", "source_id")
        if source_id is None:
            source_id = _derived_source_id(member_id, statement_date, url)
            derived_source_id_count += 1
        sectors = _statement_sectors(source, taxonomy=taxonomy)
        if not sectors:
            skipped_reasons["no_sector_match"] += 1
            continue
        for sector in sectors:
            row_source_id = f"{source_id}:{sector}" if len(sectors) > 1 else source_id
            dedupe_key = (row_source_id, sector)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            rows.append(
                {
                    "member_bioguide_id": member_id,
                    "statement_id": row_source_id,
                    "source_statement_id": source_id,
                    "statement_date": statement_date,
                    "sector": sector,
                    "statement_source_url": url,
                    "statement_title": _first_string(source, "title", "headline"),
                    "match_method": "explicit_sector"
                    if _explicit_sector_values(source)
                    else "keyword",
                }
            )
    rows.sort(
        key=lambda row: (
            str(row["statement_date"]),
            str(row["member_bioguide_id"]),
            str(row["statement_id"]),
            str(row["sector"]),
        )
    )
    return rows, skipped_reasons, derived_source_id_count


def _statement_sectors(source: dict[str, Any], *, taxonomy: _Taxonomy) -> list[str]:
    explicit: list[str] = []
    for value in _explicit_sector_values(source):
        sector = taxonomy.normalize(value)
        if sector is not None:
            explicit.append(sector)
    if explicit:
        return sorted(set(explicit))
    parts: list[str] = []
    for key in ("title", "headline", "summary", "body", "text"):
        raw_value = source.get(key)
        if isinstance(raw_value, str):
            parts.append(raw_value)
    text = " ".join(parts)
    return taxonomy.match_text(text)


def _explicit_sector_values(source: dict[str, Any]) -> list[str]:
    values: list[str] = []
    sector = source.get("sector")
    if isinstance(sector, str) and sector.strip():
        values.append(sector)
    sectors = source.get("sectors")
    if isinstance(sectors, list):
        values.extend(str(value) for value in sectors if str(value).strip())
    return values


def _statement_date(value: str) -> str | None:
    raw = value.strip()
    if not raw:
        return None
    candidate = raw[:10]
    try:
        return dt.date.fromisoformat(candidate).isoformat()
    except ValueError:
        return None


def _first_string(source: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _derived_source_id(member_id: str, statement_date: str, url: str) -> str:
    digest = hashlib.sha256(f"{member_id}|{statement_date}|{url}".encode()).hexdigest()[:16]
    return f"statement-{digest}"


@dataclass(frozen=True)
class _Taxonomy:
    aliases: dict[str, str]
    keywords: dict[str, tuple[str, ...]]

    def normalize(self, value: str) -> str | None:
        return self.aliases.get(_tokenize(value))

    def match_text(self, text: str) -> list[str]:
        normalized = f" {_tokenize(text)} "
        matches = [
            sector_id
            for sector_id, terms in self.keywords.items()
            if any(f" {term} " in normalized for term in terms)
        ]
        return sorted(matches)


def _load_taxonomy(path: Path) -> _Taxonomy:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("sectors"), list):
        raise ValueError("taxonomy must contain sectors")
    aliases: dict[str, str] = {}
    keywords: dict[str, tuple[str, ...]] = {}
    for item in raw["sectors"]:
        if not isinstance(item, dict):
            continue
        sector_id = item.get("sector_id")
        if not isinstance(sector_id, str) or not sector_id.strip():
            continue
        sector_terms = {sector_id, sector_id.replace("_", " ")}
        label = item.get("label")
        if isinstance(label, str):
            sector_terms.add(label)
        raw_aliases = item.get("aliases")
        if isinstance(raw_aliases, list):
            sector_terms.update(str(value) for value in raw_aliases)
        normalized_terms = sorted({_tokenize(term) for term in sector_terms if _tokenize(term)})
        for term in normalized_terms:
            aliases[term] = sector_id
        keywords[sector_id] = tuple(normalized_terms)
    return _Taxonomy(aliases=aliases, keywords=keywords)


def _tokenize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True))
            fh.write("\n")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _unlink_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
