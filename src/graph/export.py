"""Write the contract corpus + delta feed to disk for offline Track B consumption.

* :func:`write_contract_corpus` writes one ``EntityResolutionOutput`` per line to
  ``records.jsonl`` (sorted by canonical ID, so the bytes are deterministic for a
  given row set) plus a ``manifest.json`` carrying the ``as_of`` snapshot time and
  the content sha256 — the corpus is therefore time-resolvable and
  content-addressed, and two runs over the same rows + ``as_of`` are byte-identical.
* :func:`write_delta_feed` writes the CDC :class:`~src.graph.cdc.EntityDelta`
  stream to a JSONL file (overwrite or append) that Track B can tail.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from src.graph.cdc import EntityDelta
from src.graph.contracts import EntityResolutionOutput

_RECORDS_FILE = "records.jsonl"
_MANIFEST_FILE = "manifest.json"


@dataclass(frozen=True)
class CorpusManifest:
    """Metadata describing one exported contract corpus."""

    as_of: str
    record_count: int
    content_sha256: str
    records_path: str


def write_contract_corpus(
    rows: Iterable[EntityResolutionOutput],
    *,
    directory: Path | str,
    as_of: datetime,
) -> CorpusManifest:
    """Write the corpus to ``directory`` and return its content-addressed manifest."""
    out_dir = Path(directory)
    out_dir.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows, key=lambda row: row.canonical_id)
    content = "".join(f"{row.model_dump_json()}\n" for row in ordered)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    (out_dir / _RECORDS_FILE).write_text(content, encoding="utf-8")
    manifest = CorpusManifest(
        as_of=as_of.isoformat(),
        record_count=len(ordered),
        content_sha256=digest,
        records_path=str(out_dir / _RECORDS_FILE),
    )
    (out_dir / _MANIFEST_FILE).write_text(
        json.dumps(asdict(manifest), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def read_contract_corpus(directory: Path | str) -> list[EntityResolutionOutput]:
    """Read back a corpus written by :func:`write_contract_corpus`."""
    path = Path(directory) / _RECORDS_FILE
    return [
        EntityResolutionOutput.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_delta_feed(
    deltas: Iterable[EntityDelta],
    *,
    path: Path | str,
    append: bool = False,
) -> int:
    """Write the CDC delta stream to a JSONL file (Track B tails it). Returns count."""
    feed_path = Path(path)
    feed_path.parent.mkdir(parents=True, exist_ok=True)
    delta_list = list(deltas)
    content = "".join(f"{delta.model_dump_json()}\n" for delta in delta_list)
    mode = "a" if append else "w"
    with feed_path.open(mode, encoding="utf-8") as handle:
        handle.write(content)
    return len(delta_list)
