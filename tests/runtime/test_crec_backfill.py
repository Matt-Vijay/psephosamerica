from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from src.graph.contracts import ContractSourceAnchor, EntityResolutionOutput
from src.graph.export import write_contract_corpus
from src.runtime.crec_backfill import (
    backfill_crec_speeches,
    bill_mentions,
    date_range,
)

_OBS = datetime(2024, 6, 1, tzinfo=UTC)

_MODS = """<mods:mods xmlns:mods="http://www.loc.gov/mods/v3">
<mods:relatedItem type="constituent"><extension xmlns="http://www.loc.gov/mods/v3">
  <congMember bioGuideId="S000148" chamber="S" role="SPEAKING" state="NY">
    <name type="authority-fnf">Charles E. Schumer</name></congMember>
</extension></mods:relatedItem>
<mods:relatedItem type="constituent"><extension>
  <congMember bioGuideId="Z999999" chamber="H" role="SPEAKING" state="CA">
    <name type="authority-fnf">Unknown Member</name></congMember>
</extension></mods:relatedItem>
<mods:searchTitle>Debate on H.R. 1478 and S. 509</mods:searchTitle>
</mods:mods>"""


def _anchor() -> ContractSourceAnchor:
    return ContractSourceAnchor(
        source_system="house_clerk",
        record_id="r",
        source_url="https://x",
        content_sha256="a" * 64,
        content_address="sha256/aa/aa/" + "a" * 64,
        known_at=datetime(2023, 1, 1, tzinfo=UTC),
        valid_from=datetime(2023, 1, 1, tzinfo=UTC).date(),
    )


def _person(cid: str, bioguide: str) -> EntityResolutionOutput:
    return EntityResolutionOutput(
        canonical_id=cid,
        entity_type="person",
        display_name="Sen",
        external_ids=[f"bioguide:{bioguide}"],
        known_at=datetime(2023, 1, 1, tzinfo=UTC),
        source_anchors=[_anchor()],
    )


def _corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "contract_records"
    write_contract_corpus([_person("cp-schumer", "S000148")], directory=corpus, as_of=_OBS)
    return corpus


def _handler(mods_by_date: dict[str, str]):
    def handler(request: httpx.Request) -> httpx.Response:
        # URL is .../CREC-<date>/mods.xml
        token = request.url.path.split("CREC-")[1].split("/")[0]
        if token in mods_by_date:
            return httpx.Response(200, text=mods_by_date[token])
        return httpx.Response(404)

    return handler


def _client(mods_by_date: dict[str, str]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(_handler(mods_by_date)))


def test_date_range() -> None:
    days = date_range(date(2023, 1, 1), date(2023, 1, 3))
    assert days == [date(2023, 1, 1), date(2023, 1, 2), date(2023, 1, 3)]


def test_bill_mentions_dedup_and_cap() -> None:
    mentions = bill_mentions("see H.R. 1478, S. 509, and H.R. 1478 again")
    assert mentions == ["H.R.1478", "S.509"]
    assert len(bill_mentions(" ".join(f"H.R. {n}" for n in range(100)), limit=5)) == 5


def test_backfill_writes_edges_and_resolves_members(tmp_path: Path) -> None:
    out = tmp_path / "crec_edges.jsonl"
    progress = backfill_crec_speeches(
        start=date(2023, 3, 9),
        end=date(2023, 3, 9),
        out_path=out,
        corpus_directory=_corpus(tmp_path),
        client=_client({"2023-03-09": _MODS}),
        first_observed_at=_OBS,
    )
    assert progress.issues_found == 1
    assert progress.edges_written == 1  # Schumer resolved
    assert progress.members_unresolved == 1  # Z999999 not in corpus
    edge = json.loads(out.read_text().splitlines()[0])
    assert edge["edge_type"] == "floor_speech"
    assert edge["src_id"] == "cp-schumer"
    assert edge["dst_id"] == "congressional_record:2023-03-09"
    assert "H.R.1478" in edge["bills_mentioned"]


def test_backfill_skips_non_session_days_and_is_resumable(tmp_path: Path) -> None:
    out = tmp_path / "crec_edges.jsonl"
    corpus = _corpus(tmp_path)
    client = _client({"2023-03-09": _MODS})  # only the 9th is a session day
    first = backfill_crec_speeches(
        start=date(2023, 3, 8),
        end=date(2023, 3, 10),
        out_path=out,
        corpus_directory=corpus,
        client=client,
        first_observed_at=_OBS,
    )
    assert first.days_checked == 3 and first.issues_found == 1 and first.edges_written == 1

    # resume the same window -> the 9th is done; only the 404 days are re-checked
    second = backfill_crec_speeches(
        start=date(2023, 3, 8),
        end=date(2023, 3, 10),
        out_path=out,
        corpus_directory=corpus,
        client=_client({"2023-03-09": _MODS}),
        first_observed_at=_OBS,
    )
    assert second.edges_written == 0
    assert second.edges_total == 1


def test_done_dates_tolerates_blank_and_non_crec_lines(tmp_path: Path) -> None:
    out = tmp_path / "crec_edges.jsonl"
    # a blank line and a non-CREC edge precede a real one in the feed
    out.write_text(
        '\n{"dst_id": "cb-1", "edge_type": "vote"}\n'
        '{"dst_id": "congressional_record:2023-03-09", "edge_type": "floor_speech"}\n',
        encoding="utf-8",
    )
    progress = backfill_crec_speeches(
        start=date(2023, 3, 9),
        end=date(2023, 3, 9),
        out_path=out,
        corpus_directory=_corpus(tmp_path),
        client=_client({"2023-03-09": _MODS}),
        first_observed_at=_OBS,
    )
    # the 9th is already in the feed -> skipped, nothing new written
    assert progress.edges_written == 0


def test_backfill_respects_max_days(tmp_path: Path) -> None:
    out = tmp_path / "crec_edges.jsonl"
    progress = backfill_crec_speeches(
        start=date(2023, 3, 1),
        end=date(2023, 3, 31),
        out_path=out,
        corpus_directory=_corpus(tmp_path),
        client=_client({"2023-03-09": _MODS}),
        first_observed_at=_OBS,
        max_days=3,
    )
    assert progress.days_checked == 3


def test_backfill_skips_issue_with_no_speeches(tmp_path: Path) -> None:
    out = tmp_path / "crec_edges.jsonl"
    empty = '<mods:mods xmlns:mods="http://www.loc.gov/mods/v3"></mods:mods>'
    progress = backfill_crec_speeches(
        start=date(2023, 3, 9),
        end=date(2023, 3, 9),
        out_path=out,
        corpus_directory=_corpus(tmp_path),
        client=_client({"2023-03-09": empty}),
        first_observed_at=_OBS,
    )
    assert progress.issues_found == 0 and progress.edges_written == 0


def test_backfill_default_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import src.runtime.crec_backfill as mod

    real = httpx.Client
    monkeypatch.setattr(
        mod.httpx,
        "Client",
        lambda **_kw: real(transport=httpx.MockTransport(_handler({"2023-03-09": _MODS}))),
    )
    progress = backfill_crec_speeches(
        start=date(2023, 3, 9),
        end=date(2023, 3, 9),
        out_path=tmp_path / "e.jsonl",
        corpus_directory=_corpus(tmp_path),
        first_observed_at=_OBS,
    )
    assert progress.edges_written == 1
