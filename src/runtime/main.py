from __future__ import annotations

import sys
from typing import Any

from src.runtime import commands as runtime_commands
from src.runtime.cli import parse_args
from src.runtime.commands import COMMAND_REGISTRY
from src.runtime.output import as_json

__all__ = ["COMMAND_REGISTRY", "main", "run"]


def run(argv: list[str]) -> int:
    try:
        args = parse_args(argv)
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
