"""Operator handoff/status/packet subcommands."""

from __future__ import annotations

import argparse


def _add_verify_prediction_operator_handoff(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-operator-handoff",
        help="Verify the linked env, readiness, and resume verifier handoff artifacts.",
    )
    p.add_argument(
        "--env-preflight-verify",
        required=True,
        metavar="PATH",
        help="Path to the verify-runtime-env-preflight artifact.",
    )
    p.add_argument(
        "--readiness-summary-verify",
        required=True,
        metavar="PATH",
        help="Path to the verify-prediction-offline-readiness-summary artifact.",
    )
    p.add_argument(
        "--resume-script-verify",
        required=True,
        metavar="PATH",
        help="Path to the verify-prediction-resume-script artifact.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if any verifier artifact appears to contain literal secrets.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the operator handoff verification payload.",
    )
    p.add_argument(
        "--runbook-output",
        default=None,
        metavar="PATH",
        help="Optional Markdown path for a secret-free operator handoff runbook.",
    )


def _add_verify_prediction_operator_runbook(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-operator-runbook",
        help="Verify a generated prediction operator handoff Markdown runbook.",
    )
    p.add_argument(
        "--handoff-verify",
        required=True,
        metavar="PATH",
        help="Path to the verify-prediction-operator-handoff artifact.",
    )
    p.add_argument(
        "--runbook",
        required=True,
        metavar="PATH",
        help="Path to the generated Markdown operator runbook.",
    )
    p.add_argument(
        "--require-handoff-runbook-sha",
        action="store_true",
        help="Return non-green if the runbook SHA does not match the handoff artifact.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if the runbook appears to contain literal secrets.",
    )
    p.add_argument(
        "--require-verified-artifact-hashes",
        action="store_true",
        help="Return non-green if verifier artifact paths or hashes are absent.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the operator runbook verification payload.",
    )


def _add_prediction_operator_status(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "prediction-operator-status",
        help="Summarize prediction launch readiness from a verified operator runbook.",
    )
    p.add_argument(
        "--runbook-verify",
        required=True,
        metavar="PATH",
        help="Path to the verify-prediction-operator-runbook artifact.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if the linked status inputs appear to contain literal secrets.",
    )
    p.add_argument(
        "--require-launch-ready",
        action="store_true",
        help="Return non-green while the underlying readiness summary is still blocked.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the compact operator status payload.",
    )


def _add_verify_prediction_operator_status(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-operator-status",
        help="Verify a compact prediction operator status artifact.",
    )
    p.add_argument(
        "--artifact",
        required=True,
        metavar="PATH",
        help="Path to the prediction-operator-status artifact.",
    )
    p.add_argument(
        "--runbook-verify",
        default=None,
        metavar="PATH",
        help="Optional expected verify-prediction-operator-runbook artifact path.",
    )
    p.add_argument(
        "--require-run-metadata",
        action="store_true",
        help="Return non-green if the status artifact lacks run metadata.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if the status artifact or linked inputs contain literal secrets.",
    )
    p.add_argument(
        "--require-launch-ready",
        action="store_true",
        help="Return non-green while the verified status is still blocked.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the operator status verification payload.",
    )


def _add_prediction_operator_packet_manifest(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "prediction-operator-packet-manifest",
        help="Write a manifest for the verified prediction operator handoff packet.",
    )
    p.add_argument(
        "--status-verify",
        required=True,
        metavar="PATH",
        help="Path to the verify-prediction-operator-status artifact.",
    )
    p.add_argument(
        "--require-existing-files",
        action="store_true",
        help="Return non-green if any packet manifest file is missing.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if packet files appear to contain literal secrets.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the operator packet manifest.",
    )


def _add_verify_prediction_operator_packet_manifest(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-operator-packet-manifest",
        help="Verify a prediction operator packet manifest.",
    )
    p.add_argument(
        "--artifact",
        required=True,
        metavar="PATH",
        help="Path to the prediction-operator-packet-manifest artifact.",
    )
    p.add_argument(
        "--status-verify",
        default=None,
        metavar="PATH",
        help="Optional expected verify-prediction-operator-status artifact path.",
    )
    p.add_argument(
        "--require-run-metadata",
        action="store_true",
        help="Return non-green if the packet manifest lacks run metadata.",
    )
    p.add_argument(
        "--require-existing-files",
        action="store_true",
        help="Return non-green if any packet file is missing.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if packet files appear to contain literal secrets.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the packet manifest verification payload.",
    )


def _add_prediction_operator_packet_export(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "prediction-operator-packet-export",
        help="Copy a verified prediction operator packet into one directory.",
    )
    p.add_argument(
        "--manifest-verify",
        required=True,
        metavar="PATH",
        help="Path to the verify-prediction-operator-packet-manifest artifact.",
    )
    p.add_argument(
        "--target-dir",
        required=True,
        metavar="PATH",
        help="Directory where packet files and export manifest should be written.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if exported packet files appear to contain literal secrets.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the packet export summary.",
    )


def _add_verify_prediction_operator_packet_export(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-operator-packet-export",
        help="Verify a copied prediction operator packet export.",
    )
    p.add_argument(
        "--artifact",
        required=True,
        metavar="PATH",
        help="Path to the prediction-operator-packet-export artifact.",
    )
    p.add_argument(
        "--require-run-metadata",
        action="store_true",
        help="Return non-green if the export artifact lacks run metadata.",
    )
    p.add_argument(
        "--require-export-manifest",
        action="store_true",
        help="Return non-green if the export manifest is missing or mismatched.",
    )
    p.add_argument(
        "--require-exported-files",
        action="store_true",
        help="Return non-green if any exported file is missing or hash-mismatched.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if exported files appear to contain literal secrets.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the packet export verification payload.",
    )


def _add_verify_prediction_operator_packet_directory(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-operator-packet-directory",
        help="Verify a copied prediction operator packet directory in-place.",
    )
    p.add_argument(
        "--packet-dir",
        required=True,
        metavar="PATH",
        help="Path to the exported packet directory.",
    )
    p.add_argument(
        "--require-readme",
        action="store_true",
        help="Return non-green if README.md is missing.",
    )
    p.add_argument(
        "--require-checksums",
        action="store_true",
        help="Return non-green if SHA256SUMS is missing or mismatched.",
    )
    p.add_argument(
        "--require-exported-files",
        action="store_true",
        help="Return non-green if copied packet files are missing or hash-mismatched.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if copied packet files appear to contain literal secrets.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the packet directory verification payload.",
    )


def _add_prediction_operator_resume_plan(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "prediction-operator-resume-plan",
        help="Dry-run the prediction operator packet resume script without executing it.",
    )
    p.add_argument(
        "--packet-dir",
        required=True,
        metavar="PATH",
        help="Path to the exported operator packet directory.",
    )
    p.add_argument(
        "--dotenv",
        default=None,
        metavar="PATH",
        help="Optional dotenv file to inspect for env-key presence without printing values.",
    )
    p.add_argument(
        "--require-verified-packet",
        action="store_true",
        help="Return non-green if the packet directory fails in-place verification.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if the resume script appears to contain literal secrets.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the resume-plan dry-run payload.",
    )


def _add_verify_prediction_operator_resume_plan(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-operator-resume-plan",
        help="Verify a prediction operator resume-plan dry-run artifact.",
    )
    p.add_argument(
        "--artifact",
        required=True,
        metavar="PATH",
        help="Path to a prediction-operator-resume-plan artifact.",
    )
    p.add_argument(
        "--require-run-metadata",
        action="store_true",
        help="Return non-green if the artifact lacks run metadata.",
    )
    p.add_argument(
        "--require-matches-current-packet",
        action="store_true",
        help="Return non-green if recomputing from the packet directory differs.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if the artifact appears to contain literal secrets.",
    )
    p.add_argument(
        "--require-launch-ready",
        action="store_true",
        help="Return non-green unless the resume plan has no blocked env guards.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the resume-plan verification payload.",
    )


def _add_verify_prediction_operator_resume_run(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-operator-resume-run",
        help="Verify a saved packet run_resume.py audit artifact.",
    )
    p.add_argument(
        "--packet-dir",
        required=True,
        metavar="PATH",
        help="Path to the exported operator packet directory.",
    )
    p.add_argument(
        "--artifact",
        required=True,
        metavar="PATH",
        help="Path to a run_resume.py JSON artifact written with --output.",
    )
    p.add_argument(
        "--dotenv",
        default=None,
        metavar="PATH",
        help="Optional dotenv file to match against the artifact-recorded hash.",
    )
    p.add_argument(
        "--require-run-metadata",
        action="store_true",
        help="Return non-green if the artifact lacks run metadata.",
    )
    p.add_argument(
        "--require-ok",
        action="store_true",
        help="Return non-green unless the run_resume.py artifact is ok.",
    )
    p.add_argument(
        "--require-dry-run",
        action="store_true",
        help="Return non-green unless the saved run was a dry run.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if the artifact appears to contain literal secrets.",
    )
    p.add_argument(
        "--require-congress-prediction-inputs",
        action="store_true",
        help=(
            "Return non-green unless run metadata proves Congress load produced "
            "member, bill, and vote prediction inputs."
        ),
    )
    p.add_argument(
        "--require-strict-eval-window-run",
        action="store_true",
        help=(
            "Return non-green unless run metadata proves the resume artifact came "
            "from a strict eval-window run."
        ),
    )
    p.add_argument(
        "--require-selected-source-artifact-hashes",
        action="store_true",
        help=(
            "Return non-green unless every selected resume source artifact has a "
            "recorded SHA-256 hash."
        ),
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the resume-run verification payload.",
    )
