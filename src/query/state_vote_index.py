"""Lazy on-disk index over the 22GB state vote-edge corpus.

The combined state vote edges (``data/exports/openstates/state_vote_edges_combined.jsonl``,
~22GB / 39M edges) are far too large to fold into the in-memory :class:`GraphStore`
that serves the explorer/ask/query API. Adding them to ``build_store``'s default
``edge_paths`` would blow the serving RAM budget.

Instead this module builds a compact **byte-offset index** so a single state
legislator's (or a single bill's) votes can be answered *lazily* -- seek straight
to that entity's edge lines in the big file and parse only those, never loading
the 39M edges. The index is built once with a single streaming pass and persisted
as a small JSONL sidecar:

    {"key": "ce-<person>", "kind": "person", "offsets": [<byte>, ...]}

Query side: :class:`StateVoteIndex` memory-maps the offset table (tiny relative to
the corpus -- one int list per entity, ~tens of thousands of persons) and, on a
person/bill lookup, ``seek``s to each recorded offset in the big file and reads a
single line. Every returned vote keeps its full provenance (source_url,
content_sha256, known_at), so served facts stay citable.

This is the scalable wiring complement to the bounded recent-slice option: the
explorer can load a small recent slice into the live store for browse/ranking and
fall back to this index for "show me legislator X's full state voting record".
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

DEFAULT_EDGES = Path("data/exports/openstates/state_vote_edges_combined.jsonl")
DEFAULT_INDEX = Path("data/exports/openstates/state_vote_edges_index.jsonl")


@dataclass(frozen=True)
class StateVote:
    """One state legislator vote, with its citation, read lazily from the corpus."""

    person_id: str
    bill_id: str
    choice: str
    motion: str
    result: str
    vote_id: str | None
    source_url: str
    content_sha256: str
    known_at: str
    valid_from: str | None

    def as_citation(self) -> dict[str, Any]:
        return {
            "person_id": self.person_id,
            "bill_id": self.bill_id,
            "choice": self.choice,
            "motion": self.motion,
            "result": self.result,
            "vote_id": self.vote_id,
            "source_url": self.source_url,
            "content_sha256": self.content_sha256,
            "known_at": self.known_at,
            "valid_from": self.valid_from,
        }


def build_index(
    edges_path: Path = DEFAULT_EDGES,
    index_path: Path = DEFAULT_INDEX,
    *,
    flush_every: int = 5_000_000,
) -> dict[str, int]:
    """Stream the big edge file once, recording each line's byte offset by person + bill.

    Reads the file in binary so byte offsets line up with ``seek``. Holds only the
    offset table (lists of ints keyed by canonical id), not the edges themselves, so
    memory stays bounded by the number of distinct persons/bills, not file size.
    Returns ``{"edges": n, "persons": p, "bills": b}``.
    """
    by_person: dict[str, list[int]] = defaultdict(list)
    by_bill: dict[str, list[int]] = defaultdict(list)
    edges = 0
    with edges_path.open("rb") as handle:
        offset = handle.tell()
        line = handle.readline()
        while line:
            if line.strip():
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    offset = handle.tell()
                    line = handle.readline()
                    continue
                src = rec.get("src_id")
                dst = rec.get("dst_id")
                if isinstance(src, str):
                    by_person[src].append(offset)
                if isinstance(dst, str):
                    by_bill[dst].append(offset)
                edges += 1
            offset = handle.tell()
            line = handle.readline()

    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open("w", encoding="utf-8") as out:
        for key, offsets in by_person.items():
            out.write(json.dumps({"key": key, "kind": "person", "offsets": offsets}) + "\n")
        for key, offsets in by_bill.items():
            out.write(json.dumps({"key": key, "kind": "bill", "offsets": offsets}) + "\n")
    return {"edges": edges, "persons": len(by_person), "bills": len(by_bill)}


def _vote_from_record(rec: dict[str, Any]) -> StateVote:
    attrs = rec.get("attributes") or {}
    prov = rec.get("provenance") or {}
    return StateVote(
        person_id=str(rec.get("src_id", "")),
        bill_id=str(rec.get("dst_id", "")),
        choice=str(attrs.get("choice", "")),
        motion=str(attrs.get("motion", "")),
        result=str(attrs.get("result", "")),
        vote_id=rec.get("external_key"),
        source_url=str(prov.get("source_url", "")),
        content_sha256=str(prov.get("content_sha256", "")),
        known_at=str(prov.get("known_at", "")),
        valid_from=prov.get("valid_from"),
    )


@dataclass
class StateVoteIndex:
    """Query-on-demand view: resolves an entity's vote lines by seeking the big file."""

    edges_path: Path
    person_offsets: dict[str, list[int]]
    bill_offsets: dict[str, list[int]]

    @classmethod
    def load(
        cls, index_path: Path = DEFAULT_INDEX, edges_path: Path = DEFAULT_EDGES
    ) -> "StateVoteIndex":
        person_offsets: dict[str, list[int]] = {}
        bill_offsets: dict[str, list[int]] = {}
        with index_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                rec = json.loads(line)
                target = person_offsets if rec["kind"] == "person" else bill_offsets
                target[rec["key"]] = rec["offsets"]
        return cls(edges_path=edges_path, person_offsets=person_offsets, bill_offsets=bill_offsets)

    def _read_offsets(self, offsets: list[int], limit: int | None) -> Iterator[StateVote]:
        with self.edges_path.open("rb") as handle:
            for i, off in enumerate(offsets):
                if limit is not None and i >= limit:
                    break
                handle.seek(off)
                line = handle.readline()
                if line.strip():
                    yield _vote_from_record(json.loads(line))

    def votes_for_person(self, person_id: str, *, limit: int | None = None) -> list[StateVote]:
        """All state votes cast by a canonical person, read lazily (cited)."""
        return list(self._read_offsets(self.person_offsets.get(person_id, []), limit))

    def votes_for_bill(self, bill_id: str, *, limit: int | None = None) -> list[StateVote]:
        """All state votes cast on a canonical bill, read lazily (cited)."""
        return list(self._read_offsets(self.bill_offsets.get(bill_id, []), limit))

    def vote_count_for_person(self, person_id: str) -> int:
        return len(self.person_offsets.get(person_id, []))

    def has_person(self, person_id: str) -> bool:
        return person_id in self.person_offsets


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Build the lazy on-disk state vote index")
    p.add_argument("--edges", default=str(DEFAULT_EDGES))
    p.add_argument("--index", default=str(DEFAULT_INDEX))
    args = p.parse_args(argv)
    stats = build_index(Path(args.edges), Path(args.index))
    print(
        f"indexed {stats['edges']:,} edges across {stats['persons']:,} persons "
        f"and {stats['bills']:,} bills -> {args.index}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
