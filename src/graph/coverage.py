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
        "286 senators (LIS), 494,265 vote edges 113-119",
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
        "380,119 edges / 2,352 issues (113-119)",
    ),
    CoverageEntry(
        "govinfo BILLSTATUS bulk (113-119)",
        "federal",
        "Bill IDs + CRS content + embeddings",
        "keyless",
        "106,536 bills, 100% of substantive votes linkable",
    ),
    CoverageEntry(
        "congress-legislators roster",
        "federal",
        "Person IDs (bioguide+LIS, real names)",
        "keyless",
        "1,132 members (440 renamed + 692 added)",
    ),
    CoverageEntry(
        "BILLSTATUS bill edges (sponsor/subject/committee)",
        "federal",
        "graph edges",
        "derived",
        "1,673,411 edges / 106,999 bills",
    ),
    CoverageEntry(
        "FEC donor-industry profiles",
        "federal",
        "dossier_json.donor_profile",
        "derived",
        "518 members, $1.21B itemized",
    ),
    # ── state ──
    CoverageEntry(
        "OpenStates people (all 50 states)", "state", "Person IDs", "keyless", "7,359 legislators"
    ),
    CoverageEntry(
        "CA leginfo bulk (partial-ZIP range reads)",
        "state",
        "Bill IDs + titles + legislators + rich roll-calls",
        "keyless",
        "25,539 bills, 720 seats, 48,676 roll-calls / 2.96M votes",
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
        "134 governments (44,600+ officials)",
    ),
    CoverageEntry(
        "Text-PDF municipal minutes (pdfplumber)",
        "city",
        "minutes text + official links",
        "keyless",
        "text-layer PDFs (image-only = OCR gap)",
    ),
    CoverageEntry(
        "LOCUS-v1 ordinances (CC-BY-NC; HF LocalLaws/LOCUS-v1)",
        "city",
        "ordinance Bill IDs + function/topic + 4 dimension scores",
        "keyless",
        "2,207,679 ordinances / 2,287 jurisdictions (50 states), full corpus",
    ),
    CoverageEntry(
        "LOCUS jurisdiction linkage (folded-key)",
        "city",
        "LOCUS jurisdiction -> canonical jurisdiction",
        "derived",
        "59 linked (P 1.00 / R 1.00, 0 false merges), 2,228 minted",
    ),
    # ── cross-cutting ──
    CoverageEntry(
        "Prediction markets (Polymarket + Kalshi)",
        "cross-cutting",
        "market entities + bill links + price history",
        "keyless",
        "1,275 markets, 49 bill-linked, 5,322 price obs",
    ),
    CoverageEntry(
        "GDELT news",
        "cross-cutting",
        "news-mention edges",
        "keyless",
        "539 edges (API throttles bulk)",
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
        "141,266 rows; 139,974 w/ 384-d semantic",
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
