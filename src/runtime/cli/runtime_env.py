"""Runtime environment preflight subcommands."""

from __future__ import annotations

import argparse


def _add_runtime_env_preflight(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "runtime-env-preflight",
        help="Check required runtime environment variables without printing secret values.",
    )
    p.add_argument(
        "--require-env",
        action="append",
        default=None,
        metavar="NAME",
        help="Environment variable that must be non-empty in this process; repeatable.",
    )
    p.add_argument(
        "--dotenv",
        default=None,
        metavar="PATH",
        help="Optional .env-style file to inspect for presence only; values are never printed.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the sanitized env preflight artifact.",
    )
    p.add_argument(
        "--template-output",
        default=None,
        metavar="PATH",
        help="Optional .env template path with required keys and empty values only.",
    )


def _add_verify_runtime_env_preflight(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-runtime-env-preflight",
        help="Verify a sanitized runtime-env-preflight artifact.",
    )
    p.add_argument(
        "--artifact",
        required=True,
        metavar="PATH",
        help="Path to a runtime-env-preflight JSON artifact.",
    )
    p.add_argument(
        "--require-next-actions",
        action="store_true",
        help="Return non-green when missing env vars have no next actions.",
    )
    p.add_argument(
        "--require-template-output",
        action="store_true",
        help="Return non-green when a dotenv/template handoff is missing or stale.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if the artifact appears to contain literal secrets.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the env-preflight verification payload.",
    )
