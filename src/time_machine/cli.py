"""Small command surface for the American Legislative Time Machine."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.time_machine.build import BuildConfig, build_time_machine
from src.time_machine.catalog import query, status
from src.time_machine.govinfo_text import fetch_bill_text
from src.time_machine.integrity import generate_integrity_report
from src.time_machine.inventory import inventory_inputs, load_inventory

# One enacted bill per Congress, plus its introduced version.  This bounded
# official slice proves versioned text across the full GovInfo BILLS era while
# the exact same command accepts arbitrary 113-present package IDs.
REPRESENTATIVE_PACKAGES = (
    "BILLS-113hr2642ih",
    "BILLS-113hr2642enr",
    "BILLS-114hr22ih",
    "BILLS-114hr22enr",
    "BILLS-115hr1ih",
    "BILLS-115hr1enr",
    "BILLS-116hr748ih",
    "BILLS-116hr748enr",
    "BILLS-117hr5376ih",
    "BILLS-117hr5376enr",
    "BILLS-118hr2882ih",
    "BILLS-118hr2882enr",
    "BILLS-119hr1ih",
    "BILLS-119hr1enr",
)


def _paths(args: argparse.Namespace) -> tuple[Path, Path]:
    source = Path(args.source_root).expanduser().resolve()
    output = (
        Path(args.output).expanduser().resolve() if args.output else source / "data/time_machine"
    )
    return source, output


def _json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def _inventory(args: argparse.Namespace) -> int:
    source, output = _paths(args)
    entries = inventory_inputs(source, output, state_limit=args.state_limit)
    _json(
        {
            "status": "complete",
            "output": str(output / "inventory.json"),
            "artifacts": len(entries),
            "bytes": sum(row.byte_count for row in entries),
            "source_families": sorted({row.source_family for row in entries}),
        }
    )
    return 0


def _fetch_text(args: argparse.Namespace) -> int:
    source, output = _paths(args)
    # This is a hard ordering constraint: the preexisting corpus is inventoried
    # before the first network request.  fetch_bill_text enforces it again.
    load_inventory(output)
    packages = tuple(args.package) if args.package else REPRESENTATIVE_PACKAGES
    rows: list[dict[str, Any]] = []
    for package_id in packages:
        artifact = fetch_bill_text(package_id, output, refresh=args.refresh)
        rows.append(
            {
                "package_id": artifact.package.package_id,
                "revision": artifact.revision,
                "sha256": artifact.content_sha256,
                "bytes": artifact.byte_count,
                "issued_date": artifact.issued_date,
                "from_cache": artifact.from_cache,
                "text_characters": len(artifact.text_content),
            }
        )
        print(
            f"{artifact.package.package_id}: revision={artifact.revision} "
            f"bytes={artifact.byte_count} cache={artifact.from_cache}",
            flush=True,
        )
    prior = json.loads((output / "inventory.json").read_text(encoding="utf-8"))
    inventory_inputs(
        source,
        output,
        state_limit=prior.get("state_limit"),
    )
    _json({"status": "complete", "packages": rows, "inventory_refreshed": True})
    return 0


def _build(args: argparse.Namespace) -> int:
    source, output = _paths(args)
    result = build_time_machine(
        BuildConfig(
            source_root=source,
            output_root=output,
            state_limit=args.state_limit,
            include_states=not args.no_states,
            force=args.force,
        ),
        progress=lambda message: print(message, flush=True),
    )
    _json(result.__dict__)
    return 0


def _status(args: argparse.Namespace) -> int:
    _source, output = _paths(args)
    _json(status(output))
    return 0


def _query(args: argparse.Namespace) -> int:
    _source, output = _paths(args)
    sql = Path(args.file).read_text(encoding="utf-8") if args.file else args.sql
    if not sql:
        raise ValueError("provide SQL or --file")
    columns, rows = query(output, sql)
    if args.format == "json":
        _json([dict(zip(columns, row, strict=True)) for row in rows])
    else:
        print("\t".join(columns))
        for row in rows:
            print("\t".join("" if value is None else str(value) for value in row))
    return 0


def _integrity(args: argparse.Namespace) -> int:
    source, output = _paths(args)
    report = generate_integrity_report(output, source_root=source)
    _json(
        {
            "status": report["status"],
            "hard_failures": report["hard_failures"],
            "json": str(output / "integrity.json"),
            "markdown": str(output / "integrity.md"),
        }
    )
    return 0 if report["status"] == "pass" else 1


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="python -m src.time_machine",
        description="Local, point-in-time U.S. legislative evidence substrate",
    )
    root.add_argument("--source-root", default=".", help="existing Psephos repository/data root")
    root.add_argument("--output", default=None, help="canonical output root")
    commands = root.add_subparsers(dest="command", required=True)

    inventory = commands.add_parser("inventory", help="hash the exact local inputs")
    inventory.add_argument("--state-limit", type=int, default=None)
    inventory.set_defaults(run=_inventory)

    fetch = commands.add_parser("fetch-text", help="fetch official GovInfo BILLS XML")
    fetch.add_argument("--package", action="append", default=[])
    fetch.add_argument("--refresh", action="store_true")
    fetch.set_defaults(run=_fetch_text)

    build = commands.add_parser("build", help="publish canonical Parquet and DuckDB catalog")
    build.add_argument("--state-limit", type=int, default=None)
    build.add_argument("--no-states", action="store_true")
    build.add_argument("--force", action="store_true")
    build.set_defaults(run=_build)

    show = commands.add_parser("status", help="show build/table/integrity state")
    show.set_defaults(run=_status)

    sql = commands.add_parser("query", help="run read-only DuckDB SQL")
    sql.add_argument("sql", nargs="?", default=None)
    sql.add_argument("--file", default=None)
    sql.add_argument("--format", choices=("tsv", "json"), default="tsv")
    sql.set_defaults(run=_query)

    integrity = commands.add_parser("integrity", help="regenerate exact integrity report")
    integrity.set_defaults(run=_integrity)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return int(args.run(args))


__all__ = ["REPRESENTATIVE_PACKAGES", "main", "parser"]
