"""Private substantive-corpus audit and deterministic RegPatch suite build."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date
from itertools import combinations, pairwise
from pathlib import Path
from typing import Any

from src.regpatch.compiler import (
    EpisodeSpec,
    RuleSpec,
    episode_spec_from_mapping,
    load_episode_spec,
    try_compile_episode,
)

DEFAULT_EVALUATOR_ROOT = Path("data/regpatch_evaluator/substantive")
PILOT_SPEC = Path(__file__).parent / "pilot" / "source_spec.json"
_MANIFEST_NAME = "substantive-manifest.json"
_PLAN_NAME = "suite-plan.json"


def load_substantive_manifest(
    corpus_root: Path = DEFAULT_EVALUATOR_ROOT,
) -> dict[str, Any]:
    """Load the private manifest after checking its evaluator-pinned hash."""
    root = corpus_root.resolve()
    plan = _load_suite_plan(root)
    payload = _read_regular(root / _MANIFEST_NAME)
    actual = hashlib.sha256(payload).hexdigest()
    if actual != plan["source_manifest_sha256"]:
        raise ValueError("substantive RegPatch manifest hash mismatch")
    value = json.loads(payload)
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("substantive RegPatch manifest is invalid")
    candidates = value.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("substantive RegPatch manifest has no candidates")
    return value


def audit_retained_corpus(
    corpus_root: Path = DEFAULT_EVALUATOR_ROOT,
) -> dict[str, object]:
    """Re-hash every official source byte and measure the private handoff."""
    root = corpus_root.resolve()
    manifest = load_substantive_manifest(root)
    plan = _load_suite_plan(root)
    artifacts = 0
    retained_bytes = 0
    failures: list[dict[str, str]] = []
    documents: set[str] = set()
    agencies: set[str] = set()
    titles: set[int] = set()
    seen_paths: set[Path] = set()
    for raw_candidate in _array(manifest["candidates"], "candidates"):
        candidate = _object(raw_candidate, "candidate")
        candidate_id = _candidate_id(candidate)
        cfr = _object(candidate.get("cfr"), f"{candidate_id}.cfr")
        titles.add(int(cfr["title"]))
        files = _object(candidate.get("files"), f"{candidate_id}.files")
        receipts = [
            ("before", _object(files.get("before"), f"{candidate_id}.before")),
            ("after", _object(files.get("after"), f"{candidate_id}.after")),
        ]
        for raw_document in _array(candidate.get("documents"), f"{candidate_id}.documents"):
            document = _object(raw_document, f"{candidate_id}.document")
            document_number = str(document["document_number"])
            documents.add(document_number)
            receipts.extend(
                [
                    (
                        f"{document_number}:xml",
                        _object(document.get("xml"), f"{document_number}.xml"),
                    ),
                    (
                        f"{document_number}:metadata",
                        _object(document.get("metadata"), f"{document_number}.metadata"),
                    ),
                ]
            )
            metadata_path = _receipt_path(root, candidate_id, receipts[-1][1])
            metadata = _read_json(metadata_path)
            for raw_agency in _array(metadata.get("agencies", []), "agencies"):
                agency = _object(raw_agency, "agency")
                name = agency.get("name") or agency.get("raw_name")
                if name:
                    agencies.add(str(name))
        for role, receipt in receipts:
            path = _receipt_path(root, candidate_id, receipt)
            if path in seen_paths:
                failures.append(
                    {"candidate_id": candidate_id, "role": role, "error": "duplicate path"}
                )
                continue
            seen_paths.add(path)
            payload = _read_regular(path)
            actual_hash = hashlib.sha256(payload).hexdigest()
            artifacts += 1
            retained_bytes += len(payload)
            if actual_hash != receipt.get("sha256") or len(payload) != receipt.get("bytes"):
                failures.append(
                    {
                        "candidate_id": candidate_id,
                        "role": role,
                        "expected_sha256": str(receipt.get("sha256")),
                        "actual_sha256": actual_hash,
                    }
                )
    independent_specs = _planned_independent_specs(root, plan)
    for spec in independent_specs:
        titles.add(spec.title)
        spec_artifacts = [
            ("composition:base", spec.base),
            ("composition:target", spec.target),
            *[(f"composition:rule:{rule.order}:xml", rule.xml) for rule in spec.rules],
            *[(f"composition:rule:{rule.order}:metadata", rule.metadata) for rule in spec.rules],
        ]
        for role, artifact in spec_artifacts:
            if artifact.path in seen_paths:
                failures.append(
                    {"candidate_id": spec.episode_id, "role": role, "error": "duplicate path"}
                )
                continue
            seen_paths.add(artifact.path)
            payload = _read_regular(artifact.path)
            actual_hash = hashlib.sha256(payload).hexdigest()
            artifacts += 1
            retained_bytes += len(payload)
            if artifact.expected_sha256 != actual_hash:
                failures.append(
                    {
                        "candidate_id": spec.episode_id,
                        "role": role,
                        "expected_sha256": str(artifact.expected_sha256),
                        "actual_sha256": actual_hash,
                    }
                )
        for rule in spec.rules:
            metadata = _read_json(rule.metadata.path)
            documents.add(str(metadata["document_number"]))
            for raw_agency in _array(metadata.get("agencies", []), "agencies"):
                agency = _object(raw_agency, "agency")
                name = agency.get("name") or agency.get("raw_name")
                if name:
                    agencies.add(str(name))
    manifest_payload = _read_regular(root / _MANIFEST_NAME)
    return {
        "status": "PASS" if not failures else "FAIL",
        "source_manifest_sha256": hashlib.sha256(manifest_payload).hexdigest(),
        "candidates": len(_array(manifest["candidates"], "candidates")),
        "independent_composition_candidates": len(independent_specs),
        "cfr_titles": len(titles),
        "federal_register_documents": len(documents),
        "agencies": len(agencies),
        "artifacts": artifacts,
        "artifacts_rehashed": artifacts - len(failures),
        "retained_bytes": retained_bytes,
        "failures": failures,
    }


def compile_retained_corpus(
    destination: Path,
    *,
    corpus_root: Path = DEFAULT_EVALUATOR_ROOT,
) -> dict[str, object]:
    """Compile every corrected substantive window plus planned real compositions."""
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"refusing to overwrite compiled corpus: {destination}")
    root = corpus_root.resolve()
    audit = audit_retained_corpus(root)
    if audit["status"] != "PASS":
        raise ValueError("retained RegPatch artifact audit failed")
    manifest = load_substantive_manifest(root)
    plan = _load_suite_plan(root)
    candidates = [
        _object(value, "candidate") for value in _array(manifest["candidates"], "candidates")
    ]
    by_id = {_candidate_id(row): row for row in candidates}
    public_source_id = str(plan["public_candidate_id"])
    issue_dates = _object(plan.get("ecfr_issue_dates", {}), "ecfr_issue_dates")
    specs_by_id = {
        source_id: (
            load_episode_spec(PILOT_SPEC)
            if source_id == public_source_id
            else _candidate_spec(candidate, manifest, root, issue_dates)
        )
        for source_id, candidate in by_id.items()
    }
    results: list[dict[str, object]] = []
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{destination.name}-staging-", dir=destination.parent
    ) as temporary_name:
        staged = Path(temporary_name) / "suite"
        staged.mkdir()
        for candidate in candidates:
            source_id = _candidate_id(candidate)
            spec = specs_by_id[source_id]
            split = "public" if source_id == public_source_id else "hidden"
            result = _compile_case(spec, staged, split, attempt_kind="observed_window_attempt")
            result["source_candidate_id"] = source_id
            results.append(result)

        for raw_window in _array(plan.get("composition_windows", []), "composition_windows"):
            window = _object(raw_window, "composition window")
            source_ids = [
                str(value)
                for value in _array(window.get("candidate_ids"), "composition candidate_ids")
            ]
            if not source_ids or any(value not in by_id for value in source_ids):
                raise ValueError("composition plan refers to an unavailable candidate")
            specs = [specs_by_id[value] for value in source_ids]
            composition = _composition_spec(str(window["episode_id"]), specs)
            result = _compile_case(composition, staged, "hidden")
            result["source_candidate_ids"] = source_ids
            results.append(result)

        for composition in _planned_independent_specs(root, plan):
            results.append(_compile_case(composition, staged, "hidden"))

        accepted = [row for row in results if row["status"] == "ACCEPTED"]
        rejected = [row for row in results if row["status"] != "ACCEPTED"]
        public = [row for row in accepted if row["split"] == "public"]
        hidden = [row for row in accepted if row["split"] == "hidden"]
        observed = [
            row
            for row in accepted
            if row["episode_kind"]
            in {"observed_single_document_window", "multi_document_correction_chain"}
        ]
        correction_chains = [
            row for row in observed if row["episode_kind"] == "multi_document_correction_chain"
        ]
        independent_compositions = [
            row
            for row in accepted
            if row["episode_kind"] == "independent_multi_rule_window"
            and _integer(row.get("rule_count", 0), "rule_count") >= 2
        ]
        shapes = sorted(
            {
                str(shape)
                for row in accepted
                for shape in _array(row.get("operation_shapes", []), "operation_shapes")
            }
        )
        gate_checks = {
            "one_public_episode": len(public) == 1,
            "at_least_five_hidden_episodes": len(hidden) >= 5,
            "at_least_ten_validated_transitions": len(observed) >= 10,
            "at_least_five_operation_shapes": len(shapes) >= 5,
            "at_least_five_cfr_titles": _integer(audit["cfr_titles"], "cfr_titles") >= 5,
            "real_independent_multi_rule_composition": bool(independent_compositions),
        }
        gate_status = "PASS" if all(gate_checks.values()) else "FAIL"
        suite: dict[str, object] = {
            "schema_version": 1,
            "suite_kind": "psephos_regpatch_observed_transitions_v0",
            "status": gate_status,
            "acceptance_gate": {
                "status": gate_status,
                "checks": gate_checks,
                "actual_public": len(public),
                "actual_hidden": len(hidden),
            },
            "source_funnel": {
                **_object(plan.get("metadata_probe"), "metadata_probe"),
                "substantive_window_candidates": len(candidates),
                "compiler_accepted_observed_windows": len(observed),
                "compiler_rejected_observed_windows": sum(
                    row["episode_kind"]
                    in {"observed_single_document_window", "multi_document_correction_chain"}
                    for row in rejected
                ),
                "compiled_correction_chain_windows": len(correction_chains),
                "compiled_independent_multi_rule_windows": len(independent_compositions),
                "independent_multi_rule_window_rejections": sum(
                    row["episode_kind"] == "independent_multi_rule_window_attempt"
                    for row in rejected
                ),
            },
            "composition_evidence": {
                "correction_chain_windows": len(correction_chains),
                "correction_chains_count_as_independent_rules": False,
                "independent_multi_rule_windows": len(independent_compositions),
                "rejected_attempts": [
                    {
                        "rejection_codes": [
                            str(rejection["code"])
                            for rejection in _array(row.get("rejections", []), "rejections")
                        ],
                        "unmatched_witness_count": sum(
                            len(
                                _array(
                                    _object(rejection, "rejection")
                                    .get("evidence", {})
                                    .get("unmatched_xrefs", []),
                                    "unmatched_xrefs",
                                )
                            )
                            for rejection in _array(row.get("rejections", []), "rejections")
                            if isinstance(rejection, dict)
                            and isinstance(rejection.get("evidence"), dict)
                        ),
                    }
                    for row in rejected
                    if row["episode_kind"] == "independent_multi_rule_window_attempt"
                ],
            },
            "corpus_audit": audit,
            "episodes": accepted,
            "compiler_rejections": rejected,
            "operation_shapes": shapes,
        }
        _write_json(staged / "suite.json", suite)
        os.replace(staged, destination)
    return suite


def _compile_case(
    spec: EpisodeSpec,
    staged: Path,
    split: str,
    *,
    attempt_kind: str = "independent_multi_rule_window_attempt",
) -> dict[str, object]:
    """All plan variants use the same compiler, classifier, and receipt shape."""
    relative = Path("episodes") / split / spec.episode_id
    result = try_compile_episode(spec, staged / relative)
    classification = (
        _classify_compiled_episode(staged / relative) if result["status"] == "ACCEPTED" else None
    )
    return {
        **result,
        "split": split,
        "path": relative.as_posix(),
        "episode_kind": (
            str(classification["episode_kind"]) if classification is not None else attempt_kind
        ),
        "composition_classification": classification,
    }


def _candidate_spec(
    candidate: Mapping[str, Any],
    manifest: Mapping[str, Any],
    root: Path,
    issue_dates: Mapping[str, Any],
) -> EpisodeSpec:
    candidate_id = _candidate_id(candidate)
    cfr = _object(candidate.get("cfr"), f"{candidate_id}.cfr")
    clocks = _object(candidate.get("clocks"), f"{candidate_id}.clocks")
    files = _object(candidate.get("files"), f"{candidate_id}.files")
    title = int(cfr["title"])
    part = str(cfr["part"])
    before_date = str(clocks["ecfr_before_date"])
    after_date = str(clocks["ecfr_after_date"])
    acquired_at = str(manifest["generated_at"])

    def artifact(receipt: Mapping[str, Any], source_url: str, media_type: str) -> dict[str, object]:
        return {
            "path": str(_receipt_path(root, candidate_id, receipt)),
            "source_url": source_url,
            "acquired_at": acquired_at,
            "acquired_at_basis": "orchestrator_handoff_rehashed_no_headers_retained",
            "response_headers": {},
            "sha256": str(receipt["sha256"]),
            "media_type": media_type,
        }

    rules: list[dict[str, object]] = []
    for order, raw_document in enumerate(
        _array(candidate.get("documents"), f"{candidate_id}.documents"), start=1
    ):
        document = _object(raw_document, f"{candidate_id}.document")
        xml_receipt = _object(document.get("xml"), "document.xml")
        metadata_receipt = _object(document.get("metadata"), "document.metadata")
        metadata = _read_json(_receipt_path(root, candidate_id, metadata_receipt))
        row: dict[str, object] = {
            "order": order,
            "ecfr_amendment_date": after_date,
            "xml": artifact(xml_receipt, str(metadata["full_text_xml_url"]), "application/xml"),
            "metadata": artifact(metadata_receipt, str(metadata["json_url"]), "application/json"),
        }
        issue_date = issue_dates.get(candidate_id)
        if issue_date is None:
            row["ecfr_issue_date_basis"] = "not_retained_in_handoff"
        else:
            row["ecfr_issue_date"] = str(issue_date)
        rules.append(row)
    payload = {
        "episode_id": candidate_id,
        "scope": {"title": title, "parts": [part], "sections": [], "granularity": "part"},
        "window": {"base_date": before_date, "successor_date": after_date},
        "sources": {
            "base": artifact(
                _object(files.get("before"), "before"),
                _ecfr_url(before_date, title, part),
                "application/xml",
            ),
            "target": artifact(
                _object(files.get("after"), "after"),
                _ecfr_url(after_date, title, part),
                "application/xml",
            ),
            "rules": rules,
        },
    }
    return episode_spec_from_mapping(payload, base_dir=root)


def _composition_spec(episode_id: str, specs: Sequence[EpisodeSpec]) -> EpisodeSpec:
    if not specs:
        raise ValueError("composition requires at least one observed window")
    first, last = specs[0], specs[-1]
    if any(spec.title != first.title or spec.parts != first.parts for spec in specs):
        raise ValueError("composition windows do not share one CFR scope")
    if any(left.successor_date > right.base_date for left, right in pairwise(specs)):
        raise ValueError("composition windows overlap or are out of amendment order")
    rules: list[RuleSpec] = []
    seen: set[str] = set()
    for spec in specs:
        for rule in spec.rules:
            key = rule.xml.expected_sha256 or str(rule.xml.path)
            if key in seen:
                continue
            seen.add(key)
            rules.append(rule)
    rules.sort(key=lambda rule: (rule.ecfr_amendment_date or date.min, rule.order))
    ordered = tuple(replace(rule, order=index) for index, rule in enumerate(rules, start=1))
    return replace(
        first,
        episode_id=episode_id,
        requested_sections=(),
        granularity="part",
        successor_date=last.successor_date,
        target=last.target,
        rules=ordered,
    )


def _classify_compiled_episode(episode: Path) -> dict[str, object]:
    """Classify rule/correction structure from compiled causal contributions."""
    receipt = _read_json(episode / "evaluator" / "receipt.json")
    manifest = _read_json(episode / "manifest.json")
    raw_evidence = _array(receipt.get("causal_evidence", []), "causal_evidence")
    substantive_by_document: dict[str, set[tuple[str, str]]] = {}
    correction_documents: set[str] = set()
    for raw_row in raw_evidence:
        row = _object(raw_row, "causal evidence")
        correction_documents.update(
            str(value)
            for value in _array(row.get("correction_documents", []), "correction_documents")
        )
        if row.get("substantive_change") is not True:
            continue
        document_number = str(row.get("document_number", ""))
        region = _object(row.get("changed_region"), "changed_region")
        substantive_by_document.setdefault(document_number, set()).add(
            (str(region.get("kind", "")), str(region.get("region_id", "")))
        )

    inputs = _object(manifest.get("inputs"), "manifest inputs")
    raw_rules = _array(inputs.get("rules"), "manifest rules")
    rule_rows = [_object(value, "manifest rule") for value in raw_rules]
    document_numbers = [str(row["document_number"]) for row in rule_rows]
    primary_documents = [
        document for document in document_numbers if document not in correction_documents
    ]
    contributions: list[dict[str, object]] = []
    amendment_dates: list[date] = []
    clocks = _object(manifest.get("clocks"), "manifest clocks")
    clock_rows = {
        str(row["document_number"]): row
        for value in _array(clocks.get("rules"), "clock rules")
        if (row := _object(value, "clock rule"))
    }
    for document_number in primary_documents:
        regions = sorted(substantive_by_document.get(document_number, set()))
        clock = clock_rows.get(document_number)
        if clock is not None and clock.get("ecfr_amendment_date") is not None:
            amendment_dates.append(date.fromisoformat(str(clock["ecfr_amendment_date"])))
        contributions.append(
            {
                "document_number": document_number,
                "substantive_region_count": len(regions),
                "substantive_regions": [f"{kind}:{identity}" for kind, identity in regions],
            }
        )
    region_sets = [
        set(_array(row["substantive_regions"], "substantive_regions")) for row in contributions
    ]
    disjoint = all(left.isdisjoint(right) for left, right in combinations(region_sets, 2))
    ordered = len(amendment_dates) == len(primary_documents) and all(
        left < right for left, right in pairwise(amendment_dates)
    )
    nonempty = len(primary_documents) >= 2 and all(
        _integer(row["substantive_region_count"], "substantive_region_count") > 0
        for row in contributions
    )
    changed_regions = {
        f"{row['kind']}:{row['region_id']}"
        for value in _array(receipt.get("changed_regions", []), "changed_regions")
        if (row := _object(value, "changed region"))
    }
    contributed_regions = set().union(*region_sets) if region_sets else set()
    fully_covered = changed_regions == contributed_regions
    independent = nonempty and fully_covered and (disjoint or ordered)
    if independent:
        episode_kind = "independent_multi_rule_window"
    elif correction_documents and len(primary_documents) == 1 and len(document_numbers) > 1:
        episode_kind = "multi_document_correction_chain"
    elif len(document_numbers) == 1:
        episode_kind = "observed_single_document_window"
    else:
        episode_kind = "independent_multi_rule_window_attempt"
    return {
        "episode_kind": episode_kind,
        "validated_independent_composition": independent,
        "primary_document_count": len(primary_documents),
        "correction_document_count": len(correction_documents),
        "every_primary_document_has_substantive_contribution": nonempty,
        "changed_regions_fully_covered_by_primary_documents": fully_covered,
        "contributions_disjoint": disjoint,
        "contributions_explicitly_ordered": ordered,
        "primary_contributions": contributions,
    }


def _receipt_path(root: Path, candidate_id: str, receipt: Mapping[str, Any]) -> Path:
    raw_path = receipt.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError("source receipt lacks a path")
    path = (root / candidate_id / Path(raw_path).name).resolve()
    if root not in path.parents:
        raise ValueError("source receipt escapes the evaluator store")
    return path


def _candidate_id(candidate: Mapping[str, Any]) -> str:
    value = candidate.get("candidate_id")
    if not isinstance(value, str) or not value:
        raise ValueError("candidate lacks an identifier")
    return value


def _load_suite_plan(root: Path) -> dict[str, Any]:
    value = _read_json(root / _PLAN_NAME)
    if value.get("schema_version") != 1:
        raise ValueError("private RegPatch suite plan is invalid")
    for field in ("source_manifest_sha256", "public_candidate_id", "metadata_probe"):
        if field not in value:
            raise ValueError(f"private RegPatch suite plan lacks {field}")
    return value


def _planned_independent_specs(root: Path, plan: Mapping[str, Any]) -> list[EpisodeSpec]:
    specs: list[EpisodeSpec] = []
    for raw_path in _array(
        plan.get("independent_composition_specs", []), "independent_composition_specs"
    ):
        relative = Path(str(raw_path))
        path = (root / relative).resolve()
        if root not in path.parents:
            raise ValueError("independent composition spec escapes the evaluator store")
        payload = _read_json(path)
        specs.append(episode_spec_from_mapping(payload, base_dir=path.parent))
    return specs


def _ecfr_url(as_of: str, title: int, part: str) -> str:
    return f"https://www.ecfr.gov/api/versioner/v1/full/{as_of}/title-{title}.xml?part={part}"


def _array(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError(f"{label} must be an array")
    return value


def _integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{label} must be an integer")
    return value


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be an object")
    return value


def _read_regular(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"expected a regular source file: {path}")
    return path.read_bytes()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(_read_regular(path))
    if not isinstance(value, dict):
        raise TypeError(f"expected a JSON object: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


__all__ = [
    "DEFAULT_EVALUATOR_ROOT",
    "audit_retained_corpus",
    "compile_retained_corpus",
    "load_substantive_manifest",
]
