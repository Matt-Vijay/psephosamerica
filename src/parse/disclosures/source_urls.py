"""Compatibility exports for official disclosure source URL validation."""

from __future__ import annotations

from src.core.disclosure_source_urls import (
    HOUSE_DISCLOSURE_HOST,
    HOUSE_DISCLOSURE_PATH_KINDS,
    SENATE_DISCLOSURE_HOST,
    DisclosureArtifactUrlParts,
    DisclosureSourceChamber,
    HouseDisclosureUrlKind,
    parse_official_disclosure_artifact_url,
    validate_official_disclosure_artifact_url,
)

__all__ = [
    "HOUSE_DISCLOSURE_HOST",
    "HOUSE_DISCLOSURE_PATH_KINDS",
    "SENATE_DISCLOSURE_HOST",
    "DisclosureArtifactUrlParts",
    "DisclosureSourceChamber",
    "HouseDisclosureUrlKind",
    "parse_official_disclosure_artifact_url",
    "validate_official_disclosure_artifact_url",
]
