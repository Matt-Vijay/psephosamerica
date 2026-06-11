"""Resumable GDELT news-mention backfill for federal members -> edge feed.

The existing ``ingest/gdelt`` adapter seeded only 75 news-mention edges. This
scales it: for each federal member in the corpus (those with a ``bioguide:``
external id), it queries the **keyless** GDELT 2.0 DOC API for the member's name,
parses the returned articles, and appends ``news_mention`` edges (member ->
``outlet:<domain>``) to ``gdelt_edges.jsonl``.

GDELT asks for one request every 5 seconds, so the runner sleeps between members
(the delay is injectable for tests). Resumable: a member already in the feed is
skipped; query/parse failures are skipped and counted. Public-record only.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from src.graph.ingest.gdelt import news_mention_edge, news_provenance, parse_gdelt_article
from src.graph.export import RECORDS_FILENAME
from src.runtime.govinfo_bills_materialize import _client

_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"


@dataclass(frozen=True)
class _Member:
    canonical_id: str
    name: str


def federal_members(corpus_directory: Path | str) -> list[_Member]:
    """Corpus persons that carry a ``bioguide:`` external id (the federal members)."""
    from src.graph.export import read_contract_corpus

    if not (Path(corpus_directory) / RECORDS_FILENAME).exists():
        return []
    members: list[_Member] = []
    for row in read_contract_corpus(corpus_directory):
        if row.entity_type == "person" and any(e.startswith("bioguide:") for e in row.external_ids):
            members.append(_Member(canonical_id=row.canonical_id, name=row.display_name))
    return members


def fetch_member_articles(
    name: str, *, client: httpx.Client, timespan: str = "2y", max_records: int = 75
) -> list[dict[str, object]]:
    """Query the keyless GDELT DOC API for a member's name -> raw article records."""
    response = client.get(
        _DOC_API,
        params={
            "query": f'"{name}"',
            "mode": "artlist",
            "format": "json",
            "maxrecords": str(max_records),
            "timespan": timespan,
        },
        follow_redirects=True,
    )
    response.raise_for_status()
    payload = response.json()
    articles = payload.get("articles", [])
    return list(articles) if isinstance(articles, list) else []


def _fetch_with_retry(
    name: str,
    *,
    client: httpx.Client,
    sleep: Callable[[float], None],
    delay_seconds: float,
    max_retries: int,
) -> list[dict[str, object]]:
    """Fetch a member's articles, backing off and retrying on GDELT 429s.

    GDELT throttles to one request every 5 seconds and answers a too-fast caller
    with HTTP 429. Treating that as a permanent skip drops the member for the
    whole pass; instead we sleep an escalating multiple of the base delay and
    retry. Non-429 errors propagate to the caller's skip handling unchanged.
    """
    for attempt in range(max_retries + 1):
        try:
            return fetch_member_articles(name, client=client)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429 and attempt < max_retries:
                sleep(delay_seconds * (attempt + 2))
                continue
            raise
    raise AssertionError("unreachable")  # pragma: no cover - loop always returns or raises


@dataclass(frozen=True)
class GdeltBackfillProgress:
    """Counts from one GDELT backfill pass."""

    members_seen: int
    members_queried: int
    edges_written: int
    members_skipped: int
    edges_total: int


def _done_members(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            ids.add(str(json.loads(line)["src_id"]))
    return ids


def backfill_gdelt_mentions(
    *,
    corpus_directory: Path | str,
    out_path: Path | str,
    client: httpx.Client | None = None,
    first_observed_at: datetime | None = None,
    sleep: Callable[[float], None] = time.sleep,
    delay_seconds: float = 5.0,
    max_members: int | None = None,
    max_retries: int = 3,
) -> GdeltBackfillProgress:
    """Append news-mention edges for federal members not yet in the feed."""
    out = Path(out_path)
    observed = first_observed_at if first_observed_at is not None else datetime.now(UTC)
    members = federal_members(corpus_directory)
    done = _done_members(out)
    http, owns = _client(client)
    seen = queried = written = skipped = 0
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = (
        sum(1 for line in out.read_text().splitlines() if line.strip()) if out.exists() else 0
    )
    try:
        with out.open("a", encoding="utf-8") as handle:
            for member in members:
                seen += 1
                if member.canonical_id in done:
                    continue
                if max_members is not None and queried >= max_members:
                    break
                if queried > 0:
                    sleep(delay_seconds)
                queried += 1
                try:
                    articles = _fetch_with_retry(
                        member.name,
                        client=http,
                        sleep=sleep,
                        delay_seconds=delay_seconds,
                        max_retries=max_retries,
                    )
                except (httpx.HTTPError, ValueError):
                    skipped += 1
                    continue
                for record in articles:
                    try:
                        article = parse_gdelt_article(record)
                    except ValueError:
                        continue
                    provenance = news_provenance(
                        article,
                        content_sha256=hashlib.sha256(article.url.encode("utf-8")).hexdigest(),
                        first_observed_at=observed,
                    )
                    edge = news_mention_edge(
                        official_canonical_id=member.canonical_id,
                        article=article,
                        provenance=provenance,
                    )
                    handle.write(edge.model_dump_json() + "\n")
                    written += 1
    finally:
        if owns:
            http.close()
    return GdeltBackfillProgress(
        members_seen=seen,
        members_queried=queried,
        edges_written=written,
        members_skipped=skipped,
        edges_total=existing + written,
    )


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI glue
    import argparse

    parser = argparse.ArgumentParser(description="Resumable GDELT news-mention backfill")
    parser.add_argument("--corpus", default="data/exports/contract_records")
    parser.add_argument("--out", default="data/exports/govinfo_bills/gdelt_edges.jsonl")
    parser.add_argument("--delay", type=float, default=5.0)
    args = parser.parse_args(argv)
    progress = backfill_gdelt_mentions(
        corpus_directory=args.corpus, out_path=args.out, delay_seconds=args.delay
    )
    print(
        f"members={progress.members_queried} edges={progress.edges_written} "
        f"skipped={progress.members_skipped} total={progress.edges_total}",
        flush=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
