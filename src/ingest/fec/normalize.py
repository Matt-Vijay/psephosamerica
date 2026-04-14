"""Deterministic cleanup for FEC ingest fields.

All functions here are pure, no I/O, no DB, no network.  They operate on
single string values and return cleaned strings.
"""

from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_MULTI_SPACE = re.compile(r"\s+")

# Common suffixes / prefixes to strip from names
_NAME_SUFFIXES = re.compile(
    r"\b(JR|SR|II|III|IV|MD|PHD|ESQ|DDS|DO|CPA|MR|MRS|MS|DR|HON)\.?\s*$",
    re.IGNORECASE,
)

# Employer strings that indicate self-employment or retirement
_SELF_EMPLOYED = re.compile(
    r"^(SELF[\s\-]*EMPLOYED?|SELF|NONE|N/?A|NOT EMPLOYED|RETIRED|HOMEMAKER"
    r"|INFORMATION REQUESTED|INFORMATION REQUESTED PER BEST EFFORTS"
    r"|REFUSED|STUDENT)$",
    re.IGNORECASE,
)

# Occupation strings that indicate generic / useless values
_OCCUPATION_GENERIC = re.compile(
    r"^(NONE|N/?A|NOT EMPLOYED|RETIRED|HOMEMAKER|INFORMATION REQUESTED"
    r"|INFORMATION REQUESTED PER BEST EFFORTS|REFUSED|STUDENT)$",
    re.IGNORECASE,
)


def _strip_and_upper(s: str | None) -> str:
    """Strip, upper-case, collapse whitespace, normalize unicode."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = s.upper().strip()
    s = _MULTI_SPACE.sub(" ", s)
    return s


# ---------------------------------------------------------------------------
# Donor name normalization
# ---------------------------------------------------------------------------

def normalize_donor_name(raw: str | None) -> str:
    """Deterministic donor-name cleanup.

    - Unicode normalize → upper → collapse whitespace
    - Strip common suffixes (JR, SR, MD, etc.)
    - Remove trailing commas and periods
    - Flip "LAST, FIRST" → "FIRST LAST" for consistency
    """
    name = _strip_and_upper(raw)
    if not name:
        return ""
    # Strip suffixes iteratively (handles "JR MD" etc.)
    prev = None
    while prev != name:
        prev = name
        name = _NAME_SUFFIXES.sub("", name).rstrip(" ,.")
    # FEC names are typically "LAST, FIRST MIDDLE" — normalize to "FIRST LAST"
    if "," in name:
        parts = name.split(",", 1)
        last = parts[0].strip()
        first = parts[1].strip() if len(parts) > 1 else ""
        # Take only the first given name for the canonical key
        first_token = first.split()[0] if first else ""
        if first_token and last:
            name = f"{first_token} {last}"
        elif last:
            name = last
    return name


# ---------------------------------------------------------------------------
# Employer normalization
# ---------------------------------------------------------------------------

# Common legal-entity suffixes to strip for clustering
_ENTITY_SUFFIXES = re.compile(
    r"\b(INC|LLC|LLP|LP|LTD|CORP|CORPORATION|CO|COMPANY|GROUP|PLLC|PC|PA|PLC)\.?\s*$",
    re.IGNORECASE,
)


def normalize_employer(raw: str | None) -> str:
    """Deterministic employer-string cleanup.

    - Upper + collapse whitespace
    - Canonicalize self-employed / retired / none → sentinel values
    - Strip common entity suffixes (INC, LLC, etc.) for matching
    - Remove trailing punctuation
    """
    emp = _strip_and_upper(raw)
    if not emp:
        return ""
    if _SELF_EMPLOYED.match(emp):
        if re.match(r"RETIRED", emp, re.IGNORECASE):
            return "RETIRED"
        if re.match(r"HOMEMAKER", emp, re.IGNORECASE):
            return "HOMEMAKER"
        if re.match(r"STUDENT", emp, re.IGNORECASE):
            return "STUDENT"
        return "SELF-EMPLOYED"
    # Strip entity suffixes
    prev = None
    while prev != emp:
        prev = emp
        emp = _ENTITY_SUFFIXES.sub("", emp).rstrip(" ,.")
    return emp


# ---------------------------------------------------------------------------
# Occupation normalization
# ---------------------------------------------------------------------------

def normalize_occupation(raw: str | None) -> str:
    """Deterministic occupation-string cleanup.

    - Upper + collapse whitespace
    - Canonicalize generic / useless values → empty string
    - Strip trailing punctuation
    """
    occ = _strip_and_upper(raw)
    if not occ:
        return ""
    if _OCCUPATION_GENERIC.match(occ):
        return ""
    return occ.rstrip(" ,.")


# ---------------------------------------------------------------------------
# Committee ID normalization
# ---------------------------------------------------------------------------

_FEC_CMTE_RE = re.compile(r"^C\d{8}$")


def normalize_committee_id(raw: str | None) -> str:
    """Normalize an FEC committee ID.

    FEC committee IDs are always C followed by 8 digits (e.g. C00431445).
    This strips whitespace and upper-cases, then validates the format.
    Returns empty string if invalid.
    """
    cid = _strip_and_upper(raw)
    if not cid:
        return ""
    if _FEC_CMTE_RE.match(cid):
        return cid
    return ""
