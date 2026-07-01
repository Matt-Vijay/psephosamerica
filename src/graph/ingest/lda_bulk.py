"""Load Senate LDA lobbying disclosures from the **bulk** download archives.

The keyless ``lda.senate.gov/api/v1`` REST API hard-caps ``page_size`` at 25
(~3,900 pages/year), so a comprehensive backfill through it is impractical
(:mod:`src.graph.ingest.lda_export` stalled mid-2016 this way). The Senate
Office of Public Records historically published the *same* disclosures as bulk
quarterly archives — one ``<year>_<quarter>.zip`` of UTF-16 XML, each ``<Filing>``
element carrying the registrant, client, dollar amount, and lobbying-activity
issue lines.

That legacy host (``soprweb.senate.gov/downloads/``) was retired and the modern
LDA.gov site exposes **no** public bulk file (only the slow API), but the
Internet Archive's Wayback Machine has the original ``application/zip`` bytes
captured (``.../downloads/<year>_<quarter>.zip``), which is a public,
no-login bulk source. This module:

* maps one bulk ``<Filing>`` XML element into the **same** API-shaped dict the
  pure adapter in :mod:`src.graph.ingest.lda` already consumes
  (:func:`bulk_filing_to_record`), so registrants/clients resolve to the same
  canonical orgs and the same ``lobbying_retention`` + ``lobbying_contact``
  edges, leakage-safe (``known_at`` = the filing's ``Received`` timestamp);
* streams those records out of a downloaded ZIP (:func:`iter_bulk_records`);
* downloads the available Wayback-archived quarter ZIPs into a raw cache
  (:func:`fetch_bulk_archives`); and
* merges the bulk filings with the existing ``data/exports/lda/`` output,
  de-duplicating by filing UUID, into the combined lobbying sidecar + CDC delta
  feed + ``ingest_meta.json`` with per-year counts
  (:func:`merge_bulk_into_export`).

The XML schema (one ``<Filing>``)::

    <Filing ID="..." Year="2020" Received="2020-10-25T08:12:34.537"
            Amount="100000" Type="THIRD QUARTER" Period="...">
      <Registrant RegistrantID="..." RegistrantName="..." RegistrantCountry="USA"/>
      <Client ClientID="..." ClientName="..." ClientState="CALIFORNIA"/>
      <Lobbyists>...</Lobbyists>
      <Issues>
        <Issue Code="MEDICAL/DISEASE RESEARCH/CLINICAL LABS" SpecificIssue="...H.R. 748..."/>
      </Issues>
    </Filing>

``Issue/@Code`` is the full issue-area *name* (the API exposes a short code);
the adapter treats it as an opaque issue label and mines bill references out of
``SpecificIssue`` exactly as it does the API's ``description`` field, so no
field is fabricated.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# The legacy SOPR bulk host the Wayback Machine captured. We fetch the *original*
# archived bytes via the ``id_`` (identity) Wayback modifier so the ZIP is the
# raw upstream file, not a rewritten HTML wrapper.
SOPR_DOWNLOAD_BASE = "https://soprweb.senate.gov/downloads"
WAYBACK_BASE = "https://web.archive.org/web"
SOURCE_DESCRIPTION = (
    "Senate LDA bulk quarterly XML archives (soprweb.senate.gov/downloads/"
    "<year>_<quarter>.zip) recovered from the Internet Archive Wayback Machine; "
    "the legacy bulk host was retired and the modern LDA.gov exposes no public "
    "bulk file, only the page-25-capped REST API."
)


def _opt(value: str | None) -> str | None:
    """Trim a bulk XML attribute to ``None`` when empty/whitespace."""
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def _received_to_iso(received: str) -> str | None:
    """Stamp the bulk ``Received`` timestamp with its US/Eastern offset.

    The bulk archive records ``Received`` as a *naive* local timestamp on the
    Senate filing system's clock (US/Eastern), whereas the REST API returns an
    offset-aware ``dt_posted`` and the provenance envelope *requires* a
    timezone-aware ``known_at``. We attach the correct Eastern offset (DST-aware)
    for the filing's own date so ``known_at`` is leakage-safe and comparable to
    the API rows, rather than guessing a fixed offset. Unparseable timestamps
    return ``None`` (the filing is then skipped, never fabricated).
    """
    from zoneinfo import ZoneInfo

    try:
        naive = datetime.fromisoformat(received)
    except ValueError:
        return None
    if naive.tzinfo is not None:
        return naive.isoformat()
    eastern = naive.replace(tzinfo=ZoneInfo("America/New_York"))
    return eastern.isoformat()


def bulk_filing_to_record(filing: ET.Element) -> dict[str, Any] | None:
    """Map one bulk ``<Filing>`` element into the API-shaped filing dict.

    Returns ``None`` for a filing missing its UUID or its registrant/client ids
    (nothing to link) so the caller can skip it; never fabricates a field. The
    output mirrors the REST API record shape that :func:`parse_lda_filing` /
    :func:`parse_lda_activities` already parse:

    * ``filing_uuid`` from ``@ID`` (lowercased to match the API's UUID casing);
    * ``dt_posted`` from ``@Received`` (the public-disclosure timestamp);
    * ``filing_year`` / ``filing_type_display`` from ``@Year`` / ``@Type``;
    * ``income`` from ``@Amount`` (the bulk file carries a single dollar amount;
      ``expenses`` stays ``None`` — the retention edge records whichever is set);
    * ``client`` / ``registrant`` ``{id, name, state}`` blocks; and
    * ``lobbying_activities`` ``[{general_issue_area_code, description}]`` from
      each ``<Issue Code=... SpecificIssue=.../>``.
    """
    uuid = _opt(filing.get("ID"))
    if not uuid:
        return None
    registrant = filing.find("Registrant")
    client = filing.find("Client")
    if registrant is None or client is None:
        return None
    registrant_id = _opt(registrant.get("RegistrantID"))
    client_id = _opt(client.get("ClientID"))
    if not registrant_id or not client_id:
        return None

    received_raw = _opt(filing.get("Received"))
    if not received_raw:
        return None
    received = _received_to_iso(received_raw)
    if received is None:
        return None

    activities: list[dict[str, Any]] = []
    issues = filing.find("Issues")
    if issues is not None:
        for issue in issues.findall("Issue"):
            activities.append(
                {
                    "general_issue_area_code": _opt(issue.get("Code")) or "",
                    "description": _opt(issue.get("SpecificIssue")),
                }
            )

    return {
        "filing_uuid": uuid.lower(),
        "filing_year": _opt(filing.get("Year")),
        "filing_type_display": _opt(filing.get("Type")) or "",
        "dt_posted": received,
        "income": _opt(filing.get("Amount")),
        "expenses": None,
        "registrant": {
            "id": registrant_id,
            "name": _opt(registrant.get("RegistrantName")),
            # Bulk registrants carry only a country, never a state.
            "state": None,
        },
        "client": {
            "id": client_id,
            "name": _opt(client.get("ClientName")),
            "state": _opt(client.get("ClientState")),
        },
        "lobbying_activities": activities,
    }


def iter_bulk_records(zip_path: Path | str) -> Iterator[dict[str, Any]]:
    """Stream API-shaped filing records out of one bulk quarter ZIP.

    Each ZIP holds many UTF-16 XML members (``<year>_<quarter>_<n>_<m>.xml``),
    every member a ``<PublicFilings>`` root of ``<Filing>`` elements. Members are
    parsed incrementally (``iterparse``) so a multi-megabyte shard never loads
    whole, and malformed members are skipped rather than aborting the archive.
    """
    path = Path(zip_path)
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.lower().endswith(".xml"):
                continue
            try:
                with archive.open(name) as member:
                    for _event, elem in ET.iterparse(member):
                        if elem.tag != "Filing":
                            continue
                        record = bulk_filing_to_record(elem)
                        elem.clear()
                        if record is not None:
                            yield record
            except (ET.ParseError, zipfile.BadZipFile):
                continue


@dataclass(frozen=True)
class BulkArchiveRef:
    """One bulk quarter archive: its (year, quarter) and Wayback snapshot stamp."""

    year: int
    quarter: int
    wayback_timestamp: str

    @property
    def basename(self) -> str:
        return f"{self.year}_{self.quarter}.zip"

    @property
    def wayback_url(self) -> str:
        # The ``id_`` modifier returns the original archived bytes (no HTML rewrap).
        return f"{WAYBACK_BASE}/{self.wayback_timestamp}id_/{SOPR_DOWNLOAD_BASE}/{self.basename}"


def fetch_bulk_archives(
    archives: Iterable[BulkArchiveRef],
    *,
    raw_directory: Path | str,
    client: Any | None = None,
) -> list[Path]:  # pragma: no cover - network I/O
    """Download each bulk quarter ZIP from the Wayback Machine into ``raw_directory``.

    Skips a quarter already present on disk (idempotent / resumable). Returns the
    paths of the ZIPs available locally after the run.
    """
    import httpx

    raw_dir = Path(raw_directory)
    raw_dir.mkdir(parents=True, exist_ok=True)
    http = client if client is not None else httpx.Client(timeout=300.0, follow_redirects=True)
    owns = client is None
    headers = {"User-Agent": "psephosamerica-research (brianfeng31@gmail.com)"}
    paths: list[Path] = []
    try:
        for ref in archives:
            dest = raw_dir / ref.basename
            if dest.exists() and dest.stat().st_size > 0:
                paths.append(dest)
                continue
            resp = http.get(ref.wayback_url, headers=headers)
            if resp.status_code != 200 or not resp.content:
                continue
            dest.write_bytes(resp.content)
            paths.append(dest)
    finally:
        if owns:
            http.close()
    return paths


def _existing_filing_uuids(edges_path: Path) -> set[str]:
    """Filing UUIDs already represented in an existing lobbying-edges sidecar.

    Every edge's ``external_key`` is the filing UUID (retention) or
    ``<uuid>:<bill>:<issue>`` (contact); the leading UUID is the dedup key, so a
    bulk filing already ingested via the API is not double-counted.
    """
    uuids: set[str] = set()
    if not edges_path.exists():
        return uuids
    with edges_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                key = json.loads(line).get("external_key") or ""
            except json.JSONDecodeError:
                continue
            uuid = key.split(":", 1)[0]
            if uuid:
                uuids.add(uuid)
    return uuids


@dataclass(frozen=True)
class BulkMergeReport:
    """Counts from merging bulk filings into the existing LDA export."""

    source: str
    as_of: str
    archives_used: list[str]
    bulk_filings_scanned: int
    bulk_filings_new: int
    bulk_filings_duplicate: int
    registrants: int
    clients: int
    retention_edges: int
    bill_lobbying_edges: int
    deltas_written: int
    per_year: list[dict[str, Any]]


def _read_existing_edges(edges_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not edges_path.exists():
        return rows
    with edges_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def merge_bulk_into_export(
    *,
    zip_paths: Iterable[Path],
    out_directory: Path | str,
    as_of: datetime | None = None,
    archives_used: list[str] | None = None,
) -> BulkMergeReport:
    """Merge bulk-archive filings into the existing ``data/exports/lda/`` output.

    Reads the bulk ZIPs, drops any filing whose UUID is already in the existing
    ``lobbying_edges.jsonl`` (so the API-sampled 2025 rows aren't double-counted),
    runs the *new* filings through the same pure :func:`build_lobbying_graph`, and
    writes the **union** corpus + lobbying sidecar + a full CDC delta feed, plus an
    ``ingest_meta.json`` with the bulk source, per-year filing/edge counts, and run
    totals. The org corpus is rebuilt from the union of bulk + existing source
    anchors so canonical entities dedup globally.
    """
    from src.graph.cdc import diff_outputs
    from src.graph.export import (
        DELTAS_FILENAME,
        RECORDS_FILENAME,
        read_contract_corpus,
        write_contract_corpus,
        write_delta_feed,
    )
    from src.graph.ingest.lda_export import (
        INGEST_META_FILENAME,
        LOBBYING_EDGES_FILENAME,
        build_lobbying_graph,
        year_coverage_stats,
    )

    out_dir = Path(out_directory)
    out_dir.mkdir(parents=True, exist_ok=True)
    observed = as_of if as_of is not None else datetime.now(tz=UTC)

    edges_path = out_dir / LOBBYING_EDGES_FILENAME
    seen_uuids = _existing_filing_uuids(edges_path)

    new_records: list[dict[str, Any]] = []
    scanned = duplicate = 0
    per_year_records: dict[int, list[dict[str, Any]]] = {}
    for zip_path in zip_paths:
        for record in iter_bulk_records(zip_path):
            scanned += 1
            uuid = record.get("filing_uuid") or ""
            if uuid in seen_uuids:
                duplicate += 1
                continue
            seen_uuids.add(uuid)
            new_records.append(record)
            try:
                year = int(record.get("filing_year") or 0)
            except (TypeError, ValueError):
                year = 0
            per_year_records.setdefault(year, []).append(record)

    rows, edges, _scanned2, _skipped = build_lobbying_graph(new_records, first_observed_at=observed)

    # Union the new bulk org corpus with the existing exported corpus, keyed by
    # canonical id, so a registrant/client present in both stays one row.
    existing_rows = {row.canonical_id: row for row in read_contract_corpus(out_dir)}
    merged_rows = dict(existing_rows)
    for row in rows:
        merged_rows.setdefault(row.canonical_id, row)
    all_rows = list(merged_rows.values())

    write_contract_corpus(all_rows, directory=out_dir, as_of=observed)
    assert (out_dir / RECORDS_FILENAME).exists()
    deltas = diff_outputs({}, merged_rows)
    deltas_written = write_delta_feed(deltas, path=out_dir / DELTAS_FILENAME, append=False)

    # Union the new bulk edges with the existing sidecar, de-duped by the edge's
    # natural identity (its model_dump_json line), bulk filings now folded in.
    existing_edge_lines = [
        json.dumps(row, sort_keys=True) for row in _read_existing_edges(edges_path)
    ]
    new_edge_lines = [
        json.dumps(json.loads(edge.model_dump_json()), sort_keys=True) for edge in edges
    ]
    all_edge_lines = sorted(set(existing_edge_lines) | set(new_edge_lines))
    with edges_path.open("w", encoding="utf-8") as handle:
        for line in all_edge_lines:
            handle.write(line + "\n")

    per_year = _per_year_report(per_year_records, edges, year_coverage_stats)

    registrants = sum(
        1 for r in rows if any(k.startswith("senate_lda_registrant:") for k in r.external_ids)
    )
    clients = sum(
        1 for r in rows if any(k.startswith("senate_lda_client:") for k in r.external_ids)
    )
    retention = sum(1 for e in edges if e.edge_type == "lobbying_retention")
    contacts = sum(1 for e in edges if e.edge_type == "lobbying_contact")

    report = BulkMergeReport(
        source=SOURCE_DESCRIPTION,
        as_of=observed.isoformat(),
        archives_used=sorted(archives_used or []),
        bulk_filings_scanned=scanned,
        bulk_filings_new=len(new_records),
        bulk_filings_duplicate=duplicate,
        registrants=registrants,
        clients=clients,
        retention_edges=retention,
        bill_lobbying_edges=contacts,
        deltas_written=deltas_written,
        per_year=per_year,
    )
    (out_dir / INGEST_META_FILENAME).write_text(
        json.dumps(asdict(report), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def _per_year_report(
    per_year_records: dict[int, list[dict[str, Any]]],
    edges: Any,
    year_coverage_stats: Any,
) -> list[dict[str, Any]]:
    """Per-year filing coverage + bulk edge counts, sorted by year."""
    retention_by_year: dict[int, int] = {}
    contact_by_year: dict[int, int] = {}
    uuid_year: dict[str, int] = {}
    for year, records in per_year_records.items():
        for record in records:
            uuid_year[record.get("filing_uuid") or ""] = year
    for edge in edges:
        uuid = (edge.external_key or "").split(":", 1)[0]
        edge_year = uuid_year.get(uuid)
        if edge_year is None:
            continue
        if edge.edge_type == "lobbying_retention":
            retention_by_year[edge_year] = retention_by_year.get(edge_year, 0) + 1
        elif edge.edge_type == "lobbying_contact":
            contact_by_year[edge_year] = contact_by_year.get(edge_year, 0) + 1

    reports: list[dict[str, Any]] = []
    for year in sorted(per_year_records):
        coverage = year_coverage_stats(per_year_records[year], year=year)
        coverage["retention_edges"] = retention_by_year.get(year, 0)
        coverage["bill_lobbying_edges"] = contact_by_year.get(year, 0)
        reports.append(coverage)
    return reports


# Quarters known to have Wayback-captured bulk ZIPs (latest snapshot per quarter
# is discovered at runtime by --discover; this default is filled from a probe).
def discover_wayback_archives(
    years: Iterable[int],
    *,
    client: Any | None = None,
) -> list[BulkArchiveRef]:  # pragma: no cover - network I/O
    """Probe the Wayback CDX index for the latest captured ZIP per (year, quarter).

    Returns one :class:`BulkArchiveRef` per quarter that has at least one
    ``statuscode:200`` ``application/zip`` capture, using the most recent snapshot
    (the closest-to-retirement, hence most complete, copy of that quarter).
    """
    from concurrent.futures import ThreadPoolExecutor

    import httpx

    # The Wayback CDX index throttles each query to ~10-30s, so 50+ serial probes
    # take >10 minutes. A small thread pool over independent (year, quarter)
    # queries cuts that to roughly one query's latency.
    http = client if client is not None else httpx.Client(timeout=120.0)
    owns = client is None

    def _probe(year: int, quarter: int) -> BulkArchiveRef | None:
        cdx = (
            "https://web.archive.org/cdx/search/cdx"
            f"?url={SOPR_DOWNLOAD_BASE.replace('https://', '')}/{year}_{quarter}.zip"
            "&output=json&filter=statuscode:200&filter=mimetype:application/zip&limit=-1"
        )
        try:
            resp = http.get(cdx)
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        try:
            rows = resp.json()
        except ValueError:
            return None
        if not rows:
            return None
        body = rows[1:] if rows and rows[0] and rows[0][0] == "urlkey" else rows
        if not body:
            return None
        return BulkArchiveRef(year=year, quarter=quarter, wayback_timestamp=str(body[-1][1]))

    quarters = [(year, q) for year in sorted(set(years)) for q in (1, 2, 3, 4)]
    refs: list[BulkArchiveRef] = []
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for ref in pool.map(lambda yq: _probe(*yq), quarters):
                if ref is not None:
                    refs.append(ref)
    finally:
        if owns:
            http.close()
    return sorted(refs, key=lambda r: (r.year, r.quarter))


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI glue
    import argparse

    parser = argparse.ArgumentParser(
        description="Backfill Senate LDA lobbying via the bulk (Wayback) archives"
    )
    parser.add_argument("--out", default="data/exports/lda")
    parser.add_argument("--raw", default="data/raw/lda_bulk")
    parser.add_argument(
        "--years", default="2016-2020", help="comma-separated or START-END filing years"
    )
    args = parser.parse_args(argv)

    if "-" in args.years and "," not in args.years:
        start, end = (int(p) for p in args.years.split("-", 1))
        years = list(range(start, end + 1))
    else:
        years = [int(p) for p in args.years.split(",")]

    refs = discover_wayback_archives(years)
    if not refs:
        print(
            "LDA bulk: NO public Wayback-captured bulk ZIP found for "
            f"{years}; leaving existing export as-is (no slow-API grind).",
            flush=True,
        )
        return 0
    zip_paths = fetch_bulk_archives(refs, raw_directory=args.raw)
    report = merge_bulk_into_export(
        zip_paths=zip_paths,
        out_directory=args.out,
        archives_used=[r.basename for r in refs],
    )
    print(
        f"LDA bulk: archives={len(report.archives_used)} "
        f"scanned={report.bulk_filings_scanned} new={report.bulk_filings_new} "
        f"dup={report.bulk_filings_duplicate} registrants={report.registrants} "
        f"clients={report.clients} retention={report.retention_edges} "
        f"bill_edges={report.bill_lobbying_edges}",
        flush=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
