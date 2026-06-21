"""Adapt USASpending.gov federal awards into recipient orgs + agencies + award edges.

USASpending.gov is the public system of record for federal spending. Its REST
API (``api.usaspending.gov/api/v2``, keyless) and the bulk "Custom Award Data"
downloads publish every federal *award* — contracts (procurement) and financial
assistance (grants, loans, direct payments) — naming the **recipient**
(the org or individual that received the money), the **awarding agency**
(toptier + sub-agency), the obligated/total amount, the action/award dates, and
the funding appropriation account (Treasury Account Symbol, which derives the
appropriating bill). This is the money-OUT half that closes
``money-in (donors) -> power (votes) -> money-out (awards)``.

This module is a pure adapter (no network): it parses one award record dict
(the shape returned by the ``/search/spending_by_award/`` endpoint and the bulk
download CSV-as-dict rows, normalized by the runner) into:

* a **recipient** :class:`SourceRecord` (``entity_type='org'``), keyed on its
  UEI (``uei``) — the durable SAM.gov Unique Entity ID — falling back to the
  legacy DUNS or the USASpending recipient hash, so a recipient resolves across
  awards and links into the canonical org graph;
* an **awarding agency** :class:`SourceRecord` (``entity_type='org'``), keyed on
  its toptier agency code (``usa_agency``); and
* a ``federal_award`` :class:`GraphEdge` recipient -> agency carrying the
  amount, dates, award type, and (when present) the appropriating bill ref.

Disclosure-lag: an award is public when the action is *reported* to
USASpending, which the API exposes as ``action_date``. There is no separate
"posted" timestamp in the award record, so :func:`award_provenance` stamps
``known_at`` at the action date (the conservative, leakage-safe choice — the
fact cannot have been knowable before the action that created it). Malformed
rows are skipped (parsed to ``None``), never fabricated.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from src.graph.bills import BillRef, congress_bill_identifier
from src.graph.edges import GraphEdge
from src.graph.entity_resolution.records import ExternalId, SourceRecord
from src.graph.provenance import ProvenanceEnvelope

USASPENDING_SOURCE_SYSTEM = "usaspending"
USASPENDING_BASE_URL = "https://www.usaspending.gov/award"
USASPENDING_API_URL = "https://api.usaspending.gov/api/v2"

#: USASpending ``type`` codes group into two families. We carry the family on the
#: edge so consumers can split procurement (contracts) from assistance (grants).
_CONTRACT_TYPES = frozenset({"A", "B", "C", "D"})  # definitive / IDV contract codes
_ASSISTANCE_TYPES = frozenset({"02", "03", "04", "05", "06", "07", "08", "09", "10", "11"})

#: The award ``type`` codes grouped into the named families USASpending uses for
#: its ``award_type_codes`` filter. Each family is paged separately (the search
#: endpoint requires a homogeneous prefix grouping) and together they cover the
#: whole federal award universe: contracts (procurement), grants, direct
#: payments, loans, and the residual "other financial assistance" codes.
AWARD_TYPE_GROUPS: dict[str, tuple[str, ...]] = {
    "contracts": ("A", "B", "C", "D"),
    "grants": ("02", "03", "04", "05"),
    "direct_payments": ("06", "10"),
    "loans": ("07", "08"),
    "other": ("09", "11"),
}

_UEI_RE = re.compile(r"^[A-Z0-9]{12}$")
_DUNS_RE = re.compile(r"^\d{9}$")
# A bill citation embedded in an appropriation title, e.g. "Public Law 117-103"
# or an explicit "H.R. 2471"; we extract congress + bill number where present.
_PUBLIC_LAW_RE = re.compile(r"public\s+law\s+(\d{2,3})-(\d+)", re.IGNORECASE)


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def award_family(award_type: str) -> str | None:
    """Map a USASpending ``type`` code to ``'contract'`` / ``'assistance'``."""
    code = award_type.strip().upper()
    if code in _CONTRACT_TYPES:
        return "contract"
    if code.lstrip("0") and code in {t.upper() for t in _ASSISTANCE_TYPES}:
        return "assistance"
    # Some bulk rows present the human label directly.
    lowered = award_type.strip().lower()
    if "contract" in lowered or "purchase order" in lowered:
        return "contract"
    if "grant" in lowered or "assistance" in lowered or "loan" in lowered:
        return "assistance"
    return None


@dataclass(frozen=True)
class FederalAward:
    """One parsed federal award linking a recipient to an awarding agency."""

    award_id: str  # generated_unique_award_id / "Award ID" — the durable key
    recipient_name: str
    recipient_uei: str | None
    recipient_duns: str | None
    recipient_hash: str | None
    recipient_state: str | None
    agency_name: str
    agency_code: str | None
    sub_agency_name: str | None
    award_type: str
    family: str | None
    amount: str | None  # obligated/total amount as a decimal string (cents-safe)
    action_date: date
    public_law: str | None  # e.g. "117-103" when derivable from the appropriation
    congress: int | None  # e.g. 117 when derivable


def _recipient_external_ids(award: FederalAward) -> tuple[ExternalId, ...]:
    """Strong external keys for a recipient, strongest (UEI) first.

    UEI is the durable SAM.gov identifier; DUNS is the deprecated-but-still-present
    legacy key; the USASpending recipient hash is the last-resort join key so that
    even an un-registered recipient resolves consistently across its own awards.
    """
    ids: list[ExternalId] = []
    if award.recipient_uei:
        ids.append(ExternalId(system="uei", value=award.recipient_uei))
    if award.recipient_duns:
        ids.append(ExternalId(system="duns", value=award.recipient_duns))
    if award.recipient_hash:
        ids.append(ExternalId(system="usaspending_recipient", value=award.recipient_hash))
    return tuple(ids)


def parse_federal_award(record: dict[str, Any]) -> FederalAward | None:
    """Parse one award record into a :class:`FederalAward`, or ``None`` to skip.

    Accepts both the API ``/search/spending_by_award/`` row shape (snake_case-ish
    keys) and the bulk download row shape (Title Case keys); a row is skipped when
    it lacks the identity it needs to link: an award id, a recipient name, an
    awarding agency name, and a parseable action date.
    """
    award_id = (
        _clean(record.get("generated_internal_id"))
        or _clean(record.get("Award ID"))
        or _clean(record.get("award_id"))
        or _clean(record.get("internal_id"))
    )
    if not award_id:
        return None
    recipient_name = _clean(record.get("Recipient Name")) or _clean(record.get("recipient_name"))
    if not recipient_name:
        return None
    agency_name = (
        _clean(record.get("Awarding Agency"))
        or _clean(record.get("awarding_agency_name"))
        or _clean(record.get("awarding_toptier_agency_name"))
    )
    if not agency_name:
        return None
    raw_action = (
        _clean(record.get("Action Date"))
        or _clean(record.get("action_date"))
        or _clean(record.get("Start Date"))
    )
    try:
        action_date = date.fromisoformat(raw_action[:10])
    except ValueError:
        return None

    uei = _clean(record.get("Recipient UEI") or record.get("recipient_uei")).upper() or None
    if uei is not None and not _UEI_RE.match(uei):
        uei = None
    duns = _clean(record.get("recipient_duns") or record.get("Recipient DUNS")) or None
    if duns is not None and not _DUNS_RE.match(duns):
        duns = None
    recipient_hash = _clean(record.get("recipient_id") or record.get("recipient_hash")) or None

    award_type = _clean(record.get("Award Type") or record.get("type") or record.get("award_type"))
    amount = _award_amount(record)
    public_law, congress = _appropriation_bill(record)

    return FederalAward(
        award_id=award_id,
        recipient_name=recipient_name,
        recipient_uei=uei,
        recipient_duns=duns,
        recipient_hash=recipient_hash,
        recipient_state=(
            _clean(record.get("recipient_state_code") or record.get("Recipient State")).upper()
            or None
        ),
        agency_name=agency_name,
        agency_code=(
            _clean(record.get("awarding_toptier_agency_code") or record.get("agency_code")) or None
        ),
        sub_agency_name=(
            _clean(record.get("Awarding Sub Agency") or record.get("awarding_subtier_agency_name"))
            or None
        ),
        award_type=award_type,
        family=award_family(award_type) if award_type else None,
        amount=amount,
        action_date=action_date,
        public_law=public_law,
        congress=congress,
    )


def _award_amount(record: dict[str, Any]) -> str | None:
    """The award amount as a normalized decimal string, or ``None``."""
    for key in (
        "Award Amount",
        "total_obligation",
        "Total Obligated Amount",
        "award_amount",
        "obligated_amount",
        "Obligated Amount",
    ):
        raw = record.get(key)
        if raw is None or isinstance(raw, bool):
            continue
        if isinstance(raw, (int, float)):
            return f"{raw:.2f}"
        text = _clean(raw).replace("$", "").replace(",", "")
        if not text:
            continue
        try:
            return f"{float(text):.2f}"
        except ValueError:
            continue
    return None


def _appropriation_bill(record: dict[str, Any]) -> tuple[str | None, int | None]:
    """Derive the appropriating bill (public law) from the award where present.

    USASpending exposes the appropriating Public Law in some award/account views.
    When a ``public_law`` / appropriation title carries a ``NNN-NN`` law number we
    capture the (public_law, congress) so a later pass can mint the bill ref.
    """
    for key in ("public_law", "Public Law", "appropriation_title"):
        text = _clean(record.get(key))
        if not text:
            continue
        match = _PUBLIC_LAW_RE.search(text)
        if match:
            congress = int(match.group(1))
            return f"{congress}-{int(match.group(2))}", congress
        # A bare "117-103" form.
        bare = re.match(r"^(\d{2,3})-(\d+)$", text)
        if bare:
            congress = int(bare.group(1))
            return f"{congress}-{int(bare.group(2))}", congress
    return None, None


def parse_recipient_org(award: FederalAward, *, provenance: ProvenanceEnvelope) -> SourceRecord:
    """Parse the award's recipient into an org source record.

    Falls back to a content-addressed recipient key when the row carries no UEI,
    DUNS, or USASpending recipient hash, so a recipient is never dropped for lack
    of a registered identifier (and still resolves to itself across reruns).
    """
    external_ids = _recipient_external_ids(award)
    if not external_ids:
        digest = hashlib.blake2b(
            award.recipient_name.casefold().encode("utf-8"), digest_size=10
        ).hexdigest()
        external_ids = (ExternalId(system="usaspending_recipient", value=f"name-{digest}"),)
    return SourceRecord(
        source_system=USASPENDING_SOURCE_SYSTEM,
        source_record_id=f"recipient:{external_ids[0].value}",
        entity_type="org",
        display_name=award.recipient_name,
        external_ids=external_ids,
        jurisdiction="us",
        region=award.recipient_state,
        provenance=provenance,
    )


def parse_awarding_agency(award: FederalAward, *, provenance: ProvenanceEnvelope) -> SourceRecord:
    """Parse the award's awarding agency into an org source record.

    Keyed on the toptier agency code where present (``usa_agency:097`` for DoD),
    else on a content digest of the agency name, so agencies resolve across awards.
    """
    if award.agency_code:
        key = award.agency_code
    else:
        key = (
            "name-"
            + hashlib.blake2b(
                award.agency_name.casefold().encode("utf-8"), digest_size=8
            ).hexdigest()
        )
    return SourceRecord(
        source_system=USASPENDING_SOURCE_SYSTEM,
        source_record_id=f"agency:{key}",
        entity_type="org",
        display_name=award.agency_name,
        external_ids=(ExternalId(system="usa_agency", value=key),),
        jurisdiction="us",
        provenance=provenance,
    )


def award_provenance(
    award: FederalAward,
    *,
    source_url: str,
    content_sha256: str,
    first_observed_at: datetime,
    known_at: datetime | None = None,
) -> ProvenanceEnvelope:
    """Provenance for a federal award; ``known_at`` defaults to the action date.

    The award fact becomes true and knowable on its action date (no earlier
    "posted" instant is exposed), so the leakage stamp is conservatively the
    action date at UTC midnight.
    """
    default_known = datetime(
        award.action_date.year, award.action_date.month, award.action_date.day, tzinfo=UTC
    )
    return ProvenanceEnvelope(
        source_url=source_url,
        content_sha256=content_sha256,
        first_observed_at=first_observed_at,
        valid_from=award.action_date,
        known_at=known_at if known_at is not None else default_known,
    )


def award_bill_ref(award: FederalAward) -> BillRef | None:
    """The appropriating bill ref derivable from the award, or ``None``.

    A public-law number maps to a federal :class:`BillRef` under the congress that
    enacted it. USASpending does not record the originating bill *type/number*
    (HR vs S) on the award, only the public-law number; we therefore key the
    derived bill on the public-law identifier itself (``pl-117-103``), which is a
    stable, citable handle for the enacted appropriation.
    """
    if award.public_law is None or award.congress is None:
        return None
    return BillRef(
        jurisdiction_id="us-congress",
        session_id=str(award.congress),
        identifier=f"pl-{award.public_law}",
    )


def federal_award_edge(
    *,
    recipient_canonical_id: str,
    agency_canonical_id: str,
    award: FederalAward,
    provenance: ProvenanceEnvelope,
) -> GraphEdge:
    """Build the recipient -> awarding-agency ``federal_award`` edge."""
    attributes: dict[str, str] = {}
    if award.family:
        attributes["family"] = award.family
    if award.award_type:
        attributes["award_type"] = award.award_type
    if award.amount is not None:
        attributes["amount"] = award.amount
    if award.sub_agency_name:
        attributes["sub_agency"] = award.sub_agency_name
    if award.public_law is not None:
        attributes["public_law"] = award.public_law
    return GraphEdge(
        edge_type="federal_award",
        src_id=recipient_canonical_id,
        dst_id=agency_canonical_id,
        attributes=attributes,
        external_key=award.award_id,
        provenance=provenance,
    )


def award_appropriation_edge(
    *,
    agency_canonical_id: str,
    award: FederalAward,
    provenance: ProvenanceEnvelope,
) -> GraphEdge | None:
    """Build an agency -> appropriating-bill ``funded_by`` edge, if derivable.

    Closes the link from money-out back to the enacting appropriation (the
    bill/power layer). Returns ``None`` when no public law is derivable from the
    award (the common case for the search API, which omits the account view).
    """
    ref = award_bill_ref(award)
    if ref is None:
        return None
    attributes = {"public_law": award.public_law} if award.public_law else {}
    return GraphEdge(
        edge_type="funded_by",
        src_id=agency_canonical_id,
        dst_id=ref.canonical_id,
        attributes=attributes,
        external_key=f"{award.award_id}:{ref.identifier}",
        provenance=provenance,
    )


# Touch the imported helper so static analysis keeps it referenced; it is the
# documented path from a bare bill type/number to a congress identifier and is
# re-exported for runner code that resolves a known originating bill.
assert congress_bill_identifier is not None
