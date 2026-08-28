"""Short command surface for compiling, bundling, and grading RegPatch episodes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from src.regpatch.baselines import (
    BASELINE_NAMES,
    BaselineName,
    baseline_source,
    write_baseline,
)
from src.regpatch.compiler import (
    acquire_candidate_sources,
    compile_episode,
    expand_candidate_windows,
    probe_ecfr_versions,
    probe_federal_register,
)
from src.regpatch.corpus import (
    DEFAULT_EVALUATOR_ROOT,
    audit_retained_corpus,
    compile_retained_corpus,
)
from src.regpatch.grader import score_episode
from src.regpatch.runner import RegPatchRunner

PACKAGE_ROOT = Path(__file__).parent
PILOT_ROOT = PACKAGE_ROOT / "pilot"
PILOT_SPEC = PILOT_ROOT / "source_spec.json"
TASK_CARD = PILOT_ROOT / "TASK.md"
_TEMP_ROOT = Path("/private/tmp") if Path("/private/tmp").is_dir() else Path("/tmp")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="psephos-regpatch",
        description="Compile and score witnessed Federal Register/eCFR patch episodes.",
        epilog="Operator guide: src/regpatch/README.md",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    compile_command = commands.add_parser("compile", help="compile one retained source spec")
    compile_command.add_argument("spec", type=Path)
    compile_command.add_argument("output", type=Path)

    seed = commands.add_parser("seed", help="compile the retained 47 CFR public seed")
    seed.add_argument("output", type=Path)

    bundle = commands.add_parser("bundle", help="make a candidate-facing bundle")
    bundle.add_argument("episode", type=Path)
    bundle.add_argument("output", type=Path)
    bundle.add_argument(
        "--starter",
        choices=tuple(name for name in BASELINE_NAMES if name != "public-hardcode"),
        default="copy-before",
    )

    grade = commands.add_parser("grade", help="run and score a candidate twice")
    grade.add_argument("episode", type=Path, help="evaluator episode with private target")
    grade.add_argument("submission", type=Path, help="solution.ts/js or candidate directory")

    baselines = commands.add_parser("baselines", help="score every weak baseline")
    baselines.add_argument("episode", type=Path)

    probe = commands.add_parser("probe", help="probe official Federal Register metadata")
    probe.add_argument("store", type=Path)
    probe.add_argument("--start-date", type=date.fromisoformat, required=True)
    probe.add_argument("--end-date", type=date.fromisoformat, required=True)
    probe.add_argument("--max-candidates", type=int, default=100)

    versions = commands.add_parser(
        "version-probe", help="probe observed eCFR amendment/version windows"
    )
    versions.add_argument("store", type=Path)
    versions.add_argument("--title", type=int, required=True)
    versions.add_argument("--part", required=True)
    versions.add_argument("--start-date", type=date.fromisoformat, required=True)
    versions.add_argument("--end-date", type=date.fromisoformat, required=True)

    acquire = commands.add_parser(
        "acquire", help="expand one metadata candidate and acquire an observed version window"
    )
    acquire.add_argument("store", type=Path)
    acquire.add_argument("probe_report", type=Path)
    acquire.add_argument("candidate_index", type=int)
    acquire.add_argument("version_report", type=Path)
    acquire.add_argument("window_index", type=int)

    corpus_audit = commands.add_parser("corpus-audit", help="re-hash the private evaluator corpus")
    corpus_audit.add_argument("--store", type=Path, default=DEFAULT_EVALUATOR_ROOT)

    corpus_build = commands.add_parser("corpus-build", help="compile the private evaluator corpus")
    corpus_build.add_argument("output", type=Path)
    corpus_build.add_argument("--store", type=Path, default=DEFAULT_EVALUATOR_ROOT)

    grade_suite = commands.add_parser(
        "grade-suite", help="score one candidate over a compiled evaluator split"
    )
    grade_suite.add_argument("suite", type=Path)
    grade_suite.add_argument("submission", type=Path)
    grade_suite.add_argument("--split", choices=("public", "hidden", "all"), default="hidden")

    suite_baselines = commands.add_parser(
        "suite-baselines", help="measure weak baselines across public and hidden episodes"
    )
    suite_baselines.add_argument("suite", type=Path)

    demo = commands.add_parser("demo", help="compile, isolate, and score the retained seed")
    demo.add_argument("output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.command == "compile":
        _print(compile_episode(arguments.spec, arguments.output))
        return 0
    if arguments.command == "seed":
        _print(compile_episode(PILOT_SPEC, arguments.output))
        return 0
    if arguments.command == "bundle":
        _print(bundle_episode(arguments.episode, arguments.output, starter=arguments.starter))
        return 0
    if arguments.command == "grade":
        _print(grade_candidate(arguments.episode, arguments.submission))
        return 0
    if arguments.command == "baselines":
        _print(score_baselines(arguments.episode))
        return 0
    if arguments.command == "probe":
        _print(
            probe_federal_register(
                arguments.store,
                start_date=arguments.start_date,
                end_date=arguments.end_date,
                max_candidates=arguments.max_candidates,
            )
        )
        return 0
    if arguments.command == "version-probe":
        _print(
            probe_ecfr_versions(
                arguments.store,
                title=arguments.title,
                part=arguments.part,
                issue_date_start=arguments.start_date,
                issue_date_end=arguments.end_date,
            )
        )
        return 0
    if arguments.command == "acquire":
        report = _read_json(arguments.probe_report)
        candidate = _select_array_row(report, "candidates", arguments.candidate_index)
        version_report = _read_json(arguments.version_report)
        windows = expand_candidate_windows(candidate, version_report)
        if not 0 <= arguments.window_index < len(windows):
            raise IndexError("window_index is outside the expanded observed windows")
        candidate = windows[arguments.window_index]
        path = acquire_candidate_sources(arguments.store, candidate)
        _print(
            {
                "status": "PASS",
                "expanded_window_count": len(windows),
                "selected_window_index": arguments.window_index,
                "source_spec": str(path),
            }
        )
        return 0
    if arguments.command == "corpus-audit":
        _print(audit_retained_corpus(arguments.store))
        return 0
    if arguments.command == "corpus-build":
        _print(compile_retained_corpus(arguments.output, corpus_root=arguments.store))
        return 0
    if arguments.command == "grade-suite":
        _print(grade_suite_candidate(arguments.suite, arguments.submission, arguments.split))
        return 0
    if arguments.command == "suite-baselines":
        _print(score_suite_baselines(arguments.suite))
        return 0
    if arguments.command == "demo":
        _print(run_demo(arguments.output))
        return 0
    raise AssertionError(f"unknown command: {arguments.command}")


def bundle_episode(
    episode: Path,
    destination: Path,
    *,
    starter: BaselineName = "copy-before",
) -> dict[str, object]:
    """Copy only public episode bytes and one editable starter into a fresh directory."""
    episode = episode.resolve()
    _audit_evaluator_episode(episode)
    frozen = RegPatchRunner.freeze_episode(episode)
    private_sha256s = _regular_tree_hashes(episode / "evaluator", "evaluator tree")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"refusing to overwrite bundle: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{destination.name}-staging-", dir=destination.parent
    ) as temporary_name:
        staged = Path(temporary_name) / "bundle"
        public_episode = staged / "episode"
        public_episode.mkdir(parents=True)
        for item in frozen.files:
            target = public_episode.joinpath(*item.path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(item.content)
        submission = staged / "submission"
        submission.mkdir()
        (submission / "solution.ts").write_text(baseline_source(starter), encoding="utf-8")
        shutil.copyfile(TASK_CARD, staged / "TASK.md")
        frozen = RegPatchRunner.freeze_episode(public_episode)
        file_receipts = _file_receipts(staged)
        receipt = {
            "schema_version": 1,
            "bundle_kind": "psephos_regpatch_candidate",
            "episode_id": frozen.episode_id,
            "starter": starter,
            "files": file_receipts,
        }
        _write_json(staged / "BUNDLE.json", receipt)
        _audit_candidate_bundle(staged, private_sha256s=private_sha256s)
        os.replace(staged, destination)
    return {
        "status": "PASS",
        "episode_id": frozen.episode_id,
        "bundle": str(destination),
        "files": len(file_receipts) + 1,
        "bundle_sha256": _tree_digest(destination),
    }


def grade_candidate(episode: Path, submission: Path) -> dict[str, object]:
    """Execute twice in fresh sandboxes and compare with the evaluator-only target."""
    episode = episode.resolve()
    audit = _audit_evaluator_episode(episode)
    runner = RegPatchRunner()
    runs = runner.run_twice(submission, episode)
    with tempfile.TemporaryDirectory(prefix="psephos-regpatch-score-", dir=_TEMP_ROOT) as name:
        capture = Path(name)
        first_path = capture / "first.xml"
        second_path = capture / "second.xml"
        first_path.write_bytes(runs.first.result or b"<REGPATCH_INVALID/>")
        second_path.write_bytes(runs.second.result or b"<REGPATCH_INVALID/>")
        score = score_episode(
            episode / "manifest.json",
            episode / "input" / "base.xml",
            episode / "evaluator" / "target.xml",
            first_path,
            second_candidate_path=second_path,
            run_ok=runs.first.ok,
            second_run_ok=runs.second.ok,
            provenance_ok=(
                runs.first.candidate_provenance_valid and runs.second.candidate_provenance_valid
            ),
        )
    return {
        "status": "PASS",
        "episode_id": audit["episode_id"],
        "submission_sha256": runs.first.receipt["submission_sha256"],
        "deno_version": runner.version,
        "score": score.as_dict(),
        "runner_receipt": runs.evaluator_receipt(),
    }


def score_baselines(episode: Path) -> dict[str, object]:
    """Run the fixed anti-shortcut matrix through the real sandbox and scorer."""
    episode = episode.resolve()
    audit = _audit_evaluator_episode(episode)
    target = (episode / "evaluator" / "target.xml").read_bytes()
    rows: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="psephos-regpatch-baselines-", dir=_TEMP_ROOT) as name:
        root = Path(name)
        for baseline in BASELINE_NAMES:
            candidate = root / baseline
            write_baseline(
                baseline,
                candidate,
                public_episode_id=str(audit["episode_id"]),
                public_target=target,
            )
            report = grade_candidate(episode, candidate)
            score = report["score"]
            assert isinstance(score, dict)
            rows.append(
                {
                    "baseline": baseline,
                    "overall": score["overall"],
                    "components": score["components"],
                    "submission_sha256": report["submission_sha256"],
                }
            )
    trusted = score_episode(
        episode / "manifest.json",
        episode / "input" / "base.xml",
        episode / "evaluator" / "target.xml",
        episode / "evaluator" / "target.xml",
        second_candidate_path=episode / "evaluator" / "target.xml",
        provenance_ok=True,
    ).as_dict()
    return {
        "status": "PASS",
        "episode_id": audit["episode_id"],
        "trusted_oracle_score": trusted["overall"],
        "baselines": rows,
        "note": (
            "public-hardcode deliberately embeds this public answer; separation must be "
            "measured over evaluator-only episodes."
        ),
    }


def grade_suite_candidate(
    suite_root: Path,
    submission: Path,
    split: str = "hidden",
) -> dict[str, object]:
    """Grade one frozen procedure on a private suite without revealing hidden identities."""
    if split not in {"public", "hidden", "all"}:
        raise ValueError(f"unsupported suite split: {split}")
    suite, selected = _suite_episode_paths(suite_root, split)
    reports = [grade_candidate(path, submission) for path in selected]
    score_rows: list[dict[str, Any]] = []
    for report in reports:
        score = report.get("score")
        if not isinstance(score, dict):
            raise TypeError("episode grade lacks a score")
        score_rows.append(score)
    component_names = (
        list(score_rows[0]["components"])
        if score_rows and isinstance(score_rows[0].get("components"), dict)
        else []
    )
    aggregate = {
        "overall": _mean([float(row["overall"]) for row in score_rows]),
        "components": {
            name: _mean(
                [float(_object(row["components"], "score components")[name]) for row in score_rows]
            )
            for name in component_names
        },
    }
    result: dict[str, object] = {
        "status": "PASS",
        "suite_kind": suite.get("suite_kind"),
        "split": split,
        "episode_count": len(selected),
        "aggregate": aggregate,
        "submission_sha256": reports[0]["submission_sha256"] if reports else None,
    }
    if split == "public":
        result["public_cases"] = [
            {"episode_id": report["episode_id"], "score": report["score"]} for report in reports
        ]
    else:
        result["hidden_details_redacted"] = True
    return result


def score_suite_baselines(suite_root: Path) -> dict[str, object]:
    """Measure public-answer hardcoding and other weak procedures on held-out cases."""
    suite, public_paths = _suite_episode_paths(suite_root, "public")
    if len(public_paths) != 1:
        raise ValueError("suite baseline matrix requires exactly one public episode")
    public = public_paths[0]
    public_manifest = _read_json(public / "manifest.json")
    public_id = public_manifest.get("episode_id")
    if not isinstance(public_id, str):
        raise TypeError("public episode lacks an identifier")
    public_target = _read_regular(public / "evaluator" / "target.xml")
    _, hidden_paths = _suite_episode_paths(suite_root, "hidden")
    trusted = _score_suite_oracle(suite_root)
    rows: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(
        prefix="psephos-regpatch-suite-baselines-", dir=_TEMP_ROOT
    ) as name:
        root = Path(name)
        for baseline in BASELINE_NAMES:
            candidate = root / baseline
            write_baseline(
                baseline,
                candidate,
                public_episode_id=public_id,
                public_target=public_target,
            )
            public_report = grade_suite_candidate(suite_root, candidate, "public")
            hidden_report = grade_suite_candidate(suite_root, candidate, "hidden")
            all_aggregate = _weighted_aggregate(
                _object(public_report["aggregate"], "public aggregate"),
                _integer(public_report["episode_count"], "public episode_count"),
                _object(hidden_report["aggregate"], "hidden aggregate"),
                _integer(hidden_report["episode_count"], "hidden episode_count"),
            )
            rows.append(
                {
                    "baseline": baseline,
                    "submission_sha256": public_report["submission_sha256"],
                    "public": public_report["aggregate"],
                    "hidden": hidden_report["aggregate"],
                    "all": all_aggregate,
                }
            )
    return {
        "status": "PASS",
        "suite_kind": suite.get("suite_kind"),
        "public_episode_count": 1,
        "hidden_episode_count": len(hidden_paths),
        "trusted_oracle": trusted,
        "baselines": rows,
    }


def run_demo(destination: Path) -> dict[str, object]:
    """One command from retained official bytes to a clean bundle and score matrix."""
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"refusing to overwrite demo: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{destination.name}-staging-", dir=destination.parent
    ) as temporary_name:
        staged = Path(temporary_name) / "demo"
        staged.mkdir()
        episode = staged / "evaluator-episode"
        compile_report = compile_episode(PILOT_SPEC, episode)
        bundle_report = bundle_episode(episode, staged / "candidate-bundle")
        bundle_report["bundle"] = "candidate-bundle"
        baseline_report = score_baselines(episode)
        report = {
            "schema_version": 1,
            "status": "PASS",
            "compile": compile_report,
            "bundle": bundle_report,
            "evaluation": baseline_report,
        }
        _write_json(staged / "demo-report.json", report)
        os.replace(staged, destination)
    report["output"] = str(destination)
    report["demo_sha256"] = _tree_digest(destination)
    return report


def _audit_evaluator_episode(episode: Path) -> dict[str, object]:
    manifest = _read_json(episode / "manifest.json")
    receipt = _read_json(episode / "evaluator" / "receipt.json")
    episode_id = manifest.get("episode_id")
    if (
        not isinstance(episode_id, str)
        or receipt.get("episode_id") != episode_id
        or receipt.get("status") != "ACCEPTED"
    ):
        raise ValueError(
            "evaluator receipt and public manifest do not identify one accepted episode"
        )
    public = receipt.get("public_manifest")
    sources = receipt.get("sources")
    if not isinstance(public, dict) or not isinstance(sources, dict):
        raise TypeError("evaluator receipt is incomplete")
    _verify_receipted_file(episode / "manifest.json", public, "public manifest")
    for label in ("base", "target"):
        row = sources.get(label)
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise TypeError(f"evaluator receipt lacks {label}")
        _verify_receipted_file(episode / str(row["path"]), row, label)
    RegPatchRunner.freeze_episode(episode)
    return {"episode_id": episode_id, "target_sha256": sources["target"]["sha256"]}


def _suite_episode_paths(
    suite_root: Path,
    split: str,
) -> tuple[dict[str, Any], list[Path]]:
    root = suite_root.resolve()
    suite = _read_json(root / "suite.json")
    raw_episodes = suite.get("episodes")
    if not isinstance(raw_episodes, list):
        raise TypeError("compiled suite has no episode array")
    selected: list[Path] = []
    for raw_row in raw_episodes:
        row = _object(raw_row, "suite episode")
        row_split = row.get("split")
        relative = row.get("path")
        if row_split not in {"public", "hidden"} or not isinstance(relative, str):
            raise ValueError("compiled suite contains an invalid episode row")
        if split != "all" and row_split != split:
            continue
        path = (root / relative).resolve()
        if root not in path.parents:
            raise ValueError("compiled suite episode path escapes its root")
        selected.append(path)
    if not selected:
        raise ValueError(f"compiled suite has no {split} episodes")
    return suite, selected


def _mean(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)


def _weighted_aggregate(
    first: Mapping[str, Any],
    first_count: int,
    second: Mapping[str, Any],
    second_count: int,
) -> dict[str, object]:
    """Combine already-scored split means without executing any episode again."""
    if first_count <= 0 or second_count <= 0:
        raise ValueError("weighted suite aggregation requires two non-empty splits")
    first_components = _object(first.get("components"), "first aggregate components")
    second_components = _object(second.get("components"), "second aggregate components")
    if set(first_components) != set(second_components):
        raise ValueError("suite aggregate components disagree")
    total = first_count + second_count

    def combine(left: float, right: float) -> float:
        return round((left * first_count + right * second_count) / total, 4)

    return {
        "overall": combine(float(first["overall"]), float(second["overall"])),
        "components": {
            name: combine(float(first_components[name]), float(second_components[name]))
            for name in first_components
        },
    }


def _score_suite_oracle(suite_root: Path) -> dict[str, object]:
    """Verify every evaluator target scores 100 without invoking candidate code."""
    _, episodes = _suite_episode_paths(suite_root, "all")
    scores = [
        score_episode(
            episode / "manifest.json",
            episode / "input" / "base.xml",
            episode / "evaluator" / "target.xml",
            episode / "evaluator" / "target.xml",
            second_candidate_path=episode / "evaluator" / "target.xml",
            provenance_ok=True,
        ).as_dict()
        for episode in episodes
    ]
    overall = _mean([_number(score["overall"], "oracle overall") for score in scores])
    return {
        "episode_count": len(scores),
        "overall": overall,
        "all_episodes_100": all(
            _number(score["overall"], "oracle overall") == 100.0 for score in scores
        ),
    }


def _select_array_row(report: Mapping[str, Any], field: str, index: int) -> dict[str, Any]:
    values = report.get(field)
    if not isinstance(values, list):
        raise TypeError(f"report has no {field} array")
    if not 0 <= index < len(values):
        raise IndexError(f"index is outside the report {field} array")
    value = values[index]
    if not isinstance(value, dict):
        raise TypeError(f"selected {field} row is not an object")
    return value


def _integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{label} must be an integer")
    return value


def _number(value: object, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{label} must be numeric")
    return float(value)


def _verify_receipted_file(path: Path, row: Mapping[str, object], label: str) -> None:
    expected_hash = row.get("sha256")
    expected_bytes = row.get("byte_count")
    if not isinstance(expected_hash, str) or not isinstance(expected_bytes, int):
        raise TypeError(f"evaluator receipt has invalid {label} hash/size")
    payload = _read_regular(path)
    if len(payload) != expected_bytes or hashlib.sha256(payload).hexdigest() != expected_hash:
        raise ValueError(f"{label} bytes differ from evaluator receipt")


def _audit_candidate_bundle(
    root: Path, *, private_sha256s: set[str] | frozenset[str] = frozenset()
) -> None:
    frozen = RegPatchRunner.freeze_episode(root / "episode")
    expected_files = {
        "BUNDLE.json",
        "TASK.md",
        "submission/solution.ts",
        *(f"episode/{item.path.as_posix()}" for item in frozen.files),
    }
    expected_directories = {"episode", "submission"}
    for relative in expected_files:
        parent = Path(relative).parent
        while parent != Path("."):
            expected_directories.add(parent.as_posix())
            parent = parent.parent

    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    forbidden_markers = (
        b"evaluator/target.xml",
        b"target_sha256",
        b"private_receipt",
        b"data/regpatch_evaluator",
        b"/.git/",
        b"\\.git\\",
        b"/users/",
        b"\\users\\",
        b"/private/var/",
        b"file://",
    )
    private_markers = tuple(value.encode("ascii") for value in sorted(private_sha256s))
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError(f"candidate bundle contains a non-regular path: {relative}")
        if stat.S_ISDIR(mode):
            actual_directories.add(relative)
            continue
        actual_files.add(relative)
        payload = _read_regular(path)
        lowered = payload.lower()
        digest = hashlib.sha256(payload).hexdigest()
        if digest in private_sha256s:
            raise ValueError(f"candidate bundle contains evaluator-only bytes: {relative}")
        if any(marker in lowered for marker in (*forbidden_markers, *private_markers)):
            raise ValueError(f"candidate bundle contains a private marker: {relative}")

    if actual_files != expected_files or actual_directories != expected_directories:
        raise ValueError("candidate bundle differs from its exact public file allowlist")
    if _read_regular(root / "TASK.md") != _read_regular(TASK_CARD):
        raise ValueError("candidate task contract differs from the trusted task card")

    bundle_receipt = _read_json(root / "BUNDLE.json")
    starter = bundle_receipt.get("starter")
    if (
        bundle_receipt.get("schema_version") != 1
        or bundle_receipt.get("bundle_kind") != "psephos_regpatch_candidate"
        or bundle_receipt.get("episode_id") != frozen.episode_id
        or starter not in BASELINE_NAMES
        or starter == "public-hardcode"
    ):
        raise ValueError("candidate bundle receipt is invalid")
    expected_starter = baseline_source(starter)
    if _read_regular(root / "submission/solution.ts") != expected_starter.encode("utf-8"):
        raise ValueError("candidate starter differs from its receipted baseline")
    expected_receipts = [row for row in _file_receipts(root) if row["path"] != "BUNDLE.json"]
    if bundle_receipt.get("files") != expected_receipts:
        raise ValueError("candidate bundle file receipts disagree with its public bytes")


def _regular_tree_hashes(root: Path, label: str) -> set[str]:
    hashes: set[str] = set()
    for path in root.rglob("*"):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError(f"{label} contains a non-regular path: {path}")
        if stat.S_ISREG(mode):
            hashes.add(hashlib.sha256(_read_regular(path)).hexdigest())
    return hashes


def _file_receipts(root: Path) -> list[dict[str, object]]:
    rows = []
    for path in sorted(value for value in root.rglob("*") if value.is_file()):
        payload = _read_regular(path)
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "byte_count": len(payload),
            }
        )
    return rows


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for row in _file_receipts(root):
        digest.update(str(row["path"]).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(row["sha256"]).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _read_regular(path: Path) -> bytes:
    mode = path.lstat().st_mode
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ValueError(f"expected regular file: {path}")
    return path.read_bytes()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(_read_regular(path))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be an object")
    return value


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _print(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


__all__ = ["build_parser", "bundle_episode", "grade_candidate", "main", "run_demo"]
