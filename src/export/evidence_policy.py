"""Shared integrity policy for public evidence-card payloads."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol, TypeVar
from urllib.parse import urlparse

from src.core.disclosure_source_urls import parse_official_disclosure_artifact_url


class SourceAnchorLike(Protocol):
    @property
    def source_type(self) -> str: ...

    @property
    def source_id(self) -> str: ...

    @property
    def url(self) -> str | None: ...


class EvidenceBlockLike(Protocol):
    @property
    def section(self) -> object: ...


class EvidenceCardLike(Protocol):
    @property
    def score_delta(self) -> float: ...

    @property
    def blocks(self) -> Iterable[EvidenceBlockLike]: ...

    @property
    def source_anchors(self) -> Iterable[SourceAnchorLike]: ...


SourceAnchorT = TypeVar("SourceAnchorT", bound=SourceAnchorLike)

SOURCE_TYPES_REQUIRING_URL = frozenset(
    {
        "committee_membership",
        "congress_bill",
        "congress_vote",
        "legislative_bill",
        "legislative_vote",
        "fec_contribution",
        "financial_disclosure",
        "public_statement",
        "vote_event",
    }
)

OFFICIAL_SOURCE_HOSTS_BY_TYPE = {
    "congress_bill": frozenset(
        {
            "api.congress.gov",
            "congress.gov",
            "www.congress.gov",
        }
    ),
    "legislative_bill": frozenset(
        {
            "leginfo.legislature.ca.gov",
        }
    ),
    "legislative_vote": frozenset(
        {
            "leginfo.legislature.ca.gov",
        }
    ),
    "committee_membership": frozenset(
        {
            "api.congress.gov",
            "congress.gov",
            "www.congress.gov",
        }
    ),
    "fec_contribution": frozenset({"fec.gov", "www.fec.gov"}),
    "financial_disclosure": frozenset(
        {
            "disclosures.house.gov",
            "efdsearch.senate.gov",
        }
    ),
    "public_statement": frozenset(
        {
            "house.gov",
            "senate.gov",
            "www.house.gov",
            "www.senate.gov",
        }
    ),
    "vote_event": frozenset(
        {
            "clerk.house.gov",
            "senate.gov",
            "www.senate.gov",
        }
    ),
    "congress_vote": frozenset(
        {
            "clerk.house.gov",
            "senate.gov",
            "www.senate.gov",
        }
    ),
}


def is_https_source_url(value: str | None) -> bool:
    """Return True when *value* is an absolute HTTPS URL."""
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def is_official_source_url(source_type: str, value: str | None) -> bool:
    """Return True when a claim-bearing source URL is on an expected host."""
    if not value:
        return False
    if source_type == "financial_disclosure":
        try:
            parse_official_disclosure_artifact_url(
                value,
                label="unsupported financial disclosure source URL",
            )
        except ValueError:
            return False
        return True
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    allowed_hosts = OFFICIAL_SOURCE_HOSTS_BY_TYPE.get(source_type)
    if allowed_hosts is None:
        return True
    hostname = parsed.hostname.lower()
    if source_type in {"legislative_bill", "legislative_vote"} and _is_legislative_gov_host(
        hostname
    ):
        return True
    if source_type == "public_statement":
        return any(hostname == host or hostname.endswith(f".{host}") for host in allowed_hosts)
    return hostname in allowed_hosts


def _is_legislative_gov_host(hostname: str) -> bool:
    if not hostname.endswith(".gov"):
        return False
    labels = hostname.split(".")
    return any(token in label for label in labels for token in ("capitol", "legis", "legislature"))


def anchor_source_type(anchor: Any) -> str | None:
    """Return a source anchor's source_type from either model or dict payloads."""
    return _optional_string(_anchor_field(anchor, "source_type"))


def anchor_source_id(anchor: Any) -> str | None:
    """Return a source anchor's source_id from either model or dict payloads."""
    return _optional_string(_anchor_field(anchor, "source_id"))


def anchor_url(anchor: Any) -> str | None:
    """Return a source anchor URL from either model or dict payloads."""
    return _optional_string(_anchor_field(anchor, "url"))


def _anchor_jurisdiction_id(anchor: Any) -> str | None:
    return _optional_string(_anchor_field(anchor, "jurisdiction_id"))


def _anchor_legislative_body_id(anchor: Any) -> str | None:
    return _optional_string(_anchor_field(anchor, "legislative_body_id"))


def _anchor_legislative_session_id(anchor: Any) -> str | None:
    return _optional_string(_anchor_field(anchor, "legislative_session_id"))


def source_anchor_key(anchor: Any) -> str | None:
    """Return the stable source_type/source_id identity key for a source anchor."""
    source_type = anchor_source_type(anchor)
    source_id = anchor_source_id(anchor)
    if source_type is None or source_id is None:
        return None
    if source_type in {"legislative_bill", "legislative_vote"}:
        jurisdiction_id = _anchor_jurisdiction_id(anchor)
        legislative_body_id = _anchor_legislative_body_id(anchor)
        legislative_session_id = _anchor_legislative_session_id(anchor)
        if jurisdiction_id and legislative_body_id and legislative_session_id:
            return (
                f"{source_type}:{jurisdiction_id}:"
                f"{legislative_body_id}:{legislative_session_id}:{source_id}"
            )
    return f"{source_type}:{source_id}"


def duplicate_source_anchor_keys(anchors: Iterable[Any]) -> list[str]:
    """Return sorted source identity keys that appear more than once."""
    seen: set[str] = set()
    duplicates: set[str] = set()
    for anchor in anchors:
        key = source_anchor_key(anchor)
        if key is None:
            continue
        if key in seen:
            duplicates.add(key)
            continue
        seen.add(key)
    return sorted(duplicates)


def source_anchor_has_url(anchor: Any) -> bool:
    """Return True when an anchor carries any non-empty public URL."""
    return bool(anchor_url(anchor))


def source_anchor_has_https_url(anchor: Any) -> bool:
    """Return True when an anchor carries an absolute HTTPS URL."""
    return is_https_source_url(anchor_url(anchor))


def source_anchor_has_official_claim_url(anchor: Any) -> bool:
    """Return True when a claim-bearing anchor is backed by an official URL."""
    source_type = anchor_source_type(anchor)
    if source_type not in SOURCE_TYPES_REQUIRING_URL:
        return False
    return is_official_source_url(source_type, anchor_url(anchor))


def has_any_source_url(anchors: Iterable[Any]) -> bool:
    """Return True when at least one source anchor carries any non-empty URL."""
    return any(source_anchor_has_url(anchor) for anchor in anchors)


def has_https_source_url(anchors: Iterable[Any]) -> bool:
    """Return True when at least one source anchor carries a usable HTTPS URL."""
    return any(source_anchor_has_https_url(anchor) for anchor in anchors)


def official_source_url_count(anchors: Iterable[Any]) -> int:
    """Return the number of anchors with expected official source URLs."""
    return sum(
        1
        for anchor in anchors
        if (source_type := anchor_source_type(anchor)) in SOURCE_TYPES_REQUIRING_URL
        and is_official_source_url(str(source_type), anchor_url(anchor))
    )


def has_official_claim_source_anchor(anchors: Iterable[Any]) -> bool:
    """Return True when at least one score claim is backed by an official source."""
    return any(source_anchor_has_official_claim_url(anchor) for anchor in anchors)


def primary_source_anchor(
    anchors: Iterable[SourceAnchorT],
) -> SourceAnchorT | None:
    """Return the best display anchor for a public source CTA.

    Claim-bearing official URLs are preferred, then any official URL, then any
    HTTPS URL.  This keeps the API response fast for frontends without hiding
    the complete raw anchor list.
    """
    anchor_list = list(anchors)
    for anchor in anchor_list:
        if source_anchor_has_official_claim_url(anchor):
            return anchor
    for anchor in anchor_list:
        source_type = anchor_source_type(anchor)
        if source_type in OFFICIAL_SOURCE_HOSTS_BY_TYPE and is_official_source_url(
            str(source_type),
            anchor_url(anchor),
        ):
            return anchor
    for anchor in anchor_list:
        if source_anchor_has_https_url(anchor):
            return anchor
    return None


def source_anchors_missing_required_urls(
    anchors: Iterable[SourceAnchorT],
) -> list[SourceAnchorT]:
    """Return claim-bearing anchors that lack usable HTTPS source URLs."""
    return [
        anchor
        for anchor in anchors
        if anchor_source_type(anchor) in SOURCE_TYPES_REQUIRING_URL
        and not is_official_source_url(str(anchor_source_type(anchor)), anchor_url(anchor))
    ]


def all_required_source_anchor_urls_present(
    anchors: Iterable[SourceAnchorLike],
) -> bool:
    """Return True when every claim-bearing source anchor is HTTPS URL-backed."""
    return not source_anchors_missing_required_urls(anchors)


def describe_missing_source_anchor_urls(anchors: Iterable[SourceAnchorLike]) -> str:
    """Describe claim-bearing anchors that lack required HTTPS URLs."""
    return ", ".join(
        f"{anchor_source_type(anchor)} {anchor_source_id(anchor)}"
        for anchor in source_anchors_missing_required_urls(anchors)
    )


def has_fact_block(blocks: Iterable[EvidenceBlockLike]) -> bool:
    """Return True when an evidence card has at least one fact block."""
    for block in blocks:
        section = getattr(block.section, "value", block.section)
        if section == "fact":
            return True
    return False


def validate_evidence_card_policy(
    card: EvidenceCardLike,
    *,
    context: str = "Nonzero evidence cards",
) -> None:
    """Validate source-backed public evidence-card requirements."""
    if card.score_delta == 0:
        return

    blocks = list(card.blocks)
    anchors = list(card.source_anchors)
    if not has_fact_block(blocks):
        raise ValueError(f"{context} require at least one fact block")
    if not anchors:
        raise ValueError(f"{context} require at least one source anchor")
    duplicate_anchor_keys = duplicate_source_anchor_keys(anchors)
    if duplicate_anchor_keys:
        raise ValueError(
            f"{context} reject duplicate source anchors: {', '.join(duplicate_anchor_keys)}"
        )
    missing_source_urls = describe_missing_source_anchor_urls(anchors)
    if missing_source_urls:
        raise ValueError(
            f"{context} require HTTPS source URLs for claim-bearing anchors: {missing_source_urls}"
        )
    if not has_https_source_url(anchors):
        raise ValueError(f"{context} require at least one HTTPS source URL")
    if not has_official_claim_source_anchor(anchors):
        raise ValueError(f"{context} require at least one official source anchor")


def _anchor_field(anchor: Any, field: str) -> Any:
    if isinstance(anchor, dict):
        return anchor.get(field)
    return getattr(anchor, field, None)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text
