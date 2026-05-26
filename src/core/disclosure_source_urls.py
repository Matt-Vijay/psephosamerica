"""Official disclosure source URL validation.

The disclosure pipeline treats source URLs as provenance, not decoration. Keep
the accepted URL shapes narrow so bundles and public evidence cards cannot
smuggle spoofed hosts or ambiguous paths into source-backed claims.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import unquote, urlparse

DisclosureSourceChamber = Literal["house", "senate"]
HouseDisclosureUrlKind = Literal["ptr", "annual"]

HOUSE_DISCLOSURE_HOST = "disclosures.house.gov"
SENATE_DISCLOSURE_HOST = "efdsearch.senate.gov"
HOUSE_DISCLOSURE_PATH_KINDS = frozenset({"ptr-pdfs", "financial-pdfs"})


@dataclass(frozen=True)
class DisclosureArtifactUrlParts:
    """Structured metadata encoded in an official disclosure artifact URL."""

    chamber: DisclosureSourceChamber
    document_id: str
    filing_year: int | None = None
    house_filing_kind: HouseDisclosureUrlKind | None = None


def _unsupported_url(url: str, label: str) -> ValueError:
    return ValueError(f"{label}: {url!r}")


def parse_official_disclosure_artifact_url(
    url: str,
    *,
    chamber: DisclosureSourceChamber | None = None,
    label: str = "unsupported disclosure artifact URL",
) -> DisclosureArtifactUrlParts:
    """Parse *url* as an official House or Senate disclosure artifact URL.

    When *chamber* is provided, URLs valid for the other chamber are rejected.
    """
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise _unsupported_url(url, label) from exc

    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.params
        or port not in (None, 443)
    ):
        raise _unsupported_url(url, label)

    path = parsed.path
    decoded_path = unquote(path)
    if decoded_path != path:
        raise _unsupported_url(url, label)
    segments = decoded_path.strip("/").split("/")
    if any(not segment or segment in {".", ".."} for segment in segments):
        raise _unsupported_url(url, label)

    host = parsed.hostname.lower()
    parts: DisclosureArtifactUrlParts
    if host == HOUSE_DISCLOSURE_HOST:
        if (
            len(segments) == 4
            and segments[0] == "public_disc"
            and segments[1] in HOUSE_DISCLOSURE_PATH_KINDS
            and len(segments[2]) == 4
            and segments[2].isdigit()
            and segments[3].endswith(".pdf")
            and len(segments[3]) > len(".pdf")
        ):
            kind: HouseDisclosureUrlKind = "ptr" if segments[1] == "ptr-pdfs" else "annual"
            parts = DisclosureArtifactUrlParts(
                chamber="house",
                document_id=segments[3][: -len(".pdf")],
                filing_year=int(segments[2]),
                house_filing_kind=kind,
            )
        else:
            raise _unsupported_url(url, label)
    elif host == SENATE_DISCLOSURE_HOST:
        if len(segments) == 4 and segments[:3] == ["search", "view", "paper"]:
            parts = DisclosureArtifactUrlParts(
                chamber="senate",
                document_id=segments[3],
            )
        else:
            raise _unsupported_url(url, label)
    else:
        raise _unsupported_url(url, label)

    if chamber is not None and parts.chamber != chamber:
        raise _unsupported_url(url, label)
    return parts


def validate_official_disclosure_artifact_url(
    url: str,
    *,
    chamber: DisclosureSourceChamber | None = None,
    label: str = "unsupported disclosure artifact URL",
) -> DisclosureSourceChamber:
    """Validate *url* as an official House or Senate disclosure artifact URL."""
    return parse_official_disclosure_artifact_url(
        url,
        chamber=chamber,
        label=label,
    ).chamber
