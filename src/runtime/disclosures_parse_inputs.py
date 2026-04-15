"""Runtime loader that assembles parse inputs for unparsed disclosure artifacts.

Fetches artifact rows from the DB and reads each file from local_root using
the storage_uri path established by artifact_store.  No parse_run writes here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.query.source_artifact_rows import fetch_unparsed_disclosure_artifact_rows


@dataclass(frozen=True)
class DisclosureParseInput:
    artifact_row: dict[str, Any]
    local_bytes: bytes
    chamber: str
    source_record_id: str


def load_unparsed_disclosure_artifacts(
    conn,
    *,
    local_root: Path,
    chamber: str | None = None,
    limit: int | None = None,
) -> list[DisclosureParseInput]:
    """Return parse inputs for disclosure artifacts with no succeeded parse_run.

    Raises FileNotFoundError if a local file is absent for any returned row.
    """
    rows = fetch_unparsed_disclosure_artifact_rows(conn, chamber=chamber, limit=limit)
    inputs: list[DisclosureParseInput] = []
    for row in rows:
        path = local_root / row["storage_uri"]
        if not path.exists():
            raise FileNotFoundError(f"Artifact not found: {path}")
        inputs.append(
            DisclosureParseInput(
                artifact_row=row,
                local_bytes=path.read_bytes(),
                chamber=row["chamber"],
                source_record_id=row["source_record_id"],
            )
        )
    return inputs
