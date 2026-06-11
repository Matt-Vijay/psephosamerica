"""California ``leginfo`` bulk tables -> contract bills + legislator records.

The partial-ZIP range trick (:mod:`src.runtime.ca_leginfo_votes`) makes the
975 MB ``pubinfo_<year>.zip`` cheap to read selectively. This adapter parses the
two small tables that put California *into the contract corpus* (v7 #3):

* ``BILL_VERSION_TBL.dat`` — every bill version carries the bill's
  title/subject ("Surplus land: exempt surplus land: sectional planning
  area."), the embeddable text. The newest version's subject names the bill;
  the oldest version's date is when the bill became public (``known_at``).
* ``LEGISLATOR_TBL.dat`` — every member of the session with district, house,
  name, and party -> person :class:`~src.graph.entity_resolution.records.SourceRecord`
  keyed by ``(session, district)``.

CA bills resolve deterministically through the same
:class:`~src.graph.bills.BillRef` scheme as federal bills
(``us-ca`` / ``2025-2026`` / ``ab-13`` -> ``cb-…``), so vote linkage uses the
exact id the corpus mints. Public-record only; malformed rows are skipped.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from src.graph.bills import BillRef
from src.graph.contracts import ContractSourceAnchor, EntityResolutionOutput, build_bill_output
from src.graph.entity_resolution.records import SourceRecord
from src.graph.ingest.officials import official_record
from src.graph.jurisdictions import Jurisdiction
from src.graph.provenance import ProvenanceEnvelope

CA_SOURCE_SYSTEM = "ca_leginfo"
_MEASURE_RE = re.compile(r"^([A-Za-z]+)(\d{1,5})$")


def parse_ca_session(raw: str) -> str:
    """``20252026`` -> ``2025-2026`` (raises on malformed input)."""
    value = raw.strip().strip("`")
    if len(value) != 8 or not value.isdigit():
        raise ValueError(f"unparseable CA session: {raw!r}")
    return f"{value[:4]}-{value[4:]}"


def ca_bill_ref(session: str, measure: str) -> BillRef:
    """``("2025-2026", "AB13")`` -> the deterministic CA BillRef (``cb-…``)."""
    match = _MEASURE_RE.match(measure.strip())
    if match is None:
        raise ValueError(f"unparseable CA measure: {measure!r}")
    return BillRef(
        jurisdiction_id="us-ca",
        session_id=session,
        identifier=f"{match.group(1).lower()}-{int(match.group(2))}",
    )


def _unquote(field: str) -> str:
    field = field.strip()
    if len(field) >= 2 and field.startswith("`") and field.endswith("`"):
        return field[1:-1]
    return field


@dataclass(frozen=True)
class CaBillTitle:
    """One CA bill named by its newest version's subject."""

    raw_bill_id: str  # e.g. 202520260AB13
    session: str  # e.g. 2025-2026
    measure: str  # e.g. AB13
    subject: str
    introduced_date: str  # ISO date of the oldest version


def parse_bill_titles(version_table: str) -> list[CaBillTitle]:
    """``BILL_VERSION_TBL`` rows -> one titled bill per raw bill id.

    Columns: 0=version id, 1=bill id, 2=version number (newest = lowest), 3=date,
    6=subject. Rows without a subject or with an unparseable bill id are skipped.
    """
    by_bill: dict[str, tuple[int, str, str, str]] = {}  # id -> (best_ver, subject, date, session)
    earliest: dict[str, str] = {}
    for line in version_table.splitlines():
        if not line.strip():
            continue
        fields = [_unquote(part) for part in line.split("\t")]
        if len(fields) < 7:
            continue
        raw_bill_id, version_raw, date_raw, subject = fields[1], fields[2], fields[3], fields[6]
        if not raw_bill_id or not subject or subject == "NULL":
            continue
        if len(raw_bill_id) < 10 or not raw_bill_id[:8].isdigit():
            continue
        try:
            version = int(version_raw)
        except ValueError:
            continue
        date_iso = date_raw[:10]
        prior_date = earliest.get(raw_bill_id)
        if prior_date is None or date_iso < prior_date:
            earliest[raw_bill_id] = date_iso
        prior = by_bill.get(raw_bill_id)
        if prior is None or version < prior[0]:
            session = f"{raw_bill_id[:4]}-{raw_bill_id[4:8]}"
            by_bill[raw_bill_id] = (version, subject, date_iso, session)

    titles: list[CaBillTitle] = []
    for raw_bill_id, (_version, subject, _date, session) in sorted(by_bill.items()):
        measure = raw_bill_id[9:]
        if _MEASURE_RE.match(measure) is None:
            continue
        titles.append(
            CaBillTitle(
                raw_bill_id=raw_bill_id,
                session=session,
                measure=measure,
                subject=subject,
                introduced_date=earliest[raw_bill_id],
            )
        )
    return titles


def ca_bill_row(
    title: CaBillTitle, *, source_url: str, first_observed_at: datetime
) -> EntityResolutionOutput:
    """A titled CA bill as a pending contract row (same BillRef id as the votes)."""
    ref = ca_bill_ref(title.session, title.measure)
    introduced = datetime.fromisoformat(f"{title.introduced_date}T00:00:00+00:00")
    anchor = ContractSourceAnchor(
        source_system=CA_SOURCE_SYSTEM,
        record_id=title.raw_bill_id,
        source_url=source_url,
        content_sha256=hashlib.sha256(title.subject.encode("utf-8")).hexdigest(),
        content_address="sha256/"
        + hashlib.sha256(title.subject.encode("utf-8")).hexdigest()[:2]
        + "/"
        + hashlib.sha256(title.subject.encode("utf-8")).hexdigest()[2:4]
        + "/"
        + hashlib.sha256(title.subject.encode("utf-8")).hexdigest(),
        known_at=introduced if introduced <= first_observed_at else first_observed_at,
        valid_from=introduced.date(),
    )
    display = f"{title.measure}: {title.subject}"
    return build_bill_output(
        canonical_bill_id=ref.canonical_id,
        display_name=display[:300],
        source_anchors=[anchor],
        external_ids=[f"ca_leginfo:{title.raw_bill_id.lower()}"],
    )


def parse_ca_legislators(
    legislator_table: str, *, source_url: str, first_observed_at: datetime
) -> list[SourceRecord]:
    """``LEGISLATOR_TBL`` rows -> person SourceRecords keyed by (session, district).

    Columns: 0=district, 1=session, 3=house (S/A), 4=last name, 5=first name,
    11=party. Rows missing a district, session, or last name are skipped.
    """
    state = Jurisdiction.state("CA")
    records: list[SourceRecord] = []
    seen: set[str] = set()
    for line in legislator_table.splitlines():
        if not line.strip():
            continue
        fields = [_unquote(part) for part in line.split("\t")]
        if len(fields) < 12:
            continue
        district, session_raw = fields[0], fields[1]
        last, first, party = fields[4], fields[5], fields[11]
        if not district or not session_raw or not last:
            continue
        try:
            session = parse_ca_session(session_raw)
        except ValueError:
            continue
        record_id = f"{session}:{district.upper()}"
        if record_id in seen:
            continue
        seen.add(record_id)
        name = f"{first} {last}".strip()
        records.append(
            official_record(
                source_system=CA_SOURCE_SYSTEM,
                source_record_id=record_id,
                name=name,
                jurisdiction_code=state.code,
                external_ids=[("ca_leginfo_seat", record_id)],
                region=state.name,
                party=party or None,
                provenance=ProvenanceEnvelope(
                    source_url=source_url,
                    content_sha256=hashlib.sha256(line.encode("utf-8")).hexdigest(),
                    first_observed_at=first_observed_at,
                    valid_from=datetime(int(session[:4]), 1, 1, tzinfo=UTC).date(),
                    known_at=datetime(int(session[:4]), 1, 1, tzinfo=UTC),
                ),
            )
        )
    return records
