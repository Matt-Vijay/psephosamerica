"""Compile strict, provenance-preserving Federal Register/eCFR patch episodes.

The compiler is intentionally narrower than a generic regulatory-data loader.  It
accepts already-acquired official bytes plus their acquisition metadata, verifies
that a changed historical eCFR region contains an exact Federal Register
amendment link, and emits a clean public/private episode boundary.  Merely
consecutive eCFR snapshots are never sufficient evidence.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from defusedxml import ElementTree as SafeElementTree

from src.regpatch.projection import (
    _XREF_RE,
    _element_digest,
    _local_name,
    _normalized_text,
    _substantive_element_digest,
    _text,
    canonical_table_signature,
    is_editorial_amendment_xref,
    is_projected_editorial_node,
)
from src.regpatch.sources import (
    _EPISODE_ID_RE,
    DEFAULT_METADATA_CAP_BYTES,
    DEFAULT_SOURCE_CAP_BYTES,
    SCHEMA_VERSION,
    ArtifactSpec,
    CompileError,
    _Artifact,
    _artifact_spec,
    _document_number_from_url,
    _load_artifact,
    _mapping,
    _natural_key,
    _official_url,
    _private_artifact_receipt,
    _public_artifact_receipt,
    _sequence,
    _sha256_file,
    _write_json,
    acquire_candidate_sources,
    acquire_official_source,
    expand_candidate_windows,
    probe_ecfr_versions,
    probe_federal_register,
)

__all__ = [
    "DEFAULT_METADATA_CAP_BYTES",
    "DEFAULT_SOURCE_CAP_BYTES",
    "SCHEMA_VERSION",
    "ArtifactSpec",
    "CompileError",
    "EpisodeSpec",
    "RuleSpec",
    "acquire_candidate_sources",
    "acquire_official_source",
    "canonical_table_signature",
    "compile_episode",
    "episode_spec_from_mapping",
    "expand_candidate_windows",
    "is_editorial_amendment_xref",
    "is_projected_editorial_node",
    "load_episode_spec",
    "probe_ecfr_versions",
    "probe_federal_register",
    "try_compile_episode",
]

_DOCUMENT_NUMBER_RE = re.compile(
    r"\bFR\s+Doc\.\s+((?:C[0-9]+-)?[0-9]{4}-[0-9]{4,})\b", re.IGNORECASE
)

_SECTION_RE = re.compile(r"(?<![0-9.])([1-9][0-9]*\.[0-9]+(?:-[0-9]+)*)(?:\s*\(([^)]+)\))?")

_CORRECTION_OF_RE = re.compile(
    r"(?:in\s+FR\s+Doc[.]|in\s+Rule\s+document)\s+((?:C[0-9]+-)?[0-9]{4}-[0-9]{4,})",
    re.IGNORECASE,
)

_FR_CITATION_RE = re.compile(r"(?<![0-9])(?P<volume>[0-9]+)\s+FR\s+(?P<page>[0-9]+)")

_FR_CITATION_ENTRY_RE = re.compile(
    r"(?<![0-9])(?P<volume>[0-9]+)\s+FR\s+"
    r"(?P<pages>[0-9]+(?:\s*,\s*[0-9]+)*)\s*,\s*"
    r"(?P<month>[A-Z][a-z]{2,8})\.?\s+(?P<day>[0-9]{1,2}),\s+"
    r"(?P<year>[0-9]{4})",
    re.IGNORECASE,
)

_AMDPAR_ORDINAL_RE = re.compile(r"^\s*([0-9]+)\s*[.]\s+")

_APPENDIX_RE = re.compile(
    r"\bAppendix\s+(?P<label>.+?)\s+to\s+part\s+(?P<part>[0-9A-Za-z.-]+)",
    re.IGNORECASE,
)

_GRANULARITIES = frozenset({"section", "part", "title"})


@dataclass(frozen=True)
class RuleSpec:
    order: int
    xml: ArtifactSpec
    metadata: ArtifactSpec
    ecfr_amendment_date: date | None = None
    ecfr_issue_date: date | None = None
    ecfr_issue_date_basis: str | None = None


@dataclass(frozen=True)
class EpisodeSpec:
    episode_id: str
    title: int
    parts: tuple[str, ...]
    requested_sections: tuple[str, ...]
    granularity: str
    base_date: date
    successor_date: date
    base: ArtifactSpec
    target: ArtifactSpec
    rules: tuple[RuleSpec, ...]


@dataclass(frozen=True)
class _AmendatoryParagraph:
    ordinal: int | None
    page: int
    text: str


@dataclass(frozen=True)
class _Rule:
    spec: RuleSpec
    xml: _Artifact
    metadata: _Artifact
    document_number: str
    volume: int
    start_page: int
    end_page: int
    publication_date: date
    effective_date: date | None
    citation: str
    agency_names: tuple[str, ...]
    targets: tuple[dict[str, Any], ...]
    amendatory_paragraphs: tuple[_AmendatoryParagraph, ...]
    operation_shapes: tuple[str, ...]
    correction_of: str | None
    corrections: tuple[str, ...]
    metadata_targets: tuple[tuple[int, str], ...]
    metadata_scope_exact: bool


@dataclass(frozen=True)
class _Xref:
    region_kind: str
    region_id: str
    citation: str
    volume: int
    page: int
    publication_date: date
    amdinsn: int | None
    text: str
    attributes: Mapping[str, str]


@dataclass(frozen=True)
class _ChangedRegion:
    kind: str
    identity: str
    citation: str
    before: Any | None
    after: Any | None


def load_episode_spec(path: Path) -> EpisodeSpec:
    """Load a compiler request whose source paths are relative to the request file."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompileError("SPEC_INVALID", f"cannot read JSON request: {path}") from exc
    if not isinstance(payload, dict):
        raise CompileError("SPEC_INVALID", "compiler request must be a JSON object")
    return episode_spec_from_mapping(payload, base_dir=path.parent)


def episode_spec_from_mapping(payload: Mapping[str, Any], *, base_dir: Path) -> EpisodeSpec:
    """Validate the input-only compiler request and resolve its local source paths."""
    try:
        episode_id = str(payload["episode_id"])
        scope = _mapping(payload["scope"], "scope")
        window = _mapping(payload["window"], "window")
        sources = _mapping(payload["sources"], "sources")
        title = int(scope["title"])
        parts = tuple(str(value) for value in _sequence(scope["parts"], "scope.parts"))
        sections = tuple(
            str(value) for value in _sequence(scope.get("sections", []), "scope.sections")
        )
        granularity = str(scope["granularity"])
        base_date = date.fromisoformat(str(window["base_date"]))
        successor_date = date.fromisoformat(str(window["successor_date"]))
        base = _artifact_spec(_mapping(sources["base"], "sources.base"), base_dir)
        target = _artifact_spec(_mapping(sources["target"], "sources.target"), base_dir)
        raw_rules = _sequence(sources["rules"], "sources.rules")
        parsed_rules = []
        for index, value in enumerate(raw_rules):
            row = _mapping(value, f"sources.rules[{index}]")
            raw_amendment = row.get("ecfr_amendment_date")
            raw_issue = row.get("ecfr_issue_date")
            basis = row.get("ecfr_issue_date_basis")
            parsed_rules.append(
                RuleSpec(
                    order=int(row["order"]),
                    xml=_artifact_spec(_mapping(row["xml"], "rule.xml"), base_dir),
                    metadata=_artifact_spec(_mapping(row["metadata"], "rule.metadata"), base_dir),
                    ecfr_amendment_date=(
                        date.fromisoformat(str(raw_amendment)) if raw_amendment else None
                    ),
                    ecfr_issue_date=date.fromisoformat(str(raw_issue)) if raw_issue else None,
                    ecfr_issue_date_basis=str(basis) if basis else None,
                )
            )
        rules = tuple(parsed_rules)
    except (KeyError, TypeError, ValueError) as exc:
        raise CompileError("SPEC_INVALID", f"invalid or missing compiler field: {exc}") from exc

    if _EPISODE_ID_RE.fullmatch(episode_id) is None:
        raise CompileError("SPEC_INVALID", "episode_id must be a lowercase filesystem-safe slug")
    if not 1 <= title <= 999:
        raise CompileError("SPEC_INVALID", "scope.title must be a positive CFR title number")
    if not parts or len(set(parts)) != len(parts):
        raise CompileError("SPEC_INVALID", "scope.parts must contain unique part identifiers")
    if any(not value or "/" in value or "\\" in value for value in parts):
        raise CompileError("SPEC_INVALID", "scope.parts contains an invalid part identifier")
    if len(set(sections)) != len(sections):
        raise CompileError("SPEC_INVALID", "scope.sections must be unique")
    if granularity not in _GRANULARITIES:
        raise CompileError("SPEC_INVALID", f"unsupported granularity: {granularity}")
    if granularity == "section" and not sections:
        raise CompileError("SPEC_INVALID", "section-granularity episodes require scope.sections")
    if successor_date <= base_date:
        raise CompileError("SPEC_INVALID", "successor_date must be later than base_date")
    if not rules:
        raise CompileError("SPEC_INVALID", "at least one Federal Register rule is required")
    if tuple(rule.order for rule in rules) != tuple(range(1, len(rules) + 1)):
        raise CompileError("RULE_ORDER_INVALID", "rule order must be contiguous and start at one")
    for rule in rules:
        if rule.ecfr_amendment_date is None and rule.ecfr_issue_date is not None:
            raise CompileError(
                "RULE_CLOCKS_INCOMPLETE",
                "eCFR issue_date cannot be supplied without amendment_date",
            )
        if (
            rule.ecfr_amendment_date is not None
            and rule.ecfr_issue_date is None
            and rule.ecfr_issue_date_basis != "not_retained_in_handoff"
        ):
            raise CompileError(
                "RULE_CLOCKS_INCOMPLETE",
                "a missing eCFR issue_date requires basis=not_retained_in_handoff",
            )
        if rule.ecfr_amendment_date is None and rule.ecfr_issue_date_basis is not None:
            raise CompileError(
                "RULE_CLOCKS_INVALID",
                "ecfr_issue_date_basis requires an observed amendment_date",
            )
        if rule.ecfr_issue_date is not None and rule.ecfr_issue_date_basis is not None:
            raise CompileError(
                "RULE_CLOCKS_INVALID",
                "ecfr_issue_date_basis is only valid when issue_date is unavailable",
            )
        if rule.ecfr_amendment_date is not None:
            amendment_date = rule.ecfr_amendment_date
            issue_date = rule.ecfr_issue_date
            if not base_date < amendment_date <= successor_date:
                raise CompileError(
                    "RULE_AMENDMENT_DATE_OUTSIDE_WINDOW",
                    "eCFR amendment_date must fall inside the snapshot window",
                )
            if issue_date is not None and issue_date < amendment_date:
                raise CompileError(
                    "RULE_ISSUE_DATE_INVALID",
                    "eCFR issue_date cannot precede amendment_date",
                )
    return EpisodeSpec(
        episode_id=episode_id,
        title=title,
        parts=parts,
        requested_sections=sections,
        granularity=granularity,
        base_date=base_date,
        successor_date=successor_date,
        base=base,
        target=target,
        rules=rules,
    )


def compile_episode(spec: EpisodeSpec | Path, destination: Path) -> dict[str, Any]:
    """Compile one witnessed episode into a clean candidate/evaluator layout.

    ``manifest.json`` and ``input/`` are candidate-visible.  Target bytes, target
    hashes, the metadata sidecars, and causal receipts exist only below
    ``evaluator/``.
    """
    request = load_episode_spec(spec) if isinstance(spec, Path) else spec
    if destination.exists():
        raise CompileError("DESTINATION_EXISTS", f"refusing to overwrite: {destination}")

    base = _load_artifact(request.base, expected_media="application/xml")
    target = _load_artifact(request.target, expected_media="application/xml")
    base_root = _parse_xml(base, role="base eCFR")
    target_root = _parse_xml(target, role="successor eCFR")
    _validate_ecfr_scope(base_root, request, role="base")
    _validate_ecfr_scope(target_root, request, role="successor")
    if base.sha256 == target.sha256:
        raise CompileError("BASE_TARGET_IDENTICAL", "the two eCFR snapshots have identical bytes")

    rules = tuple(_load_rule(rule, request) for rule in request.rules)
    _validate_rule_order(rules)
    changed_region_map, changed_regions = _changed_regions(base_root, target_root, request)
    changed_sections = {
        region.identity for region in changed_region_map.values() if region.kind == "section"
    }
    new_xrefs, resolved_xrefs = _amendment_xref_transitions(base_root, target_root)
    causal_evidence, ignored_witnesses = _validate_causality(
        request, rules, changed_region_map, new_xrefs, resolved_xrefs
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{destination.name}.", dir=destination.parent
    ) as temporary_name:
        staging = Path(temporary_name) / "episode"
        (staging / "input" / "rules").mkdir(parents=True)
        (staging / "evaluator" / "metadata").mkdir(parents=True)
        shutil.copyfile(base.spec.path, staging / "input" / "base.xml")
        shutil.copyfile(target.spec.path, staging / "evaluator" / "target.xml")

        public_rule_rows: list[dict[str, Any]] = []
        private_rule_rows: list[dict[str, Any]] = []
        clocks: list[dict[str, Any]] = []
        for rule in rules:
            rule_name = f"{rule.spec.order:02d}-{rule.document_number}.xml"
            metadata_name = f"{rule.spec.order:02d}-{rule.document_number}.json"
            rule_relative = f"input/rules/{rule_name}"
            metadata_relative = f"evaluator/metadata/{metadata_name}"
            shutil.copyfile(rule.xml.spec.path, staging / rule_relative)
            shutil.copyfile(rule.metadata.spec.path, staging / metadata_relative)
            public_rule_rows.append(
                {
                    "order": rule.spec.order,
                    "path": rule_relative,
                    "document_number": rule.document_number,
                    **_public_artifact_receipt(rule.xml),
                    "declared_targets": list(rule.targets),
                    "operation_shapes": list(rule.operation_shapes),
                }
            )
            private_rule_rows.append(
                {
                    "order": rule.spec.order,
                    "document_number": rule.document_number,
                    "scope_validation": _rule_scope_receipt(rule),
                    "xml": {"path": rule_relative, **_private_artifact_receipt(rule.xml)},
                    "metadata": {
                        "path": metadata_relative,
                        **_private_artifact_receipt(rule.metadata),
                    },
                }
            )
            incorporation_dates = sorted(
                {
                    evidence["ecfr_incorporation_date"]
                    for evidence in causal_evidence
                    if evidence["document_number"] == rule.document_number
                }
            )
            ecfr_amendment_date = rule.spec.ecfr_amendment_date or date.fromisoformat(
                incorporation_dates[0]
            )
            ecfr_issue_date = rule.spec.ecfr_issue_date
            clocks.append(
                {
                    "order": rule.spec.order,
                    "document_number": rule.document_number,
                    "federal_register_issue_date": rule.publication_date.isoformat(),
                    "publication_date": rule.publication_date.isoformat(),
                    "amendment_date": ecfr_amendment_date.isoformat(),
                    "amendment_date_semantics": "eCFR versioner amendment_date",
                    "ecfr_amendment_date": ecfr_amendment_date.isoformat(),
                    "ecfr_issue_date": (ecfr_issue_date.isoformat() if ecfr_issue_date else None),
                    "ecfr_issue_date_basis": rule.spec.ecfr_issue_date_basis,
                    "observed_incorporation_dates": incorporation_dates,
                    "legal_effective_date": (
                        rule.effective_date.isoformat() if rule.effective_date else None
                    ),
                }
            )

        manifest: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "episode_id": request.episode_id,
            "task_type": "ecfr_amendatory_patch",
            "scope": {
                "title": request.title,
                "parts": list(request.parts),
                "sections": sorted(changed_sections, key=_natural_key),
                "granularity": request.granularity,
            },
            "window": {
                "base_date": request.base_date.isoformat(),
                "successor_date": request.successor_date.isoformat(),
            },
            "clocks": {
                "rules": clocks,
                "note": (
                    "Federal Register publication and eCFR incorporation are observed editorial "
                    "clocks; neither is asserted to equal legal effectiveness."
                ),
            },
            "inputs": {
                "base": {
                    "path": "input/base.xml",
                    **_public_artifact_receipt(base),
                },
                "rules": public_rule_rows,
            },
            "output_contract": {
                "result_path": "output/result.xml",
                "provenance_path": "output/provenance.json",
                "provenance_schema": {
                    "schema_version": SCHEMA_VERSION,
                    "required": [
                        "schema_version",
                        "episode_id",
                        "base_sha256",
                        "rule_sha256s",
                        "result_sha256",
                    ],
                    "rule_sha256s_order": "must match inputs.rules order",
                },
            },
        }
        manifest_path = staging / "manifest.json"
        _write_json(manifest_path, manifest)
        manifest_sha256, manifest_bytes = _sha256_file(manifest_path)

        target_receipt = {
            "path": "evaluator/target.xml",
            **_private_artifact_receipt(target),
        }
        receipt: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "receipt_kind": "regpatch_witnessed_episode",
            "episode_id": request.episode_id,
            "status": "ACCEPTED",
            "public_manifest": {
                "path": "manifest.json",
                "sha256": manifest_sha256,
                "byte_count": manifest_bytes,
            },
            "sources": {
                "base": {"path": "input/base.xml", **_private_artifact_receipt(base)},
                "rules": private_rule_rows,
                "target": target_receipt,
            },
            "changed_regions": changed_regions,
            "causal_evidence": causal_evidence,
            "ignored_editorial_witnesses": ignored_witnesses,
            "validation": {
                "before_rule_after_bytes_present": True,
                "all_source_hashes_verified": True,
                "all_changed_regions_mapped": True,
                "all_new_ecfr_amendment_links_mapped": True,
                "same_day_collisions_fully_observed": True,
                "legal_effectiveness_inferred": False,
            },
        }
        _write_json(staging / "evaluator" / "receipt.json", receipt)
        acquisition = _acquisition_manifest(request, base, target, rules)
        _write_json(staging / "evaluator" / "acquisition.json", acquisition)
        os.replace(staging, destination)

    return {
        "status": "ACCEPTED",
        "episode_id": request.episode_id,
        "changed_sections": sorted(changed_sections, key=_natural_key),
        "rule_count": len(rules),
        "operation_shapes": sorted({shape for rule in rules for shape in rule.operation_shapes}),
        "manifest_sha256": manifest_sha256,
        "target_sha256": target.sha256,
    }


def try_compile_episode(spec: EpisodeSpec | Path, destination: Path) -> dict[str, Any]:
    """Compile one request, returning an exact rejection record instead of raising."""
    episode_id = spec.episode_id if isinstance(spec, EpisodeSpec) else spec.stem
    try:
        return compile_episode(spec, destination)
    except CompileError as exc:
        return {
            "status": "REJECTED",
            "episode_id": episode_id,
            "rejections": [exc.as_rejection()],
        }


def _parse_xml(artifact: _Artifact, *, role: str) -> Any:
    try:
        return SafeElementTree.fromstring(artifact.content)
    except Exception as exc:
        raise CompileError("XML_INVALID", f"cannot safely parse {role} XML") from exc


def _load_rule(spec: RuleSpec, episode: EpisodeSpec) -> _Rule:
    xml = _load_artifact(spec.xml, expected_media="application/xml")
    metadata = _load_artifact(spec.metadata, expected_media="application/json")
    root = _parse_xml(xml, role=f"rule {spec.order}")
    try:
        payload = json.loads(metadata.content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompileError(
            "METADATA_INVALID", f"rule {spec.order} metadata is not valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise CompileError("METADATA_INVALID", f"rule {spec.order} metadata must be an object")

    try:
        document_number = str(payload["document_number"])
        volume = int(payload["volume"])
        start_page = int(payload["start_page"])
        end_page = int(payload["end_page"])
        publication_date = date.fromisoformat(str(payload["publication_date"]))
        effective_date = (
            date.fromisoformat(str(payload["effective_on"]))
            if payload.get("effective_on")
            else None
        )
        citation = str(payload["citation"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CompileError(
            "METADATA_INVALID", f"rule {spec.order} is missing required official metadata"
        ) from exc
    if end_page < start_page or volume <= 0:
        raise CompileError("METADATA_INVALID", f"rule {spec.order} has an invalid FR page range")
    if citation != f"{volume} FR {start_page}":
        raise CompileError(
            "METADATA_INVALID",
            f"rule {spec.order} citation disagrees with its volume/start page",
        )
    xml_document_numbers = {
        match.group(1)
        for elem in root.iter()
        if _local_name(str(elem.tag)) == "FRDOC"
        for match in _DOCUMENT_NUMBER_RE.finditer(_text(elem))
    }
    if xml_document_numbers != {document_number}:
        raise CompileError(
            "RULE_DOCUMENT_NUMBER_MISMATCH",
            f"rule {spec.order} XML/JSON document numbers do not agree",
            evidence={
                "metadata_document_number": document_number,
                "xml_document_numbers": sorted(xml_document_numbers),
            },
        )
    full_text_url = payload.get("full_text_xml_url")
    if full_text_url and _official_url(str(full_text_url)) != xml.spec.source_url:
        raise CompileError(
            "RULE_SOURCE_URL_MISMATCH",
            f"rule {document_number} metadata does not identify the supplied XML URL",
        )
    cfr_references = _sequence(payload.get("cfr_references", []), "cfr_references")
    metadata_targets = {
        (
            int(_mapping(row, "cfr_reference").get("title", -1)),
            str(_mapping(row, "cfr_reference").get("part", "")),
        )
        for row in cfr_references
    }
    if not any(title == episode.title for title, _part in metadata_targets):
        raise CompileError(
            "RULE_TARGET_MISMATCH",
            f"rule {document_number} metadata does not cite CFR title {episode.title}",
            evidence={
                "metadata_targets": [
                    {"title": title, "part": part} for title, part in sorted(metadata_targets)
                ]
            },
        )
    metadata_scope_exact = any((episode.title, part) in metadata_targets for part in episode.parts)
    targets = _extract_rule_targets(root)
    if not any(
        target["title"] == episode.title and target["part"] in episode.parts for target in targets
    ):
        raise CompileError(
            "RULE_TARGET_MISMATCH",
            f"rule {document_number} REGTEXT does not target the requested title/part",
            evidence={
                "metadata_targets": [
                    {"title": title, "part": part} for title, part in sorted(metadata_targets)
                ]
            },
        )
    amendatory_paragraphs = _extract_amendatory_paragraphs(root, start_page)
    if not amendatory_paragraphs:
        raise CompileError(
            "AMENDATORY_INSTRUCTIONS_MISSING",
            f"rule {document_number} contains no AMDPAR instructions",
        )
    agencies = payload.get("agencies", [])
    agency_names = tuple(
        str(_mapping(value, "agency").get("name") or _mapping(value, "agency").get("raw_name"))
        for value in _sequence(agencies, "agencies")
        if _mapping(value, "agency").get("name") or _mapping(value, "agency").get("raw_name")
    )
    correction_of = (
        _document_number_from_url(str(payload["correction_of"]))
        if payload.get("correction_of")
        else None
    )
    corrections = tuple(
        _document_number_from_url(str(value))
        for value in _sequence(payload.get("corrections", []), "corrections")
    )
    correction_markers = " ".join(
        str(payload.get(field) or "") for field in ("action", "title", "abstract")
    ).casefold()
    if correction_of is None and (
        "correction" in correction_markers or "correction" in _text(root).casefold()
    ):
        referenced = {
            match.group(1)
            for match in _CORRECTION_OF_RE.finditer(_text(root))
            if match.group(1) != document_number
        }
        if len(referenced) == 1:
            correction_of = next(iter(referenced))
    return _Rule(
        spec=spec,
        xml=xml,
        metadata=metadata,
        document_number=document_number,
        volume=volume,
        start_page=start_page,
        end_page=end_page,
        publication_date=publication_date,
        effective_date=effective_date,
        citation=citation,
        agency_names=agency_names,
        targets=tuple(targets),
        amendatory_paragraphs=tuple(amendatory_paragraphs),
        operation_shapes=tuple(sorted(_operation_shapes(amendatory_paragraphs))),
        correction_of=correction_of,
        corrections=corrections,
        metadata_targets=tuple(sorted(metadata_targets)),
        metadata_scope_exact=metadata_scope_exact,
    )


def _validate_rule_order(rules: Sequence[_Rule]) -> None:
    expected = sorted(
        rules,
        key=lambda rule: (
            rule.spec.ecfr_amendment_date or rule.publication_date,
            rule.publication_date,
            rule.volume,
            rule.start_page,
            rule.document_number,
        ),
    )
    if list(rules) != expected:
        raise CompileError(
            "RULE_ORDER_INVALID",
            "rules must be ordered by observed eCFR amendment date, then FR publication/page",
        )


def _validate_ecfr_scope(root: Any, spec: EpisodeSpec, *, role: str) -> None:
    actual_parts = {
        str(elem.attrib["N"])
        for elem in root.iter()
        if elem.attrib.get("TYPE") == "PART" and elem.attrib.get("N") is not None
    }
    if root.attrib.get("TYPE") == "PART" and root.attrib.get("N") is not None:
        actual_parts.add(str(root.attrib["N"]))
    missing_parts = sorted(set(spec.parts) - actual_parts, key=_natural_key)
    if missing_parts:
        raise CompileError(
            "SCOPE_MISMATCH",
            f"{role} eCFR XML does not contain declared parts: {missing_parts}",
        )
    title_markers = [
        str(root.attrib.get("N", "")) if root.attrib.get("TYPE") == "TITLE" else "",
        str(root.attrib.get("TITLE", "")),
        str(root.attrib.get("hierarchy_metadata", "")),
    ]
    if not any(
        marker == str(spec.title) or f"title-{spec.title}" in marker.lower()
        for marker in title_markers
    ):
        raise CompileError(
            "SCOPE_MISMATCH", f"{role} eCFR XML does not identify CFR title {spec.title}"
        )
    if spec.requested_sections:
        actual_sections = _section_map(root)
        missing_sections = sorted(
            set(spec.requested_sections) - set(actual_sections), key=_natural_key
        )
        if missing_sections:
            raise CompileError(
                "SCOPE_MISMATCH",
                f"{role} eCFR XML is missing declared sections: {missing_sections}",
            )


def _changed_regions(
    before_root: Any, after_root: Any, spec: EpisodeSpec
) -> tuple[dict[str, _ChangedRegion], list[dict[str, Any]]]:
    if _substantive_element_digest(before_root) == _substantive_element_digest(after_root):
        raise CompileError(
            "NO_SUBSTANTIVE_CHANGE_AFTER_EDITORIAL_PROJECTION",
            "before/after eCFR states differ only by editorial amendment-link XREF nodes",
        )
    before_regions = _region_map(before_root, spec.title)
    after_regions = _region_map(after_root, spec.title)
    changed: dict[str, _ChangedRegion] = {}
    regions: list[dict[str, Any]] = []
    # This order also determines citation-witness order in the private receipt.
    for key in sorted(before_regions.keys() | after_regions.keys(), key=_natural_key):
        before = before_regions.get(key)
        after = after_regions.get(key)
        before_node = before.after if before else None
        after_node = after.after if after else None
        before_digest = _substantive_element_digest(before_node)
        after_digest = _substantive_element_digest(after_node)
        if before_digest == after_digest:
            continue
        template = before or after
        assert template is not None
        changed[key] = _ChangedRegion(
            kind=template.kind,
            identity=template.identity,
            citation=template.citation,
            before=before_node,
            after=after_node,
        )
        row = {
            "kind": template.kind,
            "region_id": template.identity,
            "citation": template.citation,
            "projection": "substantive_v3_xref_cita_and_table_presentation_normalized",
            "before_sha256": before_digest,
            "after_sha256": after_digest,
            "before_raw_sha256": _element_digest(before_node),
            "after_raw_sha256": _element_digest(after_node),
            "before_present": before_node is not None,
            "after_present": after_node is not None,
        }
        if template.kind in {"section", "appendix"}:
            row[template.kind] = template.identity
        regions.append(row)
    changed_sections = {region.identity for region in changed.values() if region.kind == "section"}
    if spec.requested_sections and not changed_sections.issubset(set(spec.requested_sections)):
        raise CompileError(
            "CHANGE_OUTSIDE_DECLARED_SCOPE",
            "successor changes sections outside the requested section scope",
            evidence={"changed_sections": sorted(changed_sections, key=_natural_key)},
        )
    if not changed:
        raise CompileError("NO_CHANGED_REGIONS", "no changed eCFR regions were found")
    common_region_keys = set(before_regions) & set(after_regions)
    if _outside_region_tokens(
        before_root, spec.title, common_region_keys
    ) != _outside_region_tokens(after_root, spec.title, common_region_keys):
        raise CompileError(
            "UNMAPPED_STRUCTURAL_CHANGE",
            "the eCFR snapshots differ outside identified section/appendix/part metadata regions",
        )
    return changed, regions


def _successor_citation_causal_evidence(
    spec: EpisodeSpec,
    rules: Sequence[_Rule],
    changed_regions: Mapping[str, _ChangedRegion],
) -> tuple[list[dict[str, Any]], set[str]]:
    """Witness incorporation from successor-only CITA/SOURCE identities.

    This is deliberately a fallback for transitions with no XREF delta.  Every
    successor-only citation in a changed region must map uniquely to supplied
    official metadata, an explicit REGTEXT/AMDPAR target, and the observed eCFR
    amendment date.  That makes an omitted same-day rule a hard rejection rather
    than silently treating a coincident citation as provenance.
    """
    evidence: list[dict[str, Any]] = []
    matched_documents: set[str] = set()
    unmatched: list[dict[str, Any]] = []
    for region_key in sorted(changed_regions, key=_natural_key):
        region = changed_regions[region_key]
        for citation in _new_region_fr_citations(region):
            candidates: list[tuple[_Rule, _AmendatoryParagraph]] = []
            for rule in rules:
                if (
                    citation["volume"] != rule.volume
                    or not rule.start_page <= int(citation["page"]) <= rule.end_page
                    or citation.get("publication_date") != rule.publication_date.isoformat()
                    or rule.spec.ecfr_amendment_date != spec.successor_date
                    or not _rule_targets_region(rule, spec.title, region.kind, region.identity)
                ):
                    continue
                paragraph = _amendatory_paragraph_for_region(
                    rule, region, page=int(citation["page"])
                )
                if paragraph is not None:
                    candidates.append((rule, paragraph))
            if len(candidates) > 1:
                raise CompileError(
                    "CAUSAL_CITATION_AMBIGUOUS",
                    "successor FR citation maps to multiple supplied rules",
                    evidence={
                        "changed_region": region.citation,
                        "citation": citation,
                        "document_numbers": [rule.document_number for rule, _ in candidates],
                    },
                )
            if not candidates:
                unmatched.append(
                    {
                        "changed_region": {
                            "kind": region.kind,
                            "region_id": region.identity,
                            "citation": region.citation,
                        },
                        "citation": citation,
                    }
                )
                continue
            rule, paragraph = candidates[0]
            issue_date = rule.spec.ecfr_issue_date
            matched_documents.add(rule.document_number)
            evidence.append(
                {
                    "witness_motif": "successor_fr_citation_delta",
                    "document_number": rule.document_number,
                    "federal_register_document_citation": rule.citation,
                    "federal_register_amendment_page": (
                        f"{citation['volume']} FR {citation['page']}"
                    ),
                    "changed_region": {
                        "kind": region.kind,
                        "region_id": region.identity,
                        "citation": region.citation,
                    },
                    "substantive_change": True,
                    "federal_register_publication_date": rule.publication_date.isoformat(),
                    "pending_xref_publication_date": None,
                    "ecfr_amendment_date": spec.successor_date.isoformat(),
                    "ecfr_issue_date": issue_date.isoformat() if issue_date else None,
                    "ecfr_issue_date_basis": rule.spec.ecfr_issue_date_basis,
                    "ecfr_incorporation_date": spec.successor_date.isoformat(),
                    "ecfr_xref_text": None,
                    "ecfr_xref_attributes": {},
                    "amendatory_instruction": paragraph.ordinal,
                    "successor_citation_evidence": [citation],
                    "match_basis": [
                        "successor-only CITA/SOURCE FR citation identity",
                        "same FR volume, publication date, and page within document range",
                        "REGTEXT and AMDPAR explicitly target this changed CFR region",
                        "supplied rule has the observed eCFR amendment date",
                    ],
                }
            )
    if unmatched:
        raise CompileError(
            "CITATION_COLLISION_UNOBSERVED",
            "successor-only FR citations in changed regions lack supplied causal rules",
            evidence={"unmatched_citations": unmatched},
        )
    return evidence, matched_documents


def _validate_causality(
    spec: EpisodeSpec,
    rules: Sequence[_Rule],
    changed_regions: Mapping[str, _ChangedRegion],
    new_xrefs: Sequence[_Xref],
    resolved_xrefs: Sequence[_Xref],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    all_witnesses = [("immediate_new_xref", xref) for xref in new_xrefs] + [
        ("deferred_pending_xref_resolved", xref) for xref in resolved_xrefs
    ]
    ignored = [
        {
            "reason_code": "EDITORIAL_WITNESS_REGION_UNCHANGED",
            "motif": motif,
            **_xref_receipt(xref),
        }
        for motif, xref in all_witnesses
        if _region_key(xref.region_kind, xref.region_id) not in changed_regions
    ]
    witnesses = [
        (motif, xref)
        for motif, xref in all_witnesses
        if _region_key(xref.region_kind, xref.region_id) in changed_regions
    ]
    citation_evidence: list[dict[str, Any]] = []
    citation_documents: set[str] = set()
    if not witnesses:
        citation_evidence, citation_documents = _successor_citation_causal_evidence(
            spec, rules, changed_regions
        )
        if not citation_evidence:
            raise CompileError(
                "CAUSAL_REFERENCE_MISSING",
                "no XREF or successor CITA/SOURCE delta witnesses the substantive change",
                evidence={"ignored_editorial_witnesses": ignored},
            )
    matched_witnesses: list[tuple[str, _Xref, list[_Rule]]] = []
    unmatched_witnesses: list[tuple[str, _Xref]] = []
    for motif, xref in witnesses:
        matches = [rule for rule in rules if _xref_matches_rule(xref, rule)]
        if matches:
            matched_witnesses.append((motif, xref, matches))
            continue
        region = changed_regions[_region_key(xref.region_kind, xref.region_id)]
        before_citations = _region_fr_citations(region.before) if region.before is not None else []
        if motif == "deferred_pending_xref_resolved" and any(
            citation["volume"] == xref.volume and citation["page"] == xref.page
            for citation in before_citations
        ):
            ignored.append(
                {
                    "reason_code": "STALE_RESOLVED_XREF_ALREADY_CITED_BEFORE_WINDOW",
                    "motif": motif,
                    **_xref_receipt(xref),
                }
            )
        else:
            unmatched_witnesses.append((motif, xref))
    if unmatched_witnesses and matched_witnesses:
        raise CompileError(
            "SAME_DAY_COLLISION_UNOBSERVED",
            "not every eCFR amendment-link transition is represented by a supplied rule",
            evidence={
                "unmatched_xrefs": [
                    {"motif": motif, **_xref_receipt(xref)} for motif, xref in unmatched_witnesses
                ]
            },
        )
    if unmatched_witnesses:
        _raise_xref_mismatch(unmatched_witnesses[0][1], rules, spec.title)

    evidence: list[dict[str, Any]] = list(citation_evidence)
    matched_documents: set[str] = set(citation_documents)
    for motif, xref, matches in matched_witnesses:
        region_key = _region_key(xref.region_kind, xref.region_id)
        if len(matches) > 1:
            raise CompileError(
                "CAUSAL_REFERENCE_AMBIGUOUS",
                f"eCFR amendment link maps to multiple supplied rules: {xref.text}",
                evidence={"document_numbers": [rule.document_number for rule in matches]},
            )
        rule = matches[0]
        if not _rule_targets_region(rule, spec.title, xref.region_kind, xref.region_id):
            raise CompileError(
                "RULE_TARGET_MISMATCH",
                f"rule {rule.document_number} does not target changed region {xref.citation}",
            )
        issue_date: date | None
        if motif == "immediate_new_xref":
            if not spec.base_date < xref.publication_date <= spec.successor_date:
                raise CompileError(
                    "IMMEDIATE_XREF_OUTSIDE_WINDOW",
                    "new eCFR amendment-link publication date is outside the snapshot window",
                    evidence={"xref": _xref_receipt(xref)},
                )
            amendment_date = rule.spec.ecfr_amendment_date or xref.publication_date
            issue_date = rule.spec.ecfr_issue_date or amendment_date
            after_citations: list[dict[str, Any]] = []
        else:
            if xref.publication_date > spec.base_date:
                raise CompileError(
                    "DEFERRED_PENDING_XREF_NOT_IN_BASE",
                    "resolved amendment link was not pending by the base snapshot date",
                    evidence={"xref": _xref_receipt(xref)},
                )
            if rule.spec.ecfr_amendment_date is None:
                raise CompileError(
                    "DEFERRED_ECFR_CLOCKS_MISSING",
                    "deferred transitions require an observed eCFR amendment_date",
                    evidence={"document_number": rule.document_number},
                )
            amendment_date = rule.spec.ecfr_amendment_date
            issue_date = rule.spec.ecfr_issue_date
            if amendment_date != spec.successor_date:
                raise CompileError(
                    "DEFERRED_AMENDMENT_DATE_MISMATCH",
                    "successor snapshot date must equal the observed eCFR amendment_date",
                    evidence={
                        "successor_date": spec.successor_date.isoformat(),
                        "ecfr_amendment_date": amendment_date.isoformat(),
                    },
                )
            if changed_regions[region_key].after is None:
                raise CompileError(
                    "DEFERRED_AFTER_REGION_MISSING",
                    f"deferred changed region is absent from successor: {xref.citation}",
                )
            after_citations = [
                citation
                for region in changed_regions.values()
                if region.after is not None
                for citation in _region_fr_citations(region.after)
                if citation["volume"] == rule.volume
                and rule.start_page <= citation["page"] <= rule.end_page
            ]
        matched_documents.add(rule.document_number)
        evidence.append(
            {
                "witness_motif": motif,
                "document_number": rule.document_number,
                "federal_register_document_citation": rule.citation,
                "federal_register_amendment_page": f"{xref.volume} FR {xref.page}",
                "changed_region": {
                    "kind": xref.region_kind,
                    "region_id": xref.region_id,
                    "citation": xref.citation,
                },
                "substantive_change": True,
                "federal_register_publication_date": rule.publication_date.isoformat(),
                "pending_xref_publication_date": xref.publication_date.isoformat(),
                "ecfr_amendment_date": amendment_date.isoformat(),
                "ecfr_issue_date": issue_date.isoformat() if issue_date else None,
                "ecfr_issue_date_basis": rule.spec.ecfr_issue_date_basis,
                "ecfr_incorporation_date": amendment_date.isoformat(),
                "ecfr_xref_text": xref.text,
                "ecfr_xref_attributes": dict(sorted(xref.attributes.items())),
                "amendatory_instruction": xref.amdinsn,
                "successor_citation_evidence": after_citations,
                "match_basis": [
                    "same FR volume and amendatory page",
                    "same publication/incorporation date",
                    "same AMDPAR instruction ordinal when supplied by eCFR",
                    "same CFR title, part, and changed region",
                    *(
                        [
                            "pending XREF removed from a substantively changed region",
                            *(
                                ["successor CITA/SOURCE corroborates the same FR document"]
                                if after_citations
                                else []
                            ),
                        ]
                        if motif == "deferred_pending_xref_resolved"
                        else []
                    ),
                ],
            }
        )

    directly_mapped = {
        _region_key(
            str(_mapping(row["changed_region"], "changed_region")["kind"]),
            str(_mapping(row["changed_region"], "changed_region")["region_id"]),
        )
        for row in evidence
    }
    for region_key in sorted(set(changed_regions) - directly_mapped, key=_natural_key):
        region = changed_regions[region_key]
        candidates = [
            rule
            for rule in rules
            if rule.correction_of is None
            and rule.document_number in matched_documents
            and _rule_targets_region(rule, spec.title, region.kind, region.identity)
        ]
        if len(candidates) != 1:
            continue
        rule = candidates[0]
        paragraph = _amendatory_paragraph_for_region(rule, region)
        if paragraph is None:
            continue
        target_amendment_date = rule.spec.ecfr_amendment_date or spec.successor_date
        target_issue_date = rule.spec.ecfr_issue_date
        successor_citations = (
            [
                citation
                for citation in _region_fr_citations(region.after)
                if citation["volume"] == rule.volume
                and rule.start_page <= citation["page"] <= rule.end_page
            ]
            if region.after is not None
            else []
        )
        evidence.append(
            {
                "witness_motif": "same_rule_amendatory_target",
                "document_number": rule.document_number,
                "federal_register_document_citation": rule.citation,
                "federal_register_amendment_page": f"{rule.volume} FR {paragraph.page}",
                "changed_region": {
                    "kind": region.kind,
                    "region_id": region.identity,
                    "citation": region.citation,
                },
                "substantive_change": True,
                "federal_register_publication_date": rule.publication_date.isoformat(),
                "pending_xref_publication_date": None,
                "ecfr_amendment_date": target_amendment_date.isoformat(),
                "ecfr_issue_date": target_issue_date.isoformat() if target_issue_date else None,
                "ecfr_issue_date_basis": rule.spec.ecfr_issue_date_basis,
                "ecfr_incorporation_date": target_amendment_date.isoformat(),
                "ecfr_xref_text": None,
                "ecfr_xref_attributes": {},
                "amendatory_instruction": paragraph.ordinal,
                "successor_citation_evidence": successor_citations,
                "match_basis": [
                    "same supplied rule has an independent exact source witness in this transition",
                    "REGTEXT and AMDPAR explicitly target this changed CFR region",
                    "same observed eCFR amendment window",
                ],
            }
        )
    citation_override_documents = {
        str(row["document_number"])
        for row in evidence
        if row["witness_motif"] == "successor_fr_citation_delta"
    }
    for rule in rules:
        if rule.metadata_scope_exact or rule.document_number in citation_override_documents:
            continue
        raise CompileError(
            "RULE_METADATA_SCOPE_OVERRIDE_UNWITNESSED",
            "Federal Register metadata part discrepancy lacks an exact successor citation witness",
            evidence={"document_number": rule.document_number, **_rule_scope_receipt(rule)},
        )
    rules_by_document = {rule.document_number: rule for rule in rules}
    linked_corrections: dict[str, list[str]] = {}
    for correction in rules:
        if correction.correction_of is None:
            continue
        primary = rules_by_document.get(correction.correction_of)
        if primary is None:
            raise CompileError(
                "CORRECTION_PRIMARY_MISSING",
                "a supplied correction lacks its referenced primary rule",
                evidence={
                    "correction": correction.document_number,
                    "primary": correction.correction_of,
                },
            )
        if correction.publication_date > spec.successor_date:
            raise CompileError(
                "CORRECTION_AFTER_SUCCESSOR",
                "a supplied correction was published after the observed successor state",
                evidence={"document_number": correction.document_number},
            )
        if not any(
            target["title"] == spec.title and target["part"] in spec.parts
            for target in correction.targets
        ):
            raise CompileError(
                "CORRECTION_SCOPE_MISMATCH",
                "a supplied correction does not target the episode scope",
                evidence={"document_number": correction.document_number},
            )
        linked_corrections.setdefault(primary.document_number, []).append(
            correction.document_number
        )
        if primary.document_number in matched_documents:
            matched_documents.add(correction.document_number)
    for primary in rules:
        missing = sorted(set(primary.corrections) - set(rules_by_document))
        if missing:
            raise CompileError(
                "CORRECTION_DOCUMENT_MISSING",
                "primary metadata names correction documents absent from the episode",
                evidence={"primary": primary.document_number, "corrections": missing},
            )
    for row in evidence:
        document_number = str(row["document_number"])
        row["scope_validation"] = _rule_scope_receipt(rules_by_document[document_number])
        corrections = sorted(linked_corrections.get(document_number, []))
        if corrections:
            row["correction_documents"] = corrections

    unmatched_rules = [
        rule.document_number for rule in rules if rule.document_number not in matched_documents
    ]
    if unmatched_rules:
        raise CompileError(
            "RULE_CAUSAL_REFERENCE_MISSING",
            "supplied rules lack exact successor eCFR amendment links",
            evidence={"document_numbers": unmatched_rules},
        )
    mapped_regions = {
        _region_key(
            str(_mapping(row["changed_region"], "changed_region")["kind"]),
            str(_mapping(row["changed_region"], "changed_region")["region_id"]),
        )
        for row in evidence
        if row["substantive_change"] is True
    }
    missing_regions = sorted(set(changed_regions) - mapped_regions, key=_natural_key)
    if missing_regions:
        raise CompileError(
            "UNMAPPED_CHANGED_REGION",
            "some changed eCFR regions lack a matching supplied rule",
            evidence={
                "regions": [
                    {
                        "kind": changed_regions[key].kind,
                        "region_id": changed_regions[key].identity,
                        "citation": changed_regions[key].citation,
                    }
                    for key in missing_regions
                ]
            },
        )
    return (
        sorted(
            evidence,
            key=lambda row: (
                str(row["ecfr_incorporation_date"]),
                int(str(row["federal_register_amendment_page"]).split()[-1]),
                str(row["document_number"]),
            ),
        ),
        sorted(
            ignored,
            key=lambda row: (
                str(row["publication_date"]),
                str(row["citation"]),
                str(row["motif"]),
            ),
        ),
    )


def _amendatory_paragraph_for_region(
    rule: _Rule, region: _ChangedRegion, *, page: int | None = None
) -> _AmendatoryParagraph | None:
    identity = _normalized_identity(region.identity)
    matches = [
        paragraph
        for paragraph in rule.amendatory_paragraphs
        if identity in _normalized_identity(paragraph.text)
    ]
    if page is not None:
        on_page = [paragraph for paragraph in matches if paragraph.page == page]
        if on_page:
            matches = on_page
    return min(
        matches,
        key=lambda paragraph: (paragraph.ordinal is None, paragraph.page),
        default=None,
    )


def _raise_xref_mismatch(xref: _Xref, rules: Sequence[_Rule], title: int) -> None:
    target_rules = [
        rule
        for rule in rules
        if _rule_targets_region(rule, title, xref.region_kind, xref.region_id)
    ]
    if not target_rules:
        raise CompileError(
            "CFR_SCOPE_MISMATCH",
            f"no supplied REGTEXT/AMDPAR targets changed region {xref.citation}",
            evidence={"xref": _xref_receipt(xref)},
        )
    same_volume = [rule for rule in target_rules if rule.volume == xref.volume]
    if not same_volume:
        raise CompileError(
            "FR_VOLUME_MISMATCH",
            "eCFR amendment volume disagrees with supplied Federal Register metadata",
            evidence={
                "xref_volume": xref.volume,
                "supplied_volumes": sorted({rule.volume for rule in target_rules}),
            },
        )
    same_date = [rule for rule in same_volume if rule.publication_date == xref.publication_date]
    if not same_date:
        raise CompileError(
            "FR_DATE_MISMATCH",
            "eCFR amendment date disagrees with supplied Federal Register publication date",
            evidence={
                "xref_date": xref.publication_date.isoformat(),
                "supplied_dates": sorted(
                    {rule.publication_date.isoformat() for rule in same_volume}
                ),
            },
        )
    same_page = [rule for rule in same_date if rule.start_page <= xref.page <= rule.end_page]
    if not same_page:
        raise CompileError(
            "FR_PAGE_MISMATCH",
            "eCFR amendment page is outside supplied Federal Register page ranges",
            evidence={
                "xref_page": xref.page,
                "supplied_ranges": [
                    {
                        "document_number": rule.document_number,
                        "start_page": rule.start_page,
                        "end_page": rule.end_page,
                    }
                    for rule in same_date
                ],
            },
        )
    raise CompileError(
        "AMDPAR_REFERENCE_MISMATCH",
        "eCFR AMDINSN/page does not match a supplied AMDPAR instruction",
        evidence={
            "xref": _xref_receipt(xref),
            "supplied_instructions": [
                {
                    "document_number": rule.document_number,
                    "ordinal": paragraph.ordinal,
                    "page": paragraph.page,
                }
                for rule in same_page
                for paragraph in rule.amendatory_paragraphs
            ],
        },
    )


def _xref_matches_rule(xref: _Xref, rule: _Rule) -> bool:
    if (
        xref.volume != rule.volume
        or not rule.start_page <= xref.page <= rule.end_page
        or xref.publication_date != rule.publication_date
    ):
        return False
    if xref.amdinsn is None:
        return any(paragraph.page == xref.page for paragraph in rule.amendatory_paragraphs)
    return any(
        paragraph.ordinal == xref.amdinsn and paragraph.page == xref.page
        for paragraph in rule.amendatory_paragraphs
    )


def _rule_targets_region(rule: _Rule, title: int, region_kind: str, region_id: str) -> bool:
    normalized_id = _normalized_identity(region_id)
    return any(
        target["title"] == title
        and target.get("region_kind") == region_kind
        and _normalized_identity(str(target.get("region_id", ""))) == normalized_id
        for target in rule.targets
    )


def _extract_rule_targets(root: Any) -> list[dict[str, Any]]:
    rows: dict[tuple[int, str, str, str, str | None], dict[str, Any]] = {}
    for regtext in root.iter():
        if _local_name(str(regtext.tag)) != "REGTEXT":
            continue
        try:
            title = int(regtext.attrib["TITLE"])
            part = str(regtext.attrib["PART"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CompileError("RULE_TARGET_MISMATCH", "REGTEXT lacks TITLE/PART") from exc
        references: set[tuple[str, str, str | None]] = set()
        all_instruction_text: list[str] = []
        for elem in regtext.iter():
            name = _local_name(str(elem.tag))
            if name not in {"AMDPAR", "SECTNO"}:
                continue
            text = _text(elem)
            all_instruction_text.append(text)
            for match in _SECTION_RE.finditer(text):
                references.add(("section", match.group(1), match.group(2)))
            appendix = _APPENDIX_RE.search(text)
            if appendix and appendix.group("part") == part:
                references.add(
                    (
                        "appendix",
                        f"Appendix {appendix.group('label')} to Part {part}",
                        None,
                    )
                )
        instruction_text = "\n".join(all_instruction_text).lower()
        if "authority citation" in instruction_text:
            references.add(("authority", f"part:{part}:authority", None))
        if not references:
            references.add(("part", part, None))
        for region_kind, region_id, paragraph in references:
            key = (title, part, region_kind, region_id, paragraph)
            row: dict[str, Any] = {
                "title": title,
                "part": part,
                "region_kind": region_kind,
                "region_id": region_id,
                "paragraph": paragraph,
            }
            if region_kind == "section":
                row["section"] = region_id
            elif region_kind == "appendix":
                row["appendix"] = region_id
            rows[key] = row
    return [rows[key] for key in sorted(rows, key=lambda value: tuple(str(item) for item in value))]


def _extract_amendatory_paragraphs(root: Any, start_page: int) -> list[_AmendatoryParagraph]:
    rows: list[_AmendatoryParagraph] = []
    current_page = start_page
    for elem in root.iter():
        name = _local_name(str(elem.tag))
        if name == "PRTPAGE":
            try:
                current_page = int(elem.attrib["P"])
            except (KeyError, TypeError, ValueError) as exc:
                raise CompileError("METADATA_INVALID", "PRTPAGE lacks a numeric page") from exc
        elif name == "AMDPAR":
            text = _text(elem)
            match = _AMDPAR_ORDINAL_RE.match(text)
            rows.append(
                _AmendatoryParagraph(
                    ordinal=int(match.group(1)) if match else None,
                    page=current_page,
                    text=text,
                )
            )
    return rows


def _operation_shapes(paragraphs: Sequence[_AmendatoryParagraph]) -> set[str]:
    shapes: set[str] = set()
    text = "\n".join(paragraph.text.lower() for paragraph in paragraphs)
    patterns = {
        "add": r"\b(add|adding|insert|inserting)\b",
        "remove": r"\b(remove|removing|delete|deleting)\b",
        "revise": r"\b(revise|revising|revision)\b",
        "redesignate": r"\bredesignat(?:e|ing)\b",
        "reserve": r"\breserv(?:e|ing)\b",
        "republish": r"\brepublish(?:ing)?\b",
        "transfer": r"\btransfer(?:ring)?\b",
        "correct": r"\bcorrect(?:ing|ion)?\b",
    }
    for shape, pattern in patterns.items():
        if re.search(pattern, text):
            shapes.add(shape)
    if "authority citation" in text:
        shapes.add("authority_citation")
    if "table" in text:
        shapes.add("table_edit")
    if "heading" in text:
        shapes.add("heading_edit")
    return shapes or {"unclassified_amendatory_instruction"}


def _amendment_xref_transitions(
    before_root: Any, after_root: Any
) -> tuple[list[_Xref], list[_Xref]]:
    """Read each snapshot once, preserving witness order and repeated references."""
    before = _extract_xrefs(before_root)
    after = _extract_xrefs(after_root)
    before_keys = {_xref_key(value) for value in before}
    after_keys = {_xref_key(value) for value in after}
    return (
        [value for value in after if _xref_key(value) not in before_keys],
        [value for value in before if _xref_key(value) not in after_keys],
    )


def _region_fr_citations(region: Any) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    for elem in region.iter():
        tag = _local_name(str(elem.tag)).upper()
        if tag not in {"CITA", "SOURCE"}:
            continue
        text = _text(elem)
        dated_spans: list[tuple[int, int]] = []
        for match in _FR_CITATION_ENTRY_RE.finditer(text):
            publication_date = _parse_fr_date(
                match.group("month"), match.group("day"), match.group("year")
            )
            dated_spans.append(match.span())
            for raw_page in match.group("pages").split(","):
                citations.append(
                    {
                        "tag": tag,
                        "volume": int(match.group("volume")),
                        "page": int(raw_page.strip()),
                        "publication_date": publication_date.isoformat(),
                        "text": text,
                    }
                )
        for match in _FR_CITATION_RE.finditer(text):
            if any(start <= match.start() < end for start, end in dated_spans):
                continue
            citations.append(
                {
                    "tag": tag,
                    "volume": int(match.group("volume")),
                    "page": int(match.group("page")),
                    "publication_date": None,
                    "text": text,
                }
            )
    return citations


def _new_region_fr_citations(region: _ChangedRegion) -> list[dict[str, Any]]:
    """Return successor-only FR citation identities, ignoring CITA prose rewrites."""
    before = _region_fr_citations(region.before) if region.before is not None else []
    after = _region_fr_citations(region.after) if region.after is not None else []
    before_keys = {_fr_citation_key(citation) for citation in before}
    return [citation for citation in after if _fr_citation_key(citation) not in before_keys]


def _fr_citation_key(citation: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        str(citation["tag"]),
        int(citation["volume"]),
        int(citation["page"]),
        citation.get("publication_date"),
    )


def _extract_xrefs(root: Any) -> list[_Xref]:
    rows: list[_Xref] = []
    title = _ecfr_title(root)
    part = str(root.attrib.get("N", ""))

    def visit(
        elem: Any,
        context: tuple[str, str, str] | None,
        *,
        parent_is_root: bool,
    ) -> None:
        current = context
        descriptor = _region_descriptor(elem, parent_is_root, title, part)
        if descriptor is not None:
            current = descriptor
        if _local_name(str(elem.tag)) == "XREF":
            text = _text(elem)
            match = _XREF_RE.search(text)
            if match:
                if current is None:
                    raise CompileError(
                        "REFERENCE_OUTSIDE_CHANGED_REGION",
                        "eCFR amendment XREF is not nested in an identified changed region",
                    )
                parsed_date = _parse_fr_date(
                    match.group("month"), match.group("day"), match.group("year")
                )
                raw_instruction = elem.attrib.get("AMDINSN")
                rows.append(
                    _Xref(
                        region_kind=current[0],
                        region_id=current[1],
                        citation=current[2],
                        volume=int(match.group("volume")),
                        page=int(match.group("page")),
                        publication_date=parsed_date,
                        amdinsn=int(raw_instruction) if raw_instruction else None,
                        text=text,
                        attributes=dict(elem.attrib),
                    )
                )
        for child in list(elem):
            visit(child, current, parent_is_root=elem is root)

    visit(root, None, parent_is_root=False)
    return rows


def _section_map(root: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for elem in root.iter():
        if elem.attrib.get("TYPE") != "SECTION" or not elem.attrib.get("N"):
            continue
        key = str(elem.attrib["N"])
        if key in result:
            raise CompileError("ECFR_DUPLICATE_SECTION", f"duplicate eCFR section identity: {key}")
        result[key] = elem
    return result


def _region_map(root: Any, title: int) -> dict[str, _ChangedRegion]:
    result: dict[str, _ChangedRegion] = {}
    part = str(root.attrib.get("N", ""))

    def visit(elem: Any, *, parent_is_root: bool) -> None:
        if is_projected_editorial_node(elem):
            return
        descriptor = _region_descriptor(elem, parent_is_root, title, part)
        if descriptor is not None:
            kind, identity, citation = descriptor
            key = _region_key(kind, identity)
            if key in result:
                raise CompileError("ECFR_DUPLICATE_REGION", f"duplicate eCFR region: {citation}")
            result[key] = _ChangedRegion(
                kind=kind,
                identity=identity,
                citation=citation,
                before=None,
                after=elem,
            )
            return
        for child in list(elem):
            visit(child, parent_is_root=elem is root)

    visit(root, parent_is_root=False)
    return result


def _outside_region_tokens(root: Any, title: int, common_region_keys: set[str]) -> tuple[Any, ...]:
    tokens: list[Any] = []
    part = str(root.attrib.get("N", ""))

    def visit(elem: Any, *, parent_is_root: bool) -> None:
        descriptor = _region_descriptor(elem, parent_is_root, title, part)
        if descriptor is not None:
            key = _region_key(descriptor[0], descriptor[1])
            if key in common_region_keys:
                tokens.append(("region", key))
            return
        tokens.append(
            (
                "start",
                _local_name(str(elem.tag)),
                tuple(sorted((str(key), str(value)) for key, value in elem.attrib.items())),
                _normalized_text(elem.text),
            )
        )
        for child in list(elem):
            visit(child, parent_is_root=elem is root)
            if _normalized_text(child.tail):
                tokens.append(("tail", _normalized_text(child.tail)))
        tokens.append(("end", _local_name(str(elem.tag))))

    visit(root, parent_is_root=False)
    return tuple(tokens)


def _region_descriptor(
    elem: Any, parent_is_root: bool, title: int, part: str
) -> tuple[str, str, str] | None:
    node_type = elem.attrib.get("TYPE")
    node_number = elem.attrib.get("N")
    if node_type == "SECTION" and node_number:
        identity = str(node_number)
        return "section", identity, f"{title} CFR {identity}"
    if node_type == "APPENDIX" and node_number:
        identity = str(node_number)
        return "appendix", identity, f"{title} CFR {identity}"
    if parent_is_root:
        local = _local_name(str(elem.tag)).upper()
        if local in {"AUTH", "HEAD", "SOURCE"}:
            kind = {"AUTH": "authority", "HEAD": "heading", "SOURCE": "source_note"}[local]
            identity = f"part:{part}:{kind}"
            label = {
                "authority": "authority citation",
                "heading": "heading",
                "source_note": "source note",
            }[kind]
            return kind, identity, f"{title} CFR Part {part} {label}"
    return None


def _region_key(kind: str, identity: str) -> str:
    return f"{kind}:{_normalized_identity(identity)}"


def _normalized_identity(value: str) -> str:
    return " ".join(value.casefold().split())


def _ecfr_title(root: Any) -> int:
    markers = " ".join(str(root.attrib.get(name, "")) for name in ("TITLE", "hierarchy_metadata"))
    match = re.search(r"(?:title[- ]|\"title\"\s*:\s*\")([0-9]+)", markers, re.IGNORECASE)
    if root.attrib.get("TYPE") == "TITLE" and str(root.attrib.get("N", "")).isdigit():
        return int(root.attrib["N"])
    if match:
        return int(match.group(1))
    raise CompileError("SCOPE_MISMATCH", "cannot derive CFR title from eCFR XML")


def _rule_scope_receipt(rule: _Rule) -> dict[str, Any]:
    overridden = not rule.metadata_scope_exact
    return {
        "metadata_targets": [
            {"title": title, "part": part} for title, part in rule.metadata_targets
        ],
        "xml_targets": list(rule.targets),
        "metadata_scope_exact": rule.metadata_scope_exact,
        "metadata_scope_discrepancy_overridden_by_exact_rule_xml": overridden,
        "override_basis": (
            "exact REGTEXT/AMDPAR region plus successor-only FR citation delta"
            if overridden
            else None
        ),
    }


def _acquisition_manifest(
    spec: EpisodeSpec,
    base: _Artifact,
    target: _Artifact,
    rules: Sequence[_Rule],
) -> dict[str, Any]:
    artifacts = [
        {"role": "base_ecfr", **_private_artifact_receipt(base)},
        {"role": "successor_ecfr", **_private_artifact_receipt(target)},
    ]
    for rule in rules:
        artifacts.extend(
            [
                {
                    "role": "federal_register_rule_xml",
                    "document_number": rule.document_number,
                    **_private_artifact_receipt(rule.xml),
                },
                {
                    "role": "federal_register_document_metadata",
                    "document_number": rule.document_number,
                    **_private_artifact_receipt(rule.metadata),
                },
            ]
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "manifest_kind": "regpatch_official_source_acquisition",
        "episode_id": spec.episode_id,
        "network_bytes_acquired_by_compiler": 0,
        "status": "retained_local_bytes_rehashed",
        "requests": [
            {
                "method": "GET",
                "url": artifact["source_url"],
                "role": artifact["role"],
                "document_number": artifact.get("document_number"),
                "result": "satisfied_by_retained_verified_bytes",
            }
            for artifact in artifacts
        ],
        "artifacts": artifacts,
        "totals": {
            "artifacts": len(artifacts),
            "bytes": sum(int(artifact["byte_count"]) for artifact in artifacts),
        },
    }


def _parse_fr_date(month: str, day: str, year: str) -> date:
    normalized_month = month.rstrip(".")
    if normalized_month.casefold() == "sept":
        normalized_month = "Sep"
    value = f"{normalized_month} {day} {year}"
    for pattern in ("%b %d %Y", "%B %d %Y"):
        try:
            return datetime.strptime(value, pattern).replace(tzinfo=UTC).date()
        except ValueError:
            continue
    raise CompileError("ECFR_XREF_INVALID", f"cannot parse eCFR amendment date: {value}")


def _xref_key(xref: _Xref) -> tuple[Any, ...]:
    return (
        xref.region_kind,
        xref.region_id,
        xref.volume,
        xref.page,
        xref.publication_date,
        xref.amdinsn,
        xref.text,
        tuple(sorted(xref.attributes.items())),
    )


def _xref_receipt(xref: _Xref) -> dict[str, Any]:
    return {
        "region_kind": xref.region_kind,
        "region_id": xref.region_id,
        "citation": xref.citation,
        "volume": xref.volume,
        "page": xref.page,
        "publication_date": xref.publication_date.isoformat(),
        "amdinsn": xref.amdinsn,
        "text": xref.text,
    }
