"""Runtime loaders and access helpers for Open Pact taxonomy artifacts."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from .taxonomy_validator import validate_all


@dataclass(frozen=True)
class Sector:
    sector_id: str
    label: str
    description: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class CommitteeMapping:
    congress: int
    chamber: str
    committee_name: str
    subcommittee_name: str  # empty string when row is a full committee (no subcommittee)
    sector_id: str
    mapping_tier: str
    jurisdiction_basis: str
    basis_source: str
    notes: str


@dataclass(frozen=True)
class CrpMapping:
    crp_category: str
    crp_label: str
    sector_id: str
    mapping_tier: str
    notes: str


@dataclass
class TaxonomyRuntime:
    sectors: list[Sector]
    committee_mappings: list[CommitteeMapping]
    crp_mappings: list[CrpMapping]

    # Computed indices — not part of public constructor, not compared or repr'd
    _sector_index: dict[str, Sector] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )
    _committee_index: dict[tuple[int, str, str], CommitteeMapping] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )
    _committee_index_by_chamber: dict[tuple[int, str, str, str], CommitteeMapping] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )
    _crp_index: dict[str, CrpMapping] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        self._sector_index = {s.sector_id: s for s in self.sectors}
        self._committee_index = {
            (m.congress, m.committee_name, m.subcommittee_name): m for m in self.committee_mappings
        }
        self._committee_index_by_chamber = {
            (m.congress, m.chamber.strip().lower(), m.committee_name, m.subcommittee_name): m
            for m in self.committee_mappings
        }
        self._crp_index = {m.crp_category: m for m in self.crp_mappings}

    def sector_by_id(self, sector_id: str) -> Sector | None:
        return self._sector_index.get(sector_id)

    def committee_sector(
        self,
        committee_name: str,
        subcommittee_name: str = "",
        congress: int = 119,
        chamber: str | None = None,
    ) -> CommitteeMapping | None:
        """Return the mapping for a committee (and optionally its subcommittee).

        Falls back to the parent committee row when the subcommittee is not mapped.
        Returns None when neither the subcommittee nor the parent committee is found.
        """
        if chamber is not None:
            chamber_key = chamber.strip().lower()
            hit = self._committee_index_by_chamber.get(
                (congress, chamber_key, committee_name, subcommittee_name)
            )
            if hit is not None:
                return hit
            if subcommittee_name:
                return self._committee_index_by_chamber.get(
                    (congress, chamber_key, committee_name, "")
                )
            return None

        hit = self._committee_index.get((congress, committee_name, subcommittee_name))
        if hit is not None:
            return hit
        if subcommittee_name:
            return self._committee_index.get((congress, committee_name, ""))
        return None

    def committees_for_sector(
        self,
        sector_id: str,
        congress: int | None = None,
    ) -> list[CommitteeMapping]:
        """Return all committee mappings for a given sector, optionally filtered by congress."""
        return [
            m
            for m in self.committee_mappings
            if m.sector_id == sector_id and (congress is None or m.congress == congress)
        ]

    def crp_sector(self, crp_category: str) -> CrpMapping | None:
        return self._crp_index.get(crp_category)


# ---------------------------------------------------------------------------
# File-local loaders (explicit I/O, no global state)
# ---------------------------------------------------------------------------


def _load_sectors(path: Path) -> list[Sector]:
    with open(path) as f:
        data = yaml.safe_load(f)
    return [
        Sector(
            sector_id=item["sector_id"],
            label=item["label"],
            description=item["description"],
            aliases=tuple(item.get("aliases") or []),
        )
        for item in data["sectors"]
    ]


def _load_committee_mappings(path: Path) -> list[CommitteeMapping]:
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    return [
        CommitteeMapping(
            congress=int(row["congress"]),
            chamber=row["chamber"].strip(),
            committee_name=row["committee_name"].strip(),
            subcommittee_name=row.get("subcommittee_name", "").strip(),
            sector_id=row["sector_id"].strip(),
            mapping_tier=row["mapping_tier"].strip(),
            jurisdiction_basis=row["jurisdiction_basis"].strip(),
            basis_source=row.get("basis_source", "").strip(),
            notes=row.get("notes", "").strip(),
        )
        for row in rows
    ]


def _load_crp_crosswalk(path: Path) -> list[CrpMapping]:
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    return [
        CrpMapping(
            crp_category=row["crp_category"].strip(),
            crp_label=row["crp_label"].strip(),
            sector_id=row["sector_id"].strip(),
            mapping_tier=row["mapping_tier"].strip(),
            notes=row.get("notes", "").strip(),
        )
        for row in rows
    ]


def load_taxonomy_runtime(data_root: Path) -> TaxonomyRuntime:
    """Load and validate all taxonomy artifacts from data_root.

    Raises ValueError if any validation check fails.
    """
    errors = validate_all(data_root)
    if errors:
        messages = "; ".join(f"{e.artifact}: {e.message}" for e in errors)
        raise ValueError(f"Taxonomy validation failed: {messages}")

    return TaxonomyRuntime(
        sectors=_load_sectors(data_root / "taxonomy" / "sectors.yaml"),
        committee_mappings=_load_committee_mappings(
            data_root / "taxonomy" / "committee_sector_map.csv"
        ),
        crp_mappings=_load_crp_crosswalk(data_root / "crosswalks" / "crp_to_sector.csv"),
    )
