"""Audit missing URL-backed feature sources in prediction eval reports."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from src.evidence.source_anchor_policy import (
    SOURCE_TYPES_REQUIRING_URL,
    is_official_source_url,
)


@dataclass(frozen=True)
class PredictionSourceUrlGap:
    split: str
    model_name: str
    signal_name: str
    prediction_count: int
    url_sourced_prediction_count: int
    missing_url_sourced_prediction_count: int
    url_source_coverage_rate: float | None
    official_source_sourced_prediction_count: int
    missing_official_source_prediction_count: int
    official_source_coverage_rate: float | None
    source_family_ids: tuple[str, ...]
    sample_vote_event_ids: tuple[int, ...]
    sample_bill_keys: tuple[str, ...]
    sample_member_bioguide_ids: tuple[str, ...]
    sample_cases: tuple[dict[str, int | str], ...]


@dataclass(frozen=True)
class PredictionSourceUrlAuditResult:
    eval_report_path: str
    eval_report_sha256: str
    checked: int
    gap_count: int
    jurisdiction_count: int
    implemented_jurisdiction_count: int
    portable_jurisdiction_count: int
    portable_sample_missing_body_id_count: int
    portable_sample_missing_session_id_count: int
    legislative_body_count: int
    legislative_session_count: int
    jurisdiction_ids: tuple[str, ...]
    implemented_jurisdiction_ids: tuple[str, ...]
    portable_jurisdiction_ids: tuple[str, ...]
    legislative_body_ids: tuple[str, ...]
    legislative_session_ids: tuple[str, ...]
    source_family_count: int
    source_family_ids: tuple[str, ...]
    missing_url_sourced_prediction_count: int
    missing_official_source_prediction_count: int
    gaps: tuple[PredictionSourceUrlGap, ...]


def build_prediction_source_url_audit(
    eval_report_path: Path,
) -> PredictionSourceUrlAuditResult:
    """Load an eval report and summarize feature source URL gaps."""
    raw = json.loads(eval_report_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("prediction eval report must be an object")
    gaps = [
        *_coverage_gaps(
            raw.get("training_feature_source_coverage"),
            split="training",
            report=raw,
        ),
        *_coverage_gaps(
            raw.get("evaluation_feature_source_coverage"),
            split="evaluation",
            report=raw,
        ),
    ]
    gaps.sort(
        key=lambda gap: (
            -gap.missing_url_sourced_prediction_count,
            -gap.missing_official_source_prediction_count,
            gap.split,
            gap.model_name,
            gap.signal_name,
        )
    )
    context = _merge_contexts(_sample_context(_audited_sample_records(raw)), _gap_context(gaps))
    return PredictionSourceUrlAuditResult(
        eval_report_path=str(eval_report_path),
        eval_report_sha256=hashlib.sha256(eval_report_path.read_bytes()).hexdigest(),
        checked=1,
        gap_count=len(gaps),
        jurisdiction_count=len(context["jurisdiction_ids"]),
        implemented_jurisdiction_count=len(context["implemented_jurisdiction_ids"]),
        portable_jurisdiction_count=len(context["portable_jurisdiction_ids"]),
        portable_sample_missing_body_id_count=context["portable_sample_missing_body_id_count"],
        portable_sample_missing_session_id_count=context[
            "portable_sample_missing_session_id_count"
        ],
        legislative_body_count=len(context["legislative_body_ids"]),
        legislative_session_count=len(context["legislative_session_ids"]),
        jurisdiction_ids=tuple(context["jurisdiction_ids"]),
        implemented_jurisdiction_ids=tuple(context["implemented_jurisdiction_ids"]),
        portable_jurisdiction_ids=tuple(context["portable_jurisdiction_ids"]),
        legislative_body_ids=tuple(context["legislative_body_ids"]),
        legislative_session_ids=tuple(context["legislative_session_ids"]),
        source_family_count=len(context["source_family_ids"]),
        source_family_ids=tuple(context["source_family_ids"]),
        missing_url_sourced_prediction_count=sum(
            gap.missing_url_sourced_prediction_count for gap in gaps
        ),
        missing_official_source_prediction_count=sum(
            gap.missing_official_source_prediction_count for gap in gaps
        ),
        gaps=tuple(gaps),
    )


def _coverage_gaps(
    value: Any,
    *,
    split: str,
    report: dict[str, Any],
) -> list[PredictionSourceUrlGap]:
    if not isinstance(value, list):
        return []
    gaps: list[PredictionSourceUrlGap] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        model_name = _required_nonblank_string(item, "model_name")
        signal_name = _required_nonblank_string(item, "signal_name")
        row_label = f"{split} {model_name} {signal_name}".strip()
        prediction_count = _required_non_negative_int(
            item,
            "prediction_count",
            row_label=row_label,
        )
        url_sourced_count = _required_non_negative_int(
            item,
            "url_sourced_prediction_count",
            row_label=row_label,
        )
        if url_sourced_count > prediction_count:
            raise ValueError(
                f"{row_label} url_sourced_prediction_count cannot exceed prediction_count"
            )
        missing_count = max(0, prediction_count - url_sourced_count)
        official_sourced_count = _optional_non_negative_int(
            item,
            "official_source_sourced_prediction_count",
            default=url_sourced_count,
            row_label=row_label,
        )
        if official_sourced_count > prediction_count:
            raise ValueError(
                f"{row_label} official_source_sourced_prediction_count cannot "
                "exceed prediction_count"
            )
        _validate_rate(
            item,
            "url_source_coverage_rate",
            numerator=url_sourced_count,
            denominator=prediction_count,
            row_label=row_label,
        )
        _validate_rate(
            item,
            "official_source_coverage_rate",
            numerator=official_sourced_count,
            denominator=prediction_count,
            row_label=row_label,
        )
        missing_official_count = max(0, prediction_count - official_sourced_count)
        if prediction_count == 0 or (missing_count == 0 and missing_official_count == 0):
            continue
        samples = _sample_gap_records(
            report,
            split=split,
            model_name=model_name,
            signal_name=signal_name,
            require_official_source=missing_count == 0,
        )
        sample_cases = _sample_cases_from_records(samples)
        source_family_ids = _source_family_ids_from_records(
            samples,
            model_name=model_name,
            signal_name=signal_name,
        )
        gaps.append(
            PredictionSourceUrlGap(
                split=split,
                model_name=model_name,
                signal_name=signal_name,
                prediction_count=prediction_count,
                url_sourced_prediction_count=url_sourced_count,
                missing_url_sourced_prediction_count=missing_count,
                url_source_coverage_rate=_float_or_none(item.get("url_source_coverage_rate")),
                official_source_sourced_prediction_count=official_sourced_count,
                missing_official_source_prediction_count=missing_official_count,
                official_source_coverage_rate=_float_or_none(
                    item.get("official_source_coverage_rate")
                ),
                source_family_ids=source_family_ids,
                sample_vote_event_ids=_unique_int_values(
                    sample.get("vote_event_id") for sample in sample_cases
                ),
                sample_bill_keys=_unique_str_values(
                    sample.get("bill_context_key") or sample.get("bill_key")
                    for sample in sample_cases
                ),
                sample_member_bioguide_ids=_unique_str_values(
                    sample.get("member_bioguide_id") for sample in sample_cases
                ),
                sample_cases=sample_cases,
            )
        )
    gaps.sort(
        key=lambda gap: (
            -gap.missing_url_sourced_prediction_count,
            -gap.missing_official_source_prediction_count,
            gap.split,
            gap.model_name,
            gap.signal_name,
        )
    )
    return gaps


def _sample_cases_from_records(
    samples: list[dict[str, Any]],
) -> tuple[dict[str, int | str], ...]:
    cases: list[dict[str, int | str]] = []
    for sample in samples:
        case: dict[str, int | str] = {}
        vote_event_id = _int_value(sample.get("vote_event_id"))
        if vote_event_id:
            case["vote_event_id"] = vote_event_id
        event_key = _optional_str_value(sample.get("event_key"))
        if event_key:
            case["event_key"] = event_key
        jurisdiction_id = _optional_str_value(sample.get("jurisdiction_id"))
        if jurisdiction_id:
            case["jurisdiction_id"] = jurisdiction_id
        legislative_body_id = _optional_str_value(sample.get("legislative_body_id"))
        if legislative_body_id:
            case["legislative_body_id"] = legislative_body_id
        legislative_session_id = _optional_str_value(sample.get("legislative_session_id"))
        if legislative_session_id:
            case["legislative_session_id"] = legislative_session_id
        bill_key = _optional_str_value(sample.get("bill_key"))
        if bill_key:
            case["bill_key"] = bill_key
        bill_context_key = _optional_str_value(sample.get("bill_context_key"))
        if bill_context_key:
            case["bill_context_key"] = bill_context_key
        member_bioguide_id = _optional_str_value(sample.get("member_bioguide_id"))
        if member_bioguide_id:
            case["member_bioguide_id"] = member_bioguide_id
        if case:
            cases.append(case)
    return tuple(cases)


def _gap_context(gaps: Iterable[PredictionSourceUrlGap]) -> dict[str, Any]:
    return _sample_context(sample_case for gap in gaps for sample_case in gap.sample_cases) | {
        "source_family_ids": sorted(
            {source_family_id for gap in gaps for source_family_id in gap.source_family_ids}
        )
    }


def _sample_context(samples: Iterable[dict[str, Any]]) -> dict[str, Any]:
    jurisdictions: set[str] = set()
    legislative_bodies: set[str] = set()
    legislative_sessions: set[str] = set()
    source_families: set[str] = set()
    portable_sample_missing_body_id_count = 0
    portable_sample_missing_session_id_count = 0
    for sample_case in samples:
        source_families.update(_source_family_ids_from_sample(sample_case))
        jurisdiction_id = sample_case.get("jurisdiction_id")
        if not isinstance(jurisdiction_id, str) or not jurisdiction_id.strip():
            continue
        jurisdiction_id = jurisdiction_id.strip()
        jurisdictions.add(jurisdiction_id)
        legislative_body_id = sample_case.get("legislative_body_id")
        if not isinstance(legislative_body_id, str) or not legislative_body_id.strip():
            if jurisdiction_id != "us_congress":
                portable_sample_missing_body_id_count += 1
            continue
        legislative_body_id = legislative_body_id.strip()
        legislative_bodies.add(f"{jurisdiction_id}:{legislative_body_id}")
        legislative_session_id = sample_case.get("legislative_session_id")
        if isinstance(legislative_session_id, str) and legislative_session_id.strip():
            legislative_sessions.add(
                f"{jurisdiction_id}:{legislative_body_id}:{legislative_session_id.strip()}"
            )
        elif jurisdiction_id != "us_congress":
            portable_sample_missing_session_id_count += 1
    implemented_jurisdictions = {
        jurisdiction for jurisdiction in jurisdictions if jurisdiction == "us_congress"
    }
    portable_jurisdictions = jurisdictions - implemented_jurisdictions
    return {
        "jurisdiction_ids": sorted(jurisdictions),
        "implemented_jurisdiction_ids": sorted(implemented_jurisdictions),
        "portable_jurisdiction_ids": sorted(portable_jurisdictions),
        "portable_sample_missing_body_id_count": portable_sample_missing_body_id_count,
        "portable_sample_missing_session_id_count": portable_sample_missing_session_id_count,
        "legislative_body_ids": sorted(legislative_bodies),
        "legislative_session_ids": sorted(legislative_sessions),
        "source_family_ids": sorted(source_families),
    }


def _merge_contexts(*contexts: dict[str, Any]) -> dict[str, Any]:
    merged_sets: dict[str, set[str]] = {
        "jurisdiction_ids": set(),
        "implemented_jurisdiction_ids": set(),
        "portable_jurisdiction_ids": set(),
        "legislative_body_ids": set(),
        "legislative_session_ids": set(),
        "source_family_ids": set(),
    }
    body_missing = 0
    session_missing = 0
    for context in contexts:
        for key in merged_sets:
            values = context.get(key)
            if isinstance(values, list):
                merged_sets[key].update(value for value in values if isinstance(value, str))
        body_missing = max(
            body_missing,
            _int_value(context.get("portable_sample_missing_body_id_count")),
        )
        session_missing = max(
            session_missing,
            _int_value(context.get("portable_sample_missing_session_id_count")),
        )
    return {
        **{key: sorted(values) for key, values in merged_sets.items()},
        "portable_sample_missing_body_id_count": body_missing,
        "portable_sample_missing_session_id_count": session_missing,
    }


def _audited_sample_records(report: dict[str, Any]) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for split, coverage_key in (
        ("training", "training_feature_source_coverage"),
        ("evaluation", "evaluation_feature_source_coverage"),
    ):
        coverage = report.get(coverage_key)
        if not isinstance(coverage, list):
            continue
        for item in coverage:
            if not isinstance(item, dict):
                continue
            model_name = _optional_str_value(item.get("model_name"))
            signal_name = _optional_str_value(item.get("signal_name"))
            if model_name is None or signal_name is None:
                continue
            if split == "evaluation":
                samples.extend(
                    _sample_records_from_comparisons(
                        report.get("comparisons"),
                        model_name=model_name,
                        signal_name=signal_name,
                    )
                )
            samples.extend(
                _sample_records_from_dataset(
                    report.get("dataset"),
                    split=split,
                    signal_name=signal_name,
                )
            )
    return samples


def _source_family_ids_from_records(
    samples: list[dict[str, Any]],
    *,
    model_name: str,
    signal_name: str,
) -> tuple[str, ...]:
    source_families: set[str] = set()
    for sample in samples:
        anchors: list[Any] = []
        anchors_by_model = sample.get("feature_source_anchors_by_model")
        if isinstance(anchors_by_model, dict):
            model_anchors = anchors_by_model.get(model_name)
            if isinstance(model_anchors, dict):
                raw_anchors = model_anchors.get(signal_name)
                anchors = raw_anchors if isinstance(raw_anchors, list) else []
        else:
            anchors_by_signal = sample.get("feature_source_anchors")
            if isinstance(anchors_by_signal, dict):
                raw_anchors = anchors_by_signal.get(signal_name)
                anchors = raw_anchors if isinstance(raw_anchors, list) else []
        for anchor in anchors:
            if not isinstance(anchor, dict):
                continue
            source_type = anchor.get("source_type")
            if isinstance(source_type, str) and source_type.strip():
                source_families.add(_source_family_id(source_type, sample=sample))
    return tuple(sorted(source_families))


def _source_family_ids_from_sample(sample: dict[str, Any]) -> tuple[str, ...]:
    source_families: set[str] = set()
    anchors_by_model = sample.get("feature_source_anchors_by_model")
    if isinstance(anchors_by_model, dict):
        for model_anchors in anchors_by_model.values():
            if not isinstance(model_anchors, dict):
                continue
            for anchors in model_anchors.values():
                if isinstance(anchors, list):
                    source_families.update(_source_family_ids_from_anchors(anchors, sample=sample))
    anchors_by_signal = sample.get("feature_source_anchors")
    if isinstance(anchors_by_signal, dict):
        for anchors in anchors_by_signal.values():
            if isinstance(anchors, list):
                source_families.update(_source_family_ids_from_anchors(anchors, sample=sample))
    return tuple(sorted(source_families))


def _source_family_ids_from_anchors(
    anchors: Iterable[Any],
    *,
    sample: dict[str, Any],
) -> set[str]:
    source_families: set[str] = set()
    for anchor in anchors:
        if not isinstance(anchor, dict):
            continue
        source_type = anchor.get("source_type")
        if isinstance(source_type, str) and source_type.strip():
            source_families.add(_source_family_id(source_type, sample=sample))
    return source_families


def _source_family_id(source_type: str, *, sample: dict[str, Any] | None = None) -> str:
    source_type = source_type.strip()
    if source_type in {"vote_event", "congress_vote", "legislative_vote"}:
        jurisdiction_id = sample.get("jurisdiction_id") if sample is not None else None
        return "congress_vote" if jurisdiction_id == "us_congress" else "legislative_vote"
    return source_type


def _sample_gap_records(
    report: dict[str, Any],
    *,
    split: str,
    model_name: str,
    signal_name: str,
    require_official_source: bool,
    limit: int = 5,
) -> list[dict[str, Any]]:
    if split == "evaluation":
        comparison_samples = _sample_gap_records_from_comparisons(
            report.get("comparisons"),
            model_name=model_name,
            signal_name=signal_name,
            require_official_source=require_official_source,
            limit=limit,
        )
        if comparison_samples:
            return comparison_samples
    return _sample_gap_records_from_dataset(
        report.get("dataset"),
        split=split,
        signal_name=signal_name,
        require_official_source=require_official_source,
        limit=limit,
    )


def _sample_gap_records_from_comparisons(
    value: Any,
    *,
    model_name: str,
    signal_name: str,
    require_official_source: bool,
    limit: int,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    samples: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        signals_by_model = item.get("feature_signals_by_model")
        if not isinstance(signals_by_model, dict):
            continue
        signals = signals_by_model.get(model_name)
        if not isinstance(signals, dict) or signal_name not in signals:
            continue
        anchors_by_model = item.get("feature_source_anchors_by_model")
        anchors = []
        if isinstance(anchors_by_model, dict):
            model_anchors = anchors_by_model.get(model_name)
            if isinstance(model_anchors, dict):
                raw_anchors = model_anchors.get(signal_name)
                anchors = raw_anchors if isinstance(raw_anchors, list) else []
        if _anchors_satisfy_gap_check(
            anchors,
            require_official_source=require_official_source,
            sample=item,
        ):
            continue
        samples.append(item)
        if len(samples) >= limit:
            break
    return samples


def _sample_records_from_comparisons(
    value: Any,
    *,
    model_name: str,
    signal_name: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    samples: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        signals_by_model = item.get("feature_signals_by_model")
        if not isinstance(signals_by_model, dict):
            continue
        signals = signals_by_model.get(model_name)
        if isinstance(signals, dict) and signal_name in signals:
            samples.append(item)
    return samples


def _sample_gap_records_from_dataset(
    value: Any,
    *,
    split: str,
    signal_name: str,
    require_official_source: bool,
    limit: int,
) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    split_payload = value.get(split)
    if not isinstance(split_payload, dict):
        return []
    examples = split_payload.get("examples")
    if not isinstance(examples, list):
        return []
    samples: list[dict[str, Any]] = []
    for item in examples:
        if not isinstance(item, dict):
            continue
        features = item.get("features")
        if not isinstance(features, dict) or signal_name not in features:
            continue
        anchors_by_signal = item.get("feature_source_anchors")
        anchors = []
        if isinstance(anchors_by_signal, dict):
            raw_anchors = anchors_by_signal.get(signal_name)
            anchors = raw_anchors if isinstance(raw_anchors, list) else []
        if _anchors_satisfy_gap_check(
            anchors,
            require_official_source=require_official_source,
            sample=item,
        ):
            continue
        samples.append(item)
        if len(samples) >= limit:
            break
    return samples


def _sample_records_from_dataset(
    value: Any,
    *,
    split: str,
    signal_name: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    split_payload = value.get(split)
    if not isinstance(split_payload, dict):
        return []
    examples = split_payload.get("examples")
    if not isinstance(examples, list):
        return []
    samples: list[dict[str, Any]] = []
    for item in examples:
        if not isinstance(item, dict):
            continue
        features = item.get("features")
        if isinstance(features, dict) and signal_name in features:
            samples.append(item)
    return samples


def _anchors_satisfy_gap_check(
    anchors: list[Any],
    *,
    require_official_source: bool,
    sample: dict[str, Any] | None = None,
) -> bool:
    if require_official_source:
        return _has_official_claim_source_anchor(anchors, sample=sample)
    return _has_any_source_url(anchors)


def _has_any_source_url(anchors: Iterable[Any]) -> bool:
    return any(_source_anchor_url(anchor) is not None for anchor in anchors)


def _has_official_claim_source_anchor(
    anchors: Iterable[Any],
    *,
    sample: dict[str, Any] | None = None,
) -> bool:
    return any(_source_anchor_has_official_claim_url(anchor, sample=sample) for anchor in anchors)


def _source_anchor_has_official_claim_url(
    anchor: Any,
    *,
    sample: dict[str, Any] | None = None,
) -> bool:
    source_type = _source_anchor_source_type(anchor)
    if source_type not in SOURCE_TYPES_REQUIRING_URL:
        return False
    normalized_source_type = _source_family_id(source_type, sample=sample)
    return is_official_source_url(normalized_source_type, _source_anchor_url(anchor))


def _source_anchor_source_type(anchor: Any) -> str | None:
    return _optional_str_value(_source_anchor_field(anchor, "source_type"))


def _source_anchor_url(anchor: Any) -> str | None:
    return _optional_str_value(_source_anchor_field(anchor, "url"))


def _source_anchor_field(anchor: Any, field: str) -> Any:
    if isinstance(anchor, dict):
        return anchor.get(field)
    return getattr(anchor, field, None)


def _unique_int_values(values: Iterable[Any]) -> tuple[int, ...]:
    samples: list[int] = []
    for value in values:
        if type(value) is not int:
            continue
        if value not in samples:
            samples.append(value)
    return tuple(samples)


def _unique_str_values(values: Iterable[Any]) -> tuple[str, ...]:
    samples: list[str] = []
    for value in values:
        text = _optional_str_value(value)
        if text is None:
            continue
        if text not in samples:
            samples.append(text)
    return tuple(samples)


def _int_value(value: Any, *, default: int = 0) -> int:
    return value if type(value) is int and value > 0 else default


def _float_or_none(value: Any) -> float | None:
    if type(value) in {int, float}:
        return float(value)
    return None


def _required_non_negative_int(
    item: dict[str, Any],
    key: str,
    *,
    row_label: str,
) -> int:
    value = item.get(key)
    if type(value) is not int or value < 0:
        raise ValueError(f"{row_label} {key} must be a non-negative integer")
    return value


def _required_nonblank_string(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be nonblank")
    return value.strip()


def _optional_str_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _optional_non_negative_int(
    item: dict[str, Any],
    key: str,
    *,
    default: int,
    row_label: str,
) -> int:
    if key not in item:
        return default
    return _required_non_negative_int(item, key, row_label=row_label)


def _validate_rate(
    item: dict[str, Any],
    key: str,
    *,
    numerator: int,
    denominator: int,
    row_label: str,
) -> None:
    if key not in item:
        return
    value = item.get(key)
    if type(value) is int:
        rate_value = float(value)
    elif type(value) is float:
        rate_value = value
    else:
        raise ValueError(f"{row_label} {key} must be numeric when present")
    if denominator == 0:
        raise ValueError(f"{row_label} {key} must be omitted when prediction_count is zero")
    expected = numerator / denominator
    if abs(rate_value - expected) > 1e-9:
        raise ValueError(f"{row_label} {key} must match sourced count / prediction_count")
