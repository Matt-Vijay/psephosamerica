"""LOCUS-v1 local-ordinance corpus -> contract ordinance entities + jurisdictions.

LOCUS v1.0 (``huggingface.co/datasets/LocalLaws/LOCUS-v1``) is a chunk-level
corpus of ~2.2M U.S. municipal and county law provisions, each labeled with a
legal ``function`` (Context / Rules / Process / Enforcement), a ``topic``
(Buildings / Business / Nuisance / Zoning / Other), an ``is_substantive`` flag,
and four continuous dimension scores — ``opacity``, ``paternalism``,
``enforcement_discretion``, ``problem_salience``. Each chunk names its
jurisdiction by ``(state, city | county, source_jurisdiction_type)``.

This adapter turns one parsed LOCUS row into:

* a canonical **jurisdiction code** in the repo's hierarchical vocabulary
  (``us-ca-city-los_angeles`` / ``us-ny-county-erie``) — the entity-resolution
  join key (deliverable #2); and
* a pending **ordinance** :class:`~src.graph.contracts.EntityResolutionOutput`
  (``entity_type='bill'``), keyed deterministically via
  :class:`~src.graph.bills.BillRef` under the jurisdiction, with the four
  dimension scores + function/topic labels carried in a structured
  ``dossier_json`` so Track B can reason over them, and full provenance +
  ``known_at``.

LOCUS chunks have no enacted bill number, so each ordinance's identifier is
content-addressed from its (jurisdiction, header, content) — stable across reruns
and collision-resistant, so the same provision always mints the same bill ID.

Attribution (required): LOCUS v1.0, Peskoff/Barrow/Vu/Davenport, arXiv:2606.19334,
licensed **CC-BY-NC-4.0** (NON-COMMERCIAL). The license is carried on every row's
external IDs and recorded in :data:`LOCUS_ATTRIBUTION` so downstream consumers
see the non-commercial restriction. Public-record law only; malformed rows are
skipped, never fabricated.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.graph.bills import BillRef
from src.graph.contracts import ContractSourceAnchor, EntityResolutionOutput, build_bill_output
from src.graph.jurisdictions import Jurisdiction, slugify_place

LOCUS_SOURCE_SYSTEM = "locus_v1"
LOCUS_DATASET_URL = "https://huggingface.co/datasets/LocalLaws/LOCUS-v1"

#: Canonical attribution + license for LOCUS-v1. CC-BY-NC-4.0 is NON-COMMERCIAL;
#: this string is recorded in provenance + the release docs so the restriction
#: travels with every published ordinance row.
LOCUS_ATTRIBUTION = (
    "LOCUS v1.0 (LocalLaws/LOCUS-v1), Peskoff, Barrow, Vu & Davenport, "
    "arXiv:2606.19334, licensed CC-BY-NC-4.0 (non-commercial)."
)
LOCUS_LICENSE = "cc-by-nc-4.0"

#: The four continuous LOCUS dimension score fields (per the dataset card).
DIMENSION_FIELDS = ("opacity", "paternalism", "enforcement_discretion", "problem_salience")

_VALID_FUNCTIONS = frozenset({"Context", "Rules", "Process", "Enforcement", "Structural"})


@dataclass(frozen=True)
class LocusOrdinance:
    """One parsed LOCUS chunk (a municipal / county law provision)."""

    state: str  # 2-letter USPS, lowercased ("ak")
    place: str  # raw place slug from LOCUS ("kingcove", "monterey_county")
    level: str  # "city" or "county"
    header: str
    content: str
    function: str
    topic: str | None
    is_substantive: bool
    opacity: float | None
    paternalism: float | None
    enforcement_discretion: float | None
    problem_salience: float | None

    @property
    def jurisdiction(self) -> Jurisdiction:
        """The canonical jurisdiction entity for this ordinance's place."""
        if self.level == "county":
            return Jurisdiction.county(self.state, self.place)
        return Jurisdiction.city(self.state, self.place)

    @property
    def dimension_scores(self) -> dict[str, float]:
        """The non-null subset of the four LOCUS dimension scores."""
        raw = {
            "opacity": self.opacity,
            "paternalism": self.paternalism,
            "enforcement_discretion": self.enforcement_discretion,
            "problem_salience": self.problem_salience,
        }
        return {key: value for key, value in raw.items() if value is not None}


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _level_of(jurisdiction_type: str) -> str | None:
    """Map LOCUS ``source_jurisdiction_type`` to a repo jurisdiction level."""
    normalized = jurisdiction_type.strip().lower()
    if normalized in {"cities", "city"}:
        return "city"
    if normalized in {"counties", "county"}:
        return "county"
    return None


def _coerce_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def parse_locus_row(record: dict[str, Any]) -> LocusOrdinance | None:
    """Parse one LOCUS row dict into a :class:`LocusOrdinance`, or ``None`` to skip.

    A row is skipped (never fabricated) when it lacks the identity it needs to
    join: a 2-letter state, a recognised jurisdiction type, the matching place
    name (``city`` for cities, ``county`` for counties), a function label, and
    non-empty content.
    """
    state = _clean(record.get("state")).lower()
    if len(state) != 2 or not state.isalpha():
        return None
    level = _level_of(_clean(record.get("source_jurisdiction_type")))
    if level is None:
        return None
    place = _clean(record.get("city")) if level == "city" else _clean(record.get("county"))
    if not place:
        return None
    function = _clean(record.get("function"))
    if function not in _VALID_FUNCTIONS:
        return None
    content = _clean(record.get("content"))
    if not content:
        return None
    # Reject places that cannot be slugified (blank after folding) before they
    # reach the Jurisdiction constructor, so a bad row is skipped, not raised.
    try:
        slugify_place(place)
    except ValueError:
        return None
    topic = _clean(record.get("topic")) or None
    return LocusOrdinance(
        state=state,
        place=place,
        level=level,
        header=_clean(record.get("header")),
        content=content,
        function=function,
        topic=topic,
        is_substantive=bool(record.get("is_substantive")),
        opacity=_coerce_float(record.get("opacity")),
        paternalism=_coerce_float(record.get("paternalism")),
        enforcement_discretion=_coerce_float(record.get("enforcement_discretion")),
        problem_salience=_coerce_float(record.get("problem_salience")),
    )


def ordinance_content_sha256(ordinance: LocusOrdinance) -> str:
    """The sha256 content-address of this ordinance's raw text + header."""
    payload = f"{ordinance.header}\n{ordinance.content}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def ordinance_bill_ref(ordinance: LocusOrdinance) -> BillRef:
    """Mint the deterministic municipal :class:`BillRef` for a LOCUS ordinance.

    LOCUS chunks carry no enacted bill number, so the identifier is the short
    content digest of (jurisdiction, header, content). Same provision -> same
    bill ID across reruns; distinct provisions in the same jurisdiction get
    distinct IDs. ``session_id='locus'`` marks the source corpus (a snapshot,
    not a legislative session).
    """
    jurisdiction = ordinance.jurisdiction
    digest = hashlib.blake2b(
        f"{jurisdiction.code}|{ordinance.header}|{ordinance.content}".encode("utf-8"),
        digest_size=10,
    ).hexdigest()
    return BillRef(
        jurisdiction_id=jurisdiction.code,
        session_id="locus",
        identifier=f"ord-{digest}",
    )


def _ordinance_dossier(ordinance: LocusOrdinance) -> dict[str, Any]:
    """The structured LOCUS labels carried for Track B to reason over."""
    return {
        "source": "locus_v1",
        "attribution": LOCUS_ATTRIBUTION,
        "license": LOCUS_LICENSE,
        "function": ordinance.function,
        "topic": ordinance.topic,
        "is_substantive": ordinance.is_substantive,
        "dimension_scores": ordinance.dimension_scores,
        "jurisdiction_id": ordinance.jurisdiction.code,
        "jurisdiction_level": ordinance.level,
    }


def ordinance_row(
    ordinance: LocusOrdinance,
    *,
    known_at: datetime,
) -> EntityResolutionOutput:
    """A LOCUS ordinance as a pending contract bill row, scores in the dossier.

    ``known_at`` is the corpus snapshot's observation time: LOCUS provisions
    carry no enactment date, so the earliest knowable instant is when this
    snapshot was ingested. ``valid_from`` is that same date (the dataset is a
    point-in-time snapshot per the dataset card).
    """
    if known_at.tzinfo is None or known_at.utcoffset() is None:
        raise ValueError("known_at must be timezone-aware")
    ref = ordinance_bill_ref(ordinance)
    sha = ordinance_content_sha256(ordinance)
    anchor = ContractSourceAnchor(
        source_system=LOCUS_SOURCE_SYSTEM,
        record_id=ref.identifier,
        source_url=LOCUS_DATASET_URL,
        content_sha256=sha,
        content_address=f"sha256/{sha[:2]}/{sha[2:4]}/{sha}",
        known_at=known_at,
        valid_from=known_at.date(),
    )
    header = ordinance.header.lstrip("# ").strip()
    label = header or ordinance.content
    display = f"{ordinance.jurisdiction.name} ordinance: {label}"
    row = build_bill_output(
        canonical_bill_id=ref.canonical_id,
        display_name=display[:300],
        source_anchors=[anchor],
        external_ids=sorted(
            {f"{LOCUS_SOURCE_SYSTEM}:{ref.identifier}", f"license:{LOCUS_LICENSE}"}
        ),
    )
    return row.model_copy(update={"dossier_json": _ordinance_dossier(ordinance)})


def parse_locus_rows(
    records: list[dict[str, Any]],
    *,
    known_at: datetime,
) -> tuple[list[EntityResolutionOutput], dict[str, Jurisdiction]]:
    """Parse a batch of LOCUS rows -> (ordinance contract rows, jurisdictions seen).

    Skipped rows are dropped silently (counted by the caller via the length
    delta). The returned jurisdiction map is keyed by canonical code, so the
    caller can feed it straight into the jurisdiction entity-resolution join.
    """
    rows: list[EntityResolutionOutput] = []
    jurisdictions: dict[str, Jurisdiction] = {}
    for record in records:
        parsed = parse_locus_row(record)
        if parsed is None:
            continue
        rows.append(ordinance_row(parsed, known_at=known_at))
        jurisdiction = parsed.jurisdiction
        jurisdictions[jurisdiction.code] = jurisdiction
    return rows, jurisdictions


def now_utc() -> datetime:
    """The current instant, timezone-aware in UTC (snapshot ingestion time)."""
    return datetime.now(tz=UTC)
