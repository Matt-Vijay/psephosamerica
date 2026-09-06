from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal, Self

import httpx
from pydantic import Field, field_validator, model_validator

from src.core.files import sha256_file, write_text_atomic
from src.core.path_safety import safe_join_confined
from src.evidence.source_anchor_policy import is_official_source_url
from src.export.contracts import ExportContractModel, SourceAnchor
from src.prediction.source_anchors import (
    describe_missing_legislative_source_context,
    source_anchor_identity,
)

_OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


class BillSemanticInput(ExportContractModel):
    """Source-backed bill title/summary context sent to the semantic extractor."""

    bill_key: str
    title: str
    summary: str | None = None
    available_at: date | None = None
    source_anchors: list[SourceAnchor] = Field(min_length=1)

    @field_validator("bill_key", "title", mode="before")
    @classmethod
    def strip_required_text(cls, value: object) -> object:
        return _strip_string(value)

    @field_validator("summary", mode="before")
    @classmethod
    def strip_optional_summary(cls, value: object) -> object:
        return _strip_optional_string(value)

    @model_validator(mode="after")
    def input_text_is_nonblank(self) -> Self:
        _require_nonblank_string(self.bill_key, field_name="bill_key")
        _require_nonblank_string(self.title, field_name="title")
        missing_source_context = describe_missing_legislative_source_context(self.source_anchors)
        if missing_source_context:
            raise ValueError(
                f"source_anchors require legislative source context: {missing_source_context}"
            )
        return self


def _strip_optional_string(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


def _strip_string(value: object) -> object:
    if isinstance(value, str):
        return value.strip()
    return value


def _require_nonblank_string(value: str, *, field_name: str) -> None:
    if not value:
        raise ValueError(f"{field_name} must be nonblank")


def _ensure_sorted_unique_nonblank(values: list[str], *, field_name: str) -> None:
    if any(not value.strip() or value != value.strip() for value in values):
        raise ValueError(f"{field_name} must be sorted, unique, and nonblank")
    if values != sorted(set(values)):
        raise ValueError(f"{field_name} must be sorted, unique, and nonblank")


class BillSectorSemanticPayload(ExportContractModel):
    sector_id: str
    label: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str

    @field_validator("sector_id", "rationale", mode="before")
    @classmethod
    def strip_required_text(cls, value: object) -> object:
        return _strip_string(value)

    @field_validator("label", mode="before")
    @classmethod
    def strip_optional_label(cls, value: object) -> object:
        return _strip_optional_string(value)

    @model_validator(mode="after")
    def text_is_nonblank(self) -> Self:
        _require_nonblank_string(self.sector_id, field_name="sector_id")
        _require_nonblank_string(self.rationale, field_name="rationale")
        return self


class BillSemanticPayload(ExportContractModel):
    """LLM-produced bill semantics used as structured prediction features."""

    bill_key: str
    model_name: str | None = None
    available_at: date | None = None
    sectors: list[BillSectorSemanticPayload] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    affected_entities: list[str] = Field(default_factory=list)
    coalition_summary: str | None = None
    ideological_valence: Literal["left", "right", "mixed", "procedural", "unknown"] = "unknown"
    source_anchors: list[SourceAnchor] = Field(min_length=1)

    @field_validator("bill_key", mode="before")
    @classmethod
    def strip_bill_key(cls, value: object) -> object:
        return _strip_string(value)

    @field_validator("model_name", "coalition_summary", mode="before")
    @classmethod
    def strip_optional_text(cls, value: object) -> object:
        return _strip_optional_string(value)

    @field_validator("industries", "affected_entities", mode="before")
    @classmethod
    def strip_text_list(cls, value: object) -> object:
        if isinstance(value, list):
            return [_strip_string(item) for item in value]
        return value

    @model_validator(mode="after")
    def semantic_payload_is_canonical(self) -> Self:
        _require_nonblank_string(self.bill_key, field_name="bill_key")
        missing_source_context = describe_missing_legislative_source_context(self.source_anchors)
        if missing_source_context:
            raise ValueError(
                f"source_anchors require legislative source context: {missing_source_context}"
            )
        sector_ids = [sector.sector_id for sector in self.sectors]
        _ensure_sorted_unique_nonblank(sector_ids, field_name="semantic sector IDs")
        _ensure_sorted_unique_nonblank(self.industries, field_name="industries")
        _ensure_sorted_unique_nonblank(
            self.affected_entities,
            field_name="affected_entities",
        )
        return self


class BillSemanticIndexRowPayload(ExportContractModel):
    bill_key: str
    path: str
    sha256: str
    model_name: str | None = None
    available_at: date | None = None
    sector_ids: list[str] = Field(default_factory=list)
    source_anchor_count: int = Field(ge=0)

    @field_validator("bill_key", "path", "sha256", mode="before")
    @classmethod
    def strip_required_text(cls, value: object) -> object:
        return _strip_string(value)

    @field_validator("model_name", mode="before")
    @classmethod
    def strip_optional_model_name(cls, value: object) -> object:
        return _strip_optional_string(value)

    @field_validator("sector_ids", mode="before")
    @classmethod
    def strip_sector_ids(cls, value: object) -> object:
        if isinstance(value, list):
            return [_strip_string(item) for item in value]
        return value

    @model_validator(mode="after")
    def row_is_canonical(self) -> Self:
        _require_nonblank_string(self.bill_key, field_name="bill_key")
        _require_nonblank_string(self.path, field_name="path")
        _require_nonblank_string(self.sha256, field_name="sha256")
        if not _is_sha256_hex(self.sha256):
            raise ValueError("bill semantic index row sha256 must be sha256 hex")
        _ensure_sorted_unique_nonblank(self.sector_ids, field_name="sector_ids")
        return self


class BillSemanticIndexPayload(ExportContractModel):
    bill_count: int = Field(ge=0)
    model_names: list[str] = Field(default_factory=list)
    source_bill_count: int | None = Field(default=None, ge=0)
    source_bill_keys: list[str] = Field(default_factory=list)
    source_inputs_sha256: str | None = None
    bills: list[BillSemanticIndexRowPayload] = Field(default_factory=list)
    run_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("model_names", "source_bill_keys", mode="before")
    @classmethod
    def strip_string_lists(cls, value: object) -> object:
        if isinstance(value, list):
            return [_strip_string(item) for item in value]
        return value

    @field_validator("source_inputs_sha256", mode="before")
    @classmethod
    def strip_optional_sha256(cls, value: object) -> object:
        return _strip_optional_string(value)

    @model_validator(mode="after")
    def index_counts_match(self) -> Self:
        if self.bill_count != len(self.bills):
            raise ValueError("bill_count must match bills length")
        bill_keys = [bill.bill_key for bill in self.bills]
        if bill_keys != sorted(set(bill_keys)):
            raise ValueError("bill semantic index bill keys must be sorted and unique")
        _ensure_sorted_unique_nonblank(
            self.model_names,
            field_name="bill semantic index model_names",
        )
        _ensure_sorted_unique_nonblank(
            self.source_bill_keys,
            field_name="bill semantic index source_bill_keys",
        )
        expected_model_names = sorted(
            {str(bill.model_name) for bill in self.bills if bill.model_name is not None}
        )
        if self.model_names and self.model_names != expected_model_names:
            raise ValueError("bill semantic index model_names must match bills")
        expected_bill_keys = sorted(bill_keys)
        if self.source_bill_keys and self.source_bill_keys != expected_bill_keys:
            raise ValueError("bill semantic index source_bill_keys must match bills")
        if self.source_bill_count is not None and self.source_bill_count != len(expected_bill_keys):
            raise ValueError("bill semantic index source_bill_count must match bills")
        if self.source_inputs_sha256 is not None and not _is_sha256_hex(self.source_inputs_sha256):
            raise ValueError("bill semantic index source_inputs_sha256 must be sha256 hex")
        return self


@dataclass(frozen=True)
class BillSemanticsMaterializeResult:
    output_root: Path
    requested_count: int
    written_count: int
    cached_count: int
    index_path: Path


class OpenAIBillSemanticExtractor:
    """OpenAI Responses API adapter for bill semantic extraction.

    The API key is accepted via constructor or OPENAI_API_KEY. It is never
    serialized into returned payloads.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-5.5",
        client: httpx.Client | None = None,
        timeout: float = 60.0,
    ) -> None:
        api_key = api_key.strip()
        model = model.strip()
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for OpenAI semantic extraction")
        if not model:
            raise ValueError("model is required for OpenAI semantic extraction")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._api_key = api_key
        self._model = model
        self._client = client
        self._timeout = timeout

    @classmethod
    def from_env(cls, *, model: str | None = None) -> OpenAIBillSemanticExtractor:
        api_key = os.environ.get("OPENAI_API_KEY")
        if api_key is None or not api_key.strip():
            raise ValueError("OPENAI_API_KEY is required for OpenAI semantic extraction")
        return cls(
            api_key=api_key,
            model=model
            or os.environ.get("PSEPHOS_LLM_MODEL")
            or os.environ.get("OPENPACT_LLM_MODEL", "gpt-5.5"),
        )

    def extract(self, bill: BillSemanticInput) -> BillSemanticPayload:
        payload = self._request_payload(bill)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if self._client is not None:
            response = self._client.post(_OPENAI_RESPONSES_URL, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        else:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(_OPENAI_RESPONSES_URL, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        return BillSemanticPayload.model_validate(extract_json_from_openai_response(data))

    @property
    def model_name(self) -> str:
        return self._model

    def _request_payload(self, bill: BillSemanticInput) -> dict[str, Any]:
        return {
            "model": self._model,
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Extract source-backed bill semantics for vote prediction. "
                                "Return only valid JSON matching the requested schema. "
                                "Do not predict member votes."
                            ),
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(bill.model_dump(mode="json"), sort_keys=True),
                        }
                    ],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "bill_semantics",
                    "schema": _bill_semantic_json_schema(),
                    "strict": True,
                }
            },
        }


def extract_json_from_openai_response(response: dict[str, Any]) -> dict[str, Any]:
    """Extract JSON text from common Responses API response shapes."""
    if isinstance(response.get("output_text"), str):
        return _loads_json_object(response["output_text"])
    for item in response.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if isinstance(text, str):
                return _loads_json_object(text)
    raise ValueError("OpenAI response did not contain JSON output text")


def bill_semantics_index_path() -> str:
    return "index.json"


def bill_semantics_payload_path(bill_key: str) -> str:
    if not bill_key or not bill_key.replace("-", "").replace("_", "").isalnum():
        raise ValueError(f"Invalid bill semantic key: {bill_key!r}")
    return f"bills/{bill_key}.json"


def bill_semantic_input_from_row(row: dict[str, Any]) -> BillSemanticInput:
    bill_key = _bill_key(row)
    source_type = _bill_source_type(row)
    source_id = _bill_source_id(row, bill_key, source_type=source_type)
    source_url = _bill_source_url(row, source_type=source_type)
    return BillSemanticInput(
        bill_key=bill_key,
        title=str(row["title"]),
        summary=str(row.get("short_title") or row.get("current_status") or ""),
        available_at=_bill_semantic_available_at_from_row(row),
        source_anchors=[
            SourceAnchor(
                source_type=source_type,
                source_id=source_id,
                **_legislative_source_context(row),
                url=str(source_url) if source_url else None,
                label=_bill_source_label(source_id, source_type=source_type),
            )
        ],
    )


def _bill_source_type(row: dict[str, Any]) -> str:
    return (
        "congress_bill"
        if row.get("jurisdiction_id") in (None, "us_congress")
        else "legislative_bill"
    )


def _bill_source_id(row: dict[str, Any], bill_key: str, *, source_type: str) -> str:
    if source_type == "congress_bill":
        return bill_key
    jurisdiction_id = str(row.get("jurisdiction_id") or "").strip()
    if not jurisdiction_id or bill_key.startswith(f"{jurisdiction_id}-"):
        return bill_key
    return f"{jurisdiction_id}:{bill_key}"


def _legislative_source_context(row: dict[str, Any]) -> dict[str, str]:
    if _bill_source_type(row) == "congress_bill":
        return {}
    context: dict[str, str] = {}
    for field_name in (
        "jurisdiction_id",
        "legislative_body_id",
        "legislative_session_id",
    ):
        value = _strip_optional_string(row.get(field_name))
        if isinstance(value, str):
            context[field_name] = value
    return context


def _bill_source_url(row: dict[str, Any], *, source_type: str) -> str | None:
    if source_type == "congress_bill":
        return _official_congress_bill_url(row)
    raw_url = row.get("bill_source_url")
    if not isinstance(raw_url, str) or not raw_url.strip():
        raise ValueError("legislative_bill source URL is required")
    if not is_official_source_url(source_type, raw_url):
        raise ValueError("legislative_bill source URL must be official")
    return raw_url.strip()


def _bill_source_label(bill_key: str, *, source_type: str) -> str:
    if source_type == "congress_bill":
        return f"Congress.gov bill {bill_key}"
    return f"Legislative bill {bill_key}"


def _official_congress_bill_url(row: dict[str, Any]) -> str:
    raw_url = row.get("bill_source_url")
    if isinstance(raw_url, str) and is_official_source_url("congress_bill", raw_url):
        return raw_url
    return _congress_bill_url(row)


def materialize_bill_semantics(
    *,
    bill_rows: list[dict[str, Any]],
    output_root: Path,
    extractor: OpenAIBillSemanticExtractor,
    limit: int | None = None,
    overwrite: bool = False,
) -> BillSemanticsMaterializeResult:
    selected_rows = bill_rows[:limit] if limit is not None else bill_rows
    model_name = _extractor_model_name(extractor)
    output_root.mkdir(parents=True, exist_ok=True)
    index_rows: list[BillSemanticIndexRowPayload] = []
    source_inputs: list[BillSemanticInput] = []
    written_count = 0
    cached_count = 0
    for row in selected_rows:
        bill_input = bill_semantic_input_from_row(row)
        source_inputs.append(bill_input)
        relative_path = bill_semantics_payload_path(bill_input.bill_key)
        target = safe_join_confined(output_root, relative_path, label="bill semantics path")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not overwrite:
            payload = BillSemanticPayload.model_validate(_read_json(target))
            _validate_semantic_payload(payload, bill_input, model_name=model_name)
            cached_count += 1
        else:
            payload = extractor.extract(bill_input)
            payload = _payload_with_input_metadata(
                payload,
                bill_input,
                model_name=model_name,
            )
            _validate_semantic_payload(payload, bill_input, model_name=model_name)
            _write_text_atomic(
                target,
                json.dumps(payload.model_dump(mode="json"), sort_keys=True),
            )
            written_count += 1
        index_rows.append(
            BillSemanticIndexRowPayload(
                bill_key=payload.bill_key,
                path=relative_path,
                sha256=sha256_file(target),
                model_name=payload.model_name,
                available_at=payload.available_at,
                sector_ids=[sector.sector_id for sector in payload.sectors],
                source_anchor_count=len(payload.source_anchors),
            )
        )
    model_names = sorted({str(row.model_name) for row in index_rows if row.model_name is not None})
    source_bill_keys = sorted(row.bill_key for row in index_rows)
    source_inputs_sha256 = _bill_semantic_source_inputs_sha256(source_inputs)
    index = BillSemanticIndexPayload(
        bill_count=len(index_rows),
        model_names=model_names,
        source_bill_count=len(index_rows),
        source_bill_keys=source_bill_keys,
        source_inputs_sha256=source_inputs_sha256,
        bills=sorted(index_rows, key=lambda item: item.bill_key),
        run_metadata={
            "command": "materialize-bill-semantics",
            "bill_count": len(index_rows),
            "model_names": model_names,
            "source_bill_count": len(index_rows),
            "source_bill_keys": source_bill_keys,
            "source_inputs_sha256": source_inputs_sha256,
        },
    )
    index_path = safe_join_confined(
        output_root,
        bill_semantics_index_path(),
        label="bill semantics index path",
    )
    _write_text_atomic(
        index_path,
        json.dumps(index.model_dump(mode="json"), sort_keys=True),
    )
    return BillSemanticsMaterializeResult(
        output_root=output_root,
        requested_count=len(selected_rows),
        written_count=written_count,
        cached_count=cached_count,
        index_path=index_path,
    )


def _validate_semantic_payload(
    payload: BillSemanticPayload,
    bill_input: BillSemanticInput,
    *,
    model_name: str | None = None,
) -> None:
    if payload.bill_key != bill_input.bill_key:
        raise ValueError(
            "Semantic payload bill_key does not match requested bill_key: "
            f"{payload.bill_key!r} != {bill_input.bill_key!r}"
        )
    if payload.available_at != bill_input.available_at:
        raise ValueError(
            "Semantic payload available_at does not match requested bill availability: "
            f"{payload.available_at!r} != {bill_input.available_at!r}"
        )
    if model_name is not None and payload.model_name != model_name:
        raise ValueError(
            "Semantic payload model_name does not match requested model_name: "
            f"{payload.model_name!r} != {model_name!r}"
        )
    if not _has_input_source_anchor(payload.source_anchors, bill_input.source_anchors):
        raise ValueError("Semantic payload must retain at least one input bill source anchor")


def _payload_with_input_metadata(
    payload: BillSemanticPayload,
    bill_input: BillSemanticInput,
    *,
    model_name: str | None,
) -> BillSemanticPayload:
    return payload.model_copy(
        update={
            "available_at": bill_input.available_at,
            "model_name": model_name,
        }
    )


def _extractor_model_name(extractor: object) -> str | None:
    value = getattr(extractor, "model_name", None)
    return str(value) if value is not None else None


def _bill_semantic_source_inputs_sha256(inputs: list[BillSemanticInput]) -> str:
    encoded = json.dumps(
        [item.model_dump(mode="json") for item in sorted(inputs, key=lambda row: row.bill_key)],
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_sha256_hex(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdefABCDEF" for char in value)


def _has_input_source_anchor(
    payload_anchors: list[SourceAnchor],
    input_anchors: list[SourceAnchor],
) -> bool:
    payload_keys = {source_anchor_identity(anchor) for anchor in payload_anchors}
    input_keys = {source_anchor_identity(anchor) for anchor in input_anchors}
    return bool(payload_keys & input_keys)


def load_bill_semantic_payloads(root: Path) -> list[BillSemanticPayload]:
    index_file = safe_join_confined(root, bill_semantics_index_path(), label="bill semantics index")
    index = BillSemanticIndexPayload.model_validate(_read_json(index_file))
    payloads: list[BillSemanticPayload] = []
    for row in index.bills:
        payload_file = safe_join_confined(root, row.path, label="bill semantics payload")
        payload_bytes = payload_file.read_bytes()
        actual_sha256 = hashlib.sha256(payload_bytes).hexdigest()
        if actual_sha256 != row.sha256:
            raise ValueError(
                "Semantic payload sha256 does not match index sha256: "
                f"{actual_sha256!r} != {row.sha256!r}"
            )
        payload = BillSemanticPayload.model_validate(json.loads(payload_bytes.decode("utf-8")))
        if payload.bill_key != row.bill_key:
            raise ValueError(
                "Semantic payload bill_key does not match index bill_key: "
                f"{payload.bill_key!r} != {row.bill_key!r}"
            )
        if payload.available_at != row.available_at:
            raise ValueError(
                "Semantic payload available_at does not match index available_at: "
                f"{payload.available_at!r} != {row.available_at!r}"
            )
        if payload.model_name != row.model_name:
            raise ValueError(
                "Semantic payload model_name does not match index model_name: "
                f"{payload.model_name!r} != {row.model_name!r}"
            )
        payload_sector_ids = [sector.sector_id for sector in payload.sectors]
        if payload_sector_ids != row.sector_ids:
            raise ValueError(
                "Semantic payload sector_ids do not match index sector_ids: "
                f"{payload_sector_ids!r} != {row.sector_ids!r}"
            )
        if len(payload.source_anchors) != row.source_anchor_count:
            raise ValueError(
                "Semantic payload source_anchor_count does not match index "
                f"source_anchor_count: {len(payload.source_anchors)!r} "
                f"!= {row.source_anchor_count!r}"
            )
        payloads.append(payload)
    return payloads


def _loads_json_object(text: str) -> dict[str, Any]:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("OpenAI semantic output must be a JSON object")
    return data


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid bill semantic JSON in {path}: {exc}") from exc


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, text)


def _bill_key(row: dict[str, Any]) -> str:
    congress = _bill_key_int(row["congress"], "congress")
    bill_number = _bill_key_int(row["bill_number"], "bill_number")
    return f"{congress}-{str(row['bill_type']).lower()}-{bill_number}"


def _bill_key_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    return int(value)


def _bill_semantic_available_at_from_row(row: dict[str, Any]) -> date | None:
    return _date_from_value(row.get("latest_action_date")) or _date_from_value(
        row.get("introduced_date")
    )


def _date_from_value(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _congress_bill_url(row: dict[str, Any]) -> str:
    congress = _bill_key_int(row["congress"], "congress")
    bill_number = _bill_key_int(row["bill_number"], "bill_number")
    return (
        "https://api.congress.gov/v3/bill/"
        f"{congress}/{str(row['bill_type']).lower()}/{bill_number}"
        "?format=json"
    )


def _bill_semantic_json_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "bill_key",
            "sectors",
            "industries",
            "affected_entities",
            "coalition_summary",
            "ideological_valence",
            "source_anchors",
        ],
        "properties": {
            "bill_key": {"type": "string"},
            "sectors": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["sector_id", "label", "confidence", "rationale"],
                    "properties": {
                        "sector_id": {"type": "string"},
                        "label": {"type": ["string", "null"]},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "rationale": {"type": "string"},
                    },
                },
            },
            "industries": {"type": "array", "items": {"type": "string"}},
            "affected_entities": {"type": "array", "items": {"type": "string"}},
            "coalition_summary": {"type": ["string", "null"]},
            "ideological_valence": {
                "type": "string",
                "enum": ["left", "right", "mixed", "procedural", "unknown"],
            },
            "source_anchors": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["source_type", "source_id", "url", "label"],
                    "properties": {
                        "source_type": {"type": "string"},
                        "source_id": {"type": "string"},
                        "url": {"type": ["string", "null"]},
                        "label": {"type": "string"},
                    },
                },
            },
        },
    }
