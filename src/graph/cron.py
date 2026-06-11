"""A cron-friendly, idempotent regeneration cycle with persisted state.

:func:`run_cycle` is what a scheduler invokes each tick. It loads the prior
canonical-ID assignment and the prior contract corpus from ``state_dir``, runs
:func:`~src.graph.regenerate.regenerate_corpus` at ``as_of`` (the strict cutoff —
only facts knowable by then are used), writes the refreshed corpus, appends the
CDC deltas to a tailable feed, and persists the new assignment so canonical IDs
stay stable across cycles.

It is **idempotent**: re-running with the same inputs and ``as_of`` reproduces a
byte-identical corpus and emits *no* deltas (nothing changed). As ``as_of``
advances and new facts become knowable, only the affected entities appear in the
delta feed.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from src.graph.contracts import EntityResolutionOutput
from src.graph.edges import GraphEdge
from src.graph.entity_resolution.assignment import CanonicalAssignment
from src.graph.entity_resolution.records import SourceRecord
from src.graph.export import (
    DELTAS_FILENAME,
    RECORDS_FILENAME,
    read_contract_corpus,
    write_contract_corpus,
    write_delta_feed,
)
from src.graph.regenerate import CorpusResult, regenerate_corpus

_ASSIGNMENT_FILE = "assignment.json"


def save_assignment(assignment: CanonicalAssignment, path: Path | str) -> None:
    """Persist a canonical-ID assignment to disk."""
    Path(path).write_text(assignment.model_dump_json(), encoding="utf-8")


def load_assignment(path: Path | str) -> CanonicalAssignment | None:
    """Load a persisted assignment, or ``None`` if it does not exist yet."""
    file_path = Path(path)
    if not file_path.exists():
        return None
    return CanonicalAssignment.model_validate_json(file_path.read_text(encoding="utf-8"))


def run_cycle(
    *,
    person_records: Iterable[SourceRecord],
    bill_outputs: Iterable[EntityResolutionOutput],
    edges: Iterable[GraphEdge],
    as_of: datetime,
    state_dir: Path | str,
) -> CorpusResult:
    """Run one idempotent regeneration cycle, persisting corpus + state."""
    out_dir = Path(state_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    prior_assignment = load_assignment(out_dir / _ASSIGNMENT_FILE)
    prior_outputs: dict[str, EntityResolutionOutput] = {}
    if (out_dir / RECORDS_FILENAME).exists():
        prior_outputs = {row.canonical_id: row for row in read_contract_corpus(out_dir)}

    result = regenerate_corpus(
        person_records=person_records,
        bill_outputs=bill_outputs,
        edges=edges,
        as_of=as_of,
        prior_assignment=prior_assignment,
        prior_outputs=prior_outputs,
    )

    write_contract_corpus(result.rows, directory=out_dir, as_of=as_of)
    write_delta_feed(result.deltas, path=out_dir / DELTAS_FILENAME, append=True)
    save_assignment(result.assignment, out_dir / _ASSIGNMENT_FILE)
    return result
