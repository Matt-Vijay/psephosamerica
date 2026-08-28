from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from src.time_machine.govinfo_text import CacheIntegrityError
from src.time_machine.patch_probe import (
    ProbeError,
    _official_url,
    acquire_one,
    inventory_existing,
    verify_acquisition_receipt,
)


def _existing_govinfo_cache(root: Path) -> bytes:
    content = b"<engrossedAmendment><amendMain>At the end, add X.</amendMain></engrossedAmendment>"
    digest = hashlib.sha256(content).hexdigest()
    relative = f"raw/govinfo_bills/sha256/{digest[:2]}/{digest}.xml"
    path = root / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    package_id = "BILLS-116hr1865eas"
    manifest = {
        "schema_version": 1,
        "artifacts": [
            {
                "package_id": package_id,
                "congress": 116,
                "bill_type": "hr",
                "bill_number": 1865,
                "version_code": "eas",
                "revision": 1,
                "source_url": (
                    f"https://www.govinfo.gov/content/pkg/{package_id}/xml/{package_id}.xml"
                ),
                "relative_path": relative,
                "content_sha256": digest,
                "byte_count": len(content),
                "observed_at": "2026-08-20T00:00:00Z",
                "issued_date": None,
                "root_tag": "engrossedAmendment",
                "body_tags": ["amendMain"],
                "acquisition_url": (
                    "https://raw.githubusercontent.com/usgpo/uslm/main/"
                    f"samples/{package_id}.xml"
                ),
            }
        ],
    }
    manifest_path = root / "raw/govinfo_bills/manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return content


def test_existing_inventory_rehashes_and_does_not_infer_a_patch_edge(tmp_path: Path) -> None:
    content = _existing_govinfo_cache(tmp_path)
    receipt = inventory_existing(tmp_path, "data/time_machine")

    assert receipt["totals"] == {
        "artifacts": 1,
        "bytes": len(content),
        "published_at_present": 0,
        "published_at_missing": 1,
        "by_kind": {"official_gpo_sample_amendment_xml": 1},
    }
    artifact = receipt["artifacts"][0]
    assert artifact["content_semantics"] == "exact_official_gpo_sample_xml_bytes"
    assert artifact["source_url_role"] == "canonical_govinfo_package_url_byte_identity_unverified"
    assert artifact["source_relationships"] == [
        {"type": "belongs_to_bill_identity", "target": "bill:us:116:hr:1865"}
    ]
    assert "successor" not in json.dumps(artifact["source_relationships"]).lower()


def test_existing_inventory_detects_changed_bytes(tmp_path: Path) -> None:
    _existing_govinfo_cache(tmp_path)
    object_path = next((tmp_path / "raw/govinfo_bills/sha256").glob("*/*.xml"))
    object_path.write_bytes(b"changed")

    with pytest.raises(CacheIntegrityError, match="failed re-hash"):
        inventory_existing(tmp_path)


def test_acquisition_is_capped_content_addressed_and_resumable(tmp_path: Path) -> None:
    calls = 0
    body = b'{"packages":["BILLS-118hr1ih"]}'

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            content=body,
            headers={
                "Content-Type": "application/json",
                "ETag": '"fixture"',
                "Last-Modified": "Wed, 20 Aug 2026 00:00:00 GMT",
            },
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        first = acquire_one(
            store_root=tmp_path,
            url="https://www.govinfo.gov/bulkdata/json/BILLS/118/1/hr",
            identifier="govinfo-bills-118-1-hr-index",
            kind="package_index_metadata_json",
            relationships=("enumerates=collection:BILLS-118-1-hr",),
            cap_bytes=1024,
            acquired_at=datetime(2026, 8, 20, tzinfo=UTC),
            client=client,
        )
        second = acquire_one(
            store_root=tmp_path,
            url="https://www.govinfo.gov/bulkdata/json/BILLS/118/1/hr",
            identifier="govinfo-bills-118-1-hr-index",
            kind="package_index_metadata_json",
            cap_bytes=1024,
            client=client,
        )
        with pytest.raises(ProbeError, match="different identifier"):
            acquire_one(
                store_root=tmp_path,
                url="https://www.govinfo.gov/bulkdata/json/BILLS/118/1/hr",
                identifier="alias-that-must-not-trigger-a-second-download",
                kind="package_index_metadata_json",
                cap_bytes=1024,
                client=client,
            )

    digest = hashlib.sha256(body).hexdigest()
    assert calls == 1
    assert second == first
    assert first["sha256"] == digest
    assert first["content_semantics"] == "metadata_only"
    assert first["http_last_modified"] == "Wed, 20 Aug 2026 00:00:00 GMT"
    assert (tmp_path / first["content_path"]).read_bytes() == body
    assert verify_acquisition_receipt(tmp_path) == {
        "artifacts": 1,
        "objects": 1,
        "retained_bytes": len(body),
        "network_bytes_acquired": len(body),
    }


def test_acquisition_rejects_cumulative_cap_overrun(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = b"1234" if request.url.path.endswith("/first") else b"567890"
        return httpx.Response(
            200,
            content=body,
            headers={"Content-Length": str(len(body)), "Content-Type": "application/json"},
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        acquire_one(
            store_root=tmp_path,
            url="https://www.govinfo.gov/bulkdata/first",
            identifier="first-index",
            kind="probe_metadata_json",
            cap_bytes=9,
            client=client,
        )
        with pytest.raises(ProbeError, match="exceeds cumulative"):
            acquire_one(
                store_root=tmp_path,
                url="https://www.govinfo.gov/bulkdata/second",
                identifier="second-index",
                kind="probe_metadata_json",
                cap_bytes=9,
                client=client,
            )

    assert verify_acquisition_receipt(tmp_path) == {
        "artifacts": 1,
        "objects": 1,
        "retained_bytes": 4,
        "network_bytes_acquired": 4,
    }


def test_exact_xml_kind_rejects_html_even_on_matching_official_url(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"<html><body>metadata page</body></html>",
            headers={"Content-Type": "text/html"},
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProbeError, match="non-XML media type"):
            acquire_one(
                store_root=tmp_path,
                url="https://www.govinfo.gov/content/pkg/BILLS-118hr1ih/xml/"
                "BILLS-118hr1ih.xml",
                identifier="BILLS-118hr1ih",
                kind="official_bill_version_xml",
                cap_bytes=1024,
                client=client,
            )

    assert not (tmp_path / "manifest.json").exists()


def test_exact_xml_kind_validates_package_and_root(tmp_path: Path) -> None:
    body = b"<bill><legis-body><section>Text</section></legis-body></bill>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=body,
            headers={"Content-Type": "application/xml"},
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        row = acquire_one(
            store_root=tmp_path,
            url="https://www.govinfo.gov/content/pkg/BILLS-118hr1ih/xml/BILLS-118hr1ih.xml",
            identifier="BILLS-118hr1ih",
            kind="official_bill_version_xml",
            cap_bytes=1024,
            client=client,
        )

    assert row["content_semantics"] == "exact_official_xml_bytes"
    assert verify_acquisition_receipt(tmp_path)["retained_bytes"] == len(body)


def test_official_schema_kind_rejects_html(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"<html><body>not a schema</body></html>",
            headers={"Content-Type": "text/html"},
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProbeError, match="non-schema media type"):
            acquire_one(
                store_root=tmp_path,
                url="https://raw.githubusercontent.com/usgpo/uslm/main/uslm.xsd",
                identifier="uslm.xsd",
                kind="official_schema",
                cap_bytes=1024,
                client=client,
            )


def test_verify_rejects_missing_or_tampered_receipts(tmp_path: Path) -> None:
    with pytest.raises(ProbeError, match="does not exist"):
        verify_acquisition_receipt(tmp_path)

    body = b"{}"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=body,
            headers={"Content-Type": "application/json"},
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        acquire_one(
            store_root=tmp_path,
            url="https://www.govinfo.gov/bulkdata/index.json",
            identifier="index",
            kind="probe_metadata_json",
            cap_bytes=1024,
            client=client,
        )

    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["totals"]["retained_bytes"] = 999
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ProbeError, match="totals"):
        verify_acquisition_receipt(tmp_path)


@pytest.mark.parametrize(
    "url",
    [
        "http://www.govinfo.gov/content/pkg/BILLS-118hr1ih/xml/BILLS-118hr1ih.xml",
        "https://example.com/BILLS-118hr1ih.xml",
        "https://api.congress.gov/v3/bill?api_key=secret",
        "https://raw.githubusercontent.com/not-gpo/project/main/bill.xml",
    ],
)
def test_official_allowlist_rejects_unofficial_or_secret_urls(url: str) -> None:
    with pytest.raises(ProbeError):
        _official_url(url)


def test_official_allowlist_accepts_congress_document_files() -> None:
    url = "https://www.congress.gov/116/bills/hr1865/BILLS-116hr1865eah.pdf"
    assert _official_url(url) == url
