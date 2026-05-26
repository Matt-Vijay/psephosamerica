from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.runtime.prediction_operator_resume_run import (
    _attach_optional_output,
    verify_prediction_operator_resume_run,
)


def test_attach_optional_output_does_not_write_directly_to_final_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "resume-verify.json"
    result = {"ok": True, "command": "verify-prediction-operator-resume-run"}
    original_write_text = Path.write_text

    def guarded_write_text(path: Path, data: str, *args: object, **kwargs: object) -> int:
        if path == output:
            raise AssertionError("direct final-path write")
        return original_write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", guarded_write_text)

    annotated = _attach_optional_output(SimpleNamespace(output=str(output)), result)

    assert annotated is result
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "ok": True,
        "command": "verify-prediction-operator-resume-run",
    }
    assert result["output"] == str(output)
    assert result["output_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_verify_prediction_operator_resume_run_rejects_malformed_source_hash(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    source_hashes = _packet_source_hashes(packet_dir)
    source_hashes["checksums"] = "not-a-sha256"
    artifact = _write_resume_artifact(tmp_path, source_hashes)

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=True,
            require_dry_run=True,
            require_no_secret_literals=True,
            output=None,
        )
    )

    assert result["ok"] is False
    assert result["source_hash_invalid"] == ["checksums"]
    assert result["source_hash_invalid_count"] == 1
    assert "source_hash_invalid:checksums" in result["issues"]
    assert "source_hash_mismatch:checksums" not in result["issues"]


def test_verify_prediction_operator_resume_run_rejects_unexpected_source_hash_key(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    source_hashes = _packet_source_hashes(packet_dir)
    source_hashes["unverified_prediction_snapshot"] = "0" * 64
    artifact = _write_resume_artifact(tmp_path, source_hashes)

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=True,
            require_dry_run=True,
            require_no_secret_literals=True,
            output=None,
        )
    )

    assert result["ok"] is False
    assert "source_hash_unexpected:unverified_prediction_snapshot" in result["issues"]
    assert result["source_hash_mismatch_count"] == 0


def test_verify_prediction_operator_resume_run_rejects_malformed_dotenv_hash(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    dotenv = tmp_path / ".env"
    dotenv.write_text("OPENAI_API_KEY=placeholder\n", encoding="utf-8")
    source_hashes = _packet_source_hashes(packet_dir)
    source_hashes["dotenv"] = "bad"
    artifact = _write_resume_artifact(tmp_path, source_hashes)

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=str(dotenv),
            require_run_metadata=True,
            require_ok=True,
            require_dry_run=True,
            require_no_secret_literals=True,
            output=None,
        )
    )

    assert result["ok"] is False
    assert result["source_hash_invalid"] == ["dotenv"]
    assert result["source_hash_invalid_count"] == 1
    assert "dotenv_hash_invalid" in result["issues"]
    assert "dotenv_hash_mismatch" not in result["issues"]


def test_verify_prediction_operator_resume_run_resolves_manifest_resume_script(
    tmp_path: Path,
) -> None:
    packet_dir = tmp_path / "packet"
    packet_dir.mkdir()
    files_dir = packet_dir / "files"
    files_dir.mkdir()
    stale_direct_script = files_dir / "resume_script.sh"
    stale_direct_script.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    resume_script = files_dir / "prediction-resume-commands.sh"
    resume_script.write_text("#!/bin/sh\n", encoding="utf-8")
    (packet_dir / "packet-export-manifest.json").write_text(
        json.dumps(
            {
                "exported_files": [
                    {
                        "name": "resume_script",
                        "exported_path": "/old/location/files/prediction-resume-commands.sh",
                        "exported_sha256": _sha256(resume_script),
                        "bytes": resume_script.stat().st_size,
                    }
                ]
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (packet_dir / "SHA256SUMS").write_text("placeholder\n", encoding="utf-8")
    (packet_dir / "resume_plan.py").write_text("COMMANDS = []\n", encoding="utf-8")
    (packet_dir / "run_resume.py").write_text("print('resume')\n", encoding="utf-8")
    artifact = _write_resume_artifact(
        tmp_path,
        {
            "packet_export_manifest": _sha256(packet_dir / "packet-export-manifest.json"),
            "checksums": _sha256(packet_dir / "SHA256SUMS"),
            "resume_plan": _sha256(packet_dir / "resume_plan.py"),
            "run_resume": _sha256(packet_dir / "run_resume.py"),
            "resume_script": _sha256(resume_script),
        },
    )

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=True,
            require_dry_run=True,
            require_no_secret_literals=True,
            output=None,
        )
    )

    assert result["ok"] is True
    assert result["source_file_missing"] == []
    assert result["source_hash_mismatches"] == []


def test_verify_prediction_operator_resume_run_rejects_negative_selected_counts(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    artifact = _write_resume_artifact(tmp_path, _packet_source_hashes(packet_dir))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["selected_phase_count"] = -1
    payload["run_metadata"]["source_state"]["selected_phase_count"] = -1
    artifact.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=True,
            require_dry_run=True,
            require_no_secret_literals=True,
            output=None,
        )
    )

    assert result["ok"] is False
    assert "selected_phase_count must be a non-negative integer" in result["issues"]
    assert "selected_phase_count_mismatch" not in result["issues"]
    assert "run_metadata_source_state_mismatch" not in result["issues"]


def test_verify_prediction_operator_resume_run_rejects_malformed_selected_phases(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    artifact = _write_resume_artifact(tmp_path, _packet_source_hashes(packet_dir))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["selected_phases"] = [True, "phase-1"]
    artifact.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=True,
            require_dry_run=True,
            require_no_secret_literals=True,
            output=None,
        )
    )

    assert result["ok"] is False
    assert "selected_phases must be a list of integers" in result["issues"]
    assert "selected_phase_count_mismatch" not in result["issues"]
    assert "run_metadata_verification_flags_mismatch" not in result["issues"]


def test_verify_prediction_operator_resume_run_rejects_blank_selected_provenance(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    artifact = _write_resume_artifact(tmp_path, _packet_source_hashes(packet_dir))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["plan"] = {
        "phases": [
            {
                "phase": 1,
                "commands": [],
                "source_artifacts": ["prediction-eval-manifest.json", ""],
                "additional_reasons": ["ready", "   "],
                "missing_env": ["OPENAI_API_KEY", ""],
            }
        ]
    }
    payload["selected_phases"] = [1]
    payload["selected_phase_count"] = 1
    payload["selected_command_count"] = 0
    payload["selected_source_artifacts"] = ["prediction-eval-manifest.json", ""]
    payload["selected_additional_reasons"] = ["ready", "   "]
    payload["selected_missing_env"] = ["", "OPENAI_API_KEY"]
    payload["run_metadata"]["verification_flags"]["selected_phases"] = [1]
    payload["run_metadata"]["source_state"] |= {
        "selected_phase_count": 1,
        "selected_command_count": 0,
        "selected_source_artifact_count": 2,
        "selected_additional_reason_count": 2,
        "selected_missing_env_count": 2,
    }
    artifact.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=True,
            require_dry_run=True,
            require_no_secret_literals=True,
            output=None,
        )
    )

    assert result["ok"] is False
    assert "selected_source_artifacts must contain non-empty strings" in result["issues"]
    assert "selected_additional_reasons must contain non-empty strings" in result["issues"]
    assert "selected_missing_env must contain non-empty strings" in result["issues"]
    assert "plan phases source_artifacts must contain non-empty strings" in result["issues"]
    assert "plan phases additional_reasons must contain non-empty strings" in result["issues"]
    assert "plan phases missing_env must contain non-empty strings" in result["issues"]


def test_verify_prediction_operator_resume_run_requires_selected_artifact_hash(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    artifact = _write_resume_artifact(tmp_path, _packet_source_hashes(packet_dir))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["plan"] = {
        "phases": [
            {
                "phase": 1,
                "commands": [],
                "source_artifacts": ["unhashed-eval-manifest.json"],
                "additional_reasons": [],
                "missing_env": [],
            }
        ]
    }
    payload["selected_phases"] = [1]
    payload["selected_phase_count"] = 1
    payload["selected_source_artifacts"] = ["unhashed-eval-manifest.json"]
    payload["run_metadata"]["verification_flags"]["selected_phases"] = [1]
    payload["run_metadata"]["source_state"] |= {
        "selected_phase_count": 1,
        "selected_source_artifact_count": 1,
    }
    payload["run_metadata"]["selected_source_artifact_sha256"] = {}
    artifact.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=True,
            require_dry_run=True,
            require_no_secret_literals=True,
            output=None,
        )
    )

    assert result["ok"] is False
    assert "selected_source_artifact_missing_hash:unhashed-eval-manifest.json" in result["issues"]


def test_verify_prediction_operator_resume_run_reports_selected_artifact_hash_count(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    artifact = _write_resume_artifact(tmp_path, _packet_source_hashes(packet_dir))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["plan"] = {
        "phases": [
            {
                "phase": 1,
                "commands": [],
                "source_artifacts": ["prediction-eval-manifest.json"],
                "additional_reasons": [],
                "missing_env": [],
            }
        ]
    }
    payload["selected_phases"] = [1]
    payload["selected_phase_count"] = 1
    payload["selected_source_artifacts"] = ["prediction-eval-manifest.json"]
    payload["run_metadata"]["verification_flags"]["selected_phases"] = [1]
    payload["run_metadata"]["selected_source_artifact_sha256"] = {
        "prediction-eval-manifest.json": "0" * 64
    }
    payload["run_metadata"]["source_state"] |= {
        "selected_phase_count": 1,
        "selected_source_artifact_count": 1,
    }
    artifact.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=True,
            require_dry_run=True,
            require_no_secret_literals=True,
            output=None,
        )
    )

    assert result["ok"] is True
    assert result["run_metadata"]["source_state"]["selected_source_artifact_hash_count"] == 1


def test_verify_prediction_operator_resume_run_reports_selected_artifact_hash_coverage(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    artifact = _write_resume_artifact(tmp_path, _packet_source_hashes(packet_dir))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["plan"] = {
        "phases": [
            {
                "phase": 1,
                "commands": [],
                "source_artifacts": [
                    "prediction-eval-manifest.json",
                    "prediction-input-inventory.json",
                ],
                "additional_reasons": [],
                "missing_env": [],
            }
        ]
    }
    payload["selected_phases"] = [1]
    payload["selected_phase_count"] = 1
    payload["selected_source_artifacts"] = [
        "prediction-eval-manifest.json",
        "prediction-input-inventory.json",
    ]
    payload["run_metadata"]["verification_flags"]["selected_phases"] = [1]
    payload["run_metadata"]["selected_source_artifact_sha256"] = {
        "prediction-eval-manifest.json": "0" * 64
    }
    payload["run_metadata"]["source_state"] |= {
        "selected_phase_count": 1,
        "selected_source_artifact_count": 2,
    }
    artifact.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=False,
            require_dry_run=True,
            require_no_secret_literals=True,
            output=None,
        )
    )

    source_state = result["run_metadata"]["source_state"]
    assert result["ok"] is False
    assert source_state["selected_source_artifact_hash_count"] == 1
    assert source_state["selected_source_artifact_hash_missing_count"] == 1
    assert source_state["selected_source_artifact_hash_coverage_complete"] is False


def test_verify_prediction_operator_resume_run_can_require_selected_artifact_hash_coverage(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    artifact = _write_resume_artifact(tmp_path, _packet_source_hashes(packet_dir))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["plan"] = {
        "phases": [
            {
                "phase": 1,
                "commands": [],
                "source_artifacts": [
                    "prediction-eval-manifest.json",
                    "prediction-input-inventory.json",
                ],
                "additional_reasons": [],
                "missing_env": [],
            }
        ]
    }
    payload["selected_phases"] = [1]
    payload["selected_phase_count"] = 1
    payload["selected_source_artifacts"] = [
        "prediction-eval-manifest.json",
        "prediction-input-inventory.json",
    ]
    payload["run_metadata"]["verification_flags"]["selected_phases"] = [1]
    payload["run_metadata"]["selected_source_artifact_sha256"] = {
        "prediction-eval-manifest.json": "0" * 64
    }
    payload["run_metadata"]["source_state"] |= {
        "selected_phase_count": 1,
        "selected_source_artifact_count": 2,
    }
    artifact.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=False,
            require_dry_run=True,
            require_no_secret_literals=True,
            require_selected_source_artifact_hashes=True,
            output=None,
        )
    )

    assert result["ok"] is False
    assert result["quality_gate_failures"] == ["selected_source_artifact_hashes_incomplete"]
    assert (
        result["run_metadata"]["verification_flags"]["require_selected_source_artifact_hashes"]
        is True
    )


def test_verify_prediction_operator_resume_run_rejects_unselected_artifact_hash(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    artifact = _write_resume_artifact(tmp_path, _packet_source_hashes(packet_dir))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["run_metadata"]["selected_source_artifact_sha256"] = {
        "stale-eval-manifest.json": "0" * 64
    }
    artifact.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=True,
            require_dry_run=True,
            require_no_secret_literals=True,
            output=None,
        )
    )

    assert result["ok"] is False
    assert "selected_source_artifact_hash_unexpected:stale-eval-manifest.json" in result["issues"]


def test_verify_prediction_operator_resume_run_rejects_blank_selected_artifact_hash_key(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    artifact = _write_resume_artifact(tmp_path, _packet_source_hashes(packet_dir))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["run_metadata"]["selected_source_artifact_sha256"] = {" ": "0" * 64}
    artifact.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=True,
            require_dry_run=True,
            require_no_secret_literals=True,
            output=None,
        )
    )

    assert result["ok"] is False
    assert "selected_source_artifact_hash_key_invalid" in result["issues"]


def test_verify_prediction_operator_resume_run_rejects_selected_artifact_hash_mismatch(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    source_artifact = tmp_path / "out" / "prediction-eval-manifest-verify.json"
    source_artifact.parent.mkdir(parents=True)
    source_artifact.write_text('{"ok": true}\n', encoding="utf-8")
    artifact = _write_resume_artifact(tmp_path, _packet_source_hashes(packet_dir))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["repo_root"] = str(tmp_path)
    payload["plan"] = {
        "phases": [
            {
                "phase": 1,
                "commands": [],
                "source_artifacts": ["out/prediction-eval-manifest-verify.json"],
                "additional_reasons": [],
                "missing_env": [],
            }
        ]
    }
    payload["selected_phases"] = [1]
    payload["selected_phase_count"] = 1
    payload["selected_source_artifacts"] = ["out/prediction-eval-manifest-verify.json"]
    payload["run_metadata"]["verification_flags"]["selected_phases"] = [1]
    payload["run_metadata"]["selected_source_artifact_sha256"] = {
        "out/prediction-eval-manifest-verify.json": "0" * 64
    }
    payload["run_metadata"]["source_state"] |= {
        "selected_phase_count": 1,
        "selected_source_artifact_count": 1,
    }
    artifact.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=True,
            require_dry_run=True,
            require_no_secret_literals=True,
            output=None,
        )
    )

    assert result["ok"] is False
    assert (
        "selected_source_artifact_hash_mismatch:out/prediction-eval-manifest-verify.json"
        in result["issues"]
    )


def test_verify_prediction_operator_resume_run_rejects_negative_source_state_counts(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    artifact = _write_resume_artifact(tmp_path, _packet_source_hashes(packet_dir))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["run_metadata"]["source_state"]["selected_source_artifact_count"] = -1
    artifact.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=True,
            require_dry_run=True,
            require_no_secret_literals=True,
            output=None,
        )
    )

    assert result["ok"] is False
    assert (
        "run_metadata source_state selected_source_artifact_count must be a non-negative integer"
        in result["issues"]
    )
    assert "run_metadata_source_state_mismatch" not in result["issues"]


def test_verify_prediction_operator_resume_run_rejects_unexpected_source_state_scope_ids(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    artifact = _write_resume_artifact(tmp_path, _packet_source_hashes(packet_dir))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["run_metadata"]["source_state"]["jurisdiction_ids"] = ["state_ca", "us_congress"]
    artifact.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=True,
            require_dry_run=True,
            require_no_secret_literals=True,
            output=None,
        )
    )

    assert result["ok"] is False
    assert "run_metadata source_state unexpected: jurisdiction_ids" in result["issues"]
    assert "run_metadata_source_state_mismatch" not in result["issues"]


def test_verify_prediction_operator_resume_run_preserves_selected_sample_scope(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    artifact = _write_resume_artifact(tmp_path, _packet_source_hashes(packet_dir))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["run_metadata"]["source_state"] |= {
        "selected_sample_vote_event_ids": [101, 102],
        "selected_sample_bill_keys": ["us_congress:hr:118:1"],
        "selected_sample_member_bioguide_ids": ["A000055"],
        "selected_sample_jurisdiction_ids": ["us_congress"],
        "selected_sample_legislative_body_ids": ["us_congress:house"],
        "selected_sample_legislative_session_ids": ["us_congress:house:118"],
        "selected_sample_source_family_ids": ["congress_vote", "vote_history"],
    }
    artifact.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=True,
            require_dry_run=True,
            require_no_secret_literals=True,
            output=None,
        )
    )

    assert result["ok"] is True
    assert result["source_state_matches"] is True
    assert result["run_metadata"]["source_state"]["selected_sample_vote_event_ids"] == [101, 102]
    assert result["run_metadata"]["source_state"]["selected_sample_bill_keys"] == [
        "us_congress:hr:118:1"
    ]
    assert result["run_metadata"]["source_state"]["selected_sample_jurisdiction_ids"] == [
        "us_congress"
    ]
    assert result["run_metadata"]["source_state"]["selected_sample_source_family_ids"] == [
        "congress_vote",
        "vote_history",
    ]


def test_verify_prediction_operator_resume_run_rejects_malformed_selected_sample_scope(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    artifact = _write_resume_artifact(tmp_path, _packet_source_hashes(packet_dir))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["run_metadata"]["source_state"] |= {
        "selected_sample_vote_event_ids": [101, True],
        "selected_sample_bill_keys": ["us_congress:hr:118:1", "us_congress:hr:118:1"],
        "selected_sample_member_bioguide_ids": [" A000055"],
        "selected_sample_source_family_ids": ["congress_vote", "congress_vote"],
    }
    artifact.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=True,
            require_dry_run=True,
            require_no_secret_literals=True,
            output=None,
        )
    )

    assert result["ok"] is False
    assert (
        "run_metadata source_state selected_sample_vote_event_ids must be a list of non-negative integers"
        in result["issues"]
    )
    assert (
        "run_metadata source_state selected_sample_bill_keys must be a sorted unique list of non-empty strings"
        in result["issues"]
    )
    assert (
        "run_metadata source_state selected_sample_member_bioguide_ids must be a sorted unique list of non-empty strings"
        in result["issues"]
    )
    assert (
        "run_metadata source_state selected_sample_source_family_ids must be a sorted unique list of non-empty strings"
        in result["issues"]
    )
    assert "run_metadata_source_state_mismatch" not in result["issues"]


def test_verify_prediction_operator_resume_run_rejects_unscoped_selected_sample_body_ids(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    artifact = _write_resume_artifact(tmp_path, _packet_source_hashes(packet_dir))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["run_metadata"]["source_state"] |= {
        "selected_sample_jurisdiction_ids": ["state_ca"],
        "selected_sample_legislative_body_ids": ["ca_assembly"],
        "selected_sample_legislative_session_ids": ["state_ca:2025_regular"],
    }
    artifact.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=True,
            require_dry_run=True,
            require_no_secret_literals=True,
            output=None,
        )
    )

    assert result["ok"] is False
    assert (
        "run_metadata source_state selected_sample_legislative_body_ids must contain scoped ids"
        in result["issues"]
    )
    assert (
        "run_metadata source_state selected_sample_legislative_session_ids must contain scoped ids"
        in result["issues"]
    )
    assert "run_metadata_source_state_mismatch" not in result["issues"]


def test_verify_prediction_operator_resume_run_rejects_malformed_sample_source_family_ids(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    artifact = _write_resume_artifact(tmp_path, _packet_source_hashes(packet_dir))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["run_metadata"]["source_state"] |= {
        "selected_sample_source_family_ids": ["Congress Vote"],
    }
    artifact.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=True,
            require_dry_run=True,
            require_no_secret_literals=True,
            output=None,
        )
    )

    assert result["ok"] is False
    assert (
        "run_metadata source_state selected_sample_source_family_ids must contain normalized source family ids"
        in result["issues"]
    )
    assert "run_metadata_source_state_mismatch" not in result["issues"]


def test_verify_prediction_operator_resume_run_rejects_malformed_congress_source_state(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    artifact = _write_resume_artifact(tmp_path, _packet_source_hashes(packet_dir))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["run_metadata"]["source_state"] |= {
        "congress_load_required": "yes",
        "congress_load_prediction_vote_inputs_available": 1,
        "congress_load_source_family_ids": ["congress_vote", "congress_vote"],
        "congress_load_vote_event_row_count": True,
        "congress_load_vote_cast_row_count": -1,
    }
    artifact.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=True,
            require_dry_run=True,
            require_no_secret_literals=True,
            output=None,
        )
    )

    assert result["ok"] is False
    assert "run_metadata source_state congress_load_required must be a boolean" in result["issues"]
    assert (
        "run_metadata source_state congress_load_prediction_vote_inputs_available must be a boolean"
        in result["issues"]
    )
    assert (
        "run_metadata source_state congress_load_source_family_ids must be a sorted unique list of non-empty strings"
        in result["issues"]
    )
    assert (
        "run_metadata source_state congress_load_vote_event_row_count must be an integer"
        in result["issues"]
    )
    assert (
        "run_metadata source_state congress_load_vote_cast_row_count must be a non-negative integer"
        in result["issues"]
    )
    assert "run_metadata_source_state_mismatch" not in result["issues"]


def test_verify_prediction_operator_resume_run_rejects_malformed_congress_source_family_ids(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    artifact = _write_resume_artifact(tmp_path, _packet_source_hashes(packet_dir))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["run_metadata"]["source_state"]["congress_load_source_family_ids"] = ["Congress Vote"]
    artifact.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=True,
            require_dry_run=True,
            require_no_secret_literals=True,
            output=None,
        )
    )

    assert result["ok"] is False
    assert (
        "run_metadata source_state congress_load_source_family_ids must contain normalized source family ids"
        in result["issues"]
    )
    assert "run_metadata_source_state_mismatch" not in result["issues"]


def test_verify_prediction_operator_resume_run_reports_congress_source_state(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    artifact = _write_resume_artifact(tmp_path, _packet_source_hashes(packet_dir))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["run_metadata"]["source_state"] |= {
        "congress_load_required": True,
        "congress_load_present": True,
        "congress_load_ok": False,
        "congress_load_prediction_member_inputs_available": True,
        "congress_load_prediction_bill_inputs_available": True,
        "congress_load_prediction_vote_inputs_available": False,
        "congress_load_source_family_ids": ["committee_membership", "congress_bill"],
    }
    artifact.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=True,
            require_dry_run=True,
            require_no_secret_literals=True,
            output=None,
        )
    )

    source_state = result["run_metadata"]["source_state"]
    assert result["ok"] is True
    assert result["source_state_matches"] is True
    assert source_state["congress_load_required"] is True
    assert source_state["congress_load_present"] is True
    assert source_state["congress_load_ok"] is False
    assert source_state["congress_load_prediction_member_inputs_available"] is True
    assert source_state["congress_load_prediction_bill_inputs_available"] is True
    assert source_state["congress_load_prediction_vote_inputs_available"] is False
    assert source_state["congress_load_source_family_ids"] == [
        "committee_membership",
        "congress_bill",
    ]


def test_verify_prediction_operator_resume_run_can_require_congress_prediction_inputs(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    artifact = _write_resume_artifact(tmp_path, _packet_source_hashes(packet_dir))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["run_metadata"]["source_state"] |= {
        "congress_load_required": True,
        "congress_load_present": True,
        "congress_load_ok": True,
        "congress_load_prediction_member_inputs_available": True,
        "congress_load_prediction_bill_inputs_available": True,
        "congress_load_prediction_vote_inputs_available": True,
        "congress_load_source_family_ids": [
            "committee_membership",
            "congress_bill",
            "congress_vote",
        ],
        "congress_load_source_family_count": 3,
        "congress_load_member_row_count": 10,
        "congress_load_bill_row_count": 7,
        "congress_load_vote_event_row_count": 5,
        "congress_load_vote_cast_row_count": 500,
    }
    artifact.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=True,
            require_dry_run=True,
            require_no_secret_literals=True,
            require_congress_prediction_inputs=True,
            output=None,
        )
    )

    assert result["ok"] is True
    assert result["quality_gate_failures"] == []
    assert (
        result["run_metadata"]["verification_flags"]["require_congress_prediction_inputs"] is True
    )
    assert result["run_metadata"]["source_state"]["congress_prediction_inputs_ready"] is True


def test_verify_prediction_operator_resume_run_rejects_missing_congress_prediction_inputs(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    artifact = _write_resume_artifact(tmp_path, _packet_source_hashes(packet_dir))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["run_metadata"]["source_state"] |= {
        "congress_load_required": True,
        "congress_load_present": True,
        "congress_load_ok": True,
        "congress_load_prediction_member_inputs_available": True,
        "congress_load_prediction_bill_inputs_available": True,
        "congress_load_prediction_vote_inputs_available": False,
        "congress_load_source_family_ids": ["committee_membership", "congress_bill"],
        "congress_load_source_family_count": 2,
        "congress_load_member_row_count": 10,
        "congress_load_bill_row_count": 7,
        "congress_load_vote_event_row_count": 0,
        "congress_load_vote_cast_row_count": 0,
    }
    artifact.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=True,
            require_dry_run=True,
            require_no_secret_literals=True,
            require_congress_prediction_inputs=True,
            output=None,
        )
    )

    assert result["ok"] is False
    assert result["quality_gate_failures"] == ["congress_prediction_inputs_not_ready"]
    assert result["run_metadata"]["source_state"]["congress_prediction_inputs_ready"] is False


def test_verify_prediction_operator_resume_run_rejects_missing_congress_source_families(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    artifact = _write_resume_artifact(tmp_path, _packet_source_hashes(packet_dir))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["run_metadata"]["source_state"] |= {
        "congress_load_required": True,
        "congress_load_present": True,
        "congress_load_ok": True,
        "congress_load_prediction_member_inputs_available": True,
        "congress_load_prediction_bill_inputs_available": True,
        "congress_load_prediction_vote_inputs_available": True,
        "congress_load_source_family_ids": ["committee_membership", "congress_bill"],
        "congress_load_source_family_count": 2,
        "congress_load_member_row_count": 10,
        "congress_load_bill_row_count": 7,
        "congress_load_vote_event_row_count": 5,
        "congress_load_vote_cast_row_count": 500,
    }
    artifact.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=True,
            require_dry_run=True,
            require_no_secret_literals=True,
            require_congress_prediction_inputs=True,
            output=None,
        )
    )

    assert result["ok"] is False
    assert result["quality_gate_failures"] == ["congress_prediction_inputs_not_ready"]
    assert result["run_metadata"]["source_state"]["congress_prediction_inputs_ready"] is False


def test_verify_prediction_operator_resume_run_rejects_congress_inputs_without_row_counts(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    artifact = _write_resume_artifact(tmp_path, _packet_source_hashes(packet_dir))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["run_metadata"]["source_state"] |= {
        "congress_load_required": True,
        "congress_load_present": True,
        "congress_load_ok": True,
        "congress_load_prediction_member_inputs_available": True,
        "congress_load_prediction_bill_inputs_available": True,
        "congress_load_prediction_vote_inputs_available": True,
        "congress_load_source_family_ids": [
            "committee_membership",
            "congress_bill",
            "congress_vote",
        ],
        "congress_load_source_family_count": 3,
        "congress_load_member_row_count": 10,
        "congress_load_bill_row_count": 7,
        "congress_load_vote_event_row_count": 0,
        "congress_load_vote_cast_row_count": 500,
    }
    artifact.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=True,
            require_dry_run=True,
            require_no_secret_literals=True,
            require_congress_prediction_inputs=True,
            output=None,
        )
    )

    assert result["ok"] is False
    assert result["quality_gate_failures"] == ["congress_prediction_inputs_not_ready"]
    assert result["run_metadata"]["source_state"]["congress_prediction_inputs_ready"] is False


def test_verify_prediction_operator_resume_run_rejects_mismatched_congress_source_family_count(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    artifact = _write_resume_artifact(tmp_path, _packet_source_hashes(packet_dir))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["run_metadata"]["source_state"] |= {
        "congress_load_required": True,
        "congress_load_present": True,
        "congress_load_ok": True,
        "congress_load_prediction_member_inputs_available": True,
        "congress_load_prediction_bill_inputs_available": True,
        "congress_load_prediction_vote_inputs_available": True,
        "congress_load_source_family_ids": [
            "committee_membership",
            "congress_bill",
            "congress_vote",
        ],
        "congress_load_source_family_count": 2,
        "congress_load_member_row_count": 10,
        "congress_load_bill_row_count": 7,
        "congress_load_vote_event_row_count": 5,
        "congress_load_vote_cast_row_count": 500,
    }
    artifact.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=True,
            require_dry_run=True,
            require_no_secret_literals=True,
            require_congress_prediction_inputs=True,
            output=None,
        )
    )

    assert result["ok"] is False
    assert result["quality_gate_failures"] == ["congress_prediction_inputs_not_ready"]
    assert result["run_metadata"]["source_state"]["congress_prediction_inputs_ready"] is False


def test_verify_prediction_operator_resume_run_can_require_strict_eval_window_run(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    artifact = _write_resume_artifact(tmp_path, _packet_source_hashes(packet_dir))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["run_metadata"]["source_state"] |= _strict_eval_window_source_state()
    artifact.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=True,
            require_dry_run=True,
            require_no_secret_literals=True,
            require_congress_prediction_inputs=False,
            require_strict_eval_window_run=True,
            output=None,
        )
    )

    assert result["ok"] is True
    assert result["quality_gate_failures"] == []
    assert result["run_metadata"]["verification_flags"]["require_strict_eval_window_run"] is True
    assert result["run_metadata"]["source_state"]["strict_eval_window_run_ready"] is True


def test_verify_prediction_operator_resume_run_rejects_missing_strict_eval_window_run(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    artifact = _write_resume_artifact(tmp_path, _packet_source_hashes(packet_dir))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    strict_state = _strict_eval_window_source_state()
    strict_state["eval_window_run_requires_eval_manifest_official_source_thresholds"] = False
    payload["run_metadata"]["source_state"] |= strict_state
    artifact.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=True,
            require_dry_run=True,
            require_no_secret_literals=True,
            require_congress_prediction_inputs=False,
            require_strict_eval_window_run=True,
            output=None,
        )
    )

    assert result["ok"] is False
    assert result["quality_gate_failures"] == ["strict_eval_window_run_not_ready"]
    assert result["run_metadata"]["source_state"]["strict_eval_window_run_ready"] is False


def test_verify_prediction_operator_resume_run_rejects_malformed_strict_eval_window_state(
    tmp_path: Path,
) -> None:
    packet_dir = _write_packet_files(tmp_path)
    artifact = _write_resume_artifact(tmp_path, _packet_source_hashes(packet_dir))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["run_metadata"]["source_state"] |= _strict_eval_window_source_state()
    payload["run_metadata"]["source_state"][
        "eval_window_run_requires_input_inventory_official_source_thresholds"
    ] = "yes"
    artifact.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = verify_prediction_operator_resume_run(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            artifact=str(artifact),
            dotenv=None,
            require_run_metadata=True,
            require_ok=True,
            require_dry_run=True,
            require_no_secret_literals=True,
            require_congress_prediction_inputs=False,
            require_strict_eval_window_run=False,
            output=None,
        )
    )

    assert result["ok"] is False
    assert (
        "run_metadata source_state "
        "eval_window_run_requires_input_inventory_official_source_thresholds must be a boolean"
        in result["issues"]
    )


def _write_packet_files(tmp_path: Path) -> Path:
    packet_dir = tmp_path / "packet"
    packet_dir.mkdir()
    files_dir = packet_dir / "files"
    files_dir.mkdir()
    (packet_dir / "packet-export-manifest.json").write_text(
        json.dumps({"exported_files": []}, sort_keys=True),
        encoding="utf-8",
    )
    (packet_dir / "SHA256SUMS").write_text("placeholder\n", encoding="utf-8")
    (packet_dir / "resume_plan.py").write_text("COMMANDS = []\n", encoding="utf-8")
    (packet_dir / "run_resume.py").write_text("print('resume')\n", encoding="utf-8")
    (files_dir / "resume_script.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    return packet_dir


def _packet_source_hashes(packet_dir: Path) -> dict[str, str]:
    return {
        "packet_export_manifest": _sha256(packet_dir / "packet-export-manifest.json"),
        "checksums": _sha256(packet_dir / "SHA256SUMS"),
        "resume_plan": _sha256(packet_dir / "resume_plan.py"),
        "run_resume": _sha256(packet_dir / "run_resume.py"),
        "resume_script": _sha256(packet_dir / "files" / "resume_script.sh"),
    }


def _write_resume_artifact(tmp_path: Path, source_hashes: dict[str, str]) -> Path:
    artifact = tmp_path / "run-resume.json"
    artifact.write_text(
        json.dumps(
            {
                "ok": True,
                "command": "run_resume.py",
                "dry_run": True,
                "env_validation": "presence_only",
                "plan": {},
                "selected_phases": [],
                "selected_phase_count": 0,
                "selected_command_count": 0,
                "selected_missing_env": [],
                "selected_source_artifacts": [],
                "selected_additional_reasons": [],
                "run_metadata": {
                    "command": "run_resume.py",
                    "source_artifact_sha256": source_hashes,
                    "verification_flags": {
                        "dry_run": True,
                        "selected_phases": [],
                        "dotenv": None,
                        "output": None,
                    },
                    "source_state": {
                        "packet_verified": False,
                        "plan_launch_ready": False,
                        "plan_missing_env_count": None,
                        "loaded_key_count": None,
                        "selected_phase_count": 0,
                        "selected_command_count": 0,
                        "selected_source_artifact_count": 0,
                        "selected_additional_reason_count": 0,
                        "selected_missing_env_count": 0,
                        "env_validation": "presence_only",
                    },
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return artifact


def _strict_eval_window_source_state() -> dict[str, bool]:
    return {
        "eval_window_run_requires_input_inventory_portable_ids": True,
        "eval_window_run_requires_input_inventory_congress_source_families": True,
        "eval_window_run_requires_input_inventory_official_source_thresholds": True,
        "eval_window_run_requires_input_inventory_optional_evidence": True,
        "eval_window_run_requires_eval_manifest_model_suite": True,
        "eval_window_run_requires_eval_manifest_official_source_thresholds": True,
        "eval_window_run_requires_eval_manifest_unknown_availability_failures": True,
        "eval_window_run_requires_eval_manifest_ontology_feature_signals": True,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
