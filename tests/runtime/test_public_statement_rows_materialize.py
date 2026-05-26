from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest

from src.runtime.public_statement_rows_materialize import (
    materialize_public_statement_rows,
)


def _taxonomy(path: Path) -> Path:
    path.write_text(
        """
version: 1
sectors:
  - sector_id: energy_utilities
    label: Energy and Utilities
    aliases:
      - energy
      - grid
  - sector_id: defense_national_security
    label: Defense and National Security
    aliases:
      - defense
""",
        encoding="utf-8",
    )
    return path


def _rows(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def test_materialize_public_statement_rows_from_explicit_sector(
    tmp_path: Path,
) -> None:
    source = tmp_path / "statements.jsonl"
    source.write_text(
        json.dumps(
            {
                "member_bioguide_id": "A000001",
                "statement_id": "stmt-001",
                "statement_date": "2024-05-02",
                "sector": "energy",
                "statement_source_url": "https://a.house.gov/news/energy",
                "title": "Energy permitting statement",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "prepared.jsonl"
    taxonomy = _taxonomy(tmp_path / "sectors.yaml")

    result = materialize_public_statement_rows(
        input_path=source,
        output_path=output,
        taxonomy_path=taxonomy,
    )

    assert _rows(output) == [
        {
            "match_method": "explicit_sector",
            "member_bioguide_id": "A000001",
            "sector": "energy_utilities",
            "source_statement_id": "stmt-001",
            "statement_date": "2024-05-02",
            "statement_id": "stmt-001",
            "statement_source_url": "https://a.house.gov/news/energy",
            "statement_title": "Energy permitting statement",
        }
    ]
    assert result.row_count == 1
    assert result.skipped_count == 0
    assert result.output_sha256 == hashlib.sha256(output.read_bytes()).hexdigest()
    assert result.input_sha256 == hashlib.sha256(source.read_bytes()).hexdigest()


def test_materialize_public_statement_rows_infers_sector_and_derives_id(
    tmp_path: Path,
) -> None:
    source = tmp_path / "statements.json"
    source.write_text(
        json.dumps(
            {
                "statements": [
                    {
                        "bioguide_id": "B000002",
                        "date": "2024-06-03T10:00:00Z",
                        "url": "https://b.senate.gov/news/defense",
                        "title": "Defense authorization update",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "prepared.jsonl"

    result = materialize_public_statement_rows(
        input_path=source,
        output_path=output,
        taxonomy_path=_taxonomy(tmp_path / "sectors.yaml"),
    )

    rows = _rows(output)
    assert rows[0]["member_bioguide_id"] == "B000002"
    assert rows[0]["sector"] == "defense_national_security"
    assert rows[0]["statement_date"] == "2024-06-03"
    assert rows[0]["statement_id"].startswith("statement-")
    assert rows[0]["match_method"] == "keyword"
    assert result.derived_source_id_count == 1


def test_materialize_public_statement_rows_makes_multi_sector_ids_unique(
    tmp_path: Path,
) -> None:
    source = tmp_path / "statements.jsonl"
    source.write_text(
        json.dumps(
            {
                "member_bioguide_id": "A000001",
                "statement_id": "stmt-001",
                "statement_date": "2024-05-02",
                "sectors": ["energy", "defense"],
                "statement_source_url": "https://a.house.gov/news/security-energy",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "prepared.jsonl"

    result = materialize_public_statement_rows(
        input_path=source,
        output_path=output,
        taxonomy_path=_taxonomy(tmp_path / "sectors.yaml"),
    )

    rows = _rows(output)
    assert result.row_count == 2
    assert {row["statement_id"] for row in rows} == {
        "stmt-001:defense_national_security",
        "stmt-001:energy_utilities",
    }
    assert {row["source_statement_id"] for row in rows} == {"stmt-001"}


def test_materialize_public_statement_rows_skips_unofficial_and_unmatched(
    tmp_path: Path,
) -> None:
    source = tmp_path / "statements.jsonl"
    source.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "member_bioguide_id": "A000001",
                        "statement_date": "2024-05-02",
                        "statement_source_url": "https://example.com/news",
                        "sector": "energy",
                    }
                ),
                json.dumps(
                    {
                        "member_bioguide_id": "B000002",
                        "statement_date": "2024-06-03",
                        "statement_source_url": "https://b.house.gov/news/other",
                        "title": "General update",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "prepared.jsonl"

    result = materialize_public_statement_rows(
        input_path=source,
        output_path=output,
        taxonomy_path=_taxonomy(tmp_path / "sectors.yaml"),
    )

    assert result.row_count == 0
    assert result.skipped_reasons == {
        "no_sector_match": 1,
        "unofficial_source_url": 1,
    }
    assert output.read_text(encoding="utf-8") == ""


def test_materialize_public_statement_rows_refuses_existing_without_force(
    tmp_path: Path,
) -> None:
    source = tmp_path / "statements.jsonl"
    source.write_text("{}", encoding="utf-8")
    output = tmp_path / "prepared.jsonl"
    output.write_text("existing\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        materialize_public_statement_rows(
            input_path=source,
            output_path=output,
            taxonomy_path=_taxonomy(tmp_path / "sectors.yaml"),
        )


def test_materialize_public_statement_rows_uses_unique_temp_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "statements.jsonl"
    source.write_text(
        json.dumps(
            {
                "member_bioguide_id": "A000001",
                "statement_id": "stmt-001",
                "statement_date": "2024-05-02",
                "sector": "energy",
                "statement_source_url": "https://a.house.gov/news/energy",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "prepared.jsonl"
    seen_temp_names: list[str] = []
    original_write_text = Path.open

    def guarded_open(path: Path, *args: object, **kwargs: object):
        if path.name == ".prepared.jsonl.tmp":
            raise AssertionError("fixed temp filename used")
        if path.name.startswith(".prepared.jsonl.") and path.name.endswith(".tmp"):
            token = path.name.removeprefix(".prepared.jsonl.").removesuffix(".tmp")
            UUID(token)
            seen_temp_names.append(path.name)
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)

    materialize_public_statement_rows(
        input_path=source,
        output_path=output,
        taxonomy_path=_taxonomy(tmp_path / "sectors.yaml"),
    )

    assert len(seen_temp_names) == 1
