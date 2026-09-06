from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.regpatch import cli, evaluation


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def test_real_seed_bundle_is_exact_public_allowlist_and_runs_from_fresh_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    episode = tmp_path / "evaluator-episode"
    bundle = tmp_path / "candidate-bundle"

    assert cli.main(["seed", str(episode)]) == 0
    seed_report = json.loads(capsys.readouterr().out)
    assert seed_report["status"] == "ACCEPTED"
    target = (episode / "evaluator/target.xml").read_bytes()
    target_sha256 = _sha256(target)
    private_sha256s = {
        _sha256(path.read_bytes()) for path in (episode / "evaluator").rglob("*") if path.is_file()
    }
    assert target_sha256 in private_sha256s

    # A stray private-looking file in the evaluator episode's input tree is not
    # manifest-declared and therefore must not be published.
    (episode / "input/accidental-hidden.json").write_text(
        json.dumps(
            {
                "target_sha256": target_sha256,
                "local_path": "/Users/example/project/.git/HEAD",
            }
        ),
        encoding="utf-8",
    )
    assert cli.main(["bundle", str(episode), str(bundle)]) == 0
    bundle_report = json.loads(capsys.readouterr().out)
    assert bundle_report["status"] == "PASS"
    assert bundle_report["bundle_sha256"] == evaluation._tree_digest(bundle)

    manifest = json.loads((bundle / "episode/manifest.json").read_text(encoding="utf-8"))
    declared_inputs = {
        manifest["inputs"]["base"]["path"],
        *(row["path"] for row in manifest["inputs"]["rules"]),
    }
    expected_files = {
        "BUNDLE.json",
        "TASK.md",
        "episode/manifest.json",
        "submission/solution.ts",
        *(f"episode/{path}" for path in declared_inputs),
    }
    actual_files = {
        path.relative_to(bundle).as_posix() for path in bundle.rglob("*") if path.is_file()
    }
    assert actual_files == expected_files
    assert not any(".git" in path.parts or "evaluator" in path.parts for path in bundle.rglob("*"))

    local_markers = (
        str(tmp_path).encode().lower(),
        b"/users/",
        b"/private/var/",
        b"/.git/",
        b"file://",
    )
    private_hash_markers = tuple(value.encode("ascii") for value in private_sha256s)
    for relative in sorted(actual_files):
        payload = (bundle / relative).read_bytes()
        lowered = payload.lower()
        assert payload != target
        assert _sha256(payload) not in private_sha256s
        assert target_sha256.encode("ascii") not in lowered
        assert not any(marker in lowered for marker in local_markers)
        assert not any(marker in lowered for marker in private_hash_markers)

    receipt = json.loads((bundle / "BUNDLE.json").read_text(encoding="utf-8"))
    receipted_paths = {row["path"] for row in receipt["files"]}
    assert receipted_paths == actual_files - {"BUNDLE.json"}
    for row in receipt["files"]:
        payload = (bundle / row["path"]).read_bytes()
        assert row["byte_count"] == len(payload)
        assert row["sha256"] == _sha256(payload)

    assert cli.main(["grade", str(episode), str(bundle / "submission")]) == 0
    grade_report = json.loads(capsys.readouterr().out)
    assert grade_report["status"] == "PASS"
    assert grade_report["runner_receipt"]["deterministic"] is True
    assert 0.0 < grade_report["score"]["overall"] < 100.0

    undeclared = bundle / "episode/input/undeclared.json"
    undeclared.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="exact public file allowlist"):
        evaluation._audit_candidate_bundle(bundle, private_sha256s=private_sha256s)
    undeclared.rename(tmp_path / "quarantined-undeclared.json")

    with (bundle / "TASK.md").open("ab") as task:
        task.write(b"\ntarget_sha256=" + target_sha256.encode("ascii"))
    with pytest.raises(ValueError, match="private marker"):
        evaluation._audit_candidate_bundle(bundle, private_sha256s=private_sha256s)


def test_acquire_cli_requires_and_selects_an_observed_version_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    probe = {
        "candidates": [
            {
                "candidate_id": "document-title47-part73",
                "document_number": "2024-01928",
                "publication_date": "2024-01-31",
                "title": 47,
                "part": "73",
                "metadata_url": "https://www.federalregister.gov/api/v1/documents/2024-01928.json",
                "rule_xml_url": "https://www.federalregister.gov/documents/full_text/xml/2024/01/31/2024-01928.xml",
                "status": "REQUIRES_ECFR_VERSION_PROBE",
            }
        ]
    }
    versions = {
        "scope": {"title": 47, "part": "73"},
        "candidate_windows": [
            {
                "ecfr_amendment_date": "2024-02-09",
                "ecfr_issue_date": "2024-02-09",
                "identifiers": ["73.6030"],
                "record_count": 1,
                "substantive_record_count": 1,
            }
        ],
    }
    probe_path = tmp_path / "probe.json"
    versions_path = tmp_path / "versions.json"
    probe_path.write_text(json.dumps(probe), encoding="utf-8")
    versions_path.write_text(json.dumps(versions), encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_acquire(store: Path, candidate: dict[str, object]) -> Path:
        captured.update(candidate)
        return store / "candidates" / str(candidate["candidate_id"]) / "source-spec.json"

    monkeypatch.setattr(cli, "acquire_candidate_sources", fake_acquire)
    assert (
        cli.main(
            [
                "acquire",
                str(tmp_path / "store"),
                str(probe_path),
                "0",
                str(versions_path),
                "0",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["expanded_window_count"] == 1
    assert captured["base_date"] == "2024-02-08"
    assert captured["successor_date"] == "2024-02-09"
    assert captured["status"] == "OBSERVED_VERSION_WINDOW_UNVALIDATED_CAUSALITY"


def test_split_scores_are_weighted_without_a_third_suite_execution() -> None:
    combined = evaluation._weighted_aggregate(
        {"overall": 100.0, "components": {"changed_regions": 1.0}},
        1,
        {"overall": 60.0, "components": {"changed_regions": 0.5}},
        4,
    )

    assert combined == {
        "overall": 68.0,
        "components": {"changed_regions": 0.6},
    }


def test_public_demo_receipt_is_portable_and_baselines_stay_separated(tmp_path: Path) -> None:
    first = evaluation.run_demo(tmp_path / "first")
    second = evaluation.run_demo(tmp_path / "another directory")
    assert first["demo_sha256"] == second["demo_sha256"]
    assert first["evaluation"] == second["evaluation"]
    measured = first["evaluation"]
    assert measured["trusted_oracle_score"] == 100
    scores = {row["baseline"]: row["overall"] for row in measured["baselines"]}
    assert scores == {
        "copy-before": 69.9615,
        "copy-rule-replacement": 0.0,
        "naive-regex": 24.99,
        "public-hardcode": 100.0,
        "damaging-partial": 1.8907,
    }
    report = (tmp_path / "first/demo-report.json").read_bytes()
    assert str(tmp_path).encode() not in report
    with pytest.raises(FileExistsError):
        evaluation.run_demo(tmp_path / "first")
