"""Source-coverage registry: per-jurisdiction-tier, per-source demonstrated counts.

A single structured record of every source Track A ingests, the jurisdiction tier
it covers, what it produces, how it's accessed (keyless / local bulk /
credential-gated), and the count demonstrated on a real run. :func:`render_markdown`
renders it to the table published at ``docs/coverage-snapshot.md``; a test pins
the committed doc to this registry so they never drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Tier = Literal["federal", "state", "county", "city", "cross-cutting", "enrichment"]
Access = Literal["keyless", "local-bulk", "credential", "derived"]


@dataclass(frozen=True)
class CoverageEntry:
    """One ingested source and the coverage demonstrated for it."""

    source: str
    tier: Tier
    produces: str
    access: Access
    demonstrated: str  # human-readable real count from a run


COVERAGE: tuple[CoverageEntry, ...] = (
    # ── federal ──
    CoverageEntry(
        "House Clerk roll-call votes",
        "federal",
        "Person IDs + vote edges",
        "keyless",
        "440 members, 17,045 edges",
    ),
    CoverageEntry(
        "Senate.gov roll-call votes",
        "federal",
        "Person IDs + vote edges",
        "keyless",
        "100 senators, 4,100 edges",
    ),
    CoverageEntry(
        "FEC committee master (cm.txt)", "federal", "Org IDs", "local-bulk", "20,941 committees"
    ),
    CoverageEntry(
        "FEC contributions (itcont.txt)",
        "federal",
        "donation edges",
        "local-bulk",
        "162,723 edges ($14M)",
    ),
    CoverageEntry(
        "FEC candidate-committee linkage (ccl.txt)",
        "federal",
        "Person<->Org edges",
        "local-bulk",
        "624 affiliation edges",
    ),
    CoverageEntry("FEC member crosswalk", "federal", "Person IDs", "local-bulk", "535 members"),
    CoverageEntry(
        "Senate LDA federal lobbying", "federal", "Org IDs + lobbying edges", "keyless", "248 orgs"
    ),
    CoverageEntry(
        "Congressional Record (CREC) floor speeches",
        "federal",
        "floor_speech edges (bioguide-linked)",
        "keyless",
        "2,762 edges / 537 members",
    ),
    # ── state ──
    CoverageEntry(
        "OpenStates people (all 50 states)", "state", "Person IDs", "keyless", "7,359 legislators"
    ),
    # ── county ──
    CoverageEntry(
        "Legistar county clients",
        "county",
        "Person IDs + bills + votes",
        "keyless",
        "21 counties (in 24,368 officials)",
    ),
    # ── city ──
    CoverageEntry(
        "Legistar city clients (officials)",
        "city",
        "Person IDs",
        "keyless",
        "in 24,368 municipal officials",
    ),
    CoverageEntry(
        "Legistar matters (municipal bills)", "city", "Bill IDs", "keyless", "2,000 bills"
    ),
    CoverageEntry(
        "Legistar event-item votes", "city", "vote edges", "keyless", "1,161 municipal vote edges"
    ),
    CoverageEntry(
        "Legistar registry (cities + counties)",
        "city",
        "Person IDs across governments",
        "keyless",
        "124 governments (24,368 officials)",
    ),
    CoverageEntry(
        "Text-PDF municipal minutes (pdfplumber)",
        "city",
        "minutes text + official links",
        "keyless",
        "text-layer PDFs (image-only = OCR gap)",
    ),
    # ── cross-cutting ──
    CoverageEntry(
        "GDELT news", "cross-cutting", "news-mention edges", "keyless", "75 edges / 56 outlets"
    ),
    CoverageEntry(
        "Bluesky public posts", "cross-cutting", "social-post edges", "keyless", "78 posts"
    ),
    CoverageEntry(
        "Wayback CDX (campaign sites)",
        "cross-cutting",
        "snapshot edges",
        "keyless",
        "363 snapshots 2007-2026",
    ),
    CoverageEntry("ProPublica 990s", "cross-cutting", "Org IDs", "keyless", "175 nonprofits"),
    CoverageEntry(
        "Public statements -> sectors", "cross-cutting", "stance edges", "local-bulk", "354 edges"
    ),
    # ── enrichment / derived ──
    CoverageEntry(
        "regenerate driver (contract corpus)",
        "enrichment",
        "ready rows w/ embeddings",
        "derived",
        "7,918 rows, 100% enriched",
    ),
    CoverageEntry(
        "entity-linker (NER -> canonical_person_id)",
        "enrichment",
        "text->Person links",
        "derived",
        "P 1.00 / R 0.75 on real sample",
    ),
)


def render_markdown(coverage: tuple[CoverageEntry, ...] = COVERAGE) -> str:
    """Render the coverage registry as the published markdown snapshot."""
    lines = [
        "# Track A source-coverage snapshot",
        "",
        "Auto-generated from `src/graph/coverage.py` (pinned by",
        "`tests/graph/test_coverage.py`). Counts are demonstrated on real runs;",
        "access is keyless (no credential), local-bulk (committed/local file),",
        "or derived (computed from ingested data). Credential-gated sources are in",
        "[`ingestion-credentials.md`](ingestion-credentials.md).",
        "",
        "| Tier | Source | Produces | Access | Demonstrated |",
        "|---|---|---|---|---|",
    ]
    tier_order = {
        t: i
        for i, t in enumerate(("federal", "state", "county", "city", "cross-cutting", "enrichment"))
    }
    for entry in sorted(coverage, key=lambda e: (tier_order[e.tier], e.source)):
        lines.append(
            f"| {entry.tier} | {entry.source} | {entry.produces} | "
            f"{entry.access} | {entry.demonstrated} |"
        )
    lines.append("")
    lines.append(f"**{len(coverage)} sources** across {len(tier_order)} tiers.")
    lines.append("")
    return "\n".join(lines)
