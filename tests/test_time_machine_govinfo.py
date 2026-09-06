from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from src.time_machine.govinfo_text import (
    BillTextParseError,
    CacheIntegrityError,
    InventoryRequiredError,
    cache_bill_text_bytes,
    fetch_bill_text,
    load_govinfo_manifest,
    parse_bill_text_xml,
    parse_package_filename,
    parse_package_id,
)

USLM_XML = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<uslm:bill xmlns:uslm="http://xml.house.gov/schemas/uslm/1.0">
  <uslm:meta>
    <uslm:docDate date="2024-05-03">May 3, 2024</uslm:docDate>
    <uslm:summary>CRS metadata must never become bill text.</uslm:summary>
  </uslm:meta>
  <uslm:main>
    <uslm:meta><uslm:summary>Even nested CRS metadata is excluded.</uslm:summary></uslm:meta>
    <uslm:section>
      <uslm:num>SECTION 1.</uslm:num>
      <uslm:heading>SHORT TITLE.</uslm:heading>
      <uslm:content>This Act may be cited as the Example Act.</uslm:content>
    </uslm:section>
  </uslm:main>
</uslm:bill>
"""

REVISED_XML = USLM_XML.replace(b"Example Act", b"Revised Example Act")


def _write_inventory(output_root: Path) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "inventory.json").write_text(
        json.dumps({"schema_version": 1, "artifacts": []}), encoding="utf-8"
    )


def _client(content: bytes, calls: list[str]) -> httpx.Client:
    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=content, headers={"content-type": "application/xml"})

    return httpx.Client(transport=httpx.MockTransport(respond))


def test_package_parser_is_strict_and_keeps_digit_bearing_version_codes() -> None:
    package = parse_package_id("BILLS-118hr302eas2")

    assert package.congress == 118
    assert package.bill_type == "hr"
    assert package.bill_number == 302
    assert package.version_code == "eas2"
    assert package.bill_key == (118, "hr", 302)
    assert package.source_url == (
        "https://www.govinfo.gov/content/pkg/BILLS-118hr302eas2/xml/BILLS-118hr302eas2.xml"
    )
    assert parse_package_filename("BILLS-119sjres1enr.xml").bill_type == "sjres"

    invalid = [
        "bills-118hr302eas2",
        "BILLS-0118hr302eas2",
        "BILLS-118HR302eas2",
        "BILLS-118hr0302eas2",
        "BILLS-118hr302",
        "BILLS-118x302ih",
        "BILLS-118hr302eas2.xml",
        "raw/BILLS-118hr302eas2",
        " BILLS-118hr302eas2",
    ]
    for value in invalid:
        with pytest.raises(ValueError):
            parse_package_id(value)


def test_parser_extracts_only_namespaced_substantive_body_and_issue_date() -> None:
    parsed = parse_bill_text_xml(USLM_XML)

    assert parsed.root_tag == "bill"
    assert parsed.body_tags == ("main",)
    assert parsed.issued_date == date(2024, 5, 3)
    assert "SECTION 1." in parsed.text_content
    assert "Example Act" in parsed.text_content
    assert "CRS metadata" not in parsed.text_content


def test_parser_supports_legacy_resolution_body_but_rejects_billstatus_metadata() -> None:
    legacy = b"""\
    <resolution>
      <form><official-title>A title outside the legislative body.</official-title></form>
      <resolution-body><section><text>Resolved, That the rule is adopted.</text></section></resolution-body>
    </resolution>
    """
    parsed = parse_bill_text_xml(legacy)
    assert parsed.body_tags == ("resolution-body",)
    assert parsed.text_content == "Resolved, That the rule is adopted."
    assert "title outside" not in parsed.text_content

    billstatus = b"""\
    <billStatus><bill><title>Metadata title</title><summaries>
      <summary><text>CRS summary only</text></summary>
    </summaries></bill></billStatus>
    """
    with pytest.raises(BillTextParseError, match="no legis-body"):
        parse_bill_text_xml(billstatus)


def test_inventory_is_required_before_any_http_request(tmp_path: Path) -> None:
    calls: list[str] = []
    client = _client(USLM_XML, calls)

    with pytest.raises(InventoryRequiredError, match="hash-first inventory"):
        fetch_bill_text("BILLS-118hr1ih", tmp_path, client=client)

    assert calls == []


def test_official_browser_bytes_use_same_immutable_manifest_path(tmp_path: Path) -> None:
    _write_inventory(tmp_path)
    source = (
        "https://github.com/usgpo/uslm/raw/refs/heads/main/"
        "bill-version-samples-september-2024/BILLS-118s1325rs.xml"
    )
    first = cache_bill_text_bytes(
        "BILLS-118s1325rs",
        tmp_path,
        USLM_XML,
        observed_at=datetime(2025, 1, 1, tzinfo=UTC),
        acquisition_url=source,
    )
    second = cache_bill_text_bytes(
        "BILLS-118s1325rs",
        tmp_path,
        USLM_XML,
        acquisition_url=source,
    )
    assert first.from_cache is False
    assert second.from_cache is True
    assert second.raw_path == first.raw_path
    assert load_govinfo_manifest(tmp_path)[0].acquisition_url == source


def test_browser_cache_rejects_a_source_for_a_different_package(tmp_path: Path) -> None:
    _write_inventory(tmp_path)
    mismatched = (
        "https://github.com/usgpo/uslm/raw/refs/heads/main/"
        "bill-version-samples-september-2024/BILLS-118s999rs.xml"
    )

    with pytest.raises(ValueError, match="must identify BILLS-118s1325rs"):
        cache_bill_text_bytes(
            "BILLS-118s1325rs",
            tmp_path,
            USLM_XML,
            acquisition_url=mismatched,
        )

    assert load_govinfo_manifest(tmp_path) == ()


def test_fetch_resumes_by_manifest_and_preserves_content_revisions(tmp_path: Path) -> None:
    _write_inventory(tmp_path)
    first_calls: list[str] = []
    first_seen = datetime(2025, 1, 2, 3, 4, tzinfo=UTC)

    first = fetch_bill_text(
        "BILLS-118hr1ih",
        tmp_path,
        client=_client(USLM_XML, first_calls),
        observed_at=first_seen,
    )

    first_digest = hashlib.sha256(USLM_XML).hexdigest()
    assert first_calls == [first.source_url]
    assert first.revision == 1
    assert first.content_sha256 == first_digest
    assert first.raw_path == (
        tmp_path.resolve() / "raw/govinfo_bills/sha256" / first_digest[:2] / f"{first_digest}.xml"
    )
    assert first.raw_path.read_bytes() == USLM_XML
    assert first.observed_at == first_seen
    assert first.issued_date == date(2024, 5, 3)
    assert first.from_cache is False
    first_manifest = load_govinfo_manifest(tmp_path)
    assert first_manifest[0].source_url == first.source_url
    assert first_manifest[0].content_sha256 == first_digest

    def fail_if_called(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("resumable fetch unexpectedly contacted GovInfo")

    offline = httpx.Client(transport=httpx.MockTransport(fail_if_called))
    resumed = fetch_bill_text("BILLS-118hr1ih", tmp_path, client=offline)
    assert resumed.from_cache is True
    assert resumed.revision == 1
    assert load_govinfo_manifest(tmp_path) == first_manifest

    second_seen = datetime(2025, 2, 3, 4, 5, tzinfo=UTC)
    second_calls: list[str] = []
    revised = fetch_bill_text(
        "BILLS-118hr1ih",
        tmp_path,
        client=_client(REVISED_XML, second_calls),
        observed_at=second_seen,
        refresh=True,
    )
    assert revised.revision == 2
    assert revised.raw_path != first.raw_path
    assert revised.raw_path.read_bytes() == REVISED_XML
    assert first.raw_path.read_bytes() == USLM_XML
    assert revised.observed_at == second_seen
    assert len(load_govinfo_manifest(tmp_path)) == 2

    third_seen = datetime(2025, 3, 4, 5, 6, tzinfo=UTC)
    reverted = fetch_bill_text(
        "BILLS-118hr1ih",
        tmp_path,
        client=_client(USLM_XML, []),
        observed_at=third_seen,
        refresh=True,
    )
    assert reverted.revision == 3
    assert reverted.raw_path == first.raw_path
    assert reverted.observed_at == third_seen
    assert len(load_govinfo_manifest(tmp_path)) == 3
    assert len(list((tmp_path / "raw/govinfo_bills/sha256").glob("*/*.xml"))) == 2


def test_refresh_with_identical_bytes_is_idempotent(tmp_path: Path) -> None:
    _write_inventory(tmp_path)
    package = "BILLS-118s1is"
    fetch_bill_text(package, tmp_path, client=_client(USLM_XML, []))

    result = fetch_bill_text(
        package,
        tmp_path,
        client=_client(USLM_XML, []),
        refresh=True,
    )

    assert result.revision == 1
    assert result.from_cache is False
    assert len(load_govinfo_manifest(tmp_path)) == 1


def test_manifested_object_is_never_silently_replaced(tmp_path: Path) -> None:
    _write_inventory(tmp_path)
    artifact = fetch_bill_text("BILLS-118hr1ih", tmp_path, client=_client(USLM_XML, []))
    artifact.raw_path.write_bytes(b"corrupt")

    with pytest.raises(CacheIntegrityError, match="does not match manifest"):
        fetch_bill_text("BILLS-118hr1ih", tmp_path, client=_client(REVISED_XML, []))
