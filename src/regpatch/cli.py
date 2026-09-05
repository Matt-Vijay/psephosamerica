"""Short command surface for compiling, bundling, and grading RegPatch episodes."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from src.regpatch.baselines import BASELINE_NAMES
from src.regpatch.compiler import compile_episode
from src.regpatch.corpus import (
    DEFAULT_EVALUATOR_ROOT,
    audit_retained_corpus,
    compile_retained_corpus,
)
from src.regpatch.evaluation import (
    PACKAGE_ROOT,
    PILOT_ROOT,
    PILOT_SPEC,
    TASK_CARD,
    _read_json,
    bundle_episode,
    grade_candidate,
    grade_suite_candidate,
    run_demo,
    score_baselines,
    score_suite_baselines,
)
from src.regpatch.sources import (
    acquire_candidate_sources,
    expand_candidate_windows,
    probe_ecfr_versions,
    probe_federal_register,
)

__all__ = [
    "PACKAGE_ROOT",
    "PILOT_ROOT",
    "PILOT_SPEC",
    "TASK_CARD",
    "build_parser",
    "bundle_episode",
    "grade_candidate",
    "grade_suite_candidate",
    "main",
    "run_demo",
    "score_baselines",
    "score_suite_baselines",
]


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
        result = compile_episode(arguments.spec, arguments.output)
    elif arguments.command == "seed":
        result = compile_episode(PILOT_SPEC, arguments.output)
    elif arguments.command == "bundle":
        result = bundle_episode(arguments.episode, arguments.output, starter=arguments.starter)
    elif arguments.command == "grade":
        result = grade_candidate(arguments.episode, arguments.submission)
    elif arguments.command == "baselines":
        result = score_baselines(arguments.episode)
    elif arguments.command == "probe":
        result = probe_federal_register(
            arguments.store,
            start_date=arguments.start_date,
            end_date=arguments.end_date,
            max_candidates=arguments.max_candidates,
        )
    elif arguments.command == "version-probe":
        result = probe_ecfr_versions(
            arguments.store,
            title=arguments.title,
            part=arguments.part,
            issue_date_start=arguments.start_date,
            issue_date_end=arguments.end_date,
        )
    elif arguments.command == "acquire":
        report = _read_json(arguments.probe_report)
        candidate = _select_array_row(report, "candidates", arguments.candidate_index)
        version_report = _read_json(arguments.version_report)
        windows = expand_candidate_windows(candidate, version_report)
        if not 0 <= arguments.window_index < len(windows):
            raise IndexError("window_index is outside the expanded observed windows")
        candidate = windows[arguments.window_index]
        path = acquire_candidate_sources(arguments.store, candidate)
        result = {
            "status": "PASS",
            "expanded_window_count": len(windows),
            "selected_window_index": arguments.window_index,
            "source_spec": str(path),
        }
    elif arguments.command == "corpus-audit":
        result = audit_retained_corpus(arguments.store)
    elif arguments.command == "corpus-build":
        result = compile_retained_corpus(arguments.output, corpus_root=arguments.store)
    elif arguments.command == "grade-suite":
        result = grade_suite_candidate(arguments.suite, arguments.submission, arguments.split)
    elif arguments.command == "suite-baselines":
        result = score_suite_baselines(arguments.suite)
    elif arguments.command == "demo":
        result = run_demo(arguments.output)
    else:
        raise AssertionError(f"unknown command: {arguments.command}")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


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
