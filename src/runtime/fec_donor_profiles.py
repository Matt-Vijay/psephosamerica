"""FEC donor-industry profiles per member -> ``dossier_json`` (v7 #4).

Streams the local FEC bulk (``itcont.txt``, 1.6 GB itemized individual
contributions) once, routes each contribution to a member through the
candidate-committee linkage (``ccl.txt``) and the bioguide<->FEC-candidate
crosswalk, and aggregates a **donor profile** per member: total raised,
contribution count, and the top employers/occupations by amount (normalized
through the FEC name canonicalizer, so "SKADDEN ARPS LLP" and "SKADDEN ARPS"
pool).

Two phases so the corpus rewrite never races other merges:

* :func:`build_donor_profiles` -> ``donor_profiles.json`` artifact (no corpus
  I/O; safe to run any time),
* :func:`merge_donor_profiles_into_corpus` -> adds a ``donor_profile`` section
  to each matched member's ``dossier_json`` + delta-CDC.

Public-record disclosure data only; amounts as integer cents; members without
a crosswalk entry are skipped and counted, never guessed.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.graph.cdc import diff_outputs
from src.graph.export import (
    DELTAS_FILENAME,
    read_contract_corpus,
    write_contract_corpus,
    write_delta_feed,
)
from src.ingest.fec.bulk import iter_candidate_committee_linkage, iter_individual_contributions
from src.ingest.fec.normalize import normalize_employer, normalize_occupation

_TOP_N = 10


def load_member_candidates(crosswalk_csv: Path | str) -> dict[str, str]:
    """``member_fec.csv`` -> ``{fec_candidate_id: bioguide_id (upper)}``."""
    mapping: dict[str, str] = {}
    with Path(crosswalk_csv).open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            bioguide = (row.get("bioguide_id") or "").strip().upper()
            candidate = (row.get("fec_candidate_id") or "").strip().upper()
            if bioguide and candidate:
                mapping[candidate] = bioguide
    return mapping


def map_committees_to_members(ccl_path: Path | str, candidates: dict[str, str]) -> dict[str, str]:
    """``ccl.txt`` -> ``{fec_committee_id: bioguide}`` for crosswalked members."""
    mapping: dict[str, str] = {}
    for linkage in iter_candidate_committee_linkage(ccl_path):
        bioguide = candidates.get(linkage.fec_candidate_id.upper())
        if bioguide is not None:
            mapping.setdefault(linkage.fec_committee_id.upper(), bioguide)
    return mapping


@dataclass
class _Tally:
    total_cents: int = 0
    contributions: int = 0

    def __post_init__(self) -> None:
        self.employers: Counter[str] = Counter()
        self.occupations: Counter[str] = Counter()


def _top(counter: Counter[str]) -> list[dict[str, Any]]:
    return [
        {"name": name, "cents": cents} for name, cents in counter.most_common(_TOP_N) if cents > 0
    ]


def build_donor_profiles(
    itcont_path: Path | str,
    committee_to_member: dict[str, str],
    *,
    max_rows: int | None = None,
) -> dict[str, dict[str, Any]]:
    """One streaming pass over itemized contributions -> per-member profiles."""
    tallies: dict[str, _Tally] = {}
    seen = 0
    for record in iter_individual_contributions(itcont_path):
        seen += 1
        if max_rows is not None and seen > max_rows:
            break
        bioguide = committee_to_member.get(record.fec_committee_id.upper())
        if bioguide is None:
            continue
        cents = int(record.amount * 100)
        tally = tallies.setdefault(bioguide, _Tally())
        tally.total_cents += cents
        tally.contributions += 1
        employer = normalize_employer(record.employer)
        if employer:
            tally.employers[employer] += cents
        occupation = normalize_occupation(record.occupation)
        if occupation:
            tally.occupations[occupation] += cents

    return {
        bioguide: {
            "total_cents": tally.total_cents,
            "contributions": tally.contributions,
            "distinct_employers": len(tally.employers),
            "top_employers": _top(tally.employers),
            "top_occupations": _top(tally.occupations),
        }
        for bioguide, tally in sorted(tallies.items())
    }


def write_profiles(profiles: dict[str, dict[str, Any]], path: Path | str) -> None:
    """Persist the build phase's artifact."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(profiles, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def load_profiles(path: Path | str) -> dict[str, dict[str, Any]]:
    """Read the build phase's artifact back."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


@dataclass(frozen=True)
class DonorMergeReport:
    """Counts from one merge of donor profiles into the corpus."""

    profiles: int
    members_matched: int
    members_updated: int
    deltas_written: int


def _bioguide_of(external_ids: list[str]) -> str | None:
    for external in external_ids:
        if external.startswith("bioguide:"):
            return external.removeprefix("bioguide:").upper()
    return None


def merge_donor_profiles_into_corpus(
    *,
    main_directory: Path | str,
    profiles: dict[str, dict[str, Any]],
    as_of: datetime | None = None,
) -> DonorMergeReport:
    """Attach ``donor_profile`` to each matched member's dossier + delta-CDC."""
    main_dir = Path(main_directory)
    observed = as_of if as_of is not None else datetime.now(UTC)
    main_rows = read_contract_corpus(main_dir)

    matched = updated = 0
    out_rows = []
    for row in main_rows:
        if row.entity_type == "person":
            bioguide = _bioguide_of(row.external_ids)
            profile = profiles.get(bioguide) if bioguide else None
            if profile is not None:
                matched += 1
                dossier = dict(row.dossier_json or {})
                if dossier.get("donor_profile") != profile:
                    dossier["donor_profile"] = profile
                    row = row.model_copy(update={"dossier_json": dossier})
                    updated += 1
        out_rows.append(row)

    prior = {row.canonical_id: row for row in main_rows}
    current = {row.canonical_id: row for row in out_rows}
    deltas = diff_outputs(prior, current)
    write_contract_corpus(out_rows, directory=main_dir, as_of=observed)
    deltas_written = write_delta_feed(deltas, path=main_dir / DELTAS_FILENAME, append=True)
    return DonorMergeReport(
        profiles=len(profiles),
        members_matched=matched,
        members_updated=updated,
        deltas_written=deltas_written,
    )


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI glue
    import argparse

    parser = argparse.ArgumentParser(description="FEC donor-industry profiles per member")
    parser.add_argument("command", choices=["build", "merge"])
    parser.add_argument("--itcont", default="data/fec/itcont.txt")
    parser.add_argument("--ccl", default="data/fec/ccl.txt")
    parser.add_argument("--crosswalk", default="data/crosswalks/member_fec.csv")
    parser.add_argument("--profiles", default="data/exports/govinfo_bills/donor_profiles.json")
    parser.add_argument("--corpus", default="data/exports/contract_records")
    parser.add_argument("--max-rows", type=int, default=None)
    args = parser.parse_args(argv)
    if args.command == "build":
        candidates = load_member_candidates(args.crosswalk)
        committees = map_committees_to_members(args.ccl, candidates)
        profiles = build_donor_profiles(args.itcont, committees, max_rows=args.max_rows)
        write_profiles(profiles, args.profiles)
        total = sum(p["total_cents"] for p in profiles.values())
        print(
            f"members={len(profiles)} committees={len(committees)} total=${total / 100:,.0f}",
            flush=True,
        )
    else:
        report = merge_donor_profiles_into_corpus(
            main_directory=args.corpus, profiles=load_profiles(args.profiles)
        )
        print(
            f"profiles={report.profiles} matched={report.members_matched} "
            f"updated={report.members_updated} deltas={report.deltas_written}",
            flush=True,
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
