"""Local publish demo: synthetic payloads → planned files → written → verified.

No network calls, no database.

Usage::

    import tempfile
    from pathlib import Path
    from src.demo.publish_demo import run_publish_demo

    with tempfile.TemporaryDirectory() as tmp:
        result = run_publish_demo(Path(tmp))
        print(result.failures)   # [] if all good
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.demo.conflict_demo import DemoResult, run_conflict_demo
from src.export.filesystem import verify_written_files, write_planned_files
from src.export.writer import PlannedFile, plan_snapshot


_SNAPSHOT_ID = "2026-04-13"


@dataclass(frozen=True)
class PublishDemoResult:
    demo: DemoResult
    planned: list[PlannedFile]
    failures: list[str]
    target_dir: Path

    @property
    def planned_count(self) -> int:
        return len(self.planned)

    @property
    def success(self) -> bool:
        return len(self.failures) == 0


def run_publish_demo(target_dir: Path) -> PublishDemoResult:
    """Run the full generate-plan-write-verify flow into *target_dir*.

    Raises:
        AssertionError: Propagated from :func:`run_conflict_demo` if no rules fire.
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    demo = run_conflict_demo()

    planned = plan_snapshot(
        snapshot_id=_SNAPSHOT_ID,
        member_profiles=[demo.member_profile],
        zip_feeds=[demo.zip_feed],
        evidence_cards=[demo.evidence_card],
    )

    write_planned_files(planned, target_dir)

    failures = verify_written_files(planned, target_dir)

    return PublishDemoResult(
        demo=demo,
        planned=planned,
        failures=failures,
        target_dir=target_dir,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Write snapshot to a temp directory and print a compact JSON summary."""
    import json
    import sys
    import tempfile

    with tempfile.TemporaryDirectory(prefix="openpact_publish_demo_") as tmp:
        result = run_publish_demo(Path(tmp))

    summary = {
        "status": "ok" if result.success else "error",
        "planned_files": result.planned_count,
        "failures": result.failures,
        "snapshot_id": result.demo.snapshot_manifest.snapshot_id,
        "member": result.demo.member["full_name"],
        "bioguide_id": result.demo.member["bioguide_id"],
        "rule_fires": len(result.demo.fires),
    }
    json.dump(summary, sys.stdout, indent=2)
    sys.stdout.write("\n")
    if not result.success:
        sys.exit(1)


if __name__ == "__main__":
    main()
