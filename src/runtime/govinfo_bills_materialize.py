"""Materialize vote-linkable embedded bills from keyless govinfo BILLSTATUS bulk.

govinfo serves a JSON directory listing per ``(congress, bill type)`` at
``www.govinfo.gov/bulkdata/json/BILLSTATUS/<congress>/<type>`` (no key), each
entry linking a BILLSTATUS XML file. This runner lists and fetches those files,
parses each via :mod:`src.graph.ingest.govinfo_billstatus`, and returns pending
bill contract rows whose canonical ids already match the roll-call ``vote``
edges -- so :func:`~src.graph.regenerate.regenerate_corpus` enriches them into
dense, vote-linkable bill embeddings.

Network I/O lives here (the orchestration layer); the parse + row building stays
pure in the graph layer. Public-record only, keyless, robots-respecting; a
per-file fetch/parse failure is skipped and counted, never fabricated.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

from src.graph.contracts import EntityResolutionOutput
from src.graph.export import (
    CorpusManifest,
    read_contract_corpus,
    write_contract_corpus,
    write_delta_feed,
)
from src.graph.ingest.govinfo_billstatus import billstatus_bill_row, parse_billstatus_xml
from src.graph.regenerate import regenerate_corpus

_USER_AGENT = "openpact-research/0.1 (public-record bill ingest)"
_BULK = "https://www.govinfo.gov/bulkdata"
# House + Senate bill + (joint/concurrent/simple) resolution types.
BILLSTATUS_BILL_TYPES: tuple[str, ...] = (
    "hr",
    "s",
    "hjres",
    "sjres",
    "hconres",
    "sconres",
    "hres",
    "sres",
)


def billstatus_listing_url(congress: int, bill_type: str) -> str:
    """The keyless JSON directory-listing URL for a congress + bill type."""
    return f"{_BULK}/json/BILLSTATUS/{congress}/{bill_type}"


@dataclass(frozen=True)
class FetchReport:
    """Counts from one materialize pass (for the #1 success report)."""

    congress: int
    listed: int = 0
    fetched: int = 0
    parsed: int = 0
    skipped: int = 0
    bill_types: tuple[str, ...] = ()
    errors: list[str] = field(default_factory=list)


def _client(client: httpx.Client | None) -> tuple[httpx.Client, bool]:
    if client is not None:
        return client, False
    return httpx.Client(timeout=60.0, headers={"User-Agent": _USER_AGENT}), True


def list_billstatus_file_urls(
    congress: int, bill_type: str, *, client: httpx.Client | None = None
) -> list[str]:
    """List the BILLSTATUS XML file URLs for a congress + bill type (keyless JSON)."""
    http, owns = _client(client)
    try:
        response = http.get(
            billstatus_listing_url(congress, bill_type),
            headers={"Accept": "application/json"},
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
    finally:
        if owns:
            http.close()
    urls: list[str] = []
    for entry in payload.get("files", []):
        name = str(entry.get("name", ""))
        link = entry.get("link")
        if link and name.lower().endswith(".xml") and name.upper().startswith("BILLSTATUS-"):
            urls.append(str(link))
    return urls


def fetch_billstatus_rows(
    congress: int,
    *,
    bill_types: Iterable[str] = BILLSTATUS_BILL_TYPES,
    client: httpx.Client | None = None,
    first_observed_at: datetime | None = None,
    limit: int | None = None,
) -> tuple[list[EntityResolutionOutput], FetchReport]:
    """Fetch + parse BILLSTATUS bills for a congress into pending bill rows.

    ``limit`` caps the number of bills fetched (across types) for bounded runs;
    ``None`` fetches all listed. Per-file errors are skipped and counted.
    """
    observed = first_observed_at if first_observed_at is not None else datetime.now(UTC)
    http, owns = _client(client)
    types = tuple(bill_types)
    rows: list[EntityResolutionOutput] = []
    listed = fetched = parsed = skipped = 0
    errors: list[str] = []
    try:
        for bill_type in types:
            for url in list_billstatus_file_urls(congress, bill_type, client=http):
                listed += 1
                if limit is not None and parsed >= limit:
                    continue
                try:
                    resp = http.get(url, follow_redirects=True)
                    resp.raise_for_status()
                    fetched += 1
                    xml = resp.text
                    status = parse_billstatus_xml(xml)
                    sha = hashlib.sha256(xml.encode("utf-8")).hexdigest()
                    rows.append(
                        billstatus_bill_row(
                            status,
                            source_url=url,
                            content_sha256=sha,
                            first_observed_at=observed,
                        )
                    )
                    parsed += 1
                except (httpx.HTTPError, ValueError) as exc:
                    skipped += 1
                    errors.append(f"{url}: {type(exc).__name__}")
    finally:
        if owns:
            http.close()
    return rows, FetchReport(
        congress=congress,
        listed=listed,
        fetched=fetched,
        parsed=parsed,
        skipped=skipped,
        bill_types=types,
        errors=errors,
    )


@dataclass(frozen=True)
class BillCorpusResult:
    """The outcome of one bill-corpus materialize+export pass."""

    manifest: CorpusManifest
    bill_count: int
    delta_count: int
    fetch_reports: tuple[FetchReport, ...]


def materialize_govinfo_bill_corpus(
    *,
    congresses: Iterable[int],
    directory: Path | str,
    as_of: datetime,
    bill_types: Iterable[str] = BILLSTATUS_BILL_TYPES,
    client: httpx.Client | None = None,
    first_observed_at: datetime | None = None,
    limit_per_congress: int | None = None,
) -> BillCorpusResult:
    """Fetch BILLSTATUS bills, enrich to dense rows, export corpus + delta-CDC.

    Bills are written to ``directory`` (``records.jsonl`` + ``manifest.json``) and
    a CDC ``deltas.jsonl`` is appended by diffing against the corpus already at
    ``directory`` -- so a re-run with no new bills emits zero deltas, and new
    bills surface as ``created`` deltas Track B tails to hot-swap. Person rows are
    out of scope here (bill-only export); ``regenerate_corpus`` still produces the
    dense ``dossier_embedding`` + ``structural_embedding`` for every bill.
    """
    out_dir = Path(directory)
    types = tuple(bill_types)
    http, owns = _client(client)
    rows: list[EntityResolutionOutput] = []
    reports: list[FetchReport] = []
    try:
        for congress in congresses:
            congress_rows, report = fetch_billstatus_rows(
                congress,
                bill_types=types,
                client=http,
                first_observed_at=first_observed_at,
                limit=limit_per_congress,
            )
            rows.extend(congress_rows)
            reports.append(report)
    finally:
        if owns:
            http.close()

    prior: Mapping[str, EntityResolutionOutput] = {}
    if (out_dir / "records.jsonl").exists():
        prior = {row.canonical_id: row for row in read_contract_corpus(out_dir)}

    result = regenerate_corpus(
        person_records=[],
        bill_outputs=rows,
        edges=[],
        as_of=as_of,
        prior_outputs=prior,
    )
    manifest = write_contract_corpus(result.rows, directory=out_dir, as_of=as_of)
    delta_count = write_delta_feed(result.deltas, path=out_dir / "deltas.jsonl", append=True)
    return BillCorpusResult(
        manifest=manifest,
        bill_count=len(result.rows),
        delta_count=delta_count,
        fetch_reports=tuple(reports),
    )
