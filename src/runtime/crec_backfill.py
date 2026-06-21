"""Resumable Congressional Record (CREC) floor-speech backfill -> edge feed.

The keyless CREC MODS metadata (``govinfo.gov/metadata/pkg/CREC-<date>/mods.xml``)
tags every speech with the speaking member's **bioguide id**, so floor speeches
link straight to ``canonical_person_id`` with no NER. This walks a date range
(congresses 113-119), parses each issue's speakers via the in-repo
:mod:`src.graph.ingest.congressional_record` adapter, resolves bioguides to
canonical Person IDs from the corpus, and appends ``floor_speech`` edges (one per
member per issue) to ``crec_edges.jsonl``.

Resumable: a date already represented in the feed is skipped; non-session days
(404) are skipped. The bill-number mentions in each issue's MODS are recorded on
the edge so the speech can later be tied to the bills debated that day.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

from src.graph.ingest.congressional_record import (
    bill_mentions as _normalized_bill_mentions,
)
from src.graph.ingest.congressional_record import (
    crec_package_url,
    floor_speech_edge,
    floor_speech_provenance,
    parse_crec_mods,
    speech_bill_edges,
)
from src.graph.ingest.prediction_markets import congress_for_date
from src.runtime.bill_edges_export import build_bioguide_resolver
from src.runtime.http_client import client_or_default

_BILL_MENTION_RE = re.compile(r"\b(?:H\.?\s?R\.?|S\.?|H\.?J\.?Res\.?|S\.?J\.?Res\.?)\s?(\d{1,5})\b")


def date_range(start: date, end: date) -> list[date]:
    """Every calendar date in ``[start, end]`` (inclusive)."""
    days = (end - start).days
    return [start + timedelta(days=offset) for offset in range(days + 1)]


def bill_mentions(mods_xml: str, *, limit: int = 40) -> list[str]:
    """Distinct congress bill numbers mentioned in an issue's MODS (capped).

    Kept as the raw, human-readable token list recorded on the per-issue
    ``floor_speech`` edge's ``bills_mentioned`` attribute (e.g. ``H.R.1478``).
    The *typed* member -> bill edges use the normalized identifier form
    (``hr-1478``) from :func:`speech_bill_edges`.
    """
    seen: list[str] = []
    for match in _BILL_MENTION_RE.finditer(mods_xml):
        token = match.group(0).replace(" ", "")
        if token not in seen:
            seen.append(token)
        if len(seen) >= limit:
            break
    return seen


@dataclass(frozen=True)
class CrecBackfillProgress:
    """Counts from one CREC backfill pass."""

    days_checked: int
    issues_found: int
    edges_written: int
    members_unresolved: int
    edges_total: int
    speech_bill_edges_written: int = 0


def _done_dates(path: Path) -> set[str]:
    """ISO dates already represented in the edge feed (``dst_id`` carries the date)."""
    if not path.exists():
        return set()
    dates: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            dst = str(json.loads(line)["dst_id"])
            if dst.startswith("congressional_record:"):
                dates.add(dst.split(":", 1)[1])
    return dates


def backfill_crec_speeches(
    *,
    start: date,
    end: date,
    out_path: Path | str,
    corpus_directory: Path | str,
    client: httpx.Client | None = None,
    first_observed_at: datetime | None = None,
    max_days: int | None = None,
) -> CrecBackfillProgress:
    """Append floor-speech edges for issues in ``[start, end]`` not yet in the feed."""
    out = Path(out_path)
    observed = first_observed_at if first_observed_at is not None else datetime.now(UTC)
    resolver = build_bioguide_resolver(corpus_directory)
    done = _done_dates(out)
    http, owns = client_or_default(client)
    checked = issues = written = unresolved = bill_edges_written = 0
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = (
        sum(1 for line in out.read_text().splitlines() if line.strip()) if out.exists() else 0
    )
    try:
        with out.open("a", encoding="utf-8") as handle:
            for day in date_range(start, end):
                iso = day.isoformat()
                if iso in done:
                    continue
                if max_days is not None and checked >= max_days:
                    break
                checked += 1
                resp = http.get(crec_package_url(day), follow_redirects=True)
                if resp.status_code != 200:
                    continue
                speeches = parse_crec_mods(resp.text, issue_date=day)
                if not speeches:
                    continue
                issues += 1
                mentions = bill_mentions(resp.text)
                # Normalized identifiers (e.g. "hr-1478") for the *typed* member ->
                # bill edges, resolved under the congress sitting that day.
                normalized = _normalized_bill_mentions(resp.text)
                congress = congress_for_date(day)
                sha = hashlib.sha256(resp.text.encode("utf-8")).hexdigest()
                provenance = floor_speech_provenance(
                    source_url=crec_package_url(day),
                    content_sha256=sha,
                    speech_date=day,
                    first_observed_at=observed,
                )
                for speech in speeches:
                    member = resolver.get(speech.bioguide_id.upper())
                    if member is None:
                        unresolved += 1
                        continue
                    edge = floor_speech_edge(
                        member_canonical_id=member, speech=speech, provenance=provenance
                    )
                    record = json.loads(edge.model_dump_json())
                    record["bills_mentioned"] = mentions
                    handle.write(json.dumps(record) + "\n")
                    written += 1
                    # Typed member -> bill edges: tie the floor appearance to the
                    # specific congress bills debated that day so the graph store can
                    # join speeches to bills (said-vs-voted, follow-the-money).
                    for bill_edge in speech_bill_edges(
                        member_canonical_id=member,
                        speech=speech,
                        congress=congress,
                        bill_identifiers=normalized,
                        provenance=provenance,
                    ):
                        handle.write(bill_edge.model_dump_json() + "\n")
                        bill_edges_written += 1
    finally:
        if owns:
            http.close()
    return CrecBackfillProgress(
        days_checked=checked,
        issues_found=issues,
        edges_written=written,
        members_unresolved=unresolved,
        edges_total=existing + written + bill_edges_written,
        speech_bill_edges_written=bill_edges_written,
    )


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI glue
    import argparse

    parser = argparse.ArgumentParser(description="Resumable CREC floor-speech backfill")
    parser.add_argument("--start", default="2013-01-03")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--out", default="data/exports/govinfo_bills/crec_edges.jsonl")
    parser.add_argument("--corpus", default="data/exports/contract_records")
    parser.add_argument("--max-days", type=int, default=None, help="cap session days checked")
    args = parser.parse_args(argv)
    progress = backfill_crec_speeches(
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        out_path=args.out,
        corpus_directory=args.corpus,
        max_days=args.max_days,
    )
    print(
        f"days={progress.days_checked} issues={progress.issues_found} "
        f"edges={progress.edges_written} speech_bill_edges={progress.speech_bill_edges_written} "
        f"unresolved={progress.members_unresolved} total={progress.edges_total}",
        flush=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
