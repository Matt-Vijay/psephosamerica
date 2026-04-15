"""Pure, deterministic string cleanup for FEC ingest fields. No I/O."""

from __future__ import annotations

import re
import unicodedata

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

# Common legal-entity suffixes to strip for clustering
_ENTITY_SUFFIXES = re.compile(
    r"\b(INC|LLC|LLP|LP|LTD|CORP|CORPORATION|CO|COMPANY|GROUP|PLLC|PC|PA|PLC)\.?\s*$",
    re.IGNORECASE,
)

_FEC_CMTE_RE = re.compile(r"^C\d{8}$")


def _strip_and_upper(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = s.upper().strip()
    s = _MULTI_SPACE.sub(" ", s)
    return s


def normalize_donor_name(raw: str | None) -> str:
    """Unicode-normalize → upper → collapse whitespace → strip suffixes (JR, MD, etc.)
    → flip "LAST, FIRST" to "FIRST LAST" (taking only the first given name).
    """
    name = _strip_and_upper(raw)
    if not name:
        return ""
    prev = None
    while prev != name:
        prev = name
        name = _NAME_SUFFIXES.sub("", name).rstrip(" ,.")
    if "," in name:
        parts = name.split(",", 1)
        last = parts[0].strip()
        first = parts[1].strip() if len(parts) > 1 else ""
        first_token = first.split()[0] if first else ""
        if first_token and last:
            name = f"{first_token} {last}"
        elif last:
            name = last
    return name


def normalize_employer(raw: str | None) -> str:
    """Upper → canonicalize self-employed/retired/none → strip entity suffixes (INC, LLC, etc.)."""
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
    prev = None
    while prev != emp:
        prev = emp
        emp = _ENTITY_SUFFIXES.sub("", emp).rstrip(" ,.")
    return emp


def normalize_occupation(raw: str | None) -> str:
    """Upper → return empty string for generic/useless values."""
    occ = _strip_and_upper(raw)
    if not occ:
        return ""
    if _OCCUPATION_GENERIC.match(occ):
        return ""
    return occ.rstrip(" ,.")


def normalize_committee_id(raw: str | None) -> str:
    """Strip and upper-case; return empty string if not in C######## format."""
    cid = _strip_and_upper(raw)
    if not cid:
        return ""
    if _FEC_CMTE_RE.match(cid):
        return cid
    return ""
