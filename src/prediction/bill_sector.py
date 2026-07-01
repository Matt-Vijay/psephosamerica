"""Keyword tagger mapping a bill's question text to Psephos America sectors.

The cross-pressured experiment needs a per-(member, sector) signal, which needs
each bill assigned to sector(s). House roll-call question text usually names the
bill's topic ("Energy Permitting Reform", "Defense Appropriations", "Farm Bill",
"Veterans Health"), so a curated keyword map over the locked 15-sector taxonomy
(`data/taxonomy/sectors.yaml`) tags most substantive bills. Matching is
lowercase substring on word-ish keywords; a bill may map to several sectors.

Deterministic and dependency-free; the keyword lists extend the taxonomy
aliases with the terms that actually appear in roll-call questions.
"""

from __future__ import annotations

_SECTOR_KEYWORDS: dict[str, tuple[str, ...]] = {
    "agriculture_food": ("agricultur", "farm", "food", "crop", "nutrition", "snap", "dairy"),
    "communications_technology": (
        "telecom",
        "broadband",
        "internet",
        "technology",
        "spectrum",
        "data privacy",
        "semiconductor",
    ),
    "construction_real_estate": ("housing", "real estate", "construction", "mortgage", "rent"),
    "defense_national_security": (
        "defense",
        "national security",
        "armed forces",
        "military",
        "homeland security",
        "veterans",
        "intelligence",
        "ndaa",
    ),
    "education_workforce": ("education", "school", "student", "workforce", "teacher", "college"),
    "energy_utilities": (
        "energy",
        "oil",
        "gas",
        "pipeline",
        "electric",
        "utilit",
        "nuclear",
        "coal",
    ),
    "financial_services": ("financial", "banking", "securities", "wall street", "credit", "fdic"),
    "health": (
        "health",
        "medicare",
        "medicaid",
        "medical",
        "drug",
        "pharma",
        "hospital",
        "abortion",
    ),
    "insurance": ("insurance", "insurer", "actuarial"),
    "labor": ("labor", "union", "workers", "wage", "collective bargaining", "osha"),
    "legal_judiciary": ("judiciary", "court", "judge", "justice", "litigation", "crime", "prison"),
    "manufacturing_industry": ("manufactur", "industrial", "factory", "supply chain"),
    "natural_resources_environment": (
        "environment",
        "climate",
        "conservation",
        "wildlife",
        "public lands",
        "water",
        "emissions",
        "epa",
    ),
    "transportation_infrastructure": (
        "transportation",
        "infrastructure",
        "highway",
        "transit",
        "aviation",
        "rail",
        "airport",
    ),
    "trade_international": (
        "trade",
        "tariff",
        "import",
        "export",
        "foreign",
        "sanction",
        "customs",
    ),
}


def tag_bill_sectors(question_text: str) -> list[str]:
    """Return the sectors whose keywords appear in the bill's question text (sorted)."""
    text = question_text.lower()
    matched = {
        sector
        for sector, keywords in _SECTOR_KEYWORDS.items()
        if any(keyword in text for keyword in keywords)
    }
    return sorted(matched)


def sector_keywords() -> dict[str, tuple[str, ...]]:
    """The keyword map (for inspection/tests)."""
    return dict(_SECTOR_KEYWORDS)
