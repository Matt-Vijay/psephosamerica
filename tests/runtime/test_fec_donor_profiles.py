from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from src.graph.contracts import ContractSourceAnchor, EntityResolutionOutput
from src.graph.export import read_contract_corpus, write_contract_corpus
from src.runtime.fec_donor_profiles import (
    build_donor_profiles,
    load_member_candidates,
    load_profiles,
    map_committees_to_members,
    merge_donor_profiles_into_corpus,
    write_profiles,
)

_NOW = datetime(2026, 6, 11, tzinfo=UTC)
_KNOWN = datetime(2023, 1, 1, tzinfo=UTC)


def _crosswalk(tmp_path: Path) -> Path:
    path = tmp_path / "member_fec.csv"
    path.write_text(
        "bioguide_id,fec_candidate_id\nA000055,H6AL04098\nB000001,H0XX00000\n,\n",
        encoding="utf-8",
    )
    return path


def _ccl(tmp_path: Path) -> Path:
    path = tmp_path / "ccl.txt"
    path.write_text(
        "\n".join(
            [
                "H6AL04098|2024|2024|C00111111|H|P|1",  # member committee
                "H6AL04098|2024|2024|C00222222|H|A|2",  # second committee, same member
                "H9ZZ99999|2024|2024|C00999999|H|P|3",  # not a crosswalked member
            ]
        ),
        encoding="utf-8",
    )
    return path


def _itcont(tmp_path: Path) -> Path:
    def row(cmte: str, name: str, employer: str, occupation: str, amount: str) -> str:
        return (
            f"{cmte}|N|12P|P2024|2024|15E|IND|{name}|CITY|ST|00000|{employer}|{occupation}"
            f"|08052024|{amount}|C0|1|||x|sub{name}{amount}"
        )

    path = tmp_path / "itcont.txt"
    path.write_text(
        "\n".join(
            [
                row("C00111111", "DOE, JANE", "SKADDEN ARPS LLP", "ATTORNEY", "250"),
                row("C00111111", "ROE, RICK", "SKADDEN ARPS", "ATTORNEY", "750"),
                row("C00222222", "POE, PAT", "ACME INC", "ENGINEER", "100"),
                row("C00999999", "NON, MEMBER", "ACME INC", "ENGINEER", "999"),  # unrouted
                row("C00111111", "BLANK, BO", "", "", "50"),  # no employer/occupation
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_load_member_candidates(tmp_path: Path) -> None:
    candidates = load_member_candidates(_crosswalk(tmp_path))
    assert candidates == {"H6AL04098": "A000055", "H0XX00000": "B000001"}


def test_map_committees_to_members(tmp_path: Path) -> None:
    candidates = load_member_candidates(_crosswalk(tmp_path))
    committees = map_committees_to_members(_ccl(tmp_path), candidates)
    assert committees == {"C00111111": "A000055", "C00222222": "A000055"}


def test_build_donor_profiles_aggregates_normalized(tmp_path: Path) -> None:
    candidates = load_member_candidates(_crosswalk(tmp_path))
    committees = map_committees_to_members(_ccl(tmp_path), candidates)
    profiles = build_donor_profiles(_itcont(tmp_path), committees)
    assert set(profiles) == {"A000055"}
    profile = profiles["A000055"]
    assert profile["total_cents"] == (250 + 750 + 100 + 50) * 100
    assert profile["contributions"] == 4
    # LLP suffix normalized away -> the two SKADDEN rows pool
    assert profile["top_employers"][0] == {"name": "SKADDEN ARPS", "cents": 100000}
    assert profile["top_occupations"][0] == {"name": "ATTORNEY", "cents": 100000}
    assert profile["distinct_employers"] == 2


def test_build_respects_max_rows(tmp_path: Path) -> None:
    candidates = load_member_candidates(_crosswalk(tmp_path))
    committees = map_committees_to_members(_ccl(tmp_path), candidates)
    profiles = build_donor_profiles(_itcont(tmp_path), committees, max_rows=1)
    assert profiles["A000055"]["contributions"] == 1


def test_profiles_roundtrip(tmp_path: Path) -> None:
    profiles = {"A000055": {"total_cents": 1, "contributions": 1}}
    path = tmp_path / "profiles.json"
    write_profiles(profiles, path)
    assert load_profiles(path) == profiles


def test_load_profiles_non_dict(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("[1, 2]", encoding="utf-8")
    assert load_profiles(path) == {}


def _anchor() -> ContractSourceAnchor:
    return ContractSourceAnchor(
        source_system="house_clerk",
        record_id="r",
        source_url="https://x",
        content_sha256="a" * 64,
        content_address="sha256/aa/aa/" + "a" * 64,
        known_at=_KNOWN,
        valid_from=_KNOWN.date(),
    )


def _person(
    cid: str, external_ids: list[str], dossier: dict | None = None
) -> EntityResolutionOutput:
    return EntityResolutionOutput(
        canonical_id=cid,
        entity_type="person",
        display_name=cid,
        external_ids=external_ids,
        known_at=_KNOWN,
        source_anchors=[_anchor()],
        dossier_json=dossier,
    )


def _bill_row(cid: str) -> EntityResolutionOutput:
    return EntityResolutionOutput(
        canonical_id=cid,
        entity_type="bill",
        display_name="bill",
        external_ids=[],
        known_at=_KNOWN,
        source_anchors=[_anchor()],
    )


def test_merge_attaches_profile_and_announces(tmp_path: Path) -> None:
    corpus = tmp_path / "contract_records"
    write_contract_corpus(
        [
            _person("ce-1", ["bioguide:a000055"], {"summary": "s", "claims": []}),
            _person("ce-2", ["bioguide:z999999"]),  # no profile
            _person("ce-3", ["openstates:x"]),  # no bioguide
            _bill_row("cb-1"),
        ],
        directory=corpus,
        as_of=_NOW,
    )
    profiles = {"A000055": {"total_cents": 100, "contributions": 1}}
    report = merge_donor_profiles_into_corpus(main_directory=corpus, profiles=profiles, as_of=_NOW)
    assert report.members_matched == 1 and report.members_updated == 1
    assert report.deltas_written == 1
    rows = {r.canonical_id: r for r in read_contract_corpus(corpus)}
    assert rows["ce-1"].dossier_json["donor_profile"] == profiles["A000055"]
    assert rows["ce-1"].dossier_json["summary"] == "s"  # existing dossier preserved
    assert rows["ce-2"].dossier_json is None


def test_merge_is_idempotent(tmp_path: Path) -> None:
    corpus = tmp_path / "contract_records"
    write_contract_corpus([_person("ce-1", ["bioguide:a000055"])], directory=corpus, as_of=_NOW)
    profiles = {"A000055": {"total_cents": 100, "contributions": 1}}
    merge_donor_profiles_into_corpus(main_directory=corpus, profiles=profiles, as_of=_NOW)
    again = merge_donor_profiles_into_corpus(main_directory=corpus, profiles=profiles, as_of=_NOW)
    assert again.members_updated == 0 and again.deltas_written == 0


def test_merge_default_as_of(tmp_path: Path) -> None:
    corpus = tmp_path / "contract_records"
    write_contract_corpus([_person("ce-1", ["bioguide:a000055"])], directory=corpus, as_of=_NOW)
    report = merge_donor_profiles_into_corpus(
        main_directory=corpus, profiles={"A000055": {"total_cents": 1}}
    )
    assert report.members_updated == 1
