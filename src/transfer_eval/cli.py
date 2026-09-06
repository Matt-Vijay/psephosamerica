"""Short CLI for preparing, validating, bundling, and grading the one pilot."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from src.transfer_eval.cases import (
    CASES_ROOT,
    PILOT_CASES,
    PILOT_ROOT,
    audit_public_bundle,
    build_cases,
    case_path,
    copy_public_case,
)
from src.transfer_eval.contract import task_schema
from src.transfer_eval.reference import derive_reference, validate_oracle
from src.transfer_eval.runner import DenoRunner
from src.transfer_eval.score import aggregate_scores, score_case


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.transfer_eval")
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare", help="derive the fixed cases from inventoried ZIPs")
    prepare.add_argument("--inventory", type=Path, default=Path("data/time_machine/inventory.json"))
    prepare.add_argument("--output", type=Path, default=CASES_ROOT)

    validate = commands.add_parser("validate", help="prove fixtures and references match V1")
    validate.add_argument("--cases", type=Path, default=CASES_ROOT)
    validate.add_argument("--oracle", type=Path, default=Path("data/time_machine"))
    validate.add_argument("--without-oracle", action="store_true")

    bundle = commands.add_parser("bundle", help="make the clean candidate-facing directory")
    bundle.add_argument("output", type=Path)

    reference = commands.add_parser("reference", help="print the trusted answer for one case")
    reference.add_argument("case", type=Path)

    grade = commands.add_parser("grade", help="run and score one candidate file")
    grade.add_argument("submission", type=Path)
    grade.add_argument("--split", choices=("public", "hidden", "all"), default="hidden")
    grade.add_argument("--cases", type=Path, default=CASES_ROOT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.command == "prepare":
        paths = build_cases(arguments.inventory, arguments.output)
        public = paths[0]
        (public / "dev_expected.jsonl").write_text(
            derive_reference(public).jsonl(), encoding="utf-8"
        )
        _print({"status": "PASS", "cases": [str(path) for path in paths]})
        return 0
    if arguments.command == "validate":
        reports = []
        live_oracle = []
        for case in PILOT_CASES:
            path = case_path(case, arguments.cases)
            reference = derive_reference(path)
            report = {
                "case_id": case.case_id,
                "eligible_records": len(reference.eligible),
                "raw_records": len(reference.universe),
                "fixture": "PASS",
            }
            if not arguments.without_oracle:
                oracle_result = validate_oracle(path, arguments.oracle)
                report["oracle"] = oracle_result["status"]
                live_oracle.append(
                    {
                        "case_id": case.case_id,
                        "status": oracle_result["status"],
                        "tables": oracle_result["tables"],
                    }
                )
            reports.append(report)
        result = {"status": "PASS", "cases": reports}
        if not arguments.without_oracle and arguments.cases.resolve() == CASES_ROOT.resolve():
            _validate_receipt(arguments.oracle, live_oracle)
            result["oracle_receipt"] = "PASS"
        _print(result)
        return 0
    if arguments.command == "bundle":
        destination = arguments.output
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"refusing to overwrite: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f".{destination.name}-staging-", dir=destination.parent
        ) as temporary:
            staged = Path(temporary) / "bundle"
            staged.mkdir()
            shutil.copyfile(PILOT_ROOT / "TASK.md", staged / "TASK.md")
            shutil.copyfile(PILOT_ROOT / "starter.ts", staged / "solution.ts")
            (staged / "schema.json").write_text(
                json.dumps(task_schema(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            copy_public_case(staged / "case")
            audit = audit_public_bundle(staged)
            staged.rename(destination)
        _print(audit)
        return 0
    if arguments.command == "reference":
        sys.stdout.write(derive_reference(arguments.case).jsonl())
        return 0
    if arguments.command == "grade":
        selected = [
            case
            for case in PILOT_CASES
            if arguments.split == "all" or case.split == arguments.split
        ]
        runner = DenoRunner()
        submission_bytes, suffix = runner.read_submission(arguments.submission)
        submission_sha256 = hashlib.sha256(submission_bytes).hexdigest()
        scores = []
        run_success: list[bool] = []
        timeouts: list[bool] = []
        for case in selected:
            path = case_path(case, arguments.cases)
            reference = derive_reference(path)
            runs = runner.run_frozen(submission_bytes, suffix, path)
            scores.append(
                score_case(
                    reference,
                    runs.first.parsed,
                    runs.second.parsed,
                    first_ok=runs.first.ok,
                    second_ok=runs.second.ok,
                )
            )
            run_success.extend((runs.first.ok, runs.second.ok))
            timeouts.extend((runs.first.timed_out, runs.second.timed_out))
        grade_report = aggregate_scores(scores)
        case_details = grade_report.pop("cases")
        if arguments.split == "public":
            grade_report["cases"] = [
                {key: value for key, value in case.items() if key != "errors"}
                for case in case_details
            ]
        grade_report.update(
            {
                "contract_version": task_schema()["contract_version"],
                "deno_version": runner.version,
                "submission_sha256": submission_sha256,
                "split": arguments.split,
                "execution": {
                    "all_runs_successful": all(run_success),
                    "any_timeout": any(timeouts),
                    "case_count": len(selected),
                },
            }
        )
        _print(grade_report)
        return 0
    raise AssertionError(f"unknown command: {arguments.command}")


def _print(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _validate_receipt(oracle_root: Path, live_cases: list[dict[str, object]]) -> None:
    receipt = json.loads((PILOT_ROOT / "oracle_receipt.json").read_text(encoding="utf-8"))
    if receipt.get("cases") != live_cases:
        raise ValueError("live oracle projection differs from committed oracle receipt")
    expected = receipt["oracle"]
    paths = {
        "catalog_sha256": oracle_root / "time_machine.duckdb",
        "build_sha256": oracle_root / "build.json",
        "integrity_sha256": oracle_root / "integrity.json",
    }
    for field, path in paths.items():
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected.get(field) != actual:
            raise ValueError(f"oracle receipt hash mismatch: {field}")
    integrity = json.loads((oracle_root / "integrity.json").read_text(encoding="utf-8"))
    if expected.get("integrity_status") != integrity.get("status"):
        raise ValueError("oracle receipt integrity status mismatch")


__all__ = ["build_parser", "main"]
