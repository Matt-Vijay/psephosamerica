from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from src.regpatch.compiler import (
    CompileError,
    _changed_regions,
    acquire_official_source,
    canonical_table_signature,
    compile_episode,
    episode_spec_from_mapping,
    expand_candidate_windows,
    load_episode_spec,
    probe_federal_register,
)
from src.regpatch.corpus import _classify_compiled_episode

PILOT_SPEC = Path("src/regpatch/pilot/source_spec.json")
TARGET_SHA256 = "778b6079ddea582cdcfe1fe5adba9badb47c60752ce6100c30ecd3c267263061"


def test_changed_region_receipts_have_stable_natural_order() -> None:
    from defusedxml import ElementTree as SafeElementTree

    source = (
        '<DIV5 N="73" TYPE="PART">'
        + "".join(
            f'<DIV8 N="{identity}" TYPE="SECTION"><P>Old</P></DIV8>'
            for identity in ("73.10", "73.2", "73.9")
        )
        + "</DIV5>"
    )
    before = SafeElementTree.fromstring(source)
    after = SafeElementTree.fromstring(source.replace("Old", "New"))
    spec = replace(load_episode_spec(PILOT_SPEC), requested_sections=())

    changed, receipts = _changed_regions(before, after, spec)

    assert [region.identity for region in changed.values()] == ["73.2", "73.9", "73.10"]
    assert [row["region_id"] for row in receipts] == ["73.2", "73.9", "73.10"]


def test_real_seed_compiles_with_exact_causal_receipt_and_private_target(
    tmp_path: Path,
) -> None:
    output = tmp_path / "episode"
    result = compile_episode(PILOT_SPEC, output)

    assert result == {
        "status": "ACCEPTED",
        "episode_id": "public-47-cfr-73-2024-01928",
        "changed_sections": ["73.622"],
        "rule_count": 1,
        "operation_shapes": ["add", "authority_citation", "remove", "table_edit"],
        "manifest_sha256": result["manifest_sha256"],
        "target_sha256": TARGET_SHA256,
    }
    public = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    private = json.loads((output / "evaluator" / "receipt.json").read_text(encoding="utf-8"))
    public_bytes = (output / "manifest.json").read_bytes()
    assert TARGET_SHA256.encode() not in public_bytes
    assert b"evaluator/target.xml" not in public_bytes
    assert b"/Users/" not in public_bytes
    assert public["clocks"]["rules"][0] == {
        "amendment_date": "2024-01-31",
        "amendment_date_semantics": "eCFR versioner amendment_date",
        "document_number": "2024-01928",
        "ecfr_amendment_date": "2024-01-31",
        "ecfr_issue_date": "2024-01-31",
        "ecfr_issue_date_basis": None,
        "federal_register_issue_date": "2024-01-31",
        "legal_effective_date": "2024-03-01",
        "order": 1,
        "observed_incorporation_dates": ["2024-01-31"],
        "publication_date": "2024-01-31",
    }
    assert private["changed_regions"] == [
        {
            "after_present": True,
            "after_raw_sha256": "a0376ad277f37668bff64a8a40af7ccf19674d04c554a67e927f27d31ba701cf",
            "after_sha256": "59b2fde18f835b381cc05289182440e18bcab92a946b17c0c8192e701c37e62d",
            "before_present": True,
            "before_raw_sha256": "2996e3d3fc6fdeab79699b8b7f02826cc0fb8509cf6ab304c91890ba3abef4c1",
            "before_sha256": "4c0951c1314e1303cd601f9b9878ffa3d2ca47f47487737365b553a243b43a08",
            "citation": "47 CFR 73.622",
            "kind": "section",
            "projection": "substantive_v3_xref_cita_and_table_presentation_normalized",
            "region_id": "73.622",
            "section": "73.622",
        }
    ]
    evidence = private["causal_evidence"]
    assert [
        (row["document_number"], row["federal_register_amendment_page"]) for row in evidence
    ] == [("2024-01928", "89 FR 6024")]
    assert evidence[0]["amendatory_instruction"] == 2


def test_causal_gate_reports_precise_volume_mismatch(tmp_path: Path) -> None:
    source = json.loads(PILOT_SPEC.read_text(encoding="utf-8"))
    metadata_path = tmp_path / "document.json"
    metadata = json.loads(
        (PILOT_SPEC.parent / source["sources"]["rules"][0]["metadata"]["path"]).read_text(
            encoding="utf-8"
        )
    )
    metadata["volume"] = 90
    metadata["citation"] = "90 FR 6023"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    source["sources"]["rules"][0]["metadata"].update({"path": str(metadata_path), "sha256": None})
    source["sources"]["rules"][0]["metadata"].pop("sha256")
    spec = episode_spec_from_mapping(source, base_dir=PILOT_SPEC.parent)

    with pytest.raises(CompileError) as caught:
        compile_episode(spec, tmp_path / "episode")
    assert caught.value.code == "FR_VOLUME_MISMATCH"
    assert caught.value.evidence == {"xref_volume": 89, "supplied_volumes": [90]}


def test_acquisition_is_content_addressed_capped_and_resumable(tmp_path: Path) -> None:
    calls = 0
    body = b"<DIV5 N='73' TYPE='PART'/>"

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            content=body,
            headers={
                "Content-Length": str(len(body)),
                "Content-Type": "application/xml",
                "ETag": '"fixture"',
                "Set-Cookie": "must-not-be-retained=1",
            },
            request=request,
        )

    acquired_at = datetime(2026, 8, 28, 14, 0, tzinfo=UTC)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        first = acquire_official_source(
            tmp_path,
            url="https://www.ecfr.gov/api/versioner/v1/full/2024-01-31/title-47.xml?part=73",
            identifier="title-47-part-73-2024-01-31",
            role="successor_ecfr",
            cap_bytes=1024,
            client=client,
            acquired_at=acquired_at,
        )
        second = acquire_official_source(
            tmp_path,
            url="https://www.ecfr.gov/api/versioner/v1/full/2024-01-31/title-47.xml?part=73",
            identifier="title-47-part-73-adjacent-base",
            role="base_ecfr",
            cap_bytes=1024,
            client=client,
        )

    assert calls == 1
    assert second["sha256"] == first["sha256"]
    assert second["role"] == "base_ecfr"
    assert second["network_byte_count"] == 0
    assert second["reused_artifact_id"] == first["artifact_id"]
    assert first["content_path"].startswith(f"sha256/{first['sha256'][:2]}/")
    assert first["response_headers"] == {
        "content-length": str(len(body)),
        "content-type": "application/xml",
        "etag": '"fixture"',
    }
    assert "cookie" not in json.dumps(first).lower()
    receipt = json.loads((tmp_path / "acquisition.json").read_text(encoding="utf-8"))
    assert receipt["totals"] == {
        "artifacts": 2,
        "network_bytes_acquired": len(body),
        "objects": 1,
        "retained_bytes": len(body),
    }


def test_metadata_probe_emits_real_source_requests_without_fetching_candidate_bytes(
    tmp_path: Path,
) -> None:
    result = {
        "count": 1,
        "total_pages": 1,
        "results": [
            {
                "document_number": "2024-01928",
                "publication_date": "2024-01-31",
                "effective_on": "2024-03-01",
                "full_text_xml_url": (
                    "https://www.federalregister.gov/documents/full_text/xml/"
                    "2024/01/31/2024-01928.xml"
                ),
                "json_url": ("https://www.federalregister.gov/api/v1/documents/2024-01928.json"),
                "citation": "89 FR 6023",
                "volume": 89,
                "start_page": 6023,
                "end_page": 6024,
                "type": "Rule",
                "action": "Final rule.",
                "title": "Television Broadcasting Services",
                "agencies": [{"name": "Federal Communications Commission"}],
                "cfr_references": [{"title": 47, "part": "73"}],
            }
        ],
    }
    body = json.dumps(result).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "www.federalregister.gov"
        assert request.url.path == "/api/v1/documents.json"
        return httpx.Response(
            200,
            content=body,
            headers={"Content-Type": "application/json"},
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = probe_federal_register(
            tmp_path,
            start_date=date(2024, 1, 31),
            end_date=date(2024, 1, 31),
            max_candidates=20,
            client=client,
            acquired_at=datetime(2026, 8, 28, 14, 0, tzinfo=UTC),
        )

    assert report["totals"] == {
        "metadata_candidates": 1,
        "distinct_documents": 1,
        "distinct_titles": 1,
        "distinct_agencies": 1,
        "rejected_metadata_rows": 0,
    }
    candidate = report["candidates"][0]
    assert candidate["status"] == "REQUIRES_ECFR_VERSION_PROBE"
    assert "before_ecfr_url" not in candidate
    assert "after_ecfr_url" not in candidate
    expanded = expand_candidate_windows(
        candidate,
        {
            "scope": {"title": 47, "part": "73"},
            "candidate_windows": [
                {
                    "ecfr_amendment_date": "2024-02-09",
                    "ecfr_issue_date": "2024-02-09",
                    "identifiers": ["73.6030"],
                    "record_count": 1,
                    "substantive_record_count": 1,
                }
            ],
        },
    )
    assert len(expanded) == 1
    assert expanded[0]["before_ecfr_url"].endswith("/full/2024-02-08/title-47.xml?part=73")
    assert expanded[0]["after_ecfr_url"].endswith("/full/2024-02-09/title-47.xml?part=73")
    request = json.loads((tmp_path / "probe-request.json").read_text(encoding="utf-8"))
    assert request["status"] == "COMPLETE"
    assert request["result_path"] == "probe.json"
    assert not (tmp_path / "candidates").exists()


def test_load_spec_rejects_noncontiguous_multi_rule_order(tmp_path: Path) -> None:
    payload = json.loads(PILOT_SPEC.read_text(encoding="utf-8"))
    duplicate = json.loads(json.dumps(payload["sources"]["rules"][0]))
    duplicate["order"] = 3
    payload["sources"]["rules"].append(duplicate)
    path = tmp_path / "source-spec.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CompileError, match="RULE_ORDER_INVALID"):
        load_episode_spec(path)


def test_table_projection_is_presentation_neutral_and_supports_cals_rows() -> None:
    from defusedxml import ElementTree as SafeElementTree

    html = SafeElementTree.fromstring(
        b"<TABLE class='layout'><TBODY><TR><TD colspan='2'>Alpha</TD></TR></TBODY></TABLE>"
    )
    wrapped = SafeElementTree.fromstring(
        b"<TABLE style='width:100%'><DIV><TR><TD colspan='2'>Alpha</TD></TR></DIV></TABLE>"
    )
    cals = SafeElementTree.fromstring(
        b"<TABLE><TGROUP><TBODY><ROW><ENTRY namest='c1' nameend='c2' morerows='1'>Alpha</ENTRY><ENTRY>Beta</ENTRY></ROW></TBODY></TGROUP></TABLE>"
    )
    cals_without_spans = SafeElementTree.fromstring(
        b"<TABLE><TGROUP><TBODY><ROW><ENTRY>Alpha</ENTRY><ENTRY>Beta</ENTRY></ROW></TBODY></TGROUP></TABLE>"
    )

    assert canonical_table_signature(html) == canonical_table_signature(wrapped)
    assert canonical_table_signature(cals) == (
        (),
        (
            (
                (
                    "ENTRY",
                    (("morerows", "1"), ("nameend", "c2"), ("namest", "c1")),
                    "Alpha",
                ),
                ("ENTRY", (), "Beta"),
            ),
        ),
    )
    assert canonical_table_signature(cals) != canonical_table_signature(cals_without_spans)


def test_independent_contribution_overlap_checks_nonadjacent_rule_pairs(
    tmp_path: Path,
) -> None:
    episode = tmp_path / "episode"
    (episode / "evaluator").mkdir(parents=True)
    manifest = {
        "inputs": {
            "rules": [
                {"document_number": "rule-a"},
                {"document_number": "rule-b"},
                {"document_number": "rule-c"},
            ]
        },
        "clocks": {
            "rules": [
                {"document_number": document, "ecfr_amendment_date": "2024-02-15"}
                for document in ("rule-a", "rule-b", "rule-c")
            ]
        },
    }
    receipt = {
        "causal_evidence": [
            {
                "document_number": document,
                "substantive_change": True,
                "changed_region": {"kind": "section", "region_id": region},
            }
            for document, region in (
                ("rule-a", "1.1"),
                ("rule-b", "1.2"),
                ("rule-c", "1.1"),
            )
        ],
        "changed_regions": [
            {"kind": "section", "region_id": "1.1"},
            {"kind": "section", "region_id": "1.2"},
        ],
    }
    (episode / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (episode / "evaluator" / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")

    classification = _classify_compiled_episode(episode)

    assert classification["contributions_disjoint"] is False
    assert classification["validated_independent_composition"] is False


def test_successor_citation_delta_maps_one_witnessed_rule_and_rejects_omission(
    tmp_path: Path,
) -> None:
    base = tmp_path / "before.xml"
    target = tmp_path / "after.xml"
    rule_xml = tmp_path / "rule.xml"
    metadata = tmp_path / "rule.json"
    base.write_text(
        """<DIV5 N="10" TYPE="PART" hierarchy_metadata="title-99">
        <DIV8 N="10.1" TYPE="SECTION"><P>Old A</P><CITA>[88 FR 1, Jan. 1, 2023]</CITA></DIV8>
        <DIV8 N="10.2" TYPE="SECTION"><P>Old B</P><CITA>[88 FR 2, Jan. 1, 2023]</CITA></DIV8>
        </DIV5>""",
        encoding="utf-8",
    )
    target.write_text(
        """<DIV5 N="10" TYPE="PART" hierarchy_metadata="title-99">
        <DIV8 N="10.1" TYPE="SECTION"><P>New A</P><CITA>[88 FR 1, Jan. 1, 2023; 90 FR 101, Jan. 2, 2025]</CITA></DIV8>
        <DIV8 N="10.2" TYPE="SECTION"><P>New B</P><CITA>[88 FR 2, Jan. 1, 2023]</CITA></DIV8>
        </DIV5>""",
        encoding="utf-8",
    )
    rule_xml.write_text(
        """<FEDREG><FRDOC>FR Doc. 2025-00001</FRDOC><PRTPAGE P="101"/>
        <REGTEXT TITLE="99" PART="10">
        <AMDPAR>1. Amend section 10.1 by revising it.</AMDPAR>
        <AMDPAR>2. Amend section 10.2 by revising it.</AMDPAR>
        </REGTEXT></FEDREG>""",
        encoding="utf-8",
    )
    metadata.write_text(
        json.dumps(
            {
                "document_number": "2025-00001",
                "volume": 90,
                "start_page": 100,
                "end_page": 102,
                "publication_date": "2025-01-02",
                "effective_on": "2025-01-02",
                "citation": "90 FR 100",
                "full_text_xml_url": (
                    "https://www.federalregister.gov/documents/full_text/xml/"
                    "2025/01/02/2025-00001.xml"
                ),
                # The API index can omit a part that authoritative REGTEXT includes.
                "cfr_references": [{"title": 99, "part": "11"}],
                "agencies": [{"name": "Fixture Agency"}],
            }
        ),
        encoding="utf-8",
    )

    acquired_at = "2026-08-28T18:00:00Z"

    def artifact(path: Path, source_url: str, media_type: str) -> dict[str, object]:
        return {
            "path": str(path),
            "source_url": source_url,
            "acquired_at": acquired_at,
            "response_headers": {},
            "media_type": media_type,
        }

    payload = {
        "episode_id": "citation-delta-fixture",
        "scope": {"title": 99, "parts": ["10"], "sections": [], "granularity": "part"},
        "window": {"base_date": "2025-01-01", "successor_date": "2025-01-02"},
        "sources": {
            "base": artifact(
                base,
                "https://www.ecfr.gov/api/versioner/v1/full/2025-01-01/title-99.xml?part=10",
                "application/xml",
            ),
            "target": artifact(
                target,
                "https://www.ecfr.gov/api/versioner/v1/full/2025-01-02/title-99.xml?part=10",
                "application/xml",
            ),
            "rules": [
                {
                    "order": 1,
                    "ecfr_amendment_date": "2025-01-02",
                    "ecfr_issue_date_basis": "not_retained_in_handoff",
                    "xml": artifact(
                        rule_xml,
                        "https://www.federalregister.gov/documents/full_text/xml/"
                        "2025/01/02/2025-00001.xml",
                        "application/xml",
                    ),
                    "metadata": artifact(
                        metadata,
                        "https://www.federalregister.gov/api/v1/documents/2025-00001.json",
                        "application/json",
                    ),
                }
            ],
        },
    }
    result = compile_episode(
        episode_spec_from_mapping(payload, base_dir=tmp_path), tmp_path / "accepted"
    )
    assert result["changed_sections"] == ["10.1", "10.2"]
    receipt = json.loads(
        (tmp_path / "accepted" / "evaluator" / "receipt.json").read_text(encoding="utf-8")
    )
    assert [row["witness_motif"] for row in receipt["causal_evidence"]] == [
        "successor_fr_citation_delta",
        "same_rule_amendatory_target",
    ]

    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "90 FR 101, Jan. 2, 2025]",
            "90 FR 101, Jan. 2, 2025; 90 FR 999, Jan. 2, 2025]",
        ),
        encoding="utf-8",
    )
    with pytest.raises(CompileError) as caught:
        compile_episode(
            episode_spec_from_mapping(payload, base_dir=tmp_path), tmp_path / "rejected"
        )
    assert caught.value.code == "CITATION_COLLISION_UNOBSERVED"
