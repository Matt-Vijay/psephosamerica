"""Stage-specific CLI; normal acquisition lives in psephos.collect_virginia."""

from __future__ import annotations

import argparse
import fcntl
import json
from pathlib import Path

from psephos.collect_virginia import (
    VirginiaAcquirer,
    discover,
    media,
    prefaces,
    run,
    validate_inventory,
)

__all__ = ["VirginiaAcquirer", "discover", "media", "prefaces", "run", "validate_inventory"]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, default=Path("data"))
    p.add_argument("--limit", type=int)
    stages = p.add_mutually_exclusive_group()
    stages.add_argument("--media", action="store_true", help="Acquire embedded official images")
    stages.add_argument(
        "--prefaces", action="store_true", help="Acquire publisher agency summaries"
    )
    stages.add_argument("--inventory", action="store_true", help="Native inventory preflight only")
    args = p.parse_args()
    if args.limit is not None and (args.limit < 0 or args.inventory or args.prefaces or args.media):
        p.error("--limit must be nonnegative and applies only to chapter acquisition")
    directory = args.data / "virginia-completion"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "writer.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = (
            discover(args.data)
            if args.inventory
            else prefaces(args.data)
            if args.prefaces
            else media(args.data)
            if args.media
            else run(args.data, args.limit)
        )
    print(json.dumps(result, indent=2))
    raise SystemExit(bool(result.get("errors")))


if __name__ == "__main__":
    main()
