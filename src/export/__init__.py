from src.export.builders import (
    build_evidence_card,
    build_manifest,
    build_member_profile,
    build_zip_feed,
    sha256_hex,
)
from src.export.contracts import (
    CommitteeMembership,
    ConfidenceLabel,
    EvidenceBlock,
    EvidenceCardPayload,
    EvidenceSection,
    MemberProfilePayload,
    RecentRuleFire,
    ScoreSummary,
    SourceAnchor,
    ZipFeedPayload,
    ZipMemberSummary,
)
from src.export.manifest import ManifestEntry, SnapshotManifest

_LAZY_EXPORTS = {
    "PlannedFile": "src.export.writer",
    "evidence_path": "src.export.writer",
    "manifest_path": "src.export.writer",
    "member_path": "src.export.writer",
    "plan_snapshot": "src.export.writer",
    "serialize_payload": "src.export.writer",
    "zip_path": "src.export.writer",
    "read_manifest": "src.export.filesystem",
    "verify_written_files": "src.export.filesystem",
    "write_planned_files": "src.export.filesystem",
    "list_artifact_paths": "src.export.local_store",
    "load_evidence_card": "src.export.local_store",
    "load_manifest": "src.export.local_store",
    "load_member_profile": "src.export.local_store",
    "load_zip_feed": "src.export.local_store",
}


def __getattr__(name: str) -> object:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module 'src.export' has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


__all__ = [
    "CommitteeMembership",
    "ConfidenceLabel",
    "EvidenceBlock",
    "EvidenceCardPayload",
    "EvidenceSection",
    "ManifestEntry",
    "MemberProfilePayload",
    "PlannedFile",
    "RecentRuleFire",
    "ScoreSummary",
    "SnapshotManifest",
    "SourceAnchor",
    "ZipFeedPayload",
    "ZipMemberSummary",
    "build_evidence_card",
    "build_manifest",
    "build_member_profile",
    "build_zip_feed",
    "evidence_path",
    "list_artifact_paths",
    "load_evidence_card",
    "load_manifest",
    "load_member_profile",
    "load_zip_feed",
    "manifest_path",
    "member_path",
    "plan_snapshot",
    "read_manifest",
    "serialize_payload",
    "sha256_hex",
    "verify_written_files",
    "write_planned_files",
    "zip_path",
]
