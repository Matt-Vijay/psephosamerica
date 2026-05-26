from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from src.runtime.prediction_source_url_audit import verify_prediction_source_url_audit_command


def test_verify_prediction_source_url_audit_requires_scope_fields_for_scoped_gap_samples(
    tmp_path: Path,
) -> None:
    eval_report = tmp_path / "eval-report.json"
    eval_report.write_text("{}", encoding="utf-8")
    artifact = tmp_path / "source-url-audit.json"
    artifact.write_text(
        json.dumps(
            {
                "ok": True,
                "command": "prediction-source-url-audit",
                "eval_report_path": str(eval_report),
                "eval_report_sha256": hashlib.sha256(eval_report.read_bytes()).hexdigest(),
                "checked": 1,
                "gap_count": 1,
                "missing_url_sourced_prediction_count": 1,
                "missing_official_source_prediction_count": 1,
                "gaps": [
                    {
                        "split": "evaluation",
                        "model_name": "ontology_signal_model",
                        "signal_name": "member_vote_history",
                        "prediction_count": 1,
                        "url_sourced_prediction_count": 0,
                        "missing_url_sourced_prediction_count": 1,
                        "url_source_coverage_rate": 0.0,
                        "official_source_sourced_prediction_count": 0,
                        "missing_official_source_prediction_count": 1,
                        "official_source_coverage_rate": 0.0,
                        "sample_vote_event_ids": [20],
                        "sample_bill_keys": ["119-hr-2"],
                        "sample_member_bioguide_ids": ["B000002"],
                        "sample_cases": [
                            {
                                "vote_event_id": 20,
                                "event_key": "assembly-2025-1-20",
                                "jurisdiction_id": "state_ca",
                                "legislative_body_id": "ca_assembly",
                                "legislative_session_id": "2025_regular",
                                "bill_key": "119-hr-2",
                                "member_bioguide_id": "B000002",
                            },
                        ],
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = verify_prediction_source_url_audit_command(
        SimpleNamespace(
            artifact=str(artifact),
            require_run_metadata=False,
            require_no_gaps=False,
            require_no_official_source_gaps=False,
            output=None,
        )
    )

    assert result["ok"] is False
    assert "jurisdiction_count missing" in result["issues"]
    assert "jurisdiction_ids missing for jurisdiction_count" in result["issues"]
    assert "legislative_body_count missing" in result["issues"]
    assert "legislative_body_ids missing for legislative_body_count" in result["issues"]
    assert "legislative_session_count missing" in result["issues"]
    assert "legislative_session_ids missing for legislative_session_count" in result["issues"]


def test_verify_prediction_source_url_audit_can_require_portable_sample_context(
    tmp_path: Path,
) -> None:
    eval_report = tmp_path / "eval-report.json"
    eval_report.write_text("{}", encoding="utf-8")
    artifact = tmp_path / "source-url-audit.json"
    artifact.write_text(
        json.dumps(
            {
                "ok": False,
                "command": "prediction-source-url-audit",
                "eval_report_path": str(eval_report),
                "eval_report_sha256": hashlib.sha256(eval_report.read_bytes()).hexdigest(),
                "checked": 1,
                "gap_count": 1,
                "jurisdiction_count": 1,
                "implemented_jurisdiction_count": 0,
                "portable_jurisdiction_count": 1,
                "portable_sample_missing_body_id_count": 1,
                "portable_sample_missing_session_id_count": 1,
                "legislative_body_count": 0,
                "legislative_session_count": 0,
                "jurisdiction_ids": ["state_ca"],
                "implemented_jurisdiction_ids": [],
                "portable_jurisdiction_ids": ["state_ca"],
                "missing_url_sourced_prediction_count": 1,
                "missing_official_source_prediction_count": 1,
                "gaps": [
                    {
                        "split": "evaluation",
                        "model_name": "ontology_signal_model",
                        "signal_name": "member_vote_history",
                        "prediction_count": 1,
                        "url_sourced_prediction_count": 0,
                        "missing_url_sourced_prediction_count": 1,
                        "url_source_coverage_rate": 0.0,
                        "official_source_sourced_prediction_count": 0,
                        "missing_official_source_prediction_count": 1,
                        "official_source_coverage_rate": 0.0,
                        "sample_vote_event_ids": [20],
                        "sample_bill_keys": ["state_ca:2025:ab:1"],
                        "sample_member_bioguide_ids": ["B000002"],
                        "sample_cases": [
                            {
                                "vote_event_id": 20,
                                "event_key": "assembly-2025-1-20",
                                "jurisdiction_id": "state_ca",
                                "bill_key": "state_ca:2025:ab:1",
                                "member_bioguide_id": "B000002",
                            },
                        ],
                    }
                ],
                "quality_gate_failures": [],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = verify_prediction_source_url_audit_command(
        SimpleNamespace(
            artifact=str(artifact),
            require_run_metadata=False,
            require_no_gaps=True,
            require_no_official_source_gaps=True,
            require_no_portable_context_gaps=False,
            output=None,
        )
    )

    assert result["ok"] is False
    assert result["quality_gate_failures"] == [
        "feature_source_url_gaps",
        "feature_source_official_url_gaps",
        "portable_sample_missing_body_id_gaps",
        "portable_sample_missing_session_id_gaps",
    ]


def test_verify_prediction_source_url_audit_can_require_only_portable_context(
    tmp_path: Path,
) -> None:
    eval_report = tmp_path / "eval-report.json"
    eval_report.write_text("{}", encoding="utf-8")
    artifact = tmp_path / "source-url-audit.json"
    artifact.write_text(
        json.dumps(
            {
                "ok": True,
                "command": "prediction-source-url-audit",
                "eval_report_path": str(eval_report),
                "eval_report_sha256": hashlib.sha256(eval_report.read_bytes()).hexdigest(),
                "checked": 1,
                "gap_count": 1,
                "jurisdiction_count": 1,
                "implemented_jurisdiction_count": 0,
                "portable_jurisdiction_count": 1,
                "portable_sample_missing_body_id_count": 1,
                "portable_sample_missing_session_id_count": 1,
                "legislative_body_count": 0,
                "legislative_session_count": 0,
                "jurisdiction_ids": ["state_ca"],
                "implemented_jurisdiction_ids": [],
                "portable_jurisdiction_ids": ["state_ca"],
                "missing_url_sourced_prediction_count": 0,
                "missing_official_source_prediction_count": 0,
                "gaps": [
                    {
                        "split": "evaluation",
                        "model_name": "ontology_signal_model",
                        "signal_name": "member_vote_history",
                        "prediction_count": 1,
                        "url_sourced_prediction_count": 1,
                        "missing_url_sourced_prediction_count": 0,
                        "url_source_coverage_rate": 1.0,
                        "official_source_sourced_prediction_count": 1,
                        "missing_official_source_prediction_count": 0,
                        "official_source_coverage_rate": 1.0,
                        "sample_vote_event_ids": [20],
                        "sample_bill_keys": ["state_ca:2025:ab:1"],
                        "sample_member_bioguide_ids": ["B000002"],
                        "sample_cases": [
                            {
                                "vote_event_id": 20,
                                "event_key": "assembly-2025-1-20",
                                "jurisdiction_id": "state_ca",
                                "bill_key": "state_ca:2025:ab:1",
                                "member_bioguide_id": "B000002",
                            },
                        ],
                    }
                ],
                "quality_gate_failures": [],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = verify_prediction_source_url_audit_command(
        SimpleNamespace(
            artifact=str(artifact),
            require_run_metadata=False,
            require_no_gaps=False,
            require_no_official_source_gaps=False,
            require_no_portable_context_gaps=True,
            output=None,
        )
    )

    assert result["ok"] is False
    assert result["quality_gate_failures"] == [
        "portable_sample_missing_body_id_gaps",
        "portable_sample_missing_session_id_gaps",
    ]
    assert result["run_metadata"]["verification_flags"]["require_no_portable_context_gaps"] is True


def test_verify_prediction_source_url_audit_rejects_missing_fail_on_gap_failures(
    tmp_path: Path,
) -> None:
    eval_report = tmp_path / "eval-report.json"
    eval_report.write_text("{}", encoding="utf-8")
    eval_report_sha256 = hashlib.sha256(eval_report.read_bytes()).hexdigest()
    artifact = tmp_path / "source-url-audit.json"
    source_state = {
        "eval_report_path": str(eval_report),
        "eval_report_sha256": eval_report_sha256,
        "gap_count": 1,
        "missing_url_sourced_prediction_count": 1,
        "missing_official_source_prediction_count": 1,
        "jurisdiction_count": 1,
        "jurisdiction_ids": ["state_ca"],
        "portable_jurisdiction_count": 1,
        "portable_jurisdiction_ids": ["state_ca"],
        "portable_sample_missing_body_id_count": 1,
        "sample_case_count": 1,
        "sample_vote_event_ids": [20],
        "sample_bill_keys": ["state_ca:2025:ab:1"],
        "sample_member_bioguide_ids": ["B000002"],
        "sample_source_family_ids": ["congress_vote"],
        "sample_jurisdiction_ids": ["state_ca"],
        "sample_legislative_body_ids": [],
        "sample_legislative_session_ids": [],
        "source_family_count": 1,
        "source_family_ids": ["congress_vote"],
    }
    artifact.write_text(
        json.dumps(
            {
                "ok": True,
                "command": "prediction-source-url-audit",
                "eval_report_path": str(eval_report),
                "eval_report_sha256": eval_report_sha256,
                "checked": 1,
                "gap_count": 1,
                "jurisdiction_count": 1,
                "portable_jurisdiction_count": 1,
                "portable_sample_missing_body_id_count": 1,
                "jurisdiction_ids": ["state_ca"],
                "portable_jurisdiction_ids": ["state_ca"],
                "source_family_count": 1,
                "source_family_ids": ["congress_vote"],
                "missing_url_sourced_prediction_count": 1,
                "missing_official_source_prediction_count": 1,
                "quality_gate_failures": [],
                "gaps": [
                    {
                        "split": "evaluation",
                        "model_name": "ontology_signal_model",
                        "signal_name": "member_vote_history",
                        "prediction_count": 1,
                        "url_sourced_prediction_count": 0,
                        "missing_url_sourced_prediction_count": 1,
                        "url_source_coverage_rate": 0.0,
                        "official_source_sourced_prediction_count": 0,
                        "missing_official_source_prediction_count": 1,
                        "official_source_coverage_rate": 0.0,
                        "source_family_ids": ["congress_vote"],
                        "sample_vote_event_ids": [20],
                        "sample_bill_keys": ["state_ca:2025:ab:1"],
                        "sample_member_bioguide_ids": ["B000002"],
                        "sample_cases": [
                            {
                                "vote_event_id": 20,
                                "event_key": "assembly-2025-1-20",
                                "jurisdiction_id": "state_ca",
                                "bill_key": "state_ca:2025:ab:1",
                                "member_bioguide_id": "B000002",
                            },
                        ],
                    }
                ],
                "run_metadata": {
                    "command": "prediction-source-url-audit",
                    "artifact_sha256": eval_report_sha256,
                    "source_artifact_sha256": {"eval_report": eval_report_sha256},
                    "verification_flags": {"fail_on_gaps": True},
                    "source_state": source_state,
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = verify_prediction_source_url_audit_command(
        SimpleNamespace(
            artifact=str(artifact),
            require_run_metadata=True,
            require_no_gaps=False,
            require_no_official_source_gaps=False,
            output=None,
        )
    )

    assert result["ok"] is False
    assert (
        "run_metadata verification_flags mismatch: fail_on_gaps quality_gate_failures"
        in (result["issues"])
    )


def test_verify_prediction_source_url_audit_requires_flags_with_required_run_metadata(
    tmp_path: Path,
) -> None:
    eval_report = tmp_path / "eval-report.json"
    eval_report.write_text("{}", encoding="utf-8")
    eval_report_sha256 = hashlib.sha256(eval_report.read_bytes()).hexdigest()
    artifact = tmp_path / "source-url-audit.json"
    source_state = {
        "eval_report_path": str(eval_report),
        "eval_report_sha256": eval_report_sha256,
        "gap_count": 0,
        "missing_url_sourced_prediction_count": 0,
        "missing_official_source_prediction_count": 0,
    }
    artifact.write_text(
        json.dumps(
            {
                "ok": True,
                "command": "prediction-source-url-audit",
                "eval_report_path": str(eval_report),
                "eval_report_sha256": eval_report_sha256,
                "checked": 1,
                "gap_count": 0,
                "missing_url_sourced_prediction_count": 0,
                "missing_official_source_prediction_count": 0,
                "quality_gate_failures": [],
                "gaps": [],
                "run_metadata": {
                    "command": "prediction-source-url-audit",
                    "artifact_sha256": eval_report_sha256,
                    "source_state": source_state,
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = verify_prediction_source_url_audit_command(
        SimpleNamespace(
            artifact=str(artifact),
            require_run_metadata=True,
            require_no_gaps=False,
            require_no_official_source_gaps=False,
            output=None,
        )
    )

    assert result["ok"] is False
    assert "run_metadata verification_flags missing" in result["issues"]
    assert "run_metadata source_artifact_sha256 missing: eval_report" in result["issues"]


def test_verify_prediction_source_url_audit_requires_source_artifact_hash_with_required_run_metadata(
    tmp_path: Path,
) -> None:
    eval_report = tmp_path / "eval-report.json"
    eval_report.write_text("{}", encoding="utf-8")
    eval_report_sha256 = hashlib.sha256(eval_report.read_bytes()).hexdigest()
    artifact = tmp_path / "source-url-audit.json"
    source_state = {
        "eval_report_path": str(eval_report),
        "eval_report_sha256": eval_report_sha256,
        "gap_count": 0,
        "missing_url_sourced_prediction_count": 0,
        "missing_official_source_prediction_count": 0,
    }
    artifact.write_text(
        json.dumps(
            {
                "ok": True,
                "command": "prediction-source-url-audit",
                "eval_report_path": str(eval_report),
                "eval_report_sha256": eval_report_sha256,
                "checked": 1,
                "gap_count": 0,
                "missing_url_sourced_prediction_count": 0,
                "missing_official_source_prediction_count": 0,
                "quality_gate_failures": [],
                "gaps": [],
                "run_metadata": {
                    "command": "prediction-source-url-audit",
                    "artifact_sha256": eval_report_sha256,
                    "verification_flags": {"fail_on_gaps": False},
                    "source_state": source_state,
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = verify_prediction_source_url_audit_command(
        SimpleNamespace(
            artifact=str(artifact),
            require_run_metadata=True,
            require_no_gaps=False,
            require_no_official_source_gaps=False,
            output=None,
        )
    )

    assert result["ok"] is False
    assert result["issues"] == ["run_metadata source_artifact_sha256 missing: eval_report"]


def test_verify_prediction_source_url_audit_rejects_uncanonical_sample_scope_ids(
    tmp_path: Path,
) -> None:
    eval_report = tmp_path / "eval-report.json"
    eval_report.write_text("{}", encoding="utf-8")
    artifact = tmp_path / "source-url-audit.json"
    artifact.write_text(
        json.dumps(
            {
                "ok": True,
                "command": "prediction-source-url-audit",
                "eval_report_path": str(eval_report),
                "eval_report_sha256": hashlib.sha256(eval_report.read_bytes()).hexdigest(),
                "checked": 1,
                "gap_count": 1,
                "jurisdiction_count": 1,
                "implemented_jurisdiction_count": 0,
                "portable_jurisdiction_count": 1,
                "legislative_body_count": 1,
                "legislative_session_count": 1,
                "jurisdiction_ids": ["state_ca"],
                "implemented_jurisdiction_ids": [],
                "portable_jurisdiction_ids": ["state_ca"],
                "legislative_body_ids": ["state_ca:ca_assembly"],
                "legislative_session_ids": ["state_ca:ca_assembly:2025_regular"],
                "missing_url_sourced_prediction_count": 1,
                "missing_official_source_prediction_count": 1,
                "gaps": [
                    {
                        "split": "evaluation",
                        "model_name": "ontology_signal_model",
                        "signal_name": "member_vote_history",
                        "prediction_count": 1,
                        "url_sourced_prediction_count": 0,
                        "missing_url_sourced_prediction_count": 1,
                        "url_source_coverage_rate": 0.0,
                        "official_source_sourced_prediction_count": 0,
                        "missing_official_source_prediction_count": 1,
                        "official_source_coverage_rate": 0.0,
                        "sample_vote_event_ids": [20],
                        "sample_bill_keys": ["119-hr-2"],
                        "sample_member_bioguide_ids": ["B000002"],
                        "sample_cases": [
                            {
                                "vote_event_id": 20,
                                "event_key": "assembly-2025-1-20",
                                "jurisdiction_id": " state_ca ",
                                "legislative_body_id": " ca_assembly ",
                                "legislative_session_id": " 2025_regular ",
                                "bill_key": "119-hr-2",
                                "member_bioguide_id": "B000002",
                            },
                        ],
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = verify_prediction_source_url_audit_command(
        SimpleNamespace(
            artifact=str(artifact),
            require_run_metadata=False,
            require_no_gaps=False,
            require_no_official_source_gaps=False,
            output=None,
        )
    )

    assert result["ok"] is False
    assert (
        "gaps[0].sample_cases[0].jurisdiction_id must not have surrounding whitespace"
        in (result["issues"])
    )
    assert (
        "gaps[0].sample_cases[0].legislative_body_id must not have surrounding whitespace"
        in (result["issues"])
    )
    assert (
        "gaps[0].sample_cases[0].legislative_session_id must not have surrounding whitespace"
        in (result["issues"])
    )


def test_verify_prediction_source_url_audit_rejects_uncanonical_sample_identity_ids(
    tmp_path: Path,
) -> None:
    eval_report = tmp_path / "eval-report.json"
    eval_report.write_text("{}", encoding="utf-8")
    artifact = tmp_path / "source-url-audit.json"
    artifact.write_text(
        json.dumps(
            {
                "ok": True,
                "command": "prediction-source-url-audit",
                "eval_report_path": str(eval_report),
                "eval_report_sha256": hashlib.sha256(eval_report.read_bytes()).hexdigest(),
                "checked": 1,
                "gap_count": 1,
                "missing_url_sourced_prediction_count": 1,
                "missing_official_source_prediction_count": 1,
                "gaps": [
                    {
                        "split": "evaluation",
                        "model_name": "ontology_signal_model",
                        "signal_name": "member_vote_history",
                        "prediction_count": 1,
                        "url_sourced_prediction_count": 0,
                        "missing_url_sourced_prediction_count": 1,
                        "url_source_coverage_rate": 0.0,
                        "official_source_sourced_prediction_count": 0,
                        "missing_official_source_prediction_count": 1,
                        "official_source_coverage_rate": 0.0,
                        "sample_vote_event_ids": [20],
                        "sample_bill_keys": [" 119-hr-2 "],
                        "sample_member_bioguide_ids": [" B000002 "],
                        "sample_cases": [
                            {
                                "vote_event_id": 20,
                                "bill_key": " 119-hr-2 ",
                                "member_bioguide_id": " B000002 ",
                            },
                        ],
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = verify_prediction_source_url_audit_command(
        SimpleNamespace(
            artifact=str(artifact),
            require_run_metadata=False,
            require_no_gaps=False,
            require_no_official_source_gaps=False,
            output=None,
        )
    )

    assert result["ok"] is False
    assert "gaps[0].sample_bill_keys[0] must not have surrounding whitespace" in (result["issues"])
    assert (
        "gaps[0].sample_member_bioguide_ids[0] must not have surrounding whitespace"
        in (result["issues"])
    )
    assert (
        "gaps[0].sample_cases[0].bill_key must not have surrounding whitespace"
        in (result["issues"])
    )
    assert (
        "gaps[0].sample_cases[0].member_bioguide_id must not have surrounding whitespace"
        in result["issues"]
    )


def test_verify_prediction_source_url_audit_rejects_malformed_congress_event_key(
    tmp_path: Path,
) -> None:
    eval_report = tmp_path / "eval-report.json"
    eval_report.write_text("{}", encoding="utf-8")
    artifact = tmp_path / "source-url-audit.json"
    artifact.write_text(
        json.dumps(
            {
                "ok": True,
                "command": "prediction-source-url-audit",
                "eval_report_path": str(eval_report),
                "eval_report_sha256": hashlib.sha256(eval_report.read_bytes()).hexdigest(),
                "checked": 1,
                "gap_count": 1,
                "missing_url_sourced_prediction_count": 2,
                "missing_official_source_prediction_count": 2,
                "gaps": [
                    {
                        "split": "evaluation",
                        "model_name": "ontology_signal_model",
                        "signal_name": "member_vote_history",
                        "prediction_count": 2,
                        "url_sourced_prediction_count": 0,
                        "missing_url_sourced_prediction_count": 2,
                        "url_source_coverage_rate": 0.0,
                        "official_source_sourced_prediction_count": 0,
                        "missing_official_source_prediction_count": 2,
                        "official_source_coverage_rate": 0.0,
                        "sample_vote_event_ids": [20, 21],
                        "sample_cases": [
                            {
                                "vote_event_id": 20,
                                "event_key": "joint-119-1-20",
                                "jurisdiction_id": "us_congress",
                                "member_bioguide_id": "B000002",
                            },
                            {
                                "vote_event_id": 21,
                                "event_key": "house-119-0-21",
                                "jurisdiction_id": "us_congress",
                                "member_bioguide_id": "B000003",
                            },
                        ],
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = verify_prediction_source_url_audit_command(
        SimpleNamespace(
            artifact=str(artifact),
            require_run_metadata=False,
            require_no_gaps=False,
            require_no_official_source_gaps=False,
            output=None,
        )
    )

    assert result["ok"] is False
    assert (
        "gaps[0].sample_cases[0].event_key must be chamber-congress-session-roll"
        in (result["issues"])
    )
    assert (
        "gaps[0].sample_cases[1].event_key must use positive numeric Congress vote ids"
        in (result["issues"])
    )


def test_verify_prediction_source_url_audit_rejects_non_positive_sample_vote_ids(
    tmp_path: Path,
) -> None:
    eval_report = tmp_path / "eval-report.json"
    eval_report.write_text("{}", encoding="utf-8")
    artifact = tmp_path / "source-url-audit.json"
    artifact.write_text(
        json.dumps(
            {
                "ok": True,
                "command": "prediction-source-url-audit",
                "eval_report_path": str(eval_report),
                "eval_report_sha256": hashlib.sha256(eval_report.read_bytes()).hexdigest(),
                "checked": 1,
                "gap_count": 1,
                "missing_url_sourced_prediction_count": 2,
                "missing_official_source_prediction_count": 2,
                "gaps": [
                    {
                        "split": "evaluation",
                        "model_name": "ontology_signal_model",
                        "signal_name": "member_vote_history",
                        "prediction_count": 2,
                        "url_sourced_prediction_count": 0,
                        "missing_url_sourced_prediction_count": 2,
                        "url_source_coverage_rate": 0.0,
                        "official_source_sourced_prediction_count": 0,
                        "missing_official_source_prediction_count": 2,
                        "official_source_coverage_rate": 0.0,
                        "sample_vote_event_ids": [0, -7],
                        "sample_cases": [
                            {"vote_event_id": 0, "member_bioguide_id": "B000002"},
                            {"vote_event_id": -7, "member_bioguide_id": "B000003"},
                        ],
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = verify_prediction_source_url_audit_command(
        SimpleNamespace(
            artifact=str(artifact),
            require_run_metadata=False,
            require_no_gaps=False,
            require_no_official_source_gaps=False,
            output=None,
        )
    )

    assert result["ok"] is False
    assert "gaps[0].sample_vote_event_ids[0] must be a positive integer" in result["issues"]
    assert "gaps[0].sample_vote_event_ids[1] must be a positive integer" in result["issues"]
    assert "gaps[0].sample_cases[0].vote_event_id must be a positive integer" in (result["issues"])
    assert "gaps[0].sample_cases[1].vote_event_id must be a positive integer" in (result["issues"])


def test_verify_prediction_source_url_audit_requires_sample_identity_in_run_metadata(
    tmp_path: Path,
) -> None:
    eval_report = tmp_path / "eval-report.json"
    eval_report.write_text("{}", encoding="utf-8")
    artifact = tmp_path / "source-url-audit.json"
    source_state = {
        "eval_report_path": str(eval_report),
        "eval_report_sha256": hashlib.sha256(eval_report.read_bytes()).hexdigest(),
        "gap_count": 1,
        "missing_url_sourced_prediction_count": 1,
        "missing_official_source_prediction_count": 1,
        "jurisdiction_count": 1,
        "jurisdiction_ids": ["state_ca"],
        "portable_jurisdiction_count": 1,
        "portable_jurisdiction_ids": ["state_ca"],
        "legislative_body_count": 1,
        "legislative_body_ids": ["state_ca:ca_assembly"],
        "legislative_session_count": 1,
        "legislative_session_ids": ["state_ca:ca_assembly:2025_regular"],
    }
    artifact.write_text(
        json.dumps(
            {
                "ok": True,
                "command": "prediction-source-url-audit",
                "eval_report_path": str(eval_report),
                "eval_report_sha256": source_state["eval_report_sha256"],
                "checked": 1,
                "gap_count": 1,
                "jurisdiction_count": 1,
                "implemented_jurisdiction_count": 0,
                "portable_jurisdiction_count": 1,
                "legislative_body_count": 1,
                "legislative_session_count": 1,
                "jurisdiction_ids": ["state_ca"],
                "implemented_jurisdiction_ids": [],
                "portable_jurisdiction_ids": ["state_ca"],
                "legislative_body_ids": ["state_ca:ca_assembly"],
                "legislative_session_ids": ["state_ca:ca_assembly:2025_regular"],
                "missing_url_sourced_prediction_count": 1,
                "missing_official_source_prediction_count": 1,
                "gaps": [
                    {
                        "split": "evaluation",
                        "model_name": "ontology_signal_model",
                        "signal_name": "member_vote_history",
                        "prediction_count": 1,
                        "url_sourced_prediction_count": 0,
                        "missing_url_sourced_prediction_count": 1,
                        "url_source_coverage_rate": 0.0,
                        "official_source_sourced_prediction_count": 0,
                        "missing_official_source_prediction_count": 1,
                        "official_source_coverage_rate": 0.0,
                        "sample_vote_event_ids": [20],
                        "sample_bill_keys": ["119-hr-2"],
                        "sample_member_bioguide_ids": ["B000002"],
                        "sample_cases": [
                            {
                                "vote_event_id": 20,
                                "event_key": "assembly-2025-1-20",
                                "jurisdiction_id": "state_ca",
                                "legislative_body_id": "ca_assembly",
                                "legislative_session_id": "2025_regular",
                                "bill_key": "119-hr-2",
                                "member_bioguide_id": "B000002",
                            },
                        ],
                    }
                ],
                "run_metadata": {
                    "command": "prediction-source-url-audit",
                    "artifact_sha256": source_state["eval_report_sha256"],
                    "source_artifact_sha256": {"eval_report": source_state["eval_report_sha256"]},
                    "verification_flags": {"fail_on_gaps": False},
                    "source_state": source_state,
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = verify_prediction_source_url_audit_command(
        SimpleNamespace(
            artifact=str(artifact),
            require_run_metadata=True,
            require_no_gaps=False,
            require_no_official_source_gaps=False,
            output=None,
        )
    )

    assert result["ok"] is False
    assert "run_metadata source_state invalid type: sample_case_count" in result["issues"]
    assert "run_metadata source_state invalid type: sample_vote_event_ids" in result["issues"]
    assert "run_metadata source_state invalid type: sample_bill_keys" in result["issues"]
    assert (
        "run_metadata source_state invalid type: sample_member_bioguide_ids" in (result["issues"])
    )


def test_verify_prediction_source_url_audit_requires_source_family_metadata(
    tmp_path: Path,
) -> None:
    eval_report = tmp_path / "eval-report.json"
    eval_report.write_text("{}", encoding="utf-8")
    artifact = tmp_path / "source-url-audit.json"
    source_state = {
        "eval_report_path": str(eval_report),
        "eval_report_sha256": hashlib.sha256(eval_report.read_bytes()).hexdigest(),
        "gap_count": 1,
        "missing_url_sourced_prediction_count": 1,
        "missing_official_source_prediction_count": 1,
        "source_family_count": 1,
        "source_family_ids": ["congress_vote"],
        "sample_case_count": 1,
        "sample_vote_event_ids": [20],
        "sample_bill_keys": ["us_congress:119:hr:2"],
        "sample_member_bioguide_ids": ["B000002"],
        "sample_source_family_ids": ["congress_vote"],
        "sample_jurisdiction_ids": ["us_congress"],
        "sample_legislative_body_ids": ["us_congress:house"],
        "sample_legislative_session_ids": ["us_congress:house:congress_119_session_1"],
        "jurisdiction_count": 1,
        "jurisdiction_ids": ["us_congress"],
        "implemented_jurisdiction_count": 1,
        "implemented_jurisdiction_ids": ["us_congress"],
        "legislative_body_count": 1,
        "legislative_body_ids": ["us_congress:house"],
        "legislative_session_count": 1,
        "legislative_session_ids": ["us_congress:house:congress_119_session_1"],
    }
    artifact.write_text(
        json.dumps(
            {
                "ok": True,
                "command": "prediction-source-url-audit",
                "eval_report_path": str(eval_report),
                "eval_report_sha256": source_state["eval_report_sha256"],
                "checked": 1,
                "gap_count": 1,
                "source_family_count": 1,
                "source_family_ids": ["congress_vote"],
                "jurisdiction_count": 1,
                "implemented_jurisdiction_count": 1,
                "portable_jurisdiction_count": 0,
                "legislative_body_count": 1,
                "legislative_session_count": 1,
                "jurisdiction_ids": ["us_congress"],
                "implemented_jurisdiction_ids": ["us_congress"],
                "portable_jurisdiction_ids": [],
                "legislative_body_ids": ["us_congress:house"],
                "legislative_session_ids": ["us_congress:house:congress_119_session_1"],
                "missing_url_sourced_prediction_count": 1,
                "missing_official_source_prediction_count": 1,
                "gaps": [
                    {
                        "split": "evaluation",
                        "model_name": "ontology_signal_model",
                        "signal_name": "member_vote_history",
                        "prediction_count": 1,
                        "url_sourced_prediction_count": 0,
                        "missing_url_sourced_prediction_count": 1,
                        "url_source_coverage_rate": 0.0,
                        "official_source_sourced_prediction_count": 0,
                        "missing_official_source_prediction_count": 1,
                        "official_source_coverage_rate": 0.0,
                        "source_family_ids": ["congress_vote"],
                        "sample_vote_event_ids": [20],
                        "sample_bill_keys": ["us_congress:119:hr:2"],
                        "sample_member_bioguide_ids": ["B000002"],
                        "sample_cases": [
                            {
                                "vote_event_id": 20,
                                "event_key": "house-119-1-20",
                                "jurisdiction_id": "us_congress",
                                "legislative_body_id": "house",
                                "bill_key": "119-hr-2",
                                "bill_context_key": "us_congress:119:hr:2",
                                "member_bioguide_id": "B000002",
                            },
                        ],
                    }
                ],
                "run_metadata": {
                    "command": "prediction-source-url-audit",
                    "artifact_sha256": source_state["eval_report_sha256"],
                    "source_artifact_sha256": {"eval_report": source_state["eval_report_sha256"]},
                    "verification_flags": {"fail_on_gaps": False},
                    "source_state": source_state,
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = verify_prediction_source_url_audit_command(
        SimpleNamespace(
            artifact=str(artifact),
            require_run_metadata=True,
            require_no_gaps=False,
            require_no_official_source_gaps=False,
            output=None,
        )
    )

    assert result["ok"] is True
    assert result["issues"] == []
    verify_source_state = result["run_metadata"]["source_state"]
    assert verify_source_state["source_family_count"] == 1
    assert verify_source_state["source_family_ids"] == ["congress_vote"]
    assert verify_source_state["sample_source_family_ids"] == ["congress_vote"]


def test_verify_prediction_source_url_audit_rejects_uncanonical_gap_source_families(
    tmp_path: Path,
) -> None:
    eval_report = tmp_path / "eval-report.json"
    eval_report.write_text("{}", encoding="utf-8")
    artifact = tmp_path / "source-url-audit.json"
    artifact.write_text(
        json.dumps(
            {
                "ok": True,
                "command": "prediction-source-url-audit",
                "eval_report_path": str(eval_report),
                "eval_report_sha256": hashlib.sha256(eval_report.read_bytes()).hexdigest(),
                "checked": 1,
                "gap_count": 1,
                "source_family_count": 2,
                "source_family_ids": ["congress_bill", "financial_disclosure"],
                "missing_url_sourced_prediction_count": 1,
                "missing_official_source_prediction_count": 1,
                "gaps": [
                    {
                        "split": "evaluation",
                        "model_name": "ontology_signal_model",
                        "signal_name": "member_financial_edges",
                        "prediction_count": 1,
                        "url_sourced_prediction_count": 0,
                        "missing_url_sourced_prediction_count": 1,
                        "url_source_coverage_rate": 0.0,
                        "official_source_sourced_prediction_count": 0,
                        "missing_official_source_prediction_count": 1,
                        "official_source_coverage_rate": 0.0,
                        "source_family_ids": [
                            "financial_disclosure",
                            "congress_bill",
                            "financial_disclosure",
                        ],
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = verify_prediction_source_url_audit_command(
        SimpleNamespace(
            artifact=str(artifact),
            require_run_metadata=False,
            require_no_gaps=False,
            require_no_official_source_gaps=False,
            output=None,
        )
    )

    assert result["ok"] is False
    assert "gaps[0].source_family_ids must be a sorted unique string list" in result["issues"]


def test_verify_prediction_source_url_audit_rejects_malformed_source_family_ids(
    tmp_path: Path,
) -> None:
    eval_report = tmp_path / "eval-report.json"
    eval_report.write_text("{}", encoding="utf-8")
    artifact = tmp_path / "source-url-audit.json"
    artifact.write_text(
        json.dumps(
            {
                "ok": False,
                "command": "prediction-source-url-audit",
                "eval_report_path": str(eval_report),
                "eval_report_sha256": hashlib.sha256(eval_report.read_bytes()).hexdigest(),
                "checked": 1,
                "gap_count": 1,
                "source_family_count": 1,
                "source_family_ids": ["Congress Vote"],
                "missing_url_sourced_prediction_count": 1,
                "missing_official_source_prediction_count": 1,
                "gaps": [
                    {
                        "split": "evaluation",
                        "model_name": "ontology_signal_model",
                        "signal_name": "member_vote_history",
                        "prediction_count": 1,
                        "url_sourced_prediction_count": 0,
                        "missing_url_sourced_prediction_count": 1,
                        "url_source_coverage_rate": 0.0,
                        "official_source_sourced_prediction_count": 0,
                        "missing_official_source_prediction_count": 1,
                        "official_source_coverage_rate": 0.0,
                        "source_family_ids": ["Congress Vote"],
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = verify_prediction_source_url_audit_command(
        SimpleNamespace(
            artifact=str(artifact),
            require_run_metadata=False,
            require_no_gaps=False,
            require_no_official_source_gaps=False,
            output=None,
        )
    )

    assert result["ok"] is False
    assert "source_family_ids must be normalized source family ids" in result["issues"]
    assert "gaps[0].source_family_ids must contain normalized source family ids" in result["issues"]


def test_verify_prediction_source_url_audit_distinguishes_sample_body_identity(
    tmp_path: Path,
) -> None:
    eval_report = tmp_path / "eval-report.json"
    eval_report.write_text("{}", encoding="utf-8")
    artifact = tmp_path / "source-url-audit.json"
    artifact.write_text(
        json.dumps(
            {
                "ok": True,
                "command": "prediction-source-url-audit",
                "eval_report_path": str(eval_report),
                "eval_report_sha256": hashlib.sha256(eval_report.read_bytes()).hexdigest(),
                "checked": 1,
                "gap_count": 1,
                "jurisdiction_count": 1,
                "implemented_jurisdiction_count": 0,
                "portable_jurisdiction_count": 1,
                "legislative_body_count": 2,
                "legislative_session_count": 2,
                "jurisdiction_ids": ["state_ca"],
                "implemented_jurisdiction_ids": [],
                "portable_jurisdiction_ids": ["state_ca"],
                "legislative_body_ids": ["state_ca:ca_assembly", "state_ca:ca_senate"],
                "legislative_session_ids": [
                    "state_ca:ca_assembly:2025_regular",
                    "state_ca:ca_senate:2025_regular",
                ],
                "missing_url_sourced_prediction_count": 2,
                "missing_official_source_prediction_count": 2,
                "gaps": [
                    {
                        "split": "evaluation",
                        "model_name": "ontology_signal_model",
                        "signal_name": "member_vote_history",
                        "prediction_count": 2,
                        "url_sourced_prediction_count": 0,
                        "missing_url_sourced_prediction_count": 2,
                        "url_source_coverage_rate": 0.0,
                        "official_source_sourced_prediction_count": 0,
                        "missing_official_source_prediction_count": 2,
                        "official_source_coverage_rate": 0.0,
                        "sample_vote_event_ids": [7],
                        "sample_bill_keys": ["state_ca:2025:bicameral:1"],
                        "sample_member_bioguide_ids": ["CA-A001"],
                        "sample_cases": [
                            {
                                "vote_event_id": 7,
                                "event_key": "state-ca-2025-7",
                                "jurisdiction_id": "state_ca",
                                "legislative_body_id": "ca_assembly",
                                "legislative_session_id": "2025_regular",
                                "bill_key": "state_ca:2025:bicameral:1",
                                "member_bioguide_id": "CA-A001",
                            },
                            {
                                "vote_event_id": 7,
                                "event_key": "state-ca-2025-7",
                                "jurisdiction_id": "state_ca",
                                "legislative_body_id": "ca_senate",
                                "legislative_session_id": "2025_regular",
                                "bill_key": "state_ca:2025:bicameral:1",
                                "member_bioguide_id": "CA-A001",
                            },
                        ],
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = verify_prediction_source_url_audit_command(
        SimpleNamespace(
            artifact=str(artifact),
            require_run_metadata=False,
            require_no_gaps=False,
            require_no_official_source_gaps=False,
            output=None,
        )
    )

    assert result["ok"] is True
    assert result["issues"] == []
