from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from src.graph.contracts import ContractSourceAnchor, EntityResolutionOutput
from src.graph.export import read_contract_corpus, write_contract_corpus
from src.graph.ingest.congress_legislators import parse_legislators
from src.graph.materialize import materialize_person_nodes
from src.runtime.roster_merge import (
    ROSTER_FILES,
    _index_by_bioguide,
    fetch_roster,
    merge_roster_into_corpus,
    roster_file_url,
)

_OBS = datetime(2024, 6, 1, tzinfo=UTC)
_AS_OF = datetime(2024, 6, 2, tzinfo=UTC)
_KNOWN = datetime(2023, 1, 1, tzinfo=UTC)


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
    cid: str,
    name: str,
    external_ids: list[str],
    *,
    embedded: bool = False,
) -> EntityResolutionOutput:
    extra: dict = {}
    if embedded:
        extra = {
            "dossier_embedding": [0.1, 0.2],
            "structural_embedding": [0.3, 0.4],
            "semantic_embedding": [0.5, 0.6],
            "dossier_json": {"summary": "kept"},
            "enrichment_status": "ready",
        }
    return EntityResolutionOutput(
        canonical_id=cid,
        entity_type="person",
        display_name=name,
        external_ids=external_ids,
        known_at=_KNOWN,
        source_anchors=[_anchor()],
        **extra,
    )


def _bill(cid: str) -> EntityResolutionOutput:
    return EntityResolutionOutput(
        canonical_id=cid,
        entity_type="bill",
        display_name="A Bill",
        external_ids=[],
        known_at=_KNOWN,
        source_anchors=[_anchor()],
    )


def _member(
    bioguide: str, *, official: str, lis: str | None = None, end: str = "2027-01-03"
) -> dict:
    ids: dict = {"bioguide": bioguide}
    if lis:
        ids["lis"] = lis
    return {
        "id": ids,
        "name": {"official_full": official},
        "terms": [{"type": "rep", "start": "2021-01-03", "end": end, "state": "CA"}],
    }


def _corpus(tmp_path: Path, rows: list[EntityResolutionOutput]) -> Path:
    corpus = tmp_path / "contract_records"
    write_contract_corpus(rows, directory=corpus, as_of=_AS_OF)
    return corpus


# --------------------------------------------------------------------------- fetch


def test_roster_file_url() -> None:
    assert roster_file_url("legislators-current.json").endswith(
        "congress-legislators/legislators-current.json"
    )


def _roster_client(payloads: dict[str, object]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, text=json.dumps(payloads[name]))

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_roster_concatenates_files() -> None:
    payloads = {
        ROSTER_FILES[0]: [_member("C000001", official="Cur Rent")],
        ROSTER_FILES[1]: [_member("H000001", official="His Torical"), "junk"],
    }
    members = fetch_roster(client=_roster_client(payloads))
    assert [m["id"]["bioguide"] for m in members] == ["C000001", "H000001"]  # "junk" dropped


def test_fetch_roster_tolerates_non_list() -> None:
    members = fetch_roster(client=_roster_client({n: {"oops": 1} for n in ROSTER_FILES}))
    assert members == []


def test_fetch_roster_default_client(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.runtime.roster_merge as mod

    real = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=json.dumps([_member("Z000001", official="Zed")]))

    monkeypatch.setattr(
        mod.httpx, "Client", lambda **_kw: real(transport=httpx.MockTransport(handler))
    )
    members = fetch_roster()
    assert all(m["id"]["bioguide"] == "Z000001" for m in members) and len(members) == 2


def test_index_by_bioguide_skips_keyless() -> None:
    fed = _person("ce-fed", "Fed", ["bioguide:m001199"])
    state = _person("ce-state", "State", ["openstates:x"])  # no bioguide -> skipped
    index = _index_by_bioguide([fed, state])
    assert set(index) == {"bioguide:m001199"} and index["bioguide:m001199"] is fed


# --------------------------------------------------------------------------- merge


def test_patches_placeholder_name_and_keeps_embeddings(tmp_path: Path) -> None:
    fed = _person("ce-fed", "M001199", ["bioguide:m001199"], embedded=True)
    corpus = _corpus(tmp_path, [fed])
    report = merge_roster_into_corpus(
        main_directory=corpus,
        roster_records=[_member("M001199", official="Maria Real", lis="S275")],
        as_of=_AS_OF,
        first_observed_at=_OBS,
    )
    assert report.names_patched == 1 and report.persons_added == 0
    rows = {r.canonical_id: r for r in read_contract_corpus(corpus)}
    patched = rows["ce-fed"]
    assert patched.display_name == "Maria Real"
    assert patched.external_ids == ["bioguide:m001199", "lis:s275"]  # lis unioned, sorted
    # canonical id + all three embeddings + dossier preserved untouched
    assert patched.dossier_embedding == [0.1, 0.2]
    assert patched.structural_embedding == [0.3, 0.4]
    assert patched.semantic_embedding == [0.5, 0.6]
    assert patched.dossier_json == {"summary": "kept"}
    assert report.deltas_written == 1  # one "updated" delta


def test_adds_missing_member_as_pending_person(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path, [_person("ce-fed", "M001199", ["bioguide:m001199"])])
    report = merge_roster_into_corpus(
        main_directory=corpus,
        roster_records=[
            _member("M001199", official="Maria Real"),
            _member("N000999", official="New Member", lis="S900"),
        ],
        as_of=_AS_OF,
        first_observed_at=_OBS,
    )
    assert report.names_patched == 1 and report.persons_added == 1
    rows = read_contract_corpus(corpus)
    added = [r for r in rows if "bioguide:n000999" in r.external_ids]
    assert len(added) == 1
    assert added[0].display_name == "New Member"
    assert added[0].enrichment_status == "pending"  # enrichment deferred to regenerate


def test_non_federal_persons_and_bills_untouched(tmp_path: Path) -> None:
    rows = [
        _person("ce-fed", "M001199", ["bioguide:m001199"]),
        _person("ce-state", "Jane State", ["openstates:x"]),  # no bioguide
        _bill("cb-1"),
    ]
    corpus = _corpus(tmp_path, rows)
    merge_roster_into_corpus(
        main_directory=corpus,
        roster_records=[_member("M001199", official="Maria Real")],
        as_of=_AS_OF,
        first_observed_at=_OBS,
    )
    out = {r.canonical_id: r for r in read_contract_corpus(corpus)}
    assert out["ce-state"].display_name == "Jane State"  # untouched
    assert out["cb-1"].entity_type == "bill"  # untouched


def test_noop_when_name_already_real_and_no_new_ids(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path, [_person("ce-fed", "Maria Real", ["bioguide:m001199", "lis:s275"])])
    report = merge_roster_into_corpus(
        main_directory=corpus,
        roster_records=[_member("M001199", official="Maria Real", lis="S275")],
        as_of=_AS_OF,
        first_observed_at=_OBS,
    )
    assert report.names_patched == 0 and report.persons_added == 0 and report.deltas_written == 0


def test_merge_is_idempotent(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path, [_person("ce-fed", "M001199", ["bioguide:m001199"])])
    records = [_member("M001199", official="Maria Real"), _member("N000999", official="New Member")]
    merge_roster_into_corpus(
        main_directory=corpus, roster_records=records, as_of=_AS_OF, first_observed_at=_OBS
    )
    second = merge_roster_into_corpus(
        main_directory=corpus, roster_records=records, as_of=_AS_OF, first_observed_at=_OBS
    )
    assert second.names_patched == 0 and second.persons_added == 0 and second.deltas_written == 0


def test_out_of_window_member_dropped(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path, [_person("ce-fed", "M001199", ["bioguide:m001199"])])
    report = merge_roster_into_corpus(
        main_directory=corpus,
        roster_records=[_member("OLD001", official="Old Timer", end="2001-01-03")],
        as_of=_AS_OF,
        first_observed_at=_OBS,
    )
    assert report.roster_members == 0 and report.persons_added == 0


def test_default_first_observed_at(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path, [_person("ce-fed", "M001199", ["bioguide:m001199"])])
    report = merge_roster_into_corpus(
        main_directory=corpus,
        roster_records=[_member("M001199", official="Maria Real")],
        as_of=_AS_OF,
    )
    assert report.names_patched == 1  # observed defaulted to now(UTC)


def test_add_skips_canonical_id_collision(tmp_path: Path) -> None:
    # Discover the canonical id the materializer assigns to a new member, then seed
    # the corpus with a decoy row under that exact id -> the add branch must skip it.
    member = _member("N000999", official="New Member")
    nodes, _ = materialize_person_nodes(parse_legislators([member], first_observed_at=_OBS))
    collide_id = nodes[0].canonical_id
    corpus = _corpus(tmp_path, [_person(collide_id, "Decoy", ["openstates:d"])])
    report = merge_roster_into_corpus(
        main_directory=corpus,
        roster_records=[member],
        as_of=_AS_OF,
        first_observed_at=_OBS,
    )
    assert report.persons_added == 0  # collision guard skipped the add
    out = {r.canonical_id: r for r in read_contract_corpus(corpus)}
    assert out[collide_id].display_name == "Decoy"  # decoy preserved
