"""Legacy edition-specific CLI; normal acquisition lives in psephos.collect_georgia."""

from __future__ import annotations

import argparse
import fcntl
from pathlib import Path

from psephos.collect_georgia import (
    CAP,
    FILE_CAP,
    GeorgiaAcquirer,
    old_state,
    plan,
    run,
)
from psephos.store import Store, json_text

__all__ = ["CAP", "FILE_CAP", "GeorgiaAcquirer", "old_state", "plan", "run"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--index-receipt", type=int)
    parser.add_argument("--edition", help="Exact cover filing-through date from publisher evidence")
    parser.add_argument("--acquire-only", action="store_true")
    args = parser.parse_args()
    s = Store(args.data)
    directory = s.root / "georgia-completion"
    directory.mkdir(exist_ok=True)
    try:
        with (directory / "writer.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state = plan(s, directory, args.index_receipt, args.edition)
            print(json_text(run(s, directory, state, args.acquire_only)), flush=True)
    finally:
        s.close()


if __name__ == "__main__":
    main()
