from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from src.graph.contracts import ContractSourceAnchor, EntityResolutionOutput, build_bill_output
from src.graph.export import read_contract_corpus, write_contract_corpus
from src.graph.ingest.govinfo_billstatus import (
    billstatus_bill_row,
    canonical_bill_id,
    parse_billstatus_xml,
)
from src.runtime.bill_corpus_merge import merge_bill_corpus_into_main

_AS_OF = datetime(2025, 1, 1, tzinfo=UTC)


def _anchor(system: str) -> ContractSourceAnchor:
    return ContractSourceAnchor(
        source_system=system,
        record_id="r1",
        source_url="https://x",
        content_sha256="a" * 64,
        content_address="sha256/aa/aa/" + "a" * 64,
        known_at=datetime(2023, 1, 1, tzinfo=UTC),
        valid_from=datetime(2023, 1, 1, tzinfo=UTC).date(),
    )


def _person(canonical_id: str) -> EntityResolutionOutput:
    return EntityResolutionOutput(
        canonical_id=canonical_id,
        entity_type="person",
        display_name="Rep. Example",
        external_ids=["bioguide:x000001"],
        known_at=datetime(2023, 1, 1, tzinfo=UTC),
        source_anchors=[_anchor("house_clerk")],
    )


def _billstatus_xml(number: int, title: str) -> str:
    return (
        f"<billStatus><bill><congress>118</congress><type>HR</type><number>{number}</number>"
        f"<title>{title}</title><introducedDate>2023-03-14</introducedDate></bill></billStatus>"
    )


def _govinfo_bill(number: int, title: str) -> EntityResolutionOutput:
    return billstatus_bill_row(
        parse_billstatus_xml(_billstatus_xml(number, title)),
        source_url="https://x",
        content_sha256="b" * 64,
        first_observed_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def test_merge_preserves_persons_supersedes_and_adds_bills(tmp_path: Path) -> None:
    main = tmp_path / "contract_records"
    bills = tmp_path / "govinfo_bills"

    # Pre-existing main corpus: one person + a thin pre-existing bill #1.
    old_bill_id = canonical_bill_id(parse_billstatus_xml(_billstatus_xml(1, "x")))
    old_bill = build_bill_output(
        canonical_bill_id=old_bill_id,
        display_name="Old thin title for HR1",
        source_anchors=[_anchor("house_clerk")],
    )
    write_contract_corpus([_person("cp-1"), old_bill], directory=main, as_of=_AS_OF)

    # govinfo bill corpus: HR1 (supersedes old) + HR2 (new).
    write_contract_corpus(
        [_govinfo_bill(1, "Lower Energy Costs Act"), _govinfo_bill(2, "Second Act")],
        directory=bills,
        as_of=_AS_OF,
    )

    report = merge_bill_corpus_into_main(main_directory=main, bills_directory=bills, as_of=_AS_OF)
    assert report.main_before == 2
    assert report.bills_in == 2
    assert report.persons == 1  # person preserved
    assert report.bills_after == 2  # HR1 (superseded) + HR2 (new)
    assert report.merged_total == 3
    # 1 created (HR2) + 1 updated (HR1 title/source change)
    assert report.deltas_written == 2

    merged = read_contract_corpus(main)
    by_id = {r.canonical_id: r for r in merged}
    assert by_id["cp-1"].entity_type == "person"
    # HR1 now carries the govinfo title, not the old thin one
    assert by_id[old_bill_id].display_name == "Lower Energy Costs Act"


def test_merge_idempotent_second_pass_no_deltas(tmp_path: Path) -> None:
    main = tmp_path / "contract_records"
    bills = tmp_path / "govinfo_bills"
    write_contract_corpus([_person("cp-1")], directory=main, as_of=_AS_OF)
    write_contract_corpus([_govinfo_bill(1, "Act One")], directory=bills, as_of=_AS_OF)

    first = merge_bill_corpus_into_main(main_directory=main, bills_directory=bills, as_of=_AS_OF)
    assert first.deltas_written == 1  # HR1 created
    second = merge_bill_corpus_into_main(main_directory=main, bills_directory=bills, as_of=_AS_OF)
    assert second.deltas_written == 0  # nothing changed
    assert second.persons == 1 and second.bills_after == 1
