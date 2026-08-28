"""Compile strict, provenance-preserving Federal Register/eCFR patch episodes.

The compiler is intentionally narrower than a generic regulatory-data loader.  It
accepts already-acquired official bytes plus their acquisition metadata, verifies
that a changed historical eCFR region contains an exact Federal Register
amendment link, and emits a clean public/private episode boundary.  Merely
consecutive eCFR snapshots are never sufficient evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx
from defusedxml import ElementTree as SafeElementTree

SCHEMA_VERSION = 1

_EPISODE_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{2,127}")
_DOCUMENT_NUMBER_RE = re.compile(
    r"\bFR\s+Doc\.\s+((?:C[0-9]+-)?[0-9]{4}-[0-9]{4,})\b", re.IGNORECASE
)
_SECTION_RE = re.compile(r"(?<![0-9.])([1-9][0-9]*\.[0-9]+(?:-[0-9]+)*)(?:\s*\(([^)]+)\))?")
_XREF_RE = re.compile(
    r"Link\s+to\s+(?:an\s+amendment|a\s+correction(?:\s+of\s+the\s+above\s+amendment)?)"
    r"\s+published\s+at\s+"
    r"(?P<volume>[0-9]+)\s+FR\s+(?P<page>[0-9]+),\s+"
    r"(?P<month>[A-Z][a-z]{2,8})\.?\s+(?P<day>[0-9]{1,2}),\s+(?P<year>[0-9]{4})",
    re.IGNORECASE,
)
_XREF_ID_RE = re.compile(r"[0-9]{8}")
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
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_SECRET_QUERY_KEYS = frozenset({"api_key", "apikey", "key", "token", "access_token"})
_SAFE_HEADER_NAMES = frozenset(
    {
        "content-length",
        "content-encoding",
        "content-type",
        "date",
        "etag",
        "last-modified",
        "x-ratelimit-limit",
        "x-ratelimit-remaining",
        "x-request-id",
    }
)
_GRANULARITIES = frozenset({"section", "part", "title"})
_TABLE_WRAPPER_TAGS = frozenset({"THEAD", "TBODY", "TFOOT"})
_TABLE_TAGS = frozenset({"TABLE", "TGROUP", "ROW", "TR", "ENTRY", "TD", "TH"})
_TABLE_PRESENTATION_ATTRIBUTES = frozenset(
    {
        "align",
        "border",
        "cellpadding",
        "cellspacing",
        "class",
        "frame",
        "style",
        "valign",
        "width",
    }
)
_CITATION_ONLY_TAGS = frozenset({"CITA"})
DEFAULT_METADATA_CAP_BYTES = 500 * 1024 * 1024
DEFAULT_SOURCE_CAP_BYTES = 5 * 1024 * 1024 * 1024
_PROBE_FIELDS = (
    "document_number",
    "publication_date",
    "effective_on",
    "full_text_xml_url",
    "json_url",
    "citation",
    "agencies",
    "cfr_references",
    "type",
    "action",
    "title",
    "start_page",
    "end_page",
    "volume",
)


class CompileError(RuntimeError):
    """A machine-classifiable reason that an episode cannot be compiled."""

    def __init__(self, code: str, detail: str, *, evidence: Mapping[str, Any] | None = None):
        self.code = code
        self.detail = detail
        self.evidence = dict(evidence or {})
        super().__init__(f"{code}: {detail}")

    def as_rejection(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "detail": self.detail}
        if self.evidence:
            result["evidence"] = self.evidence
        return result


@dataclass(frozen=True)
class ArtifactSpec:
    path: Path
    source_url: str
    acquired_at: str
    response_headers: Mapping[str, str]
    acquired_at_basis: str = "supplied_http_receipt"
    expected_sha256: str | None = None
    media_type: str | None = None


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
class _Artifact:
    spec: ArtifactSpec
    content: bytes
    sha256: str
    byte_count: int


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
        rules = tuple(
            RuleSpec(
                order=int(_mapping(value, f"sources.rules[{index}]")["order"]),
                xml=_artifact_spec(
                    _mapping(_mapping(value, f"sources.rules[{index}]")["xml"], "rule.xml"),
                    base_dir,
                ),
                metadata=_artifact_spec(
                    _mapping(
                        _mapping(value, f"sources.rules[{index}]")["metadata"],
                        "rule.metadata",
                    ),
                    base_dir,
                ),
                ecfr_amendment_date=(
                    date.fromisoformat(
                        str(_mapping(value, f"sources.rules[{index}]")["ecfr_amendment_date"])
                    )
                    if _mapping(value, f"sources.rules[{index}]").get("ecfr_amendment_date")
                    else None
                ),
                ecfr_issue_date=(
                    date.fromisoformat(
                        str(_mapping(value, f"sources.rules[{index}]")["ecfr_issue_date"])
                    )
                    if _mapping(value, f"sources.rules[{index}]").get("ecfr_issue_date")
                    else None
                ),
                ecfr_issue_date_basis=(
                    str(_mapping(value, f"sources.rules[{index}]")["ecfr_issue_date_basis"])
                    if _mapping(value, f"sources.rules[{index}]").get("ecfr_issue_date_basis")
                    else None
                ),
            )
            for index, value in enumerate(raw_rules)
        )
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
    new_xrefs = _new_amendment_xrefs(base_root, target_root)
    resolved_xrefs = _resolved_amendment_xrefs(base_root, target_root)
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


def acquire_official_source(
    store_root: Path,
    *,
    url: str,
    identifier: str,
    role: str,
    cap_bytes: int = DEFAULT_SOURCE_CAP_BYTES,
    client: httpx.Client | None = None,
    acquired_at: datetime | None = None,
) -> dict[str, Any]:
    """Acquire one allowlisted official resource into a content-addressed store.

    The acquisition receipt is the resume index: a repeated request re-hashes and
    reuses its retained object without touching the network.  ``cap_bytes`` limits
    cumulative network bytes in this store, not merely the current response.
    """
    source_url = _official_url(url)
    if not identifier or len(identifier) > 256 or any(ord(char) < 32 for char in identifier):
        raise CompileError("ACQUISITION_IDENTIFIER_INVALID", "identifier is missing or invalid")
    if not role or len(role) > 128:
        raise CompileError("ACQUISITION_ROLE_INVALID", "role is missing or invalid")
    if cap_bytes <= 0:
        raise CompileError("ACQUISITION_CAP_INVALID", "cap_bytes must be positive")

    root = store_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    receipt_path = root / "acquisition.json"
    receipt = _load_acquisition_receipt(receipt_path)
    matching_url = [row for row in receipt["artifacts"] if row.get("request_url") == source_url]
    for row in matching_url:
        object_path = _content_path(root, str(row["content_path"]))
        digest, byte_count = _sha256_file(object_path)
        if digest != row.get("sha256") or byte_count != row.get("byte_count"):
            raise CompileError(
                "ACQUISITION_CACHE_CORRUPT",
                f"retained object failed re-hash: {row.get('content_path')}",
            )
    if matching_url:
        identities = {
            (str(row.get("sha256")), str(row.get("content_path"))) for row in matching_url
        }
        if len(identities) != 1:
            raise CompileError(
                "ACQUISITION_IDENTITY_CONFLICT",
                "one request URL maps to incompatible retained objects",
            )
        for row in matching_url:
            if row.get("identifier") == identifier and row.get("role") == role:
                return dict(row)
        reference_time = acquired_at or datetime.now(UTC)
        if reference_time.tzinfo is None or reference_time.utcoffset() is None:
            raise CompileError("SOURCE_TIMESTAMP_INVALID", "acquired_at must be timezone-aware")
        reused = dict(matching_url[0])
        reused.update(
            {
                "identifier": identifier,
                "role": role,
                "network_byte_count": 0,
                "reused_artifact_id": reused["artifact_id"],
                "logical_reference_recorded_at": reference_time.astimezone(UTC)
                .isoformat()
                .replace("+00:00", "Z"),
            }
        )
        receipt["artifacts"].append(reused)
        receipt["artifacts"].sort(key=lambda value: (str(value["role"]), str(value["identifier"])))
        receipt["totals"] = _acquisition_totals(
            receipt["artifacts"],
            network_bytes=int(receipt["totals"]["network_bytes_acquired"]),
        )
        _write_json_atomic(receipt_path, receipt)
        return dict(reused)

    prior_network_bytes = int(receipt["totals"]["network_bytes_acquired"])
    if prior_network_bytes >= cap_bytes:
        raise CompileError(
            "ACQUISITION_CAP_EXCEEDED",
            "the acquisition store has already reached its cumulative byte cap",
            evidence={"cap_bytes": cap_bytes, "network_bytes_acquired": prior_network_bytes},
        )

    owns_client = client is None
    transport = client or httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(60.0, connect=20.0),
        headers={
            "Accept-Encoding": "identity",
            "User-Agent": "Psephos-RegPatch/1.0 (+local research compiler)",
        },
    )
    temporary_path: Path | None = None
    try:
        with transport.stream(
            "GET", source_url, headers={"Accept-Encoding": "identity"}
        ) as response:
            if response.status_code != 200:
                raise CompileError(
                    "ACQUISITION_HTTP_ERROR",
                    f"official source returned HTTP {response.status_code}",
                    evidence={"url": source_url, "status_code": response.status_code},
                )
            final_url = _official_url(str(response.url))
            safe_headers = _safe_headers(response.headers)
            declared_length = _content_length(safe_headers.get("content-length"))
            remaining = cap_bytes - prior_network_bytes
            if declared_length is not None and declared_length > remaining:
                raise CompileError(
                    "ACQUISITION_CAP_EXCEEDED",
                    "Content-Length exceeds the remaining acquisition cap",
                    evidence={"content_length": declared_length, "remaining_bytes": remaining},
                )

            object_root = root / "sha256"
            object_root.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(prefix=".download.", dir=object_root)
            temporary_path = Path(temporary_name)
            hasher = hashlib.sha256()
            byte_count = 0
            network_byte_count = 0
            content_encoding = safe_headers.get("content-encoding", "identity").strip().lower()
            if content_encoding in {"", "identity"}:
                decoder: Any | None = None
            elif content_encoding == "gzip":
                decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            elif content_encoding == "deflate":
                decoder = zlib.decompressobj()
            else:
                raise CompileError(
                    "ACQUISITION_ENCODING_UNSUPPORTED",
                    f"unsupported HTTP Content-Encoding: {content_encoding}",
                )
            predecoded = response.is_stream_consumed
            with os.fdopen(descriptor, "wb") as handle:
                try:
                    chunks = (response.content,) if predecoded else response.iter_raw()
                    if predecoded and content_encoding not in {"", "identity"}:
                        decoder = None
                        network_byte_count = declared_length or len(response.content)
                        if network_byte_count > remaining:
                            raise CompileError(
                                "ACQUISITION_CAP_EXCEEDED",
                                "response exceeds the remaining acquisition cap",
                                evidence={"remaining_bytes": remaining},
                            )
                    for raw_chunk in chunks:
                        if not raw_chunk:
                            continue
                        if not predecoded or content_encoding in {"", "identity"}:
                            network_byte_count += len(raw_chunk)
                        if network_byte_count > remaining:
                            raise CompileError(
                                "ACQUISITION_CAP_EXCEEDED",
                                "raw response exceeds the remaining acquisition cap",
                                evidence={"remaining_bytes": remaining},
                            )
                        chunk = decoder.decompress(raw_chunk) if decoder else raw_chunk
                        if not chunk:
                            continue
                        byte_count += len(chunk)
                        if byte_count > remaining:
                            raise CompileError(
                                "ACQUISITION_DECODED_CAP_EXCEEDED",
                                "decoded response exceeds the remaining acquisition cap",
                                evidence={"remaining_bytes": remaining},
                            )
                        hasher.update(chunk)
                        handle.write(chunk)
                    final_chunk = decoder.flush() if decoder else b""
                except zlib.error as exc:
                    raise CompileError(
                        "ACQUISITION_ENCODING_INVALID",
                        f"cannot decode HTTP Content-Encoding {content_encoding}",
                    ) from exc
                if final_chunk:
                    byte_count += len(final_chunk)
                    if byte_count > remaining:
                        raise CompileError(
                            "ACQUISITION_DECODED_CAP_EXCEEDED",
                            "decoded response exceeds the remaining acquisition cap",
                            evidence={"remaining_bytes": remaining},
                        )
                    hasher.update(final_chunk)
                    handle.write(final_chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if (
                declared_length is not None
                and (not predecoded or content_encoding in {"", "identity"})
                and network_byte_count != declared_length
            ):
                raise CompileError(
                    "ACQUISITION_LENGTH_MISMATCH",
                    "raw response size disagrees with Content-Length",
                    evidence={"declared": declared_length, "actual": network_byte_count},
                )
            hexdigest = hasher.hexdigest()
            media_type = safe_headers.get("content-type", "application/octet-stream")
            suffix = _source_suffix(final_url, media_type)
            relative = f"sha256/{hexdigest[:2]}/{hexdigest}{suffix}"
            object_path = root / relative
            object_path.parent.mkdir(parents=True, exist_ok=True)
            if object_path.exists():
                existing_digest, existing_size = _sha256_file(object_path)
                if existing_digest != hexdigest or existing_size != byte_count:
                    raise CompileError(
                        "ACQUISITION_CACHE_CORRUPT",
                        f"content-addressed target is corrupt: {relative}",
                    )
                temporary_path.unlink()
            else:
                os.replace(temporary_path, object_path)
            temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        if owns_client:
            transport.close()

    acquired = acquired_at or datetime.now(UTC)
    if acquired.tzinfo is None or acquired.utcoffset() is None:
        raise CompileError("SOURCE_TIMESTAMP_INVALID", "acquired_at must be timezone-aware")
    row = {
        "artifact_id": f"sha256:{hexdigest}",
        "identifier": identifier,
        "role": role,
        "request_url": source_url,
        "source_url": final_url,
        "content_path": relative,
        "media_type": media_type.partition(";")[0].strip().lower(),
        "byte_count": byte_count,
        "network_byte_count": network_byte_count,
        "content_decoded_for_storage": content_encoding not in {"", "identity"},
        "sha256": hexdigest,
        "acquired_at": acquired.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "response_headers": safe_headers,
        "http_status": 200,
    }
    receipt["artifacts"].append(row)
    receipt["artifacts"].sort(key=lambda value: (str(value["role"]), str(value["identifier"])))
    receipt["totals"] = _acquisition_totals(
        receipt["artifacts"],
        network_bytes=prior_network_bytes + network_byte_count,
    )
    _write_json_atomic(receipt_path, receipt)
    return dict(row)


def probe_federal_register(
    store_root: Path,
    *,
    start_date: date,
    end_date: date,
    max_candidates: int = 100,
    page: int = 1,
    per_page: int = 1000,
    cap_bytes: int = DEFAULT_METADATA_CAP_BYTES,
    client: httpx.Client | None = None,
    acquired_at: datetime | None = None,
) -> dict[str, Any]:
    """Probe FederalRegister.gov rule metadata and emit transition requests.

    This step downloads metadata only.  Each candidate is a title/part projection
    of a real document with an official XML URL; strict before/rule/after
    validation occurs only after ``acquire_candidate_sources`` and
    ``compile_episode``.
    """
    if end_date < start_date:
        raise CompileError("PROBE_WINDOW_INVALID", "end_date precedes start_date")
    if not 1 <= max_candidates <= 10000:
        raise CompileError("PROBE_LIMIT_INVALID", "max_candidates must be 1..10000")
    if not 1 <= page or not 1 <= per_page <= 1000:
        raise CompileError("PROBE_LIMIT_INVALID", "page/per_page are outside API bounds")

    query: list[tuple[str, str | int]] = [
        ("conditions[publication_date][gte]", start_date.isoformat()),
        ("conditions[publication_date][lte]", end_date.isoformat()),
        ("conditions[type][]", "RULE"),
        ("order", "oldest"),
        ("page", page),
        ("per_page", per_page),
    ]
    query.extend(("fields[]", field) for field in _PROBE_FIELDS)
    url = f"https://www.federalregister.gov/api/v1/documents.json?{urlencode(query)}"
    root = store_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    request_path = root / "probe-request.json"
    request_receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "manifest_kind": "regpatch_metadata_probe_request",
        "status": "REQUESTED",
        "requested_at": (acquired_at or datetime.now(UTC))
        .astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z"),
        "window": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "limits": {
            "max_candidates": max_candidates,
            "page": page,
            "per_page": per_page,
            "cap_bytes": cap_bytes,
        },
        "requests": [{"method": "GET", "url": url, "role": "federal_register_search"}],
    }
    _write_json_atomic(request_path, request_receipt)
    try:
        artifact = acquire_official_source(
            root,
            url=url,
            identifier=(
                f"federal-register-rules-{start_date.isoformat()}-{end_date.isoformat()}-p{page}"
            ),
            role="federal_register_search_metadata",
            cap_bytes=cap_bytes,
            client=client,
            acquired_at=acquired_at,
        )
        payload = json.loads(_content_path(root, str(artifact["content_path"])).read_bytes())
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise CompileError("PROBE_RESPONSE_INVALID", "Federal Register response lacks results")
        candidates, rejections = _probe_candidates(payload["results"], max_candidates)
        report: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "manifest_kind": "regpatch_metadata_probe",
            "status": "COMPLETE",
            "window": request_receipt["window"],
            "search_artifact": artifact,
            "api_count": payload.get("count"),
            "api_total_pages": payload.get("total_pages"),
            "candidates": candidates,
            "rejections": rejections,
            "totals": _probe_totals(candidates, rejections),
        }
        _write_json_atomic(root / "probe.json", report)
        request_receipt["status"] = "COMPLETE"
        request_receipt["result_path"] = "probe.json"
        _write_json_atomic(request_path, request_receipt)
        return report
    except Exception as exc:
        request_receipt["status"] = "FAILED"
        request_receipt["failure"] = (
            exc.as_rejection()
            if isinstance(exc, CompileError)
            else {"code": "PROBE_FAILED", "detail": str(exc)}
        )
        _write_json_atomic(request_path, request_receipt)
        raise


def probe_ecfr_versions(
    store_root: Path,
    *,
    title: int,
    part: str,
    issue_date_start: date,
    issue_date_end: date,
    page: int = 1,
    cap_bytes: int = DEFAULT_METADATA_CAP_BYTES,
    client: httpx.Client | None = None,
    acquired_at: datetime | None = None,
) -> dict[str, Any]:
    """Probe observed eCFR content-version clocks for one title/part.

    Candidate windows are derived from returned ``amendment_date`` records.  The
    Federal Register publication date is deliberately not assumed to be the eCFR
    state-transition date.
    """
    if not 1 <= title <= 999 or not part or any(char in part for char in "/\\"):
        raise CompileError("VERSION_PROBE_SCOPE_INVALID", "invalid CFR title/part")
    if issue_date_end < issue_date_start:
        raise CompileError("VERSION_PROBE_WINDOW_INVALID", "issue-date window is reversed")
    if page < 1:
        raise CompileError("VERSION_PROBE_PAGE_INVALID", "page must be positive")
    query = urlencode(
        [
            ("issue_date[gte]", issue_date_start.isoformat()),
            ("issue_date[lte]", issue_date_end.isoformat()),
            ("part", part),
            ("page", page),
        ]
    )
    url = f"https://www.ecfr.gov/api/versioner/v1/versions/title-{title}.json?{query}"
    root = store_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    stem = f"versions-title-{title}-part-{_safe_slug(part)}-page-{page}"
    request_path = root / f"{stem}-request.json"
    request: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "manifest_kind": "regpatch_ecfr_version_probe_request",
        "status": "REQUESTED",
        "scope": {"title": title, "part": part},
        "issue_date_window": {
            "gte": issue_date_start.isoformat(),
            "lte": issue_date_end.isoformat(),
        },
        "page": page,
        "cap_bytes": cap_bytes,
        "requests": [{"method": "GET", "url": url, "role": "ecfr_versions_metadata"}],
    }
    _write_json_atomic(request_path, request)
    try:
        artifact = acquire_official_source(
            root,
            url=url,
            identifier=stem,
            role="ecfr_versions_metadata",
            cap_bytes=cap_bytes,
            client=client,
            acquired_at=acquired_at,
        )
        payload = json.loads(_content_path(root, str(artifact["content_path"])).read_bytes())
        if not isinstance(payload, dict) or not isinstance(payload.get("content_versions"), list):
            raise CompileError("VERSION_PROBE_RESPONSE_INVALID", "response lacks content_versions")
        records, rejections = _version_records(payload["content_versions"], title=title, part=part)
        windows = _version_windows(records, title=title, part=part)
        report: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "manifest_kind": "regpatch_ecfr_version_probe",
            "status": "COMPLETE",
            "scope": request["scope"],
            "issue_date_window": request["issue_date_window"],
            "page": page,
            "source_artifact": artifact,
            "api_meta": payload.get("meta"),
            "content_versions": records,
            "candidate_windows": windows,
            "rejections": rejections,
            "totals": {
                "content_versions": len(records),
                "substantive_versions": sum(row["substantive"] is True for row in records),
                "candidate_windows": len(windows),
                "rejected_rows": len(rejections),
            },
        }
        result_path = root / f"{stem}.json"
        _write_json_atomic(result_path, report)
        request["status"] = "COMPLETE"
        request["result_path"] = result_path.name
        _write_json_atomic(request_path, request)
        return report
    except Exception as exc:
        request["status"] = "FAILED"
        request["failure"] = (
            exc.as_rejection()
            if isinstance(exc, CompileError)
            else {"code": "VERSION_PROBE_FAILED", "detail": str(exc)}
        )
        _write_json_atomic(request_path, request)
        raise


def expand_candidate_windows(
    candidate: Mapping[str, Any], version_report: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Join one FR metadata candidate to observed eCFR amendment windows."""
    try:
        base_id = str(candidate["candidate_id"])
        title = int(candidate["title"])
        part = str(candidate["part"])
        publication_date = date.fromisoformat(str(candidate["publication_date"]))
        report_scope = _mapping(version_report["scope"], "version_report.scope")
        windows = _sequence(version_report["candidate_windows"], "candidate_windows")
    except (KeyError, TypeError, ValueError) as exc:
        raise CompileError(
            "VERSION_JOIN_INVALID", f"invalid candidate/version report: {exc}"
        ) from exc
    if int(report_scope.get("title", -1)) != title or str(report_scope.get("part", "")) != part:
        raise CompileError("VERSION_JOIN_SCOPE_MISMATCH", "candidate and version scope disagree")
    expanded: list[dict[str, Any]] = []
    for raw_window in windows:
        window = _mapping(raw_window, "candidate_window")
        amendment_date = date.fromisoformat(str(window["ecfr_amendment_date"]))
        issue_date = date.fromisoformat(str(window["ecfr_issue_date"]))
        if amendment_date < publication_date:
            continue
        row = dict(candidate)
        row.update(
            {
                "candidate_id": f"{base_id}-amend-{amendment_date.isoformat()}",
                "base_date": (amendment_date - timedelta(days=1)).isoformat(),
                "successor_date": amendment_date.isoformat(),
                "ecfr_amendment_date": amendment_date.isoformat(),
                "ecfr_issue_date": issue_date.isoformat(),
                "before_ecfr_url": _ecfr_full_url(amendment_date - timedelta(days=1), title, part),
                "after_ecfr_url": _ecfr_full_url(amendment_date, title, part),
                "version_evidence": {
                    "identifiers": list(_sequence(window["identifiers"], "identifiers")),
                    "record_count": int(window["record_count"]),
                    "substantive_record_count": int(window["substantive_record_count"]),
                },
                "status": "OBSERVED_VERSION_WINDOW_UNVALIDATED_CAUSALITY",
            }
        )
        expanded.append(row)
    return expanded


def acquire_candidate_sources(
    store_root: Path,
    candidate: Mapping[str, Any],
    *,
    cap_bytes: int = DEFAULT_SOURCE_CAP_BYTES,
    client: httpx.Client | None = None,
    acquired_at: datetime | None = None,
) -> Path:
    """Acquire a probed source quartet and write a compiler-ready request."""
    try:
        candidate_id = str(candidate["candidate_id"])
        document_number = str(candidate["document_number"])
        date.fromisoformat(str(candidate["publication_date"]))
        base_date = date.fromisoformat(str(candidate["base_date"]))
        successor_date = date.fromisoformat(str(candidate["successor_date"]))
        ecfr_amendment_date = date.fromisoformat(str(candidate["ecfr_amendment_date"]))
        ecfr_issue_date = date.fromisoformat(str(candidate["ecfr_issue_date"]))
        title = int(candidate["title"])
        part = str(candidate["part"])
        metadata_url = _official_url(str(candidate["metadata_url"]))
        rule_url = _official_url(str(candidate["rule_xml_url"]))
        before_url = _official_url(str(candidate["before_ecfr_url"]))
        after_url = _official_url(str(candidate["after_ecfr_url"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise CompileError("CANDIDATE_INVALID", f"invalid probe candidate: {exc}") from exc
    if _EPISODE_ID_RE.fullmatch(candidate_id) is None:
        raise CompileError("CANDIDATE_INVALID", "candidate_id is not a safe episode identifier")
    if base_date >= successor_date or ecfr_amendment_date != successor_date:
        raise CompileError(
            "CANDIDATE_WINDOW_INVALID",
            "candidate must use the observed amendment date as successor and an earlier base",
        )
    if ecfr_issue_date < ecfr_amendment_date:
        raise CompileError("CANDIDATE_WINDOW_INVALID", "eCFR issue date precedes amendment date")
    if before_url != _ecfr_full_url(base_date, title, part) or after_url != _ecfr_full_url(
        successor_date, title, part
    ):
        raise CompileError(
            "CANDIDATE_SOURCE_URL_MISMATCH",
            "eCFR source URLs disagree with the observed version window",
        )

    root = store_root.resolve()
    request_dir = root / "candidates" / candidate_id
    request_dir.mkdir(parents=True, exist_ok=True)
    requests = [
        ("federal_register_document_metadata", metadata_url, f"{document_number}-metadata"),
        ("federal_register_rule_xml", rule_url, f"{document_number}-xml"),
        ("base_ecfr", before_url, f"title-{title}-part-{part}-{base_date}"),
        ("successor_ecfr", after_url, f"title-{title}-part-{part}-{successor_date}"),
    ]
    acquisition_request: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "manifest_kind": "regpatch_candidate_acquisition_request",
        "candidate_id": candidate_id,
        "status": "REQUESTED",
        "cap_bytes": cap_bytes,
        "requests": [
            {"order": order, "method": "GET", "role": role, "url": url}
            for order, (role, url, _identifier) in enumerate(requests, start=1)
        ],
    }
    _write_json_atomic(request_dir / "acquisition-request.json", acquisition_request)
    artifacts: dict[str, dict[str, Any]] = {}
    try:
        for role, url, identifier in requests:
            artifacts[role] = acquire_official_source(
                root,
                url=url,
                identifier=identifier,
                role=role,
                cap_bytes=cap_bytes,
                client=client,
                acquired_at=acquired_at,
            )
    except Exception as exc:
        acquisition_request["status"] = "FAILED"
        acquisition_request["failure"] = (
            exc.as_rejection()
            if isinstance(exc, CompileError)
            else {"code": "ACQUISITION_FAILED", "detail": str(exc)}
        )
        _write_json_atomic(request_dir / "acquisition-request.json", acquisition_request)
        raise

    def source_row(role: str, media_type: str) -> dict[str, Any]:
        artifact = artifacts[role]
        object_path = _content_path(root, str(artifact["content_path"]))
        return {
            "path": os.path.relpath(object_path, request_dir),
            "source_url": artifact["source_url"],
            "sha256": artifact["sha256"],
            "acquired_at": artifact["acquired_at"],
            "acquired_at_basis": "captured_http_response",
            "response_headers": artifact["response_headers"],
            "media_type": media_type,
        }

    spec = {
        "episode_id": candidate_id,
        "scope": {
            "title": title,
            "parts": [part],
            "sections": [],
            "granularity": "part",
        },
        "window": {
            "base_date": base_date.isoformat(),
            "successor_date": successor_date.isoformat(),
        },
        "sources": {
            "base": source_row("base_ecfr", "application/xml"),
            "target": source_row("successor_ecfr", "application/xml"),
            "rules": [
                {
                    "order": 1,
                    "ecfr_amendment_date": ecfr_amendment_date.isoformat(),
                    "ecfr_issue_date": ecfr_issue_date.isoformat(),
                    "xml": source_row("federal_register_rule_xml", "application/xml"),
                    "metadata": source_row(
                        "federal_register_document_metadata", "application/json"
                    ),
                }
            ],
        },
    }
    spec_path = request_dir / "source-spec.json"
    _write_json_atomic(spec_path, spec)
    acquisition_request["status"] = "COMPLETE"
    acquisition_request["source_spec"] = "source-spec.json"
    acquisition_request["artifacts"] = artifacts
    _write_json_atomic(request_dir / "acquisition-request.json", acquisition_request)
    return spec_path


def _artifact_spec(payload: Mapping[str, Any], base_dir: Path) -> ArtifactSpec:
    local_path = Path(str(payload["path"]))
    path = local_path if local_path.is_absolute() else base_dir / local_path
    headers = _mapping(payload.get("response_headers", {}), "response_headers")
    acquired_at = str(payload["acquired_at"])
    _parse_timestamp(acquired_at)
    expected = payload.get("sha256")
    expected_sha256 = str(expected) if expected is not None else None
    if expected_sha256 is not None and _SHA256_RE.fullmatch(expected_sha256) is None:
        raise CompileError("SPEC_INVALID", "artifact sha256 must be 64 lowercase hex characters")
    return ArtifactSpec(
        path=path.resolve(),
        source_url=_official_url(str(payload["source_url"])),
        acquired_at=acquired_at,
        response_headers=_safe_headers(headers),
        acquired_at_basis=str(payload.get("acquired_at_basis", "supplied_http_receipt")),
        expected_sha256=expected_sha256,
        media_type=str(payload["media_type"]) if payload.get("media_type") else None,
    )


def _load_artifact(spec: ArtifactSpec, *, expected_media: str) -> _Artifact:
    if not spec.path.is_file():
        raise CompileError("SOURCE_PATH_MISSING", f"source file does not exist: {spec.path.name}")
    digest, size = _sha256_file(spec.path)
    if spec.expected_sha256 is not None and digest != spec.expected_sha256:
        raise CompileError(
            "SOURCE_HASH_MISMATCH",
            f"source failed SHA-256 verification: {spec.path.name}",
            evidence={"expected": spec.expected_sha256, "actual": digest},
        )
    media_type = (spec.media_type or expected_media).partition(";")[0].strip().lower()
    if media_type != expected_media:
        raise CompileError(
            "SOURCE_MEDIA_TYPE_INVALID",
            f"expected {expected_media}, found {media_type}: {spec.path.name}",
        )
    try:
        content = spec.path.read_bytes()
    except OSError as exc:
        raise CompileError("SOURCE_READ_FAILED", f"cannot read source: {spec.path.name}") from exc
    return _Artifact(spec=spec, content=content, sha256=digest, byte_count=size)


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
    for key in set(before_regions) | set(after_regions):
        before = before_regions.get(key)
        after = after_regions.get(key)
        if _substantive_element_digest(
            before.after if before else None
        ) == _substantive_element_digest(after.after if after else None):
            continue
        template = before or after
        assert template is not None
        changed[key] = _ChangedRegion(
            kind=template.kind,
            identity=template.identity,
            citation=template.citation,
            before=before.after if before else None,
            after=after.after if after else None,
        )
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
    regions: list[dict[str, Any]] = []
    for key in sorted(changed, key=_natural_key):
        region = changed[key]
        row = {
            "kind": region.kind,
            "region_id": region.identity,
            "citation": region.citation,
            "projection": "substantive_v3_xref_cita_and_table_presentation_normalized",
            "before_sha256": _substantive_element_digest(region.before),
            "after_sha256": _substantive_element_digest(region.after),
            "before_raw_sha256": _element_digest(region.before),
            "after_raw_sha256": _element_digest(region.after),
            "before_present": region.before is not None,
            "after_present": region.after is not None,
        }
        if region.kind == "section":
            row["section"] = region.identity
        elif region.kind == "appendix":
            row["appendix"] = region.identity
        regions.append(row)
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
    match_matrix = [
        [rule for rule in rules if _xref_matches_rule(xref, rule)] for _motif, xref in witnesses
    ]
    unmatched_indexes = [index for index, matches in enumerate(match_matrix) if not matches]
    stale_indexes: set[int] = set()
    for index in unmatched_indexes:
        motif, xref = witnesses[index]
        region = changed_regions[_region_key(xref.region_kind, xref.region_id)]
        before_citations = _region_fr_citations(region.before) if region.before is not None else []
        if motif == "deferred_pending_xref_resolved" and any(
            citation["volume"] == xref.volume and citation["page"] == xref.page
            for citation in before_citations
        ):
            stale_indexes.add(index)
            ignored.append(
                {
                    "reason_code": "STALE_RESOLVED_XREF_ALREADY_CITED_BEFORE_WINDOW",
                    "motif": motif,
                    **_xref_receipt(xref),
                }
            )
    fatal_unmatched = [index for index in unmatched_indexes if index not in stale_indexes]
    if fatal_unmatched and any(match_matrix):
        raise CompileError(
            "SAME_DAY_COLLISION_UNOBSERVED",
            "not every eCFR amendment-link transition is represented by a supplied rule",
            evidence={
                "unmatched_xrefs": [
                    {
                        "motif": witnesses[index][0],
                        **_xref_receipt(witnesses[index][1]),
                    }
                    for index in fatal_unmatched
                ]
            },
        )
    if fatal_unmatched:
        _raise_xref_mismatch(witnesses[fatal_unmatched[0]][1], rules, spec.title)

    retained = [index for index in range(len(witnesses)) if index not in stale_indexes]
    witnesses = [witnesses[index] for index in retained]
    match_matrix = [match_matrix[index] for index in retained]

    evidence: list[dict[str, Any]] = list(citation_evidence)
    matched_documents: set[str] = set(citation_documents)
    for (motif, xref), matches in zip(witnesses, match_matrix, strict=True):
        region_key = _region_key(xref.region_kind, xref.region_id)
        is_substantive_region = region_key in changed_regions
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
            if not is_substantive_region:
                after_citations = []
            else:
                after_region = changed_regions[region_key].after
                if after_region is None:
                    raise CompileError(
                        "DEFERRED_AFTER_REGION_MISSING",
                        f"deferred changed region is absent from successor: {xref.citation}",
                    )
                after_citations = [
                    citation
                    for region in changed_regions.values()
                    if region.after is not None
                    for citation in _region_fr_citations(region.after)
                ]
                matching_citations = [
                    citation
                    for citation in after_citations
                    if citation["volume"] == rule.volume
                    and rule.start_page <= citation["page"] <= rule.end_page
                ]
                after_citations = matching_citations
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
                "substantive_change": is_substantive_region,
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
                        if motif == "deferred_pending_xref_resolved" and is_substantive_region
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


def _new_amendment_xrefs(before_root: Any, after_root: Any) -> list[_Xref]:
    before = {_xref_key(value) for value in _extract_xrefs(before_root)}
    return [value for value in _extract_xrefs(after_root) if _xref_key(value) not in before]


def _resolved_amendment_xrefs(before_root: Any, after_root: Any) -> list[_Xref]:
    after = {_xref_key(value) for value in _extract_xrefs(after_root)}
    return [value for value in _extract_xrefs(before_root) if _xref_key(value) not in after]


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


def _element_digest(elem: Any | None) -> str | None:
    if elem is None:
        return None
    return hashlib.sha256(SafeElementTree.tostring(elem, encoding="utf-8")).hexdigest()


def _substantive_element_digest(elem: Any | None) -> str | None:
    """Hash the candidate-scored XML projection."""
    if elem is None:
        return None
    tokens: list[Any] = []

    def visit(node: Any) -> bool:
        if is_projected_editorial_node(node):
            return False
        if _local_name(str(node.tag)).upper() == "TABLE":
            tokens.append(("semantic-table", canonical_table_signature(node)))
            return True
        tokens.append(
            (
                "start",
                _local_name(str(node.tag)),
                tuple(sorted((str(key), str(value)) for key, value in node.attrib.items())),
                _normalized_text(node.text),
            )
        )
        for child in list(node):
            retained = visit(child)
            tail = _normalized_text(child.tail)
            if tail:
                tokens.append(("tail" if retained else "projected-tail", tail))
        tokens.append(("end", _local_name(str(node.tag))))
        return True

    visit(elem)
    encoded = json.dumps(tokens, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_table_signature(elem: Any) -> tuple[Any, ...]:
    """Return presentation-neutral, order-preserving semantics for one eCFR table."""
    captions = tuple(
        _joined_itertext(node)
        for node in elem.iter()
        if _local_name(str(node.tag)).upper() == "CAPTION" and _joined_itertext(node)
    )
    rows: list[tuple[Any, ...]] = []
    for row in elem.iter():
        if _local_name(str(row.tag)).upper() not in {"TR", "ROW"}:
            continue
        cells: list[tuple[Any, ...]] = []
        for cell in list(row):
            tag = _local_name(str(cell.tag)).upper()
            if tag not in {"TH", "TD", "ENTRY"}:
                continue
            spans = tuple(
                (name.lower(), str(value))
                for name, value in sorted(cell.attrib.items())
                if name.lower() in {"colspan", "rowspan", "namest", "nameend", "morerows"}
            )
            cells.append((tag, spans, _joined_itertext(cell)))
        rows.append(tuple(cells))
    return (captions, tuple(rows))


def _joined_itertext(elem: Any) -> str:
    return " ".join(part for value in elem.itertext() if (part := _normalized_text(str(value))))


def is_editorial_amendment_xref(elem: Any) -> bool:
    """Return true only for the exact synthetic eCFR pending-amendment link shape."""
    if _local_name(str(elem.tag)) != "XREF" or _XREF_RE.search(_text(elem)) is None:
        return False
    identifier = str(elem.attrib.get("ID", ""))
    reference_id = str(elem.attrib.get("REFID", ""))
    instruction = str(elem.attrib.get("AMDINSN", ""))
    if (
        _XREF_ID_RE.fullmatch(identifier) is None
        or not reference_id.isdigit()
        or (instruction and not instruction.isdigit())
    ):
        return False
    try:
        date.fromisoformat(f"{identifier[:4]}-{identifier[4:6]}-{identifier[6:]}")
    except ValueError:
        return False
    return True


def is_projected_editorial_node(elem: Any) -> bool:
    """Identify non-derivable eCFR editorial metadata excluded from reward."""
    return _local_name(str(elem.tag)).upper() == "CITA" or is_editorial_amendment_xref(elem)


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


def _public_artifact_receipt(artifact: _Artifact) -> dict[str, Any]:
    return {
        "source_url": artifact.spec.source_url,
        "sha256": artifact.sha256,
        "byte_count": artifact.byte_count,
        "acquired_at": artifact.spec.acquired_at,
        "acquired_at_basis": artifact.spec.acquired_at_basis,
        "response_headers": dict(sorted(artifact.spec.response_headers.items())),
    }


def _private_artifact_receipt(artifact: _Artifact) -> dict[str, Any]:
    return {
        **_public_artifact_receipt(artifact),
        "artifact_id": f"sha256:{artifact.sha256}",
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


def _load_acquisition_receipt(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": SCHEMA_VERSION,
            "manifest_kind": "regpatch_acquisition_receipt",
            "artifacts": [],
            "totals": {
                "artifacts": 0,
                "objects": 0,
                "retained_bytes": 0,
                "network_bytes_acquired": 0,
            },
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompileError("ACQUISITION_RECEIPT_INVALID", f"cannot read receipt: {path}") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("manifest_kind") != "regpatch_acquisition_receipt"
        or not isinstance(payload.get("artifacts"), list)
        or not isinstance(payload.get("totals"), dict)
    ):
        raise CompileError("ACQUISITION_RECEIPT_INVALID", f"invalid receipt: {path}")
    return payload


def _acquisition_totals(
    artifacts: Sequence[Mapping[str, Any]], *, network_bytes: int
) -> dict[str, int]:
    unique = {str(value["sha256"]): int(value["byte_count"]) for value in artifacts}
    return {
        "artifacts": len(artifacts),
        "objects": len(unique),
        "retained_bytes": sum(unique.values()),
        "network_bytes_acquired": network_bytes,
    }


def _content_path(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts:
        raise CompileError("ACQUISITION_RECEIPT_INVALID", "content path escapes acquisition root")
    candidate = (root / Path(*pure.parts)).resolve()
    resolved_root = root.resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise CompileError("ACQUISITION_RECEIPT_INVALID", "content path escapes acquisition root")
    return candidate


def _content_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        result = int(value)
    except ValueError as exc:
        raise CompileError("ACQUISITION_HEADER_INVALID", "Content-Length is not numeric") from exc
    if result < 0:
        raise CompileError("ACQUISITION_HEADER_INVALID", "Content-Length is negative")
    return result


def _source_suffix(url: str, media_type: str) -> str:
    suffix = PurePosixPath(urlsplit(url).path).suffix.lower()
    if suffix in {".xml", ".json", ".txt", ".html", ".pdf"}:
        return suffix
    normalized = media_type.partition(";")[0].strip().lower()
    return {
        "application/json": ".json",
        "application/xml": ".xml",
        "text/xml": ".xml",
        "text/plain": ".txt",
        "text/html": ".html",
        "application/pdf": ".pdf",
    }.get(normalized, ".bin")


def _probe_candidates(
    raw_results: Sequence[Any], max_candidates: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    for index, value in enumerate(raw_results):
        if len(candidates) >= max_candidates:
            break
        if not isinstance(value, Mapping):
            rejections.append(
                {"code": "PROBE_RESULT_INVALID", "result_index": index, "detail": "not an object"}
            )
            continue
        document_number = str(value.get("document_number") or "")
        if not document_number:
            rejections.append(
                {
                    "code": "PROBE_DOCUMENT_NUMBER_MISSING",
                    "result_index": index,
                    "detail": "result has no document number",
                }
            )
            continue
        try:
            publication = date.fromisoformat(str(value["publication_date"]))
        except (KeyError, ValueError):
            rejections.append(
                {
                    "code": "PROBE_PUBLICATION_DATE_MISSING",
                    "document_number": document_number,
                }
            )
            continue
        rule_url = value.get("full_text_xml_url")
        if not rule_url:
            rejections.append(
                {
                    "code": "PROBE_RULE_XML_URL_MISSING",
                    "document_number": document_number,
                }
            )
            continue
        try:
            rule_url = _official_url(str(rule_url))
            metadata_url = _official_url(
                str(value.get("json_url") or _document_metadata_url(document_number))
            )
        except CompileError as exc:
            rejections.append(
                {
                    **exc.as_rejection(),
                    "document_number": document_number,
                }
            )
            continue
        references = value.get("cfr_references")
        if not isinstance(references, list) or not references:
            rejections.append(
                {
                    "code": "PROBE_CFR_REFERENCE_MISSING",
                    "document_number": document_number,
                }
            )
            continue
        accepted_reference = False
        for reference in references:
            if len(candidates) >= max_candidates:
                break
            if not isinstance(reference, Mapping):
                continue
            try:
                title = int(reference["title"])
                part = str(reference["part"])
            except (KeyError, TypeError, ValueError):
                continue
            if not 1 <= title <= 999 or not part or any(char in part for char in "/\\"):
                continue
            key = (document_number, title, part)
            if key in seen:
                continue
            seen.add(key)
            accepted_reference = True
            agencies = value.get("agencies", [])
            agency_names = sorted(
                {
                    str(row.get("name") or row.get("raw_name"))
                    for row in agencies
                    if isinstance(row, Mapping) and (row.get("name") or row.get("raw_name"))
                }
            )
            safe_part = re.sub(r"[^a-z0-9.-]+", "-", part.lower()).strip("-")
            candidate_id = (
                f"fr-{document_number}-title-{title}-part-{safe_part}-{publication.isoformat()}"
            )
            candidates.append(
                {
                    "candidate_id": candidate_id,
                    "document_number": document_number,
                    "document_type": value.get("type"),
                    "action": value.get("action"),
                    "document_title": value.get("title"),
                    "citation": value.get("citation"),
                    "volume": value.get("volume"),
                    "start_page": value.get("start_page"),
                    "end_page": value.get("end_page"),
                    "publication_date": publication.isoformat(),
                    "effective_on": value.get("effective_on"),
                    "title": title,
                    "part": part,
                    "agencies": agency_names,
                    "metadata_url": metadata_url,
                    "rule_xml_url": rule_url,
                    "status": "REQUIRES_ECFR_VERSION_PROBE",
                }
            )
        if not accepted_reference:
            rejections.append(
                {
                    "code": "PROBE_CFR_REFERENCE_INVALID",
                    "document_number": document_number,
                }
            )
    return candidates, rejections


def _probe_totals(
    candidates: Sequence[Mapping[str, Any]], rejections: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    agencies = {
        str(agency)
        for candidate in candidates
        for agency in _sequence(candidate.get("agencies", []), "candidate.agencies")
    }
    return {
        "metadata_candidates": len(candidates),
        "distinct_documents": len({candidate["document_number"] for candidate in candidates}),
        "distinct_titles": len({candidate["title"] for candidate in candidates}),
        "distinct_agencies": len(agencies),
        "rejected_metadata_rows": len(rejections),
    }


def _version_records(
    raw_records: Sequence[Any], *, title: int, part: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    for index, value in enumerate(raw_records):
        if not isinstance(value, Mapping):
            rejections.append(
                {"code": "VERSION_ROW_INVALID", "row_index": index, "detail": "not an object"}
            )
            continue
        try:
            row_title = int(value["title"])
            row_part = str(value["part"])
            identifier = str(value["identifier"])
            amendment_date = date.fromisoformat(str(value["amendment_date"]))
            issue_date = date.fromisoformat(str(value["issue_date"]))
            row_type = str(value["type"])
            substantive = value["substantive"] is True
            removed = value.get("removed") is True
        except (KeyError, TypeError, ValueError) as exc:
            rejections.append(
                {
                    "code": "VERSION_ROW_INVALID",
                    "row_index": index,
                    "detail": f"missing/invalid field: {exc}",
                }
            )
            continue
        if row_title != title or row_part != part:
            rejections.append(
                {
                    "code": "VERSION_ROW_SCOPE_MISMATCH",
                    "row_index": index,
                    "title": row_title,
                    "part": row_part,
                }
            )
            continue
        if row_type not in {"section", "appendix"} or not identifier:
            rejections.append(
                {
                    "code": "VERSION_ROW_IDENTITY_INVALID",
                    "row_index": index,
                    "type": row_type,
                    "identifier": identifier,
                }
            )
            continue
        if issue_date < amendment_date:
            rejections.append(
                {
                    "code": "VERSION_ROW_CLOCK_INVALID",
                    "row_index": index,
                    "amendment_date": amendment_date.isoformat(),
                    "issue_date": issue_date.isoformat(),
                }
            )
            continue
        records.append(
            {
                "title": row_title,
                "part": row_part,
                "type": row_type,
                "identifier": identifier,
                "name": value.get("name"),
                "subpart": value.get("subpart"),
                "amendment_date": amendment_date.isoformat(),
                "issue_date": issue_date.isoformat(),
                "substantive": substantive,
                "removed": removed,
            }
        )
    records.sort(
        key=lambda row: (
            str(row["amendment_date"]),
            str(row["issue_date"]),
            str(row["type"]),
            _natural_key(str(row["identifier"])),
        )
    )
    return records, rejections


def _version_windows(
    records: Sequence[Mapping[str, Any]], *, title: int, part: str
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in records:
        if row.get("substantive") is not True:
            continue
        key = (str(row["amendment_date"]), str(row["issue_date"]))
        grouped.setdefault(key, []).append(row)
    return [
        {
            "title": title,
            "part": part,
            "base_date": (date.fromisoformat(amendment) - timedelta(days=1)).isoformat(),
            "successor_date": amendment,
            "ecfr_amendment_date": amendment,
            "ecfr_issue_date": issue,
            "identifiers": sorted({str(row["identifier"]) for row in rows}, key=_natural_key),
            "record_count": len(rows),
            "substantive_record_count": sum(row.get("substantive") is True for row in rows),
            "removed_record_count": sum(row.get("removed") is True for row in rows),
        }
        for (amendment, issue), rows in sorted(grouped.items())
    ]


def _safe_slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9.-]+", "-", value.casefold()).strip("-")
    if not result:
        raise CompileError("IDENTIFIER_INVALID", "value cannot form a safe slug")
    return result


def _document_metadata_url(document_number: str) -> str:
    return f"https://www.federalregister.gov/api/v1/documents/{document_number}.json"


def _document_number_from_url(value: str) -> str:
    official = _official_url(value)
    name = PurePosixPath(urlsplit(official).path).name.removesuffix(".json")
    if re.fullmatch(r"(?:C[0-9]+-)?[0-9]{4}-[0-9]{4,}", name) is None:
        raise CompileError("METADATA_INVALID", "correction URL lacks a document number")
    return name


def _ecfr_full_url(as_of: date, title: int, part: str) -> str:
    return (
        f"https://www.ecfr.gov/api/versioner/v1/full/{as_of.isoformat()}/"
        f"title-{title}.xml?{urlencode({'part': part})}"
    )


def _official_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise CompileError("SOURCE_URL_NOT_OFFICIAL", "invalid source URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.fragment
        or any(key.lower() in _SECRET_QUERY_KEYS for key, _ in parse_qsl(parsed.query))
    ):
        raise CompileError("SOURCE_URL_NOT_OFFICIAL", "source URL must be secret-free HTTPS")
    host = (parsed.hostname or "").lower()
    path = parsed.path
    accepted = (
        (host == "www.ecfr.gov" and path.startswith("/api/versioner/v1/"))
        or (
            host == "www.federalregister.gov"
            and path.startswith(("/api/v1/", "/documents/full_text/xml/"))
        )
        or (host == "www.govinfo.gov" and path.startswith(("/content/pkg/", "/bulkdata/")))
    )
    if not accepted:
        raise CompileError(
            "SOURCE_URL_NOT_OFFICIAL",
            f"URL is outside the RegPatch official-source allowlist: {value}",
        )
    return value


def _safe_headers(value: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_name, raw_value in value.items():
        name = str(raw_name).strip().lower()
        if name in _SAFE_HEADER_NAMES:
            result[name] = str(raw_value).strip()
    return result


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CompileError("SOURCE_TIMESTAMP_INVALID", "acquired_at must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CompileError("SOURCE_TIMESTAMP_INVALID", "acquired_at must be timezone-aware")
    return parsed.astimezone(UTC)


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


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CompileError("SPEC_INVALID", f"{field} must be an object")
    return value


def _sequence(value: Any, field: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise CompileError("SPEC_INVALID", f"{field} must be an array")
    return value


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _text(elem: Any) -> str:
    return " ".join("".join(elem.itertext()).split())


def _normalized_text(value: str | None) -> str:
    return " ".join((value or "").split())


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


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


def _natural_key(value: str) -> tuple[Any, ...]:
    return tuple(int(part) if part.isdigit() else part for part in re.split(r"([0-9]+)", value))
