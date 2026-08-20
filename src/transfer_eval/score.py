"""Deterministic, implementation-agnostic behavioral score for the pilot."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from statistics import fmean
from typing import Any, Iterable

from src.transfer_eval.contract import ParsedOutput, RECORD_TYPES, canonical_output, semantic_key
from src.transfer_eval.reference import ReferenceSlice

_TYPE_WEIGHTS = {
    "source": 0.05,
    "bill": 0.20,
    "action": 0.25,
    "roll_call": 0.20,
    "member_vote": 0.30,
}
_COMPONENT_WEIGHTS = {
    "schema": 0.10,
    "keys": 0.25,
    "temporal": 0.20,
    "fidelity": 0.25,
    "provenance": 0.15,
    "determinism": 0.05,
}
_FIDELITY_FIELDS = {
    "source": (),
    "bill": (
        "jurisdiction_id",
        "session",
        "identifier",
        "title",
        "classifications",
        "subjects",
        "chamber",
    ),
    "action": ("organization_id", "description", "classifications", "event_date"),
    "roll_call": (
        "bill_id",
        "organization_id",
        "chamber",
        "identifier",
        "motion",
        "result",
        "event_date",
    ),
    "member_vote": ("person_id", "member_name", "choice"),
}
_PROVENANCE_FIELDS = {
    "source": ("source_url", "content_sha256", "available_at"),
    "bill": ("source_id", "source_urls"),
    "action": ("source_id", "source_urls"),
    "roll_call": ("source_id", "source_urls"),
    "member_vote": ("source_id", "source_urls"),
}


@dataclass(frozen=True)
class CaseScore:
    case_id: str
    components: dict[str, float]
    false_record_factor: float
    overall: float
    expected_records: int
    output_lines: int
    errors: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "components": {key: round(value, 6) for key, value in self.components.items()},
            "false_record_factor": round(self.false_record_factor, 6),
            "overall": round(self.overall, 4),
            "expected_records": self.expected_records,
            "output_lines": self.output_lines,
            "errors": list(self.errors),
        }


def score_case(
    reference: ReferenceSlice,
    first: ParsedOutput,
    second: ParsedOutput,
    *,
    first_ok: bool = True,
    second_ok: bool = True,
) -> CaseScore:
    if not first_ok or not second_ok:
        components = {name: 0.0 for name in _COMPONENT_WEIGHTS}
        return CaseScore(
            reference.case_id,
            components,
            0.0,
            0.0,
            len(reference.eligible),
            max(first.nonblank_lines, second.nonblank_lines),
            tuple(first.errors + second.errors),
        )
    run_scores = [_single(reference, first), _single(reference, second)]
    components = {
        key: min(score[key] for score in run_scores)
        for key in ("schema", "keys", "temporal", "fidelity", "provenance")
    }
    components["determinism"] = float(
        first_ok
        and second_ok
        and not first.errors
        and not second.errors
        and canonical_output(first.records) == canonical_output(second.records)
    )
    factors = [_false_record_factor(reference, output) for output in (first, second)]
    false_record_factor = min(factors)
    base = sum(components[name] * weight for name, weight in _COMPONENT_WEIGHTS.items())
    overall = 100.0 * base * false_record_factor
    errors = tuple(first.errors + second.errors)
    return CaseScore(
        reference.case_id,
        components,
        false_record_factor,
        overall,
        len(reference.eligible),
        max(first.nonblank_lines, second.nonblank_lines),
        errors,
    )


def aggregate_scores(scores: Iterable[CaseScore]) -> dict[str, Any]:
    values = tuple(scores)
    if not values:
        raise ValueError("at least one case score is required")
    return {
        "overall": round(fmean(score.overall for score in values), 4),
        "components": {
            name: round(fmean(score.components[name] for score in values), 6)
            for name in _COMPONENT_WEIGHTS
        },
        "cases": [score.as_dict() for score in values],
    }


def _single(reference: ReferenceSlice, output: ParsedOutput) -> dict[str, float]:
    expected = _by_key(reference.eligible)
    universe = {semantic_key(row) for row in reference.universe}
    eligible = set(expected)
    candidates = _by_key_many(output.records)
    correct_empty = not eligible and not output.nonblank_lines
    parse_rate = (
        output.valid_lines / output.nonblank_lines
        if output.nonblank_lines
        else float(correct_empty)
    )
    unique_rate = (
        len(candidates) / output.valid_lines if output.valid_lines else float(correct_empty)
    )
    reference_rate = (
        _reference_rate(output.records, reference) if output.records else float(correct_empty)
    )
    schema = fmean((parse_rate, unique_rate, reference_rate))

    key_scores: dict[str, float] = {}
    for kind in RECORD_TYPES:
        truth = {key for key in eligible if key[0] == kind}
        predicted = {key for key in candidates if key[0] == kind}
        key_scores[kind] = _f1(truth, predicted)
    keys = sum(key_scores[kind] * _TYPE_WEIGHTS[kind] for kind in RECORD_TYPES)

    predicted = set(candidates)
    positives = len(eligible)
    negatives = len(universe - eligible)
    true_positive_rate = len(predicted & eligible) / positives if positives else 1.0
    true_negative_rate = len((universe - eligible) - predicted) / negatives if negatives else 1.0
    temporal = math.sqrt(true_positive_rate * true_negative_rate)
    fidelity = _field_component(expected, candidates, _FIDELITY_FIELDS)
    provenance = _field_component(expected, candidates, _PROVENANCE_FIELDS)
    return {
        "schema": schema,
        "keys": keys,
        "temporal": temporal,
        "fidelity": fidelity,
        "provenance": provenance,
    }


def _reference_rate(records: tuple[dict[str, Any], ...], reference: ReferenceSlice) -> float:
    if not records:
        return 0.0
    sources = {row["source_id"] for row in records if row["record_type"] == "source"}
    bills = {(row["source_id"], row["bill_id"]) for row in records if row["record_type"] == "bill"}
    rolls = {
        (row["source_id"], row["jurisdiction_id"], row["session"], row["roll_call_id"])
        for row in records
        if row["record_type"] == "roll_call"
    }
    dangling = {
        semantic_key(row)
        for row in reference.eligible
        if row["record_type"] == "roll_call"
        and row["bill_id"] is not None
        and (row["source_id"], row["bill_id"])
        not in {
            (bill["source_id"], bill["bill_id"])
            for bill in reference.eligible
            if bill["record_type"] == "bill"
        }
    }
    valid = 0
    for row in records:
        kind = row["record_type"]
        okay = row["source_id"] in sources
        if kind == "action":
            okay = okay and (row["source_id"], row["bill_id"]) in bills
        elif kind == "roll_call" and row["bill_id"] is not None:
            okay = okay and (
                (row["source_id"], row["bill_id"]) in bills or semantic_key(row) in dangling
            )
        elif kind == "member_vote":
            okay = (
                okay
                and (
                    row["source_id"],
                    row["jurisdiction_id"],
                    row["session"],
                    row["roll_call_id"],
                )
                in rolls
            )
        valid += int(okay)
    return valid / len(records)


def _field_component(
    expected: dict[tuple[str, ...], dict[str, Any]],
    candidates: dict[tuple[str, ...], list[dict[str, Any]]],
    fields_by_type: dict[str, tuple[str, ...]],
) -> float:
    weighted = 0.0
    weight_total = 0.0
    by_type: dict[str, list[float]] = defaultdict(list)
    for key, truth in expected.items():
        fields = fields_by_type[key[0]]
        for field in fields:
            matches = [_value_score(truth[field], row[field]) for row in candidates.get(key, ())]
            by_type[key[0]].append(max(matches, default=0.0))
    for kind, values in by_type.items():
        weighted += fmean(values) * _TYPE_WEIGHTS[kind]
        weight_total += _TYPE_WEIGHTS[kind]
    return weighted / weight_total if weight_total else 1.0


def _value_score(expected: Any, actual: Any) -> float:
    if isinstance(expected, list):
        expected_set, actual_set = set(expected), set(actual) if isinstance(actual, list) else set()
        return _f1(expected_set, actual_set)
    return float(expected == actual)


def _f1(expected: set[Any], predicted: set[Any]) -> float:
    if not expected and not predicted:
        return 1.0
    if not expected or not predicted:
        return 0.0
    overlap = len(expected & predicted)
    precision, recall = overlap / len(predicted), overlap / len(expected)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _by_key(records: tuple[dict[str, Any], ...]) -> dict[tuple[str, ...], dict[str, Any]]:
    return {semantic_key(row): row for row in records}


def _by_key_many(
    records: tuple[dict[str, Any], ...],
) -> dict[tuple[str, ...], list[dict[str, Any]]]:
    result: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        result[semantic_key(row)].append(row)
    return result


def _false_record_factor(reference: ReferenceSlice, output: ParsedOutput) -> float:
    if not reference.eligible:
        return float(not output.nonblank_lines)
    if not output.nonblank_lines:
        return 0.0
    expected = {semantic_key(row) for row in reference.eligible}
    predicted = {semantic_key(row) for row in output.records}
    return len(expected & predicted) / output.nonblank_lines


__all__ = ["CaseScore", "aggregate_scores", "score_case"]
