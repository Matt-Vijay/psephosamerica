"""Export bill graph edges from the content sidecar to a JSONL edge feed.

The contract corpus is nodes-only; this persists the bill **edges** the directive
asks for to ``bill_edges.jsonl`` (one ``GraphEdge`` per line), so a graph loader
(or Track B) can consume them. From each content-sidecar record it emits:

* **classification** edges (``bill -> policy_area`` / ``-> legislative_subject``)
  -- a real CRS topic for every bill, straight from the sidecar's
  ``policy_area`` + ``subjects`` (works on partial records too).
* **sponsorship** + **committee referral** edges when the record carries the full
  BillStatus fields (sponsors / cosponsors / committees) -- members resolved to
  canonical Person IDs via the corpus's ``bioguide:`` external ids.

Resumable: a bill whose edges are already in the feed is skipped. Leakage-gated
``known_at`` is the bill's introduced day when known, else the observation time.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from src.graph.edges import GraphEdge
from src.graph.ingest.billstatus_edges import (
    classification_edges_from_fields,
    committee_referral_edges,
    sponsorship_edges,
)
from src.graph.export import RECORDS_FILENAME
from src.graph.ingest.govinfo_billstatus import billstatus_from_record, bulk_billstatus_url
from src.graph.provenance import ProvenanceEnvelope

_BULK_ROOT = "https://www.govinfo.gov/bulkdata/BILLSTATUS"


def build_bioguide_resolver(corpus_directory: Path | str) -> dict[str, str]:
    """``{bioguide_id: canonical_person_id}`` from the corpus's person rows."""
    from src.graph.export import read_contract_corpus

    resolver: dict[str, str] = {}
    if not (Path(corpus_directory) / RECORDS_FILENAME).exists():
        return resolver
    for row in read_contract_corpus(corpus_directory):
        if row.entity_type != "person":
            continue
        for external in row.external_ids:
            if external.startswith("bioguide:"):
                resolver[external.split(":", 1)[1].upper()] = row.canonical_id
    return resolver


def _provenance(record: Mapping[str, Any], *, first_observed_at: datetime) -> ProvenanceEnvelope:
    text = str(record.get("text") or record["canonical_id"])
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    introduced_raw = record.get("introduced_date")
    introduced = date.fromisoformat(introduced_raw) if introduced_raw else None
    if introduced is not None:
        known = datetime(introduced.year, introduced.month, introduced.day, tzinfo=UTC)
        valid_from = introduced
    else:
        known = first_observed_at
        valid_from = first_observed_at.date()
    congress, bill_type = record.get("congress"), record.get("bill_type")
    source_url = (
        bulk_billstatus_url(int(congress), str(bill_type)) if congress and bill_type else _BULK_ROOT
    )
    return ProvenanceEnvelope(
        source_url=source_url,
        content_sha256=sha,
        first_observed_at=first_observed_at,
        valid_from=valid_from,
        known_at=known,
    )


def edges_for_record(
    record: Mapping[str, Any],
    *,
    resolve_bioguide: dict[str, str],
    first_observed_at: datetime,
) -> list[GraphEdge]:
    """All graph edges derivable from one content-sidecar record."""
    provenance = _provenance(record, first_observed_at=first_observed_at)
    edges = classification_edges_from_fields(
        str(record["canonical_id"]),
        record.get("policy_area"),
        record.get("subjects", []),
        provenance=provenance,
    )
    # Sponsor/cosponsor + committee edges need the full BillStatus fields.
    if record.get("congress") and record.get("bill_type") and record.get("number"):
        status = billstatus_from_record(dict(record))
        edges += sponsorship_edges(
            status,
            resolve_bioguide=resolve_bioguide.get,
            source_url=provenance.source_url,
            content_sha256=provenance.content_sha256,
            first_observed_at=first_observed_at,
        )
        edges += committee_referral_edges(status, provenance=provenance)
    return edges


@dataclass(frozen=True)
class EdgeExportProgress:
    """Counts from one edge-export pass."""

    bills_seen: int
    bills_new: int
    edges_written: int
    by_type: dict[str, int]
    total_edges: int


def _existing_bill_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            ids.add(str(json.loads(line)["src_id"]))
    return ids


def export_bill_edges(
    *,
    content_sidecar: Path | str,
    corpus_directory: Path | str,
    out_path: Path | str,
    first_observed_at: datetime,
    max_bills: int | None = None,
) -> EdgeExportProgress:
    """Emit edges for sidecar bills not yet in ``out_path`` (resumable, append)."""
    sidecar = Path(content_sidecar)
    out = Path(out_path)
    resolver = build_bioguide_resolver(corpus_directory)
    done = _existing_bill_ids(out)

    by_type: dict[str, int] = {}
    written = seen = new = 0
    out.parent.mkdir(parents=True, exist_ok=True)
    existing_total = (
        sum(1 for line in out.read_text().splitlines() if line.strip()) if out.exists() else 0
    )
    with out.open("a", encoding="utf-8") as handle:
        for line in sidecar.read_text(encoding="utf-8").splitlines() if sidecar.exists() else []:
            if not line.strip():
                continue
            record = json.loads(line)
            seen += 1
            if record["canonical_id"] in done:
                continue
            if max_bills is not None and new >= max_bills:
                break
            new += 1
            for edge in edges_for_record(
                record, resolve_bioguide=resolver, first_observed_at=first_observed_at
            ):
                handle.write(edge.model_dump_json() + "\n")
                written += 1
                by_type[edge.edge_type] = by_type.get(edge.edge_type, 0) + 1
    return EdgeExportProgress(
        bills_seen=seen,
        bills_new=new,
        edges_written=written,
        by_type=by_type,
        total_edges=existing_total + written,
    )


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI glue
    import argparse

    parser = argparse.ArgumentParser(description="Export bill graph edges from the content sidecar")
    parser.add_argument("--content", default="data/exports/govinfo_bills/bill_content.jsonl")
    parser.add_argument("--corpus", default="data/exports/contract_records")
    parser.add_argument("--out", default="data/exports/govinfo_bills/bill_edges.jsonl")
    args = parser.parse_args(argv)
    progress = export_bill_edges(
        content_sidecar=args.content,
        corpus_directory=args.corpus,
        out_path=args.out,
        first_observed_at=datetime.now(UTC),
    )
    print(
        f"bills_new={progress.bills_new} edges_written={progress.edges_written} "
        f"by_type={progress.by_type} total={progress.total_edges}",
        flush=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
