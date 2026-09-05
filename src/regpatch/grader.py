"""Deterministic structural scorer for observed eCFR patch episodes.

The scorer intentionally knows nothing about how a candidate applied Federal
Register instructions.  It compares the candidate's XML with the evaluator-only
historical successor while using the historical predecessor to distinguish the
small changed surface from the much larger preservation surface.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from collections.abc import Hashable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

from src.regpatch.projection import canonical_table_signature, is_projected_editorial_node

SCORE_VERSION = "psephos.regpatch.score.v0"
PROJECTION_VERSION = "psephos.regpatch.ecfr-substantive.v3"
MAX_XML_BYTES = 64 * 1024 * 1024
MAX_XML_NODES = 1_000_000
MAX_XML_DEPTH = 512

_COMPONENT_WEIGHTS = {
    "xml_validity": 0.05,
    "node_citation_identity": 0.15,
    "changed_regions": 0.30,
    "unaffected_preservation": 0.25,
    "structure_integrity": 0.15,
    "provenance": 0.05,
    "determinism": 0.05,
}
_IDENTITY_ATTRIBUTES = ("N", "ID", "REFID", "PART", "SECTION", "SECTNO")
_TABLE_TAGS = frozenset(
    {"TABLE", "TGROUP", "THEAD", "TBODY", "TFOOT", "ROW", "TR", "ENTRY", "TD", "TH"}
)
_AUTHORITY_TAGS = frozenset({"AUTH", "AUTHORITY"})
_CITATION_TAGS = frozenset({"CFR", "CITA", "SECTNO", "FRDOC", "XREF"})
_SECTION_REFERENCE = re.compile(
    r"(?:§{1,2}|sections?)\s*[\u2009 ]*(\d+(?:\.\d+)?(?:\([A-Za-z0-9]+\))*)",
    re.IGNORECASE,
)
_CFR_REFERENCE = re.compile(r"\b(\d+)\s+CFR\s+(?:part\s+)?(\d+(?:\.\d+)?)", re.IGNORECASE)
_LEADING_NUMBER = re.compile(r"^\s*(?:§\s*)?(\d+(?:\.\d+)?(?:\([A-Za-z0-9]+\))*)")
_PARAGRAPH_NUMBER = re.compile(r"^\s*(\([A-Za-z0-9]+\)(?:\([A-Za-z0-9]+\))*)")
_WORD_OR_PUNCTUATION = re.compile(r"\w+|[^\w\s]", re.UNICODE)
type _Segment = tuple[str, tuple[tuple[str, str], ...], int]
type _NodeKey = tuple[_Segment, ...]
type _Shallow = tuple[str, tuple[tuple[str, str], ...], str, tuple[str, ...]]
type _FactToken = tuple[_NodeKey, _Shallow]
type _ChildIdentity = tuple[str, tuple[tuple[str, str], ...]]


@dataclass(frozen=True)
class EpisodeScore:
    """Stable, JSON-ready result for one episode."""

    episode_id: str
    components: dict[str, float]
    penalty_factor: float
    penalties: dict[str, dict[str, float | int]]
    overall: float
    counts: dict[str, int]
    metrics: dict[str, object]
    errors: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "score_version": SCORE_VERSION,
            "episode_id": self.episode_id,
            "components": {name: round(self.components[name], 6) for name in _COMPONENT_WEIGHTS},
            "penalty_factor": round(self.penalty_factor, 6),
            "penalties": self.penalties,
            "overall": round(self.overall, 4),
            "counts": self.counts,
            "metrics": self.metrics,
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class _Fact:
    key: _NodeKey
    parent: _NodeKey | None
    tag: str
    attrs: tuple[tuple[str, str], ...]
    text: str
    tails: tuple[str, ...]
    ancestors: tuple[str, ...]
    ordered_children: tuple[_ChildIdentity, ...]
    subtree_digest: str

    @property
    def shallow(self) -> _Shallow:
        return (self.tag, self.attrs, self.text, self.tails)

    @property
    def token(self) -> _FactToken:
        return (self.key, self.shallow)


@dataclass(frozen=True)
class _Document:
    facts: tuple[_Fact, ...]
    canonical_digest: str
    excluded_editorial_witnesses: int

    @property
    def fact_counts(self) -> Counter[_FactToken]:
        return Counter(fact.token for fact in self.facts)

    @property
    def key_counts(self) -> Counter[_NodeKey]:
        return Counter(fact.key for fact in self.facts)


@dataclass(frozen=True)
class _RunScore:
    components: dict[str, float]
    metrics: dict[str, object]
    penalties: dict[str, tuple[int, float]]
    counts: dict[str, int]
    document: _Document | None
    errors: tuple[str, ...]


class _XmlFailure(ValueError):
    """A bounded, user-facing XML validation failure."""


def score_episode(
    manifest: Mapping[str, object] | str | Path,
    before_path: str | Path,
    target_path: str | Path,
    candidate_path: str | Path,
    *,
    second_candidate_path: str | Path | None = None,
    run_ok: bool = True,
    second_run_ok: bool = True,
    provenance_ok: bool | None = None,
) -> EpisodeScore:
    """Score one candidate, requiring evaluator evidence for provenance and determinism.

    ``target_path`` remains evaluator-only.  ``provenance_ok`` must be established by
    the runner from its receipt; candidate-authored claims are not trusted.  A second
    output is required for the determinism component and penalty.
    """

    manifest_value = _load_manifest(manifest)
    episode_id = manifest_value.get("episode_id")
    if not isinstance(episode_id, str) or not episode_id:
        raise ValueError("episode manifest requires a non-empty episode_id")

    before = _trusted_document(before_path, "before")
    target = _trusted_document(target_path, "target")
    first = _score_candidate(
        candidate_path,
        before,
        target,
        manifest_value,
        run_ok=run_ok,
    )
    runs = [first]
    if second_candidate_path is not None:
        runs.append(
            _score_candidate(
                second_candidate_path,
                before,
                target,
                manifest_value,
                run_ok=second_run_ok,
            )
        )

    components = {
        name: min(run.components[name] for run in runs)
        for name in (
            "xml_validity",
            "node_citation_identity",
            "changed_regions",
            "unaffected_preservation",
            "structure_integrity",
        )
    }
    provenance = provenance_ok is True
    deterministic = bool(
        second_candidate_path is not None
        and first.document is not None
        and runs[1].document is not None
        and first.document.canonical_digest == runs[1].document.canonical_digest
        and run_ok
        and second_run_ok
    )
    components["provenance"] = float(provenance)
    components["determinism"] = float(deterministic)

    penalty_names = tuple(first.penalties)
    penalties: dict[str, dict[str, float | int]] = {}
    for name in penalty_names:
        count = max(run.penalties[name][0] for run in runs)
        factor = min(run.penalties[name][1] for run in runs)
        penalties[name] = {"count": count, "factor": round(factor, 6)}
    penalties["missing_provenance"] = {
        "count": int(not provenance),
        "factor": 1.0 if provenance else 0.75,
    }
    penalties["nondeterminism"] = {
        "count": int(not deterministic),
        "factor": 1.0 if deterministic else 0.5,
    }

    penalty_factor = math.prod(float(value["factor"]) for value in penalties.values())
    base = sum(components[name] * weight for name, weight in _COMPONENT_WEIGHTS.items())
    overall = 100.0 * base * penalty_factor
    representative = min(
        runs,
        key=lambda run: sum(
            run.components[name] * _COMPONENT_WEIGHTS[name] for name in run.components
        ),
    )
    metrics = dict(representative.metrics)
    metrics["determinism"] = {
        "checked": second_candidate_path is not None,
        "semantic_match": deterministic,
    }
    metrics["provenance"] = {"evaluator_verified": provenance}
    errors = tuple(error for run in runs for error in run.errors)
    if not provenance:
        errors += ("missing evaluator-verified provenance receipt",)
    if not deterministic:
        errors += ("two successful semantically identical runs were not observed",)
    return EpisodeScore(
        episode_id=episode_id,
        components=components,
        penalty_factor=penalty_factor,
        penalties=penalties,
        overall=overall,
        counts=representative.counts,
        metrics=metrics,
        errors=errors,
    )


def _load_manifest(manifest: Mapping[str, object] | str | Path) -> Mapping[str, object]:
    if isinstance(manifest, Mapping):
        return manifest
    path = Path(manifest)
    if path.is_symlink() or not path.is_file():
        raise ValueError("episode manifest must be a regular file")
    try:
        value: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid episode manifest: {type(exc).__name__}") from exc
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("episode manifest must be a JSON object")
    return cast(dict[str, object], value)


def _trusted_document(path: str | Path, label: str) -> _Document:
    try:
        return _parse_document(path)
    except _XmlFailure as exc:
        raise ValueError(f"invalid trusted {label} XML: {exc}") from exc


def _score_candidate(
    path: str | Path,
    before: _Document,
    target: _Document,
    manifest: Mapping[str, object],
    *,
    run_ok: bool,
) -> _RunScore:
    if not run_ok:
        return _failed_run("candidate runner did not complete successfully", runner_failure=True)
    try:
        candidate = _parse_document(path)
    except _XmlFailure as exc:
        return _failed_run(str(exc), malformed=True)

    before_counts = before.fact_counts
    target_counts = target.fact_counts
    candidate_counts = candidate.fact_counts
    changed_target = target_counts - before_counts
    removed_before = before_counts - target_counts
    unaffected = before_counts & target_counts

    node_prf = _counter_prf(target.key_counts, candidate.key_counts)
    citation_prf = _counter_prf(_citation_counts(target), _citation_counts(candidate))
    identity = (node_prf["f1"] + citation_prf["f1"]) / 2.0

    target_agreement, exact_rate = _changed_target_agreement(changed_target, target, candidate)
    stale_remaining = (removed_before & candidate_counts).total()
    stale_clearance = 1.0 - stale_remaining / removed_before.total() if removed_before else 1.0
    changed_score = math.sqrt(target_agreement * stale_clearance)

    preserved = (unaffected & candidate_counts).total()
    unaffected_score = preserved / unaffected.total() if unaffected else 1.0
    integrity_metrics = _integrity_metrics(target, candidate)
    integrity = sum(integrity_metrics.values()) / len(integrity_metrics)

    candidate_new = candidate_counts - before_counts
    fabricated = (candidate_new - changed_target).total()
    fabrication_denominator = max(changed_target.total(), candidate_new.total(), 1)
    fabrication_rate = min(1.0, fabricated / fabrication_denominator)
    fabrication_factor = 1.0 - 0.75 * fabrication_rate
    dropped = (unaffected - candidate_counts).total()
    dropped_rate = dropped / unaffected.total() if unaffected else 0.0
    dropped_factor = 1.0 - dropped_rate
    duplicates = _duplicate_excess(before, target, candidate)
    duplicate_denominator = max(changed_target.total(), 1)
    duplicate_factor = 1.0 / (1.0 + duplicates / duplicate_denominator)

    selectors = _selector_metrics(manifest.get("changed_regions"), target, changed_target)
    counts = {
        "before_nodes": len(before.facts),
        "target_nodes": len(target.facts),
        "candidate_nodes": len(candidate.facts),
        "changed_target_nodes": changed_target.total(),
        "removed_before_nodes": removed_before.total(),
        "unaffected_target_nodes": unaffected.total(),
        "before_projected_editorial_witnesses": before.excluded_editorial_witnesses,
        "target_projected_editorial_witnesses": target.excluded_editorial_witnesses,
        "candidate_projected_editorial_witnesses": candidate.excluded_editorial_witnesses,
    }
    metrics: dict[str, object] = {
        "canonical_projection": {
            "version": PROJECTION_VERSION,
            "excluded_node_kinds": ["ecfr_editorial_amendment_xref", "ecfr_cita"],
            "table_normalization": "ordered_cells_spans_and_caption_text",
        },
        "node_identity": _rounded_prf(node_prf),
        "citation_identity": _rounded_prf(citation_prf),
        "changed_regions": {
            "target_agreement": round(target_agreement, 6),
            "exact_rate": round(exact_rate, 6),
            "stale_clearance": round(stale_clearance, 6),
            **selectors,
        },
        "unaffected_preservation": {
            "preserved": preserved,
            "expected": unaffected.total(),
            "recall": round(unaffected_score, 6),
        },
        "structure_integrity": {name: round(value, 6) for name, value in integrity_metrics.items()},
    }
    return _RunScore(
        components={
            "xml_validity": 1.0,
            "node_citation_identity": identity,
            "changed_regions": changed_score,
            "unaffected_preservation": unaffected_score,
            "structure_integrity": integrity,
        },
        metrics=metrics,
        penalties={
            "malformed_xml": (0, 1.0),
            "runner_failure": (0, 1.0),
            "fabricated_content": (fabricated, fabrication_factor),
            "dropped_unaffected": (dropped, dropped_factor),
            "duplicate_nodes": (duplicates, duplicate_factor),
        },
        counts=counts,
        document=candidate,
        errors=(),
    )


def _failed_run(
    error: str,
    *,
    malformed: bool = False,
    runner_failure: bool = False,
) -> _RunScore:
    zeros = {
        "xml_validity": 0.0,
        "node_citation_identity": 0.0,
        "changed_regions": 0.0,
        "unaffected_preservation": 0.0,
        "structure_integrity": 0.0,
    }
    return _RunScore(
        components=zeros,
        metrics={"failure": error},
        penalties={
            "malformed_xml": (int(malformed), 0.0 if malformed else 1.0),
            "runner_failure": (int(runner_failure), 0.0 if runner_failure else 1.0),
            "fabricated_content": (0, 1.0),
            "dropped_unaffected": (0, 1.0),
            "duplicate_nodes": (0, 1.0),
        },
        counts={
            "before_nodes": 0,
            "target_nodes": 0,
            "candidate_nodes": 0,
            "changed_target_nodes": 0,
            "removed_before_nodes": 0,
            "unaffected_target_nodes": 0,
            "before_projected_editorial_witnesses": 0,
            "target_projected_editorial_witnesses": 0,
            "candidate_projected_editorial_witnesses": 0,
        },
        document=None,
        errors=(error,),
    )


def _parse_document(path_value: str | Path) -> _Document:
    path = Path(path_value)
    if path.is_symlink() or not path.is_file():
        raise _XmlFailure("XML output must be a regular file")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise _XmlFailure(f"cannot stat XML output ({type(exc).__name__})") from exc
    if size > MAX_XML_BYTES:
        raise _XmlFailure(f"XML output exceeds {MAX_XML_BYTES} byte limit")
    try:
        tree = SafeET.parse(str(path), forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (ET.ParseError, DefusedXmlException, OSError, RecursionError, ValueError) as exc:
        raise _XmlFailure(f"unsafe or malformed XML ({type(exc).__name__})") from exc
    root = tree.getroot()
    if root is None or not isinstance(root.tag, str):
        raise _XmlFailure("XML document has no element root")
    _validate_shape(root)
    facts: list[_Fact] = []
    projection_count = [0]
    root_key = ((_local_name(root.tag), _identity_attrs(root), 0),)
    root_digest = _walk_document(root, root_key, None, (), facts, projection_count)
    return _Document(tuple(facts), root_digest, projection_count[0])


def _validate_shape(root: ET.Element) -> None:
    seen = 0
    stack: list[tuple[ET.Element, int]] = [(root, 1)]
    while stack:
        element, depth = stack.pop()
        seen += 1
        if seen > MAX_XML_NODES:
            raise _XmlFailure(f"XML output exceeds {MAX_XML_NODES} node limit")
        if depth > MAX_XML_DEPTH:
            raise _XmlFailure(f"XML output exceeds {MAX_XML_DEPTH} depth limit")
        children = [child for child in element if isinstance(child.tag, str)]
        stack.extend((child, depth + 1) for child in reversed(children))


def _walk_document(
    element: ET.Element,
    key: _NodeKey,
    parent: _NodeKey | None,
    ancestors: tuple[str, ...],
    facts: list[_Fact],
    projection_count: list[int],
) -> str:
    tag = _local_name(element.tag)
    if tag.upper() == "TABLE":
        text = json.dumps(
            canonical_table_signature(element),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        table_shallow: _Shallow = (tag, (), text, ())
        digest = hashlib.sha256(
            json.dumps((table_shallow, ()), ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        facts.append(
            _Fact(
                key=key,
                parent=parent,
                tag=tag,
                attrs=(),
                text=text,
                tails=(),
                ancestors=ancestors,
                ordered_children=(),
                subtree_digest=digest,
            )
        )
        return digest
    attrs = tuple(sorted((str(name), str(value)) for name, value in element.attrib.items()))
    text = _normalize_text(element.text)
    tails: list[str] = []
    sibling_counts: dict[str, int] = defaultdict(int)
    child_digests: list[tuple[str, str]] = []
    ordered_children: list[_ChildIdentity] = []
    for child in element:
        if not isinstance(child.tag, str):
            continue
        if is_projected_editorial_node(child):
            projection_count[0] += 1
            child_tail = _normalize_text(child.tail)
            if child_tail:
                tails.append(child_tail)
            continue
        child_tag = _local_name(child.tag)
        sibling_counts[child_tag] += 1
        anchors = _identity_attrs(child)
        ordered_children.append((child_tag, anchors))
        ordinal = 0 if anchors else sibling_counts[child_tag]
        child_key = key + ((child_tag, anchors, ordinal),)
        child_digest = _walk_document(
            child, child_key, key, ancestors + (tag,), facts, projection_count
        )
        child_tail = _normalize_text(child.tail)
        if child_tail:
            tails.append(child_tail)
        child_digests.append((child_digest, child_tail))
    shallow: _Shallow = (tag, attrs, text, tuple(tails))
    payload = json.dumps(
        (shallow, child_digests), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    facts.append(
        _Fact(
            key=key,
            parent=parent,
            tag=tag,
            attrs=attrs,
            text=text,
            tails=tuple(tails),
            ancestors=ancestors,
            ordered_children=tuple(ordered_children),
            subtree_digest=digest,
        )
    )
    return digest


def _identity_attrs(element: ET.Element) -> tuple[tuple[str, str], ...]:
    values = tuple(
        (name, str(element.attrib[name])) for name in _IDENTITY_ATTRIBUTES if name in element.attrib
    )
    if values and "TYPE" in element.attrib:
        return (("TYPE", str(element.attrib["TYPE"])), *values)
    return values


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _normalize_text(value: str | None) -> str:
    return " ".join(value.split()) if value else ""


def _counter_prf[CounterKey: Hashable](
    expected: Counter[CounterKey], actual: Counter[CounterKey]
) -> dict[str, float]:
    overlap = (expected & actual).total()
    expected_total = expected.total()
    actual_total = actual.total()
    precision = overlap / actual_total if actual_total else float(expected_total == 0)
    recall = overlap / expected_total if expected_total else float(actual_total == 0)
    f1 = 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def _rounded_prf(values: Mapping[str, float]) -> dict[str, float]:
    return {name: round(value, 6) for name, value in values.items()}


def _changed_target_agreement(
    changed: Counter[_FactToken], target: _Document, candidate: _Document
) -> tuple[float, float]:
    if not changed:
        return 1.0, 1.0
    target_lookup = {fact.token: fact for fact in target.facts}
    pools: dict[_NodeKey, list[_Fact]] = defaultdict(list)
    for fact in candidate.facts:
        pools[fact.key].append(fact)
    scores: list[float] = []
    exact = 0
    for token, count in sorted(changed.items(), key=lambda item: repr(item[0])):
        truth = target_lookup[token]
        for _ in range(count):
            choices = pools.get(truth.key, [])
            if not choices:
                scores.append(0.0)
                continue
            ranked = [
                (_fact_similarity(truth, value), index) for index, value in enumerate(choices)
            ]
            best, index = max(ranked)
            chosen = choices.pop(index)
            scores.append(best)
            exact += int(chosen.shallow == truth.shallow)
    return sum(scores) / len(scores), exact / changed.total()


def _fact_similarity(expected: _Fact, actual: _Fact) -> float:
    tag = float(expected.tag == actual.tag)
    attrs = _counter_prf(Counter(expected.attrs), Counter(actual.attrs))["f1"]
    expected_text = _WORD_OR_PUNCTUATION.findall(" ".join((expected.text, *expected.tails)))
    actual_text = _WORD_OR_PUNCTUATION.findall(" ".join((actual.text, *actual.tails)))
    text = _counter_prf(Counter(expected_text), Counter(actual_text))["f1"]
    return 0.2 * tag + 0.3 * attrs + 0.5 * text


def _citation_counts(document: _Document) -> Counter[str]:
    values: Counter[str] = Counter()
    for fact in document.facts:
        attrs = dict(fact.attrs)
        if "N" in attrs and (attrs.get("TYPE") or fact.tag.startswith("DIV")):
            values[f"node:{fact.tag}:{attrs['N']}"] += 1
        hierarchy = attrs.get("hierarchy_metadata", "")
        citation_match = re.search(r'"citation"\s*:\s*"([^"]+)"', hierarchy)
        if citation_match:
            values[f"hierarchy:{citation_match.group(1)}"] += 1
        text = " ".join((fact.text, *fact.tails))
        if fact.tag in _CITATION_TAGS and text:
            values[f"{fact.tag}:{text}"] += 1
        for match in _SECTION_REFERENCE.finditer(text):
            values[f"section:{match.group(1)}"] += 1
        for match in _CFR_REFERENCE.finditer(text):
            values[f"cfr:{match.group(1)}:{match.group(2)}"] += 1
    return values


def _integrity_metrics(target: _Document, candidate: _Document) -> dict[str, float]:
    expected_edges: Counter[object] = Counter(
        (fact.parent, fact.key) for fact in target.facts if fact.parent is not None
    )
    candidate_edges: Counter[object] = Counter(
        (fact.parent, fact.key) for fact in candidate.facts if fact.parent is not None
    )
    expected_order: Counter[object] = Counter(
        (fact.key, fact.ordered_children) for fact in target.facts if fact.ordered_children
    )
    candidate_order: Counter[object] = Counter(
        (fact.key, fact.ordered_children) for fact in candidate.facts if fact.ordered_children
    )
    return {
        "numbering": _counter_prf(_numbering_counts(target), _numbering_counts(candidate))["f1"],
        "hierarchy": _counter_prf(expected_edges, candidate_edges)["f1"],
        "sibling_order": _counter_prf(expected_order, candidate_order)["f1"],
        "cross_references": _counter_prf(
            _cross_reference_counts(target), _cross_reference_counts(candidate)
        )["f1"],
        "tables": _counter_prf(
            _category_counts(target, _TABLE_TAGS), _category_counts(candidate, _TABLE_TAGS)
        )["f1"],
        "authority_citations": _counter_prf(
            _category_counts(target, _AUTHORITY_TAGS),
            _category_counts(candidate, _AUTHORITY_TAGS),
        )["f1"],
    }


def _numbering_counts(document: _Document) -> Counter[str]:
    values: Counter[str] = Counter()
    for fact in document.facts:
        attrs = dict(fact.attrs)
        if "N" in attrs:
            values[f"{fact.tag}:N={attrs['N']}"] += 1
        if fact.tag in {"HEAD", "SECTNO"}:
            match = _LEADING_NUMBER.match(fact.text)
            if match:
                values[f"heading:{match.group(1)}"] += 1
        if fact.tag == "P":
            match = _PARAGRAPH_NUMBER.match(fact.text)
            if match:
                values[f"paragraph:{match.group(1)}"] += 1
    return values


def _cross_reference_counts(document: _Document) -> Counter[str]:
    values: Counter[str] = Counter()
    for fact in document.facts:
        text = " ".join((fact.text, *fact.tails))
        for match in _SECTION_REFERENCE.finditer(text):
            values[f"section:{match.group(1)}"] += 1
        for match in _CFR_REFERENCE.finditer(text):
            values[f"cfr:{match.group(1)}:{match.group(2)}"] += 1
        if fact.tag == "XREF":
            values[f"xref:{fact.attrs}:{text}"] += 1
    return values


def _category_counts(document: _Document, category: frozenset[str]) -> Counter[object]:
    return Counter(
        fact.token
        for fact in document.facts
        if fact.tag in category or any(ancestor in category for ancestor in fact.ancestors)
    )


def _duplicate_excess(before: _Document, target: _Document, candidate: _Document) -> int:
    strong_before = Counter(fact.key for fact in before.facts if fact.key[-1][1])
    strong_target = Counter(fact.key for fact in target.facts if fact.key[-1][1])
    strong_candidate = Counter(fact.key for fact in candidate.facts if fact.key[-1][1])
    excess = sum(
        max(0, count - max(strong_before[key], strong_target[key]))
        for key, count in strong_candidate.items()
    )

    before_rows = _row_signatures(before)
    target_rows = _row_signatures(target)
    candidate_rows = _row_signatures(candidate)
    excess += sum(
        max(0, count - max(before_rows[key], target_rows[key]))
        for key, count in candidate_rows.items()
    )
    return excess


def _row_signatures(document: _Document) -> Counter[tuple[object, ...]]:
    values: Counter[tuple[object, ...]] = Counter()
    for fact in document.facts:
        if fact.tag not in {"ROW", "TR"}:
            continue
        section = next(
            (
                segment
                for segment in reversed(fact.key[:-1])
                if segment[0].startswith("DIV") and dict(segment[1]).get("TYPE") == "SECTION"
            ),
            None,
        )
        values[(section, fact.tag, fact.subtree_digest)] += 1
    return values


def _selector_metrics(
    value: object, target: _Document, changed: Counter[_FactToken]
) -> dict[str, int | float]:
    if not isinstance(value, list) or not value:
        return {"declared_selectors": 0, "selector_coverage": 1.0}
    selectors = value
    selected: set[_NodeKey] = set()
    for fact in target.facts:
        display = _display_key(fact.key)
        attrs = dict(fact.attrs)
        haystack = " ".join(
            (display, fact.text, attrs.get("N", ""), attrs.get("hierarchy_metadata", ""))
        )
        for selector in selectors:
            needles: list[str] = []
            if isinstance(selector, str):
                needles = [selector]
            elif isinstance(selector, dict):
                needles = [
                    str(selector[name])
                    for name in ("identity", "node_id", "citation", "section")
                    if name in selector and isinstance(selector[name], (str, int, float))
                ]
            if any(needle in haystack for needle in needles):
                selected.add(fact.key)
    changed_keys = {key for key, _ in changed}
    covered = sum(
        1
        for key in changed_keys
        if any(key[: len(selected_key)] == selected_key for selected_key in selected)
    )
    coverage = covered / len(changed_keys) if changed_keys else 1.0
    return {
        "declared_selectors": len(selectors),
        "selected_target_nodes": len(selected),
        "selector_coverage": round(coverage, 6),
    }


def _display_key(key: _NodeKey) -> str:
    parts: list[str] = []
    for tag, attrs, ordinal in key:
        if attrs:
            rendered = ",".join(f"{name}={value}" for name, value in attrs)
            parts.append(f"{tag}[{rendered}]")
        else:
            parts.append(f"{tag}[{ordinal}]")
    return "/" + "/".join(parts)


__all__ = [
    "MAX_XML_BYTES",
    "PROJECTION_VERSION",
    "SCORE_VERSION",
    "EpisodeScore",
    "score_episode",
]
