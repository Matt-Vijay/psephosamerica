from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import Any

from src.runtime import commands as runtime_commands
from src.runtime.cli import parse_args
from src.runtime.commands import COMMAND_REGISTRY
from src.runtime.oracle_contracts import CongressOracleOptions, LocalOracleOptions
from src.runtime.output import as_json

__all__ = ["COMMAND_REGISTRY", "main", "run"]


def _current_congress(today: dt.date | None = None) -> int:
    current = today if today is not None else dt.date.today()
    return ((current.year - 1789) // 2) + 1


def _dispatch_run_oracle_local(args: Any) -> dict[str, Any]:
    runtime = runtime_commands.build_runtime()
    configured_congress = args.congress if args.congress is not None else _current_congress()
    congress_source = "explicit-arg" if args.congress is not None else "current-date-default"
    congress_options = CongressOracleOptions(
        congress=configured_congress,
        chamber=None if args.chamber == "both" else args.chamber,
        limit=args.limit,
        congress_source=congress_source,
    )
    options = LocalOracleOptions(
        congress_options=congress_options,
        snapshot_date=args.snapshot_date,
        target_dir=Path(args.target_dir),
        snapshot_id=args.snapshot_id,
    )
    result = runtime_commands.run_oracle_local_command(
        runtime.context,
        Path(args.congress_archive),
        runtime_commands.load_disclosures_bundle(Path(args.disclosures_bundle)),
        options,
    )
    summary = runtime_commands.summarize_local_oracle_run_result(result)
    return {"ok": runtime_commands._oracle_summary_ok(summary), "command": "run-oracle-local", **summary}


def run(argv: list[str]) -> int:
    try:
        args = parse_args(argv)
        if args.command == "run-oracle-local":
            result = _dispatch_run_oracle_local(args)
        else:
            result = runtime_commands.dispatch_command(args)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1
    except Exception as exc:  # noqa: BLE001
        print(as_json({"ok": False, "error": str(exc)}))
        return 1

    print(as_json(result))
    return 0 if _result_succeeded(result) else 1


def main() -> None:
    sys.exit(run(sys.argv[1:]))


def _result_succeeded(result: dict[str, Any]) -> bool:
    return result.get("ok") is not False


if __name__ == "__main__":
    main()
