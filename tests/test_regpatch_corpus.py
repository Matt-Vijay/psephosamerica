"""Public bytes exercise suite plumbing; duplicated fixtures never count as scale."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from src.regpatch import corpus, evaluation
from src.regpatch.baselines import baseline_source


def _json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_corpus_audit_compilation_scoring_and_fail_closed_receipts(tmp_path: Path) -> None:
    root = tmp_path / "retained"
    root.mkdir()
    spec = json.loads(corpus.PILOT_SPEC.read_text())
    candidates = []
    # Both are copies of the already-public seed, not genuine hidden episodes.
    for candidate_id in ("fixture-public", "fixture-copy"):
        directory = root / candidate_id
        directory.mkdir()

        def receipt(source: dict[str, object], directory: Path = directory) -> dict[str, object]:
            original = corpus.PILOT_SPEC.parent / str(source["path"])
            destination = directory / original.name
            shutil.copyfile(original, destination)
            payload = destination.read_bytes()
            return {
                "path": destination.name,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }

        rule = spec["sources"]["rules"][0]
        candidates.append(
            {
                "candidate_id": candidate_id,
                "cfr": {"title": 47, "part": "73"},
                "clocks": {"ecfr_before_date": "2024-01-30", "ecfr_after_date": "2024-01-31"},
                "files": {
                    "before": receipt(spec["sources"]["base"]),
                    "after": receipt(spec["sources"]["target"]),
                },
                "documents": [
                    {
                        "document_number": "2024-01928",
                        "xml": receipt(rule["xml"]),
                        "metadata": receipt(rule["metadata"]),
                    }
                ],
            }
        )
    manifest = {
        "schema_version": 1,
        "generated_at": "2026-08-28T18:00:00Z",
        "candidates": candidates,
    }
    manifest_path = root / "substantive-manifest.json"
    _json(manifest_path, manifest)
    plan = {
        "schema_version": 1,
        "source_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "public_candidate_id": "fixture-public",
        "metadata_probe": {},
        "ecfr_issue_dates": {candidate["candidate_id"]: "2024-01-31" for candidate in candidates},
    }
    plan_path = root / "suite-plan.json"
    _json(plan_path, plan)
    audit = corpus.audit_retained_corpus(root)
    assert audit["status"] == "PASS" and audit["artifacts_rehashed"] == 8
    suite_path = tmp_path / "suite"
    suite = corpus.compile_retained_corpus(suite_path, corpus_root=root)
    assert suite["status"] == "FAIL"  # A repeated public example cannot satisfy the scale gate.
    assert len(suite["episodes"]) == 2
    assert suite["acceptance_gate"]["checks"]["real_independent_multi_rule_composition"] is False
    assert suite["composition_evidence"]["correction_chain_windows"] == 0
    submission = tmp_path / "submission"
    submission.mkdir()
    (submission / "solution.ts").write_text(baseline_source("copy-before"))
    public = evaluation.grade_suite_candidate(suite_path, submission, "public")
    hidden = evaluation.grade_suite_candidate(suite_path, submission, "hidden")
    assert public["aggregate"] == hidden["aggregate"]
    assert 0 < public["aggregate"]["overall"] < 100
    assert "public_cases" in public and "public_cases" not in hidden
    assert hidden["hidden_details_redacted"] is True
    matrix = evaluation.score_suite_baselines(suite_path)
    assert matrix["trusted_oracle"]["all_episodes_100"] is True
    for baseline in matrix["baselines"]:
        combined = evaluation._weighted_aggregate(baseline["public"], 1, baseline["hidden"], 1)
        assert baseline["all"] == combined
        if baseline["baseline"] == "public-hardcode":
            assert baseline["public"]["overall"] == 100
            assert baseline["hidden"]["overall"] < 100
    with pytest.raises(ValueError, match="unsupported suite split"):
        evaluation.grade_suite_candidate(suite_path, submission, "typo")
    with pytest.raises(FileExistsError):
        corpus.compile_retained_corpus(suite_path, corpus_root=root)

    # A valid receipt is not permission to trust changing local bytes.
    before = root / candidates[0]["candidate_id"] / str(candidates[0]["files"]["before"]["path"])
    original = before.read_bytes()
    before.write_bytes(b"tampered")
    assert corpus.audit_retained_corpus(root)["status"] == "FAIL"
    with pytest.raises(ValueError, match="artifact audit failed"):
        corpus.compile_retained_corpus(tmp_path / "rejected", corpus_root=root)
    before.write_bytes(original)
    manifest_path.write_text("{}")
    with pytest.raises(ValueError, match="manifest hash mismatch"):
        corpus.load_substantive_manifest(root)
    for value in ({"schema_version": 0}, {"schema_version": 1}):
        _json(plan_path, value)
        with pytest.raises(ValueError, match="suite plan"):
            corpus.load_substantive_manifest(root)
