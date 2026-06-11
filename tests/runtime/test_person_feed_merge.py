from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from src.graph.contracts import ContractSourceAnchor, EntityResolutionOutput
from src.graph.export import read_contract_corpus, write_contract_corpus
from src.runtime.person_feed_merge import (
    load_person_feed,
    merge_person_feed_into_corpus,
)

_NOW = datetime(2026, 6, 11, tzinfo=UTC)
_KNOWN = datetime(2024, 1, 1, tzinfo=UTC)


def _person(cid: str, name: str) -> EntityResolutionOutput:
    return EntityResolutionOutput(
        canonical_id=cid,
        entity_type="person",
        display_name=name,
        external_ids=[f"legistar:alpha:{cid}"],
        known_at=_KNOWN,
        source_anchors=[
            ContractSourceAnchor(
                source_system="legistar",
                record_id=cid,
                source_url="https://webapi.legistar.com/v1/alpha/persons",
                content_sha256="a" * 64,
                content_address="sha256/aa/aa/" + "a" * 64,
                known_at=_KNOWN,
                valid_from=_KNOWN.date(),
            )
        ],
    )


def _feed(tmp_path: Path, rows: list[EntityResolutionOutput], blanks: bool = False) -> Path:
    path = tmp_path / "persons.jsonl"
    text = "".join(row.model_dump_json() + "\n" for row in rows)
    if blanks:
        text = "\n" + text + "\n"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_person_feed_dedups_and_skips_blanks(tmp_path: Path) -> None:
    a = _person("ce-a", "Ada")
    feed = _feed(tmp_path, [a, a, _person("ce-b", "Bob")], blanks=True)
    rows = load_person_feed(feed)
    assert set(rows) == {"ce-a", "ce-b"}
    assert load_person_feed(tmp_path / "absent.jsonl") == {}


def test_merge_adds_only_unseen(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    enriched = _person("ce-a", "Ada Enriched")
    write_contract_corpus([enriched], directory=corpus, as_of=_NOW)
    feed = _feed(tmp_path, [_person("ce-a", "Ada Feed"), _person("ce-b", "Bob")])
    report = merge_person_feed_into_corpus(feed_path=feed, main_directory=corpus, as_of=_NOW)
    assert report.feed_rows == 2 and report.feed_unique == 2
    assert report.persons_added == 1 and report.deltas_written == 1
    rows = {r.canonical_id: r for r in read_contract_corpus(corpus)}
    assert rows["ce-a"].display_name == "Ada Enriched"  # existing row never clobbered
    assert rows["ce-b"].display_name == "Bob"


def test_merge_is_idempotent(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    write_contract_corpus([], directory=corpus, as_of=_NOW)
    feed = _feed(tmp_path, [_person("ce-a", "Ada")])
    merge_person_feed_into_corpus(feed_path=feed, main_directory=corpus, as_of=_NOW)
    again = merge_person_feed_into_corpus(feed_path=feed, main_directory=corpus, as_of=_NOW)
    assert again.persons_added == 0 and again.deltas_written == 0


def test_merge_missing_feed_is_noop(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    write_contract_corpus([_person("ce-a", "Ada")], directory=corpus, as_of=_NOW)
    report = merge_person_feed_into_corpus(
        feed_path=tmp_path / "absent.jsonl", main_directory=corpus
    )
    assert report.persons_added == 0 and report.corpus_total == 1
