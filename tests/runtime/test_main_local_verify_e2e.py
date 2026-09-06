"""End-to-end tests for run(argv) with the verify-publish command.

Goals
-----
- Call run(argv) directly — no subprocesses.
- Exercise the verify-publish CLI path end-to-end.
- Use real temp publish trees built by the published-snapshot fixture support.
- No database access required: verify-publish is a pure filesystem operation.
- No network calls.

Boundary patch strategy
-----------------------
verify-publish requires no database connection; the verification pipeline
reads only the local filesystem.  Therefore no DB or runtime patches are
needed for this command path.

If the verify-publish dispatch is absent from main.py the tests in
TestRunVerifyPublishE2E will fail with an error indicating the command
is unknown.  Those failures are intentional and serve as a regression gate
until the dispatch is wired.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.runtime.main import run
from tests.support.published_snapshot_fixtures import (
    make_evidence_card,
    make_member_profile,
    make_snapshot,
    make_zip_feed,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _argv(publish_root: Path) -> list[str]:
    return ["verify-publish", "--publish-root", str(publish_root)]


def _run_and_parse(publish_root: Path, capsys) -> tuple[int, dict]:
    code = run(_argv(publish_root))
    out = json.loads(capsys.readouterr().out)
    return code, out


# ---------------------------------------------------------------------------
# Valid publish tree
# ---------------------------------------------------------------------------


class TestRunVerifyPublishE2E:
    """run(argv) with verify-publish over a real temp publish tree."""

    def test_exits_zero_for_valid_tree(self, tmp_path: Path) -> None:
        make_snapshot(tmp_path)
        code = run(_argv(tmp_path))
        assert code == 0

    def test_output_ok_true_for_valid_tree(self, tmp_path: Path, capsys) -> None:
        make_snapshot(tmp_path)
        code, out = _run_and_parse(tmp_path, capsys)
        assert code == 0
        assert out["ok"] is True

    def test_output_command_field(self, tmp_path: Path, capsys) -> None:
        make_snapshot(tmp_path)
        _, out = _run_and_parse(tmp_path, capsys)
        assert out.get("command") == "verify-publish"

    def test_output_total_errors_zero_for_valid(self, tmp_path: Path, capsys) -> None:
        make_snapshot(tmp_path)
        _, out = _run_and_parse(tmp_path, capsys)
        assert out.get("total_errors", 0) == 0

    def test_output_stages_present(self, tmp_path: Path, capsys) -> None:
        make_snapshot(tmp_path)
        _, out = _run_and_parse(tmp_path, capsys)
        stages = out.get("stages", [])
        stage_names = {s["stage"] for s in stages}
        assert {"manifest", "profiles", "evidence", "ontology", "prediction", "zip"} == stage_names

    def test_multiple_members_and_cards_ok(self, tmp_path: Path, capsys) -> None:
        profiles = [
            make_member_profile(bioguide_id="A000001", slug="alice-smith", name="Alice Smith"),
            make_member_profile(bioguide_id="B000002", slug="bob-jones", name="Bob Jones"),
        ]
        cards = [
            make_evidence_card(
                evidence_card_id="ec-a001",
                member_slug="alice-smith",
                member_bioguide_id="A000001",
                member_name="Alice Smith",
            ),
            make_evidence_card(
                evidence_card_id="ec-b002",
                member_slug="bob-jones",
                member_bioguide_id="B000002",
                member_name="Bob Jones",
            ),
        ]
        make_snapshot(tmp_path, member_profiles=profiles, evidence_cards=cards)
        code, out = _run_and_parse(tmp_path, capsys)
        assert code == 0
        assert out["ok"] is True

    def test_with_zip_feeds_ok(self, tmp_path: Path, capsys) -> None:
        feeds = [make_zip_feed(zip_code="94102"), make_zip_feed(zip_code="10001")]
        make_snapshot(tmp_path, zip_feeds=feeds)
        code, out = _run_and_parse(tmp_path, capsys)
        assert code == 0
        assert out["ok"] is True

    def test_output_is_valid_json(self, tmp_path: Path, capsys) -> None:
        make_snapshot(tmp_path)
        run(_argv(tmp_path))
        raw = capsys.readouterr().out
        # Must not raise.
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# Broken publish tree — run exits 0, output ok=False
# ---------------------------------------------------------------------------


class TestRunVerifyPublishBrokenTree:
    """Broken trees produce ok=False in output and a nonzero exit code."""

    def test_missing_member_profile_ok_false(self, tmp_path: Path, capsys) -> None:
        make_snapshot(tmp_path)
        member_files = list((tmp_path / "members").glob("*.json"))
        assert member_files
        member_files[0].unlink()

        code, out = _run_and_parse(tmp_path, capsys)
        assert code == 1
        assert out["ok"] is False

    def test_missing_evidence_card_ok_false(self, tmp_path: Path, capsys) -> None:
        make_snapshot(tmp_path)
        ev_files = list((tmp_path / "evidence").glob("*.json"))
        assert ev_files
        ev_files[0].unlink()

        code, out = _run_and_parse(tmp_path, capsys)
        assert code == 1
        assert out["ok"] is False

    def test_corrupt_member_profile_ok_false(self, tmp_path: Path, capsys) -> None:
        make_snapshot(tmp_path)
        member_files = list((tmp_path / "members").glob("*.json"))
        member_files[0].write_bytes(b"not valid json {{{")

        code, out = _run_and_parse(tmp_path, capsys)
        assert code == 1
        assert out["ok"] is False

    def test_total_errors_nonzero_for_broken_tree(self, tmp_path: Path, capsys) -> None:
        make_snapshot(tmp_path)
        member_files = list((tmp_path / "members").glob("*.json"))
        member_files[0].unlink()

        _, out = _run_and_parse(tmp_path, capsys)
        assert out.get("total_errors", 0) >= 1

    def test_empty_root_ok_false(self, tmp_path: Path, capsys) -> None:
        empty_root = tmp_path / "empty"
        empty_root.mkdir()
        code, out = _run_and_parse(empty_root, capsys)
        assert code == 1
        assert out["ok"] is False


# ---------------------------------------------------------------------------
# CLI argument validation
# ---------------------------------------------------------------------------


class TestRunVerifyPublishCliArgs:
    """CLI argument edge cases for the verify-publish subcommand."""

    def test_missing_publish_root_arg_exits_nonzero(self) -> None:
        code = run(["verify-publish"])
        assert code != 0

    def test_nonexistent_root_exits_nonzero_ok_false(self, tmp_path: Path, capsys) -> None:
        missing = tmp_path / "does_not_exist"
        code, out = _run_and_parse(missing, capsys)
        assert code == 1
        assert out["ok"] is False

    def test_publish_root_is_forwarded_to_verifier(self, tmp_path: Path, capsys) -> None:
        """Prove the path arg is wired through: a valid tree at the given root passes."""
        sub = tmp_path / "publish_out"
        sub.mkdir()
        make_snapshot(sub)
        _, out = _run_and_parse(sub, capsys)
        assert out["ok"] is True
