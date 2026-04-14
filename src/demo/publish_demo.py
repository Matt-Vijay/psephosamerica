"""Local publish demo: synthetic canonical rows -> written files.

Proves the full stack can generate and publish a real snapshot locally
without any network calls or database connections.

Flow:
    1. Obtain synthetic payloads from :func:`~src.demo.conflict_demo.run_conflict_demo`.
    2. Build a :class:`~src.export.writer.PlannedFile` list via
       :func:`~src.export.writer.plan_snapshot`.
    3. Write the planned files to a caller-supplied temp directory via
       :func:`~src.export.filesystem.write_planned_files`.
    4. Verify on-disk content matches planned hashes via
       :func:`~src.export.filesystem.verify_written_files`.

Usage::

    import tempfile
    from pathlib import Path
    from src.demo.publish_demo import run_publish_demo

    with tempfile.TemporaryDirectory() as tmp:
        result = run_publish_demo(Path(tmp))
        print(result.failures)   # [] if all good
        print(result.planned_count)
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
    """All objects produced by a local publish demo run.

    Attributes:
        demo:          Full conflict-demo result (payloads, fires, manifest).
        planned:       Ordered list of files planned for publication.
        failures:      Relative paths that failed hash verification after write.
                       Empty list means all files verified successfully.
        target_dir:    Directory where files were written.
    """

    demo: DemoResult
    planned: list[PlannedFile]
    failures: list[str]
    target_dir: Path

    @property
    def planned_count(self) -> int:
        """Total number of files planned (data files + manifest)."""
        return len(self.planned)

    @property
    def success(self) -> bool:
        """True when every written file passed hash verification."""
        return len(self.failures) == 0


def run_publish_demo(target_dir: Path) -> PublishDemoResult:
    """Execute a deterministic local publish flow.

    Runs the conflict demo to obtain synthetic canonical payloads, plans the
    full snapshot file set, writes every file into *target_dir*, and verifies
    on-disk content against the planned SHA-256 hashes.

    Args:
        target_dir: Directory to write snapshot files into.  Created if it
                    does not exist.  Caller owns cleanup (use a
                    ``tempfile.TemporaryDirectory``).

    Returns:
        :class:`PublishDemoResult` with all intermediate objects and a
        ``failures`` list that is empty on success.

    Raises:
        AssertionError: Propagated from :func:`run_conflict_demo` if no rules
                        fire against the synthetic data.
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
