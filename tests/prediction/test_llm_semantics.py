from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from src.export.contracts import SourceAnchor
from src.prediction.llm_semantics import (
    BillSemanticInput,
    BillSemanticIndexPayload,
    BillSemanticIndexRowPayload,
    BillSemanticPayload,
    BillSectorSemanticPayload,
    OpenAIBillSemanticExtractor,
    bill_semantic_input_from_row,
    extract_json_from_openai_response,
    load_bill_semantic_payloads,
    materialize_bill_semantics,
)


def _input() -> BillSemanticInput:
    return BillSemanticInput(
        bill_key="119-hr-1",
        title="Energy Permitting Reform Act",
        summary="Accelerates permitting for pipelines and electric transmission.",
        source_anchors=[
            SourceAnchor(
                source_type="congress_bill",
                source_id="119-hr-1",
                url="https://api.congress.gov/v3/bill/119/hr/1?format=json",
                label="Congress.gov bill",
            )
        ],
    )


def test_extract_json_from_openai_response_supports_output_text() -> None:
    payload = {"sectors": [{"sector_id": "energy", "confidence": 0.9}]}

    assert extract_json_from_openai_response({"output_text": json.dumps(payload)}) == payload


def test_bill_semantic_input_normalizes_and_rejects_blank_text() -> None:
    bill = BillSemanticInput(
        bill_key=" 119-hr-1 ",
        title=" Energy Permitting Reform Act ",
        summary=" Accelerates permitting. ",
        source_anchors=_input().source_anchors,
    )

    assert bill.bill_key == "119-hr-1"
    assert bill.title == "Energy Permitting Reform Act"
    assert bill.summary == "Accelerates permitting."

    with pytest.raises(ValueError, match="bill_key must be nonblank"):
        BillSemanticInput(
            bill_key=" ",
            title="Energy Permitting Reform Act",
            source_anchors=_input().source_anchors,
        )

    with pytest.raises(ValueError, match="title must be nonblank"):
        BillSemanticInput(
            bill_key="119-hr-1",
            title=" ",
            source_anchors=_input().source_anchors,
        )


def test_bill_semantic_input_uses_legislative_bill_anchor_for_non_congress_rows() -> None:
    bill = bill_semantic_input_from_row(
        {
            "jurisdiction_id": "state_ca",
            "legislative_body_id": "ca_assembly",
            "legislative_session_id": "2025_regular",
            "congress": 2025,
            "bill_type": "ab",
            "bill_number": 12,
            "title": "California Energy Reliability Act",
            "short_title": "Energy reliability",
            "latest_action_date": "2025-03-01",
            "bill_source_url": "https://leginfo.legislature.ca.gov/faces/billTextClient.xhtml?bill_id=20250AB12",
        }
    )

    assert bill.bill_key == "2025-ab-12"
    assert bill.source_anchors[0].source_type == "legislative_bill"
    assert bill.source_anchors[0].source_id == "state_ca:2025-ab-12"
    assert bill.source_anchors[0].jurisdiction_id == "state_ca"
    assert bill.source_anchors[0].legislative_body_id == "ca_assembly"
    assert bill.source_anchors[0].legislative_session_id == "2025_regular"
    assert bill.source_anchors[0].label == "Legislative bill state_ca:2025-ab-12"
    assert bill.source_anchors[0].url == (
        "https://leginfo.legislature.ca.gov/faces/billTextClient.xhtml?bill_id=20250AB12"
    )


def test_bill_semantic_input_rejects_unofficial_legislative_bill_source_url() -> None:
    with pytest.raises(ValueError, match="legislative_bill source URL must be official"):
        bill_semantic_input_from_row(
            {
                "jurisdiction_id": "state_ca",
                "congress": 2025,
                "bill_type": "ab",
                "bill_number": 12,
                "title": "California Energy Reliability Act",
                "bill_source_url": "https://example.invalid/bills/ab12",
            }
        )


def test_bill_semantic_input_rejects_legislative_anchor_without_context() -> None:
    with pytest.raises(ValueError, match="legislative source context"):
        BillSemanticInput(
            bill_key="2025-ab-12",
            title="California Energy Reliability Act",
            source_anchors=[
                SourceAnchor(
                    source_type="legislative_bill",
                    source_id="2025-ab-12",
                    url=(
                        "https://leginfo.legislature.ca.gov/faces/"
                        "billTextClient.xhtml?bill_id=20250AB12"
                    ),
                    label="Legislative bill 2025-ab-12",
                )
            ],
        )


def test_bill_semantic_payload_rejects_legislative_anchor_without_context() -> None:
    with pytest.raises(ValueError, match="legislative source context"):
        BillSemanticPayload(
            bill_key="2025-ab-12",
            sectors=[],
            source_anchors=[
                SourceAnchor(
                    source_type="legislative_bill",
                    source_id="2025-ab-12",
                    url=(
                        "https://leginfo.legislature.ca.gov/faces/"
                        "billTextClient.xhtml?bill_id=20250AB12"
                    ),
                    label="Legislative bill 2025-ab-12",
                )
            ],
        )


def test_bill_semantic_input_requires_legislative_bill_source_url() -> None:
    with pytest.raises(ValueError, match="legislative_bill source URL is required"):
        bill_semantic_input_from_row(
            {
                "jurisdiction_id": "state_ca",
                "congress": 2025,
                "bill_type": "ab",
                "bill_number": 12,
                "title": "California Energy Reliability Act",
            }
        )


def test_openai_bill_semantic_extractor_requires_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        OpenAIBillSemanticExtractor.from_env()


def test_openai_bill_semantic_extractor_rejects_blank_constructor_key() -> None:
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        OpenAIBillSemanticExtractor(api_key="   ")


def test_openai_bill_semantic_extractor_rejects_blank_model() -> None:
    with pytest.raises(ValueError, match="model is required"):
        OpenAIBillSemanticExtractor(api_key="test-key", model=" ")


def test_openai_bill_semantic_extractor_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout must be positive"):
        OpenAIBillSemanticExtractor(api_key="test-key", timeout=0)


def test_openai_bill_semantic_extractor_posts_without_exposing_key() -> None:
    seen_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(dict(request.headers))
        body = json.loads(request.content)
        assert body["model"] == "gpt-5.5"
        assert "Energy Permitting Reform Act" in json.dumps(body)
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "bill_key": "119-hr-1",
                        "sectors": [
                            {
                                "sector_id": "energy",
                                "label": "Energy",
                                "confidence": 0.92,
                                "rationale": "The bill concerns permitting for energy infrastructure.",
                            }
                        ],
                        "industries": ["electric transmission", "pipeline"],
                        "affected_entities": ["utilities"],
                        "coalition_summary": "Energy infrastructure supporters are likely aligned.",
                        "ideological_valence": "mixed",
                        "source_anchors": [
                            {
                                "source_type": "congress_bill",
                                "source_id": "119-hr-1",
                                "url": "https://api.congress.gov/v3/bill/119/hr/1?format=json",
                                "label": "Congress.gov bill",
                            }
                        ],
                    }
                )
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    extractor = OpenAIBillSemanticExtractor(api_key="test-key", client=client)

    result = extractor.extract(_input())

    assert seen_headers["authorization"] == "Bearer test-key"
    assert result.bill_key == "119-hr-1"
    assert result.sectors[0].sector_id == "energy"


def test_bill_semantic_payload_normalizes_sector_and_list_text() -> None:
    payload = BillSemanticPayload(
        bill_key=" 119-hr-1 ",
        model_name=" semantic-test-model ",
        sectors=[
            BillSectorSemanticPayload(
                sector_id=" energy ",
                label=" Energy ",
                confidence=0.9,
                rationale=" Energy infrastructure. ",
            )
        ],
        industries=[" pipeline ", " utilities "],
        affected_entities=[" electric utilities "],
        coalition_summary=" Energy coalition. ",
        ideological_valence="mixed",
        source_anchors=_input().source_anchors,
    )

    assert payload.bill_key == "119-hr-1"
    assert payload.model_name == "semantic-test-model"
    assert payload.sectors[0].sector_id == "energy"
    assert payload.sectors[0].label == "Energy"
    assert payload.sectors[0].rationale == "Energy infrastructure."
    assert payload.industries == ["pipeline", "utilities"]
    assert payload.affected_entities == ["electric utilities"]
    assert payload.coalition_summary == "Energy coalition."


def test_bill_semantic_payload_rejects_blank_or_duplicate_list_values() -> None:
    with pytest.raises(ValueError, match="industries must be sorted, unique, and nonblank"):
        BillSemanticPayload(
            bill_key="119-hr-1",
            sectors=[],
            industries=["utilities", " "],
            source_anchors=_input().source_anchors,
        )

    with pytest.raises(ValueError, match="affected_entities must be sorted, unique, and nonblank"):
        BillSemanticPayload(
            bill_key="119-hr-1",
            sectors=[],
            affected_entities=["utilities", "utilities"],
            source_anchors=_input().source_anchors,
        )


def test_bill_sector_semantic_rejects_blank_identity_or_rationale() -> None:
    with pytest.raises(ValueError, match="sector_id must be nonblank"):
        BillSectorSemanticPayload(
            sector_id=" ",
            confidence=0.9,
            rationale="Energy infrastructure.",
        )

    with pytest.raises(ValueError, match="rationale must be nonblank"):
        BillSectorSemanticPayload(
            sector_id="energy",
            confidence=0.9,
            rationale=" ",
        )


def test_bill_semantic_input_from_row_replaces_unofficial_bill_source_url() -> None:
    result = bill_semantic_input_from_row(
        {
            "congress": 119,
            "bill_type": "hr",
            "bill_number": 1,
            "title": "Energy Permitting Reform Act",
            "short_title": "Energy Permitting Reform",
            "current_status": "Introduced",
            "bill_source_url": "https://example.invalid/bill/119/hr/1",
        }
    )

    assert result.source_anchors[0].url == ("https://api.congress.gov/v3/bill/119/hr/1?format=json")


def test_bill_semantic_input_from_row_rejects_boolean_bill_key_parts() -> None:
    with pytest.raises(ValueError, match="congress must be an integer"):
        bill_semantic_input_from_row(
            {
                "congress": True,
                "bill_type": "hr",
                "bill_number": 1,
                "title": "Energy Permitting Reform Act",
            }
        )

    with pytest.raises(ValueError, match="bill_number must be an integer"):
        bill_semantic_input_from_row(
            {
                "congress": 119,
                "bill_type": "hr",
                "bill_number": True,
                "title": "Energy Permitting Reform Act",
            }
        )


def test_materialize_bill_semantics_writes_payloads_and_index(tmp_path) -> None:
    class Extractor:
        model_name = "semantic-test-model"

        def __init__(self) -> None:
            self.calls = 0

        def extract(self, bill: BillSemanticInput) -> BillSemanticPayload:
            self.calls += 1
            return BillSemanticPayload(
                bill_key=bill.bill_key,
                sectors=[
                    BillSectorSemanticPayload(
                        sector_id="energy",
                        label="Energy",
                        confidence=0.9,
                        rationale="Energy title.",
                    )
                ],
                industries=["utilities"],
                affected_entities=["utilities"],
                coalition_summary="Energy coalition.",
                ideological_valence="mixed",
                source_anchors=bill.source_anchors,
            )

    extractor = Extractor()
    result = materialize_bill_semantics(
        bill_rows=[
            {
                "congress": 119,
                "bill_type": "hr",
                "bill_number": 1,
                "title": "Energy Permitting Reform Act",
                "short_title": "Energy Permitting Reform",
                "introduced_date": "2024-11-05",
                "latest_action_date": "2024-11-20",
                "current_status": "Introduced",
                "bill_source_url": None,
            }
        ],
        output_root=tmp_path,
        extractor=extractor,  # type: ignore[arg-type]
    )

    assert result.written_count == 1
    assert extractor.calls == 1
    loaded = load_bill_semantic_payloads(tmp_path)
    assert loaded[0].bill_key == "119-hr-1"
    assert loaded[0].model_name == "semantic-test-model"
    assert loaded[0].available_at.isoformat() == "2024-11-20"
    assert loaded[0].sectors[0].sector_id == "energy"
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert index["model_names"] == ["semantic-test-model"]
    assert index["source_bill_count"] == 1
    assert index["source_bill_keys"] == ["119-hr-1"]
    assert len(index["source_inputs_sha256"]) == 64
    assert index["run_metadata"] == {
        "bill_count": 1,
        "command": "materialize-bill-semantics",
        "model_names": ["semantic-test-model"],
        "source_bill_count": 1,
        "source_bill_keys": ["119-hr-1"],
        "source_inputs_sha256": index["source_inputs_sha256"],
    }
    assert index["bills"][0]["model_name"] == "semantic-test-model"
    assert index["bills"][0]["available_at"] == "2024-11-20"
    payload_bytes = (tmp_path / index["bills"][0]["path"]).read_bytes()
    assert index["bills"][0]["sha256"] == hashlib.sha256(payload_bytes).hexdigest()


def test_materialize_bill_semantics_uses_unique_temp_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Extractor:
        model_name = "semantic-test-model"

        def extract(self, bill: BillSemanticInput) -> BillSemanticPayload:
            return BillSemanticPayload(
                bill_key=bill.bill_key,
                sectors=[
                    BillSectorSemanticPayload(
                        sector_id="energy",
                        confidence=0.9,
                        rationale="Energy title.",
                    )
                ],
                source_anchors=bill.source_anchors,
            )

    expected_final_paths = {
        tmp_path / "bills" / "119-hr-1.json",
        tmp_path / "index.json",
    }
    seen_temp_names: set[str] = set()
    original_open = Path.open

    def guarded_open(path: Path, *args: object, **kwargs: object):
        mode = str(args[0]) if args else str(kwargs.get("mode", "r"))
        if path in expected_final_paths and any(flag in mode for flag in ("w", "a", "x", "+")):
            raise AssertionError(f"direct final-path write: {path.name}")
        if path.name in {".119-hr-1.json.tmp", ".index.json.tmp"}:
            raise AssertionError("fixed temp filename used")
        if path.name.startswith(".119-hr-1.json.") and path.name.endswith(".tmp"):
            UUID(path.name.removeprefix(".119-hr-1.json.").removesuffix(".tmp"))
            seen_temp_names.add(path.name)
        if path.name.startswith(".index.json.") and path.name.endswith(".tmp"):
            UUID(path.name.removeprefix(".index.json.").removesuffix(".tmp"))
            seen_temp_names.add(path.name)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)

    materialize_bill_semantics(
        bill_rows=[
            {
                "congress": 119,
                "bill_type": "hr",
                "bill_number": 1,
                "title": "Energy Permitting Reform Act",
                "current_status": "Introduced",
                "bill_source_url": None,
            }
        ],
        output_root=tmp_path,
        extractor=Extractor(),  # type: ignore[arg-type]
    )

    assert len(seen_temp_names) == 2
    assert all(path.is_file() for path in expected_final_paths)


def test_load_bill_semantics_rejects_index_model_name_mismatch(tmp_path) -> None:
    payload = BillSemanticPayload(
        bill_key="119-hr-1",
        model_name="semantic-test-model",
        available_at=None,
        sectors=[],
        source_anchors=_input().source_anchors,
    )
    bills = tmp_path / "bills"
    bills.mkdir()
    payload_path = bills / "119-hr-1.json"
    payload_path.write_text(payload.model_dump_json(), encoding="utf-8")
    (tmp_path / "index.json").write_text(
        json.dumps(
            {
                "bill_count": 1,
                "model_names": ["wrong-model"],
                "source_bill_count": 1,
                "source_bill_keys": ["119-hr-1"],
                "bills": [
                    {
                        "bill_key": "119-hr-1",
                        "path": "bills/119-hr-1.json",
                        "sha256": hashlib.sha256(payload_path.read_bytes()).hexdigest(),
                        "model_name": "semantic-test-model",
                        "available_at": None,
                        "sector_ids": [],
                        "source_anchor_count": 1,
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="model_names must match bills"):
        load_bill_semantic_payloads(tmp_path)


def test_bill_semantic_index_rejects_duplicate_or_blank_keys_and_model_names() -> None:
    with pytest.raises(ValueError, match="bill semantic index bill keys must be sorted and unique"):
        BillSemanticIndexPayload(
            bill_count=2,
            bills=[
                {
                    "bill_key": "119-hr-1",
                    "path": "bills/119-hr-1.json",
                    "sha256": "a" * 64,
                    "sector_ids": [],
                    "source_anchor_count": 1,
                },
                {
                    "bill_key": "119-hr-1",
                    "path": "bills/119-hr-1-copy.json",
                    "sha256": "b" * 64,
                    "sector_ids": [],
                    "source_anchor_count": 1,
                },
            ],
        )

    with pytest.raises(ValueError, match="bill semantic index model_names must be sorted"):
        BillSemanticIndexPayload(
            bill_count=0,
            model_names=["semantic-test-model", " "],
            bills=[],
        )


def test_bill_semantic_index_row_rejects_invalid_sha256() -> None:
    with pytest.raises(ValueError, match="bill semantic index row sha256 must be sha256 hex"):
        BillSemanticIndexRowPayload(
            bill_key="119-hr-1",
            path="bills/119-hr-1.json",
            sha256="not-a-sha256",
            sector_ids=[],
            source_anchor_count=1,
        )


def test_bill_semantic_index_row_rejects_boolean_source_anchor_count() -> None:
    with pytest.raises(ValueError, match="source_anchor_count must be an integer"):
        BillSemanticIndexRowPayload(
            bill_key="119-hr-1",
            path="bills/119-hr-1.json",
            sha256="a" * 64,
            sector_ids=[],
            source_anchor_count=True,
        )


def test_bill_semantic_index_rejects_boolean_bill_count() -> None:
    with pytest.raises(ValueError, match="bill_count must be an integer"):
        BillSemanticIndexPayload(
            bill_count=True,
            bills=[
                {
                    "bill_key": "119-hr-1",
                    "path": "bills/119-hr-1.json",
                    "sha256": "a" * 64,
                    "sector_ids": [],
                    "source_anchor_count": 1,
                }
            ],
        )


def test_materialize_bill_semantics_reuses_cache(tmp_path) -> None:
    class FailingExtractor:
        def extract(self, bill: BillSemanticInput) -> BillSemanticPayload:
            raise AssertionError("cache should avoid extractor")

    payload = BillSemanticPayload(
        bill_key="119-hr-1",
        sectors=[
            BillSectorSemanticPayload(
                sector_id="energy",
                label="Energy",
                confidence=0.9,
                rationale="Energy title.",
            )
        ],
        industries=[],
        affected_entities=[],
        coalition_summary=None,
        ideological_valence="mixed",
        source_anchors=_input().source_anchors,
    )
    (tmp_path / "bills").mkdir()
    (tmp_path / "bills" / "119-hr-1.json").write_text(
        payload.model_dump_json(),
        encoding="utf-8",
    )

    result = materialize_bill_semantics(
        bill_rows=[
            {
                "congress": 119,
                "bill_type": "hr",
                "bill_number": 1,
                "title": "Energy Permitting Reform Act",
                "short_title": "Energy Permitting Reform",
                "current_status": "Introduced",
                "bill_source_url": None,
            }
        ],
        output_root=tmp_path,
        extractor=FailingExtractor(),  # type: ignore[arg-type]
    )

    assert result.cached_count == 1
    assert result.written_count == 0


def test_materialize_bill_semantics_rejects_cached_payload_from_different_model(
    tmp_path,
) -> None:
    class FailingExtractor:
        model_name = "new-model"

        def extract(self, bill: BillSemanticInput) -> BillSemanticPayload:
            raise AssertionError("mismatched cache should fail before extraction")

    payload = BillSemanticPayload(
        bill_key="119-hr-1",
        model_name="old-model",
        sectors=[],
        source_anchors=_input().source_anchors,
    )
    (tmp_path / "bills").mkdir()
    (tmp_path / "bills" / "119-hr-1.json").write_text(
        payload.model_dump_json(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="model_name"):
        materialize_bill_semantics(
            bill_rows=[
                {
                    "congress": 119,
                    "bill_type": "hr",
                    "bill_number": 1,
                    "title": "Energy Permitting Reform Act",
                    "short_title": "Energy Permitting Reform",
                    "current_status": "Introduced",
                    "bill_source_url": None,
                }
            ],
            output_root=tmp_path,
            extractor=FailingExtractor(),  # type: ignore[arg-type]
        )


def test_materialize_bill_semantics_rejects_mismatched_extractor_bill_key(tmp_path) -> None:
    class MismatchedExtractor:
        def extract(self, bill: BillSemanticInput) -> BillSemanticPayload:
            return BillSemanticPayload(
                bill_key="119-hr-999",
                sectors=[
                    BillSectorSemanticPayload(
                        sector_id="energy",
                        label="Energy",
                        confidence=0.9,
                        rationale="Energy title.",
                    )
                ],
                industries=["utilities"],
                affected_entities=["utilities"],
                coalition_summary="Energy coalition.",
                ideological_valence="mixed",
                source_anchors=bill.source_anchors,
            )

    with pytest.raises(ValueError, match="bill_key"):
        materialize_bill_semantics(
            bill_rows=[
                {
                    "congress": 119,
                    "bill_type": "hr",
                    "bill_number": 1,
                    "title": "Energy Permitting Reform Act",
                    "short_title": "Energy Permitting Reform",
                    "current_status": "Introduced",
                    "bill_source_url": None,
                }
            ],
            output_root=tmp_path,
            extractor=MismatchedExtractor(),  # type: ignore[arg-type]
        )

    assert not (tmp_path / "bills" / "119-hr-1.json").exists()


def test_materialize_bill_semantics_rejects_mismatched_cached_bill_key(tmp_path) -> None:
    class FailingExtractor:
        def extract(self, bill: BillSemanticInput) -> BillSemanticPayload:
            raise AssertionError("mismatched cache should fail before extraction")

    payload = BillSemanticPayload(
        bill_key="119-hr-999",
        sectors=[
            BillSectorSemanticPayload(
                sector_id="energy",
                label="Energy",
                confidence=0.9,
                rationale="Energy title.",
            )
        ],
        industries=[],
        affected_entities=[],
        coalition_summary=None,
        ideological_valence="mixed",
        source_anchors=_input().source_anchors,
    )
    (tmp_path / "bills").mkdir()
    (tmp_path / "bills" / "119-hr-1.json").write_text(
        payload.model_dump_json(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="bill_key"):
        materialize_bill_semantics(
            bill_rows=[
                {
                    "congress": 119,
                    "bill_type": "hr",
                    "bill_number": 1,
                    "title": "Energy Permitting Reform Act",
                    "short_title": "Energy Permitting Reform",
                    "current_status": "Introduced",
                    "bill_source_url": None,
                }
            ],
            output_root=tmp_path,
            extractor=FailingExtractor(),  # type: ignore[arg-type]
        )


def test_load_bill_semantic_payloads_rejects_index_payload_bill_key_mismatch(
    tmp_path,
) -> None:
    payload = BillSemanticPayload(
        bill_key="119-hr-999",
        sectors=[
            BillSectorSemanticPayload(
                sector_id="energy",
                label="Energy",
                confidence=0.9,
                rationale="Energy title.",
            )
        ],
        industries=[],
        affected_entities=[],
        coalition_summary=None,
        ideological_valence="mixed",
        source_anchors=_input().source_anchors,
    )
    (tmp_path / "bills").mkdir()
    (tmp_path / "bills" / "119-hr-1.json").write_text(
        payload.model_dump_json(),
        encoding="utf-8",
    )
    (tmp_path / "index.json").write_text(
        json.dumps(
            {
                "bill_count": 1,
                "bills": [
                    {
                        "bill_key": "119-hr-1",
                        "path": "bills/119-hr-1.json",
                        "sector_ids": ["energy"],
                        "source_anchor_count": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="bill_key"):
        load_bill_semantic_payloads(tmp_path)


def test_load_bill_semantic_payloads_rejects_sha256_mismatch(tmp_path) -> None:
    payload = BillSemanticPayload(
        bill_key="119-hr-1",
        sectors=[],
        source_anchors=_input().source_anchors,
    )
    (tmp_path / "bills").mkdir()
    payload_path = tmp_path / "bills" / "119-hr-1.json"
    payload_path.write_text(payload.model_dump_json(), encoding="utf-8")
    (tmp_path / "index.json").write_text(
        json.dumps(
            {
                "bill_count": 1,
                "bills": [
                    {
                        "bill_key": "119-hr-1",
                        "path": "bills/119-hr-1.json",
                        "sha256": "0" * 64,
                        "available_at": None,
                        "sector_ids": [],
                        "source_anchor_count": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="sha256"):
        load_bill_semantic_payloads(tmp_path)


def test_load_bill_semantic_payloads_rejects_index_sector_mismatch(tmp_path) -> None:
    payload = BillSemanticPayload(
        bill_key="119-hr-1",
        sectors=[
            BillSectorSemanticPayload(
                sector_id="energy",
                confidence=0.9,
                rationale="Energy title.",
            )
        ],
        source_anchors=_input().source_anchors,
    )
    (tmp_path / "bills").mkdir()
    payload_path = tmp_path / "bills" / "119-hr-1.json"
    payload_path.write_text(payload.model_dump_json(), encoding="utf-8")
    (tmp_path / "index.json").write_text(
        json.dumps(
            {
                "bill_count": 1,
                "bills": [
                    {
                        "bill_key": "119-hr-1",
                        "path": "bills/119-hr-1.json",
                        "sha256": hashlib.sha256(payload_path.read_bytes()).hexdigest(),
                        "available_at": None,
                        "sector_ids": ["finance"],
                        "source_anchor_count": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="sector_ids"):
        load_bill_semantic_payloads(tmp_path)


def test_load_bill_semantic_payloads_rejects_index_source_anchor_count_mismatch(
    tmp_path,
) -> None:
    payload = BillSemanticPayload(
        bill_key="119-hr-1",
        sectors=[],
        source_anchors=_input().source_anchors,
    )
    (tmp_path / "bills").mkdir()
    payload_path = tmp_path / "bills" / "119-hr-1.json"
    payload_path.write_text(payload.model_dump_json(), encoding="utf-8")
    (tmp_path / "index.json").write_text(
        json.dumps(
            {
                "bill_count": 1,
                "bills": [
                    {
                        "bill_key": "119-hr-1",
                        "path": "bills/119-hr-1.json",
                        "sha256": hashlib.sha256(payload_path.read_bytes()).hexdigest(),
                        "available_at": None,
                        "sector_ids": [],
                        "source_anchor_count": 0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="source_anchor_count"):
        load_bill_semantic_payloads(tmp_path)


def test_load_bill_semantic_payloads_rejects_index_model_mismatch(tmp_path) -> None:
    payload = BillSemanticPayload(
        bill_key="119-hr-1",
        model_name="semantic-test-model",
        sectors=[],
        source_anchors=_input().source_anchors,
    )
    (tmp_path / "bills").mkdir()
    payload_path = tmp_path / "bills" / "119-hr-1.json"
    payload_path.write_text(payload.model_dump_json(), encoding="utf-8")
    (tmp_path / "index.json").write_text(
        json.dumps(
            {
                "bill_count": 1,
                "bills": [
                    {
                        "bill_key": "119-hr-1",
                        "path": "bills/119-hr-1.json",
                        "sha256": hashlib.sha256(payload_path.read_bytes()).hexdigest(),
                        "available_at": None,
                        "model_name": "other-model",
                        "sector_ids": [],
                        "source_anchor_count": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="model_name"):
        load_bill_semantic_payloads(tmp_path)


def test_load_bill_semantic_payloads_rejects_invalid_source_inputs_sha256(
    tmp_path,
) -> None:
    payload = BillSemanticPayload(
        bill_key="119-hr-1",
        model_name="semantic-test-model",
        sectors=[],
        source_anchors=_input().source_anchors,
    )
    (tmp_path / "bills").mkdir()
    payload_path = tmp_path / "bills" / "119-hr-1.json"
    payload_path.write_text(payload.model_dump_json(), encoding="utf-8")
    (tmp_path / "index.json").write_text(
        json.dumps(
            {
                "bill_count": 1,
                "model_names": ["semantic-test-model"],
                "source_inputs_sha256": "not-a-sha256",
                "bills": [
                    {
                        "bill_key": "119-hr-1",
                        "path": "bills/119-hr-1.json",
                        "sha256": hashlib.sha256(payload_path.read_bytes()).hexdigest(),
                        "available_at": None,
                        "model_name": "semantic-test-model",
                        "sector_ids": [],
                        "source_anchor_count": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="source_inputs_sha256"):
        load_bill_semantic_payloads(tmp_path)


def test_materialize_bill_semantics_rejects_payload_without_input_source_anchor(
    tmp_path,
) -> None:
    class UnsourcedExtractor:
        def extract(self, bill: BillSemanticInput) -> BillSemanticPayload:
            return BillSemanticPayload(
                bill_key=bill.bill_key,
                sectors=[
                    BillSectorSemanticPayload(
                        sector_id="energy",
                        label="Energy",
                        confidence=0.9,
                        rationale="Energy title.",
                    )
                ],
                industries=[],
                affected_entities=[],
                coalition_summary=None,
                ideological_valence="mixed",
                source_anchors=[
                    SourceAnchor(
                        source_type="other",
                        source_id="other-source",
                        url="https://example.invalid/other",
                        label="Other source",
                    )
                ],
            )

    with pytest.raises(ValueError, match="source anchor"):
        materialize_bill_semantics(
            bill_rows=[
                {
                    "congress": 119,
                    "bill_type": "hr",
                    "bill_number": 1,
                    "title": "Energy Permitting Reform Act",
                    "short_title": "Energy Permitting Reform",
                    "current_status": "Introduced",
                    "bill_source_url": None,
                }
            ],
            output_root=tmp_path,
            extractor=UnsourcedExtractor(),  # type: ignore[arg-type]
        )


def test_materialize_bill_semantics_rejects_legislative_source_scope_mismatch(
    tmp_path,
) -> None:
    class WrongScopeExtractor:
        def extract(self, bill: BillSemanticInput) -> BillSemanticPayload:
            return BillSemanticPayload(
                bill_key=bill.bill_key,
                sectors=[],
                source_anchors=[
                    SourceAnchor(
                        source_type="legislative_bill",
                        source_id="state_ca:2025-ab-12",
                        jurisdiction_id="state_ca",
                        legislative_body_id="ca_senate",
                        legislative_session_id="2025_regular",
                        url=(
                            "https://leginfo.legislature.ca.gov/faces/"
                            "billTextClient.xhtml?bill_id=20250AB12"
                        ),
                        label="Wrong chamber bill anchor",
                    )
                ],
            )

    with pytest.raises(ValueError, match="source anchor"):
        materialize_bill_semantics(
            bill_rows=[
                {
                    "jurisdiction_id": "state_ca",
                    "legislative_body_id": "ca_assembly",
                    "legislative_session_id": "2025_regular",
                    "congress": 2025,
                    "bill_type": "ab",
                    "bill_number": 12,
                    "title": "California Energy Reliability Act",
                    "short_title": "Energy reliability",
                    "bill_source_url": (
                        "https://leginfo.legislature.ca.gov/faces/"
                        "billTextClient.xhtml?bill_id=20250AB12"
                    ),
                }
            ],
            output_root=tmp_path,
            extractor=WrongScopeExtractor(),  # type: ignore[arg-type]
        )
