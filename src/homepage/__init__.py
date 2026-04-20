"""Homepage feed payload package.

Exports the small public surface for v1 homepage/feed payload construction.
"""

from src.homepage.builders import (
    build_featured_lookup_entries,
    build_homepage_feed,
    build_recent_events,
    build_top_changes,
    extract_recent_evidence_card_ids,
)
from src.homepage.contracts import (
    HomepageFeedPayload,
    MemberMovementSummary,
    RecentEventSummary,
)

__all__ = [
    "HomepageFeedPayload",
    "MemberMovementSummary",
    "RecentEventSummary",
    "build_featured_lookup_entries",
    "build_homepage_feed",
    "build_recent_events",
    "build_top_changes",
    "extract_recent_evidence_card_ids",
]
