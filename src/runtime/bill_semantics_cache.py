from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from src.prediction.llm_semantics import BillSemanticIndexPayload


def bill_semantics_index_sha256(root: Path | None) -> str | None:
    if root is None:
        return None
    index_path = root / "index.json"
    if not index_path.is_file():
        raise ValueError(f"bill-semantics index not found: {index_path}")
    return hashlib.sha256(index_path.read_bytes()).hexdigest()


def bill_semantics_index_model_names(root: Path | None) -> list[str]:
    if root is None:
        return []
    index_path = root / "index.json"
    if not index_path.is_file():
        return []
    index = json.loads(index_path.read_text(encoding="utf-8"))
    bills = index.get("bills") if isinstance(index, dict) else None
    if not isinstance(bills, list):
        return []
    return sorted(
        {
            str(row["model_name"])
            for row in bills
            if isinstance(row, dict) and row.get("model_name") is not None
        }
    )


def bill_semantics_index_payload(root: Path) -> BillSemanticIndexPayload:
    return BillSemanticIndexPayload.model_validate(bill_semantics_index_object(root))


def bill_semantics_index_object(root: Path) -> dict[str, Any]:
    index_path = root / "index.json"
    data = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("bill semantics index must be an object")
    return data


def bill_semantics_index_run_metadata_failures(
    *,
    index_payload: BillSemanticIndexPayload,
    index_object: dict[str, Any],
) -> list[str]:
    run_metadata = index_object.get("run_metadata")
    if not isinstance(run_metadata, dict):
        return ["index_run_metadata_missing"]
    expected = {
        "command": "materialize-bill-semantics",
        "bill_count": index_payload.bill_count,
        "model_names": list(index_payload.model_names),
        "source_bill_count": index_payload.source_bill_count,
        "source_bill_keys": list(index_payload.source_bill_keys),
        "source_inputs_sha256": index_payload.source_inputs_sha256,
    }
    failures: list[str] = []
    for key in sorted(set(run_metadata) - set(expected)):
        failures.append(f"index_run_metadata_unexpected:{key}")
    for key, expected_value in expected.items():
        actual_value = run_metadata.get(key)
        if _bill_semantics_index_run_metadata_value_invalid(key, actual_value):
            failures.append(f"index_run_metadata_invalid:{key}")
        elif actual_value != expected_value:
            failures.append(f"index_run_metadata_mismatch:{key}")
    return failures


def _bill_semantics_index_run_metadata_value_invalid(key: str, value: Any) -> bool:
    if key in {"bill_count", "source_bill_count"}:
        return not isinstance(value, int) or isinstance(value, bool) or value < 0
    if key in {"model_names", "source_bill_keys"}:
        return not _sorted_unique_nonblank_string_list(value)
    if key == "source_inputs_sha256":
        return value is not None and (not isinstance(value, str) or not is_sha256_hex(value))
    if key == "command":
        return not isinstance(value, str) or not value.strip() or value != value.strip()
    return False


def _sorted_unique_nonblank_string_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(item, str) and item and item.strip() == item for item in value)
        and value == sorted(value)
        and len(set(value)) == len(value)
    )


def validate_bill_semantics_run_metadata(
    *,
    run_metadata: dict[Any, Any],
    issues: list[str],
    require_cache_state: bool = False,
) -> int:
    checked = 0
    if require_cache_state:
        for key in (
            "bill_semantics_root",
            "bill_semantics_index_sha256",
            "bill_semantics_model_names",
        ):
            if key not in run_metadata:
                issues.append(f"run_metadata missing: {key}")
    bill_semantics_root = run_metadata.get("bill_semantics_root")
    expected_bill_semantics_index_sha = run_metadata.get("bill_semantics_index_sha256")
    expected_model_names = run_metadata.get("bill_semantics_model_names")
    if bill_semantics_root is not None:
        if not isinstance(bill_semantics_root, str):
            issues.append("bill-semantics index: root must be a string or null")
        elif not bill_semantics_root.strip():
            issues.append("bill-semantics index: root must be non-empty")
        elif bill_semantics_root != bill_semantics_root.strip():
            issues.append("bill-semantics index: root must be trimmed")
    if expected_bill_semantics_index_sha is not None and not is_sha256_hex(
        expected_bill_semantics_index_sha
    ):
        issues.append("bill-semantics index: invalid sha256")
    if expected_model_names is not None:
        _validate_bill_semantics_model_names(expected_model_names, issues)
    if bill_semantics_root and not expected_bill_semantics_index_sha:
        issues.append("bill-semantics index: missing sha256")
    elif expected_bill_semantics_index_sha and not bill_semantics_root:
        issues.append("bill-semantics index: missing root")
    elif bill_semantics_root and expected_bill_semantics_index_sha:
        checked += 1
        bill_semantics_index = Path(str(bill_semantics_root)) / "index.json"
        if not bill_semantics_index.is_file():
            issues.append(f"bill-semantics index: file not found: {bill_semantics_index}")
        else:
            if is_sha256_hex(expected_bill_semantics_index_sha):
                actual_bill_semantics_index_sha = hashlib.sha256(
                    bill_semantics_index.read_bytes()
                ).hexdigest()
                if actual_bill_semantics_index_sha != expected_bill_semantics_index_sha:
                    issues.append(
                        "bill-semantics index: sha256 mismatch: "
                        f"expected {expected_bill_semantics_index_sha}, "
                        f"got {actual_bill_semantics_index_sha}"
                    )
            if expected_model_names is not None:
                if isinstance(expected_model_names, list):
                    actual_model_names = bill_semantics_index_model_names(
                        Path(str(bill_semantics_root))
                    )
                    normalized_expected_model_names = sorted(
                        str(model_name) for model_name in expected_model_names
                    )
                    if normalized_expected_model_names != actual_model_names:
                        issues.append(
                            "bill-semantics index: model_names mismatch: "
                            f"expected {normalized_expected_model_names!r}, "
                            f"got {actual_model_names!r}"
                        )
    return checked


def _validate_bill_semantics_model_names(value: Any, issues: list[str]) -> None:
    if not isinstance(value, list):
        issues.append("bill-semantics index: model_names must be a list")
        return
    if any(not isinstance(model_name, str) for model_name in value):
        issues.append("bill-semantics index: model_names must be a string list")
        return
    if any(not model_name for model_name in value):
        issues.append("bill-semantics index: model_names must be non-empty")
    if any(model_name != model_name.strip() for model_name in value):
        issues.append("bill-semantics index: model_names must be trimmed")
    if len(set(value)) != len(value):
        issues.append("bill-semantics index: model_names must be unique")


def has_bill_semantics_cache(run_metadata: Any) -> bool:
    if not isinstance(run_metadata, dict):
        return False
    return bool(
        run_metadata.get("bill_semantics_root") and run_metadata.get("bill_semantics_index_sha256")
    )


def bill_semantics_cache_failures(
    *,
    run_metadata: dict[Any, Any],
    required_model_names: list[str],
    require_source_inputs_sha256: bool,
    issues: list[str],
) -> list[str]:
    root_raw = run_metadata.get("bill_semantics_root")
    if not root_raw:
        return ["bill_semantics_cache_missing"]
    root = Path(str(root_raw))
    failures: list[str] = []
    try:
        index_payload = bill_semantics_index_payload(root)
        index_object = bill_semantics_index_object(root)
    except Exception as exc:  # noqa: BLE001
        issues.append(f"bill-semantics index: {exc}")
        return failures
    actual_model_names = set(index_payload.model_names)
    failures.extend(
        f"missing_model_name:{model_name}"
        for model_name in required_model_names
        if model_name not in actual_model_names
    )
    if not require_source_inputs_sha256:
        return failures
    if index_payload.source_inputs_sha256 is None:
        failures.append("source_inputs_sha256_missing")
    elif not is_sha256_hex(index_payload.source_inputs_sha256):
        failures.append("source_inputs_sha256_invalid")
    if "source_bill_count" not in index_object:
        failures.append("source_bill_count_missing")
    if "source_bill_keys" not in index_object:
        failures.append("source_bill_keys_missing")
    failures.extend(
        bill_semantics_index_run_metadata_failures(
            index_payload=index_payload,
            index_object=index_object,
        )
    )
    return failures


def is_sha256_hex(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(char in "0123456789abcdefABCDEF" for char in value)
