"""Adapt govinfo BILLSTATUS bulk XML into vote-linkable, densely-embeddable bills.

govinfo publishes every bill's status as keyless bulk XML
(``www.govinfo.gov/bulkdata/BILLSTATUS/<congress>/<billtype>``, no auth), each
file carrying the bill's title, CRS **policy area** + **legislative subjects**,
sponsors, cosponsors, committees, and the CRS **summary** text.
:func:`parse_billstatus_xml` turns one file into a :class:`BillStatus`.

Two properties make this the data unlock Track B needs:

* **Vote-linkable for free.** The canonical bill id is minted by the *same*
  :class:`~src.graph.bills.BillRef` the roll-call vote adapters use
  (``congress`` + ``type`` + ``number`` -> ``hr-1`` -> ``cb-<digest>``), so every
  parsed bill is already the ``dst_id`` of its ``vote`` edges -- no fuzzy match.
* **Dense, not collapsed.** :func:`billstatus_dossier_text` builds the embedding
  text from the bill's own title + policy area + subjects + summary, so two
  same-sector bills embed to *different* points -- the discrimination the
  sector-bag stand-in could not provide.

Sponsor/cosponsor edges fall out of the same record via
``ingest.sponsorships.sponsorship_edge``; committee names are carried for the
committee-membership edges. Parsed with ``defusedxml`` (no XXE surface) and
defensively over the minor schema drift between congresses 113-119.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from defusedxml import ElementTree as ET

from src.graph.bills import BillRef, congress_bill_identifier
from src.graph.provenance import ProvenanceEnvelope


@dataclass(frozen=True)
class BillSponsor:
    """A bill (co)sponsor: bioguide id, name, and the action date (if recorded)."""

    bioguide_id: str
    full_name: str
    sponsorship_date: date | None


@dataclass(frozen=True)
class BillStatus:
    """One parsed BILLSTATUS record."""

    congress: int
    bill_type: str  # normalized lowercase congress bill type, e.g. "hr"
    number: int
    title: str
    introduced_date: date | None
    policy_area: str | None
    subjects: tuple[str, ...]
    sponsors: tuple[BillSponsor, ...]
    cosponsors: tuple[BillSponsor, ...]
    committees: tuple[str, ...]
    summary_text: str | None


def bulk_billstatus_url(congress: int, bill_type: str) -> str:
    """The keyless govinfo bulk-data directory URL for a congress + bill type."""
    normalized = "".join(ch for ch in bill_type.strip().lower() if ch.isalpha())
    if not normalized:
        raise ValueError(f"invalid bill type: {bill_type!r}")
    return f"https://www.govinfo.gov/bulkdata/BILLSTATUS/{congress}/{normalized}"


def _text(element: object, *paths: str) -> str | None:
    """First non-empty text across candidate child paths (tolerates schema drift)."""
    if element is None:
        return None
    for path in paths:
        found: str | None = element.findtext(path)  # type: ignore[attr-defined]
        if found and found.strip():
            return found.strip()
    return None


def _iter(element: object, *paths: str) -> list[object]:
    """Children at the first path that yields any (tolerates schema drift)."""
    if element is None:
        return []
    for path in paths:
        items = element.findall(path)  # type: ignore[attr-defined]
        if items:
            return list(items)
    return []


def _date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.strip()[:10])
    except ValueError:
        return None


def _sponsors(container: object) -> tuple[BillSponsor, ...]:
    out: list[BillSponsor] = []
    seen: set[str] = set()
    for item in _iter(container, "item"):
        bioguide = _text(item, "bioguideId", "bioguide_id")
        if not bioguide or bioguide in seen:
            continue
        seen.add(bioguide)
        out.append(
            BillSponsor(
                bioguide_id=bioguide,
                full_name=_text(item, "fullName", "name") or bioguide,
                sponsorship_date=_date(_text(item, "sponsorshipDate")),
            )
        )
    return tuple(out)


def parse_billstatus_xml(xml: str) -> BillStatus:
    """Parse one govinfo BILLSTATUS XML document into a :class:`BillStatus`."""
    if not xml.strip():
        raise ValueError("empty BILLSTATUS document")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError(f"invalid BILLSTATUS XML: {exc}") from exc
    bill = root.find("bill") if root.tag != "bill" else root
    if bill is None:
        raise ValueError("BILLSTATUS XML is missing <bill>")

    congress_raw = _text(bill, "congress")
    number_raw = _text(bill, "number", "billNumber")
    type_raw = _text(bill, "type", "billType")
    if not (congress_raw and number_raw and type_raw):
        raise ValueError("BILLSTATUS <bill> is missing congress/type/number")
    # Validate the type against the congress vocabulary (raises on garbage).
    bill_type = congress_bill_identifier(type_raw, int(number_raw)).split("-", 1)[0]

    subjects = tuple(
        name
        for item in _iter(
            bill.find("subjects"),
            "legislativeSubjects/item",
            "billSubjects/legislativeSubjects/item",
            "item",
        )
        if (name := _text(item, "name"))
    )
    committees = tuple(
        name
        for item in _iter(bill.find("committees"), "item", "billCommittees/item")
        if (name := _text(item, "name"))
    )
    summary = _text(
        bill.find("summaries"),
        "summary/text",
        "billSummaries/item/text",
        "summary/item/text",
    )

    return BillStatus(
        congress=int(congress_raw),
        bill_type=bill_type,
        number=int(number_raw),
        title=_text(bill, "title") or f"{type_raw} {number_raw}",
        introduced_date=_date(_text(bill, "introducedDate")),
        policy_area=_text(bill.find("policyArea"), "name"),
        subjects=subjects,
        sponsors=_sponsors(bill.find("sponsors")),
        cosponsors=_sponsors(bill.find("cosponsors")),
        committees=committees,
        summary_text=summary,
    )


def bill_ref(status: BillStatus) -> BillRef:
    """The canonical :class:`BillRef` for a parsed bill (same one votes resolve to)."""
    return BillRef(
        jurisdiction_id="us-congress",
        session_id=str(status.congress),
        identifier=congress_bill_identifier(status.bill_type, status.number),
    )


def canonical_bill_id(status: BillStatus) -> str:
    """The ``cb-<digest>`` id shared with the bill's roll-call ``vote`` edges."""
    return bill_ref(status).canonical_id


def govinfo_record_id(status: BillStatus) -> str:
    """A stable govinfo source-record id, e.g. ``BILLSTATUS-118hr1``."""
    return f"BILLSTATUS-{status.congress}{status.bill_type}{status.number}"


def billstatus_dossier_text(status: BillStatus) -> str:
    """Dense embedding text: title + policy area + subjects + CRS summary.

    Distinct per bill (not collapsed to its sector), so the LocalTextEmbedder
    produces a content embedding that tells two same-sector bills apart.
    """
    parts: list[str] = [status.title]
    if status.policy_area:
        parts.append(f"Policy area: {status.policy_area}.")
    if status.subjects:
        parts.append("Subjects: " + "; ".join(status.subjects) + ".")
    if status.summary_text:
        parts.append(status.summary_text)
    return "\n".join(parts)


def billstatus_provenance(
    *,
    status: BillStatus,
    source_url: str,
    content_sha256: str,
    first_observed_at: datetime,
    known_at: datetime | None = None,
) -> ProvenanceEnvelope:
    """Provenance for a bill record; ``known_at`` defaults to the introduced day.

    A bill is publicly knowable when introduced, so the leakage gate sits there;
    when the introduced date is absent we fall back to ``first_observed_at``.
    """
    introduced = status.introduced_date
    if known_at is not None:
        default_known = known_at
    elif introduced is not None:
        default_known = datetime(introduced.year, introduced.month, introduced.day, tzinfo=UTC)
    else:
        default_known = first_observed_at
    return ProvenanceEnvelope(
        source_url=source_url,
        content_sha256=content_sha256,
        first_observed_at=first_observed_at,
        valid_from=introduced or default_known.date(),
        known_at=default_known,
    )
