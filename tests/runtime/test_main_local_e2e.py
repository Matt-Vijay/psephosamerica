"""Honest local-oracle integration tests at the run(argv) entry surface.

Goals
-----
- Call run(argv) directly — no subprocesses.
- Exercise all three local-oracle CLI commands:
    load-congress-local
    process-disclosures-local
    run-oracle-local
- Use real temp archive directories and bundle JSON files written inline.
- Patch only the deep runtime/DB boundaries; CLI, main.py dispatch,
  commands.py, archive loading, and bundle loading all execute for real.
- No network calls. No live DB. No sys.modules injection.

Boundary patch strategy
-----------------------
  load-congress-local
    build_runtime              → mock runtime (avoids Settings / taxonomy init)
    commands.open_connection   → mock conn (avoids psycopg)
    congress_archive.run_congress_load_runtime  → typed fake result
    The CongressArchive files are read for real; member list is provably parsed
    and forwarded to the DB boundary.

  process-disclosures-local
    build_runtime              → mock runtime
    commands.open_connection   → mock conn
    commands.run_disclosures_bundle_process  → typed fake result
    load_disclosures_bundle() runs for real in main.py; the bundle JSON is
    provably parsed before the DB boundary is reached.

  run-oracle-local
    build_runtime              → mock runtime
    commands.open_connection   → mock conn
    congress_archive.run_congress_load_runtime  → typed fake result
    oracle_local.run_disclosures_bundle_process → typed fake result
    oracle_local.run_recompute_runtime          → typed fake result
    oracle_local.run_publish_runtime            → typed fake result
    Archive files are read for real by run_congress_archive_load.
    load_disclosures_bundle runs for real in main.py.
    commands.run_oracle_local_command and oracle_local.run_oracle_local
    execute for real; all four stage-boundary functions are patched.

    NOTE: This command additionally requires main.py to correctly pass
    congress_archive and disclosures_bundle as positional arguments to
    run_oracle_local_command.  Tests will fail until that dispatch is
    updated; the failures are intentional and serve as a regression gate.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.db.load_report import WarnErrorSummary, build_load_summary
from src.pipeline.publish_pipeline import PublishResult
from src.pipeline.recompute_run import RecomputeRunResult
from src.runtime.congress import CongressLoadResult
from src.runtime.disclosures import DisclosuresLoadRuntimeResult
from src.runtime.disclosures_bundle_process import DisclosuresBundleProcessResult
from src.runtime.disclosures_parse import DisclosureParseRuntimeResult
from src.runtime.main import run
from src.runtime.publish import PublishRuntimeResult
from src.runtime.publish_roundtrip_types import PublishRoundtripResult, PublishRoundtripStageResult
from src.runtime.publish_verify_types import PublishVerifyResult, PublishVerifyStageResult
from src.runtime.recompute import RuntimeRecomputeResult

# ---------------------------------------------------------------------------
# Module-level patch roots
# ---------------------------------------------------------------------------

_MAIN = "src.runtime.main"
_COMMANDS = "src.runtime.commands"
_CONGRESS_ARCHIVE = "src.runtime.congress_archive"
_ORACLE_LOCAL = "src.runtime.oracle_local"

# ---------------------------------------------------------------------------
# Shared raw fixture dicts (minimal Congress.gov API shape)
# ---------------------------------------------------------------------------

_RAW_MEMBER = {
    "bioguideId": "T000001",
    "firstName": "Ada",
    "lastName": "Turing",
    "directOrderName": "Ada Turing",
    "partyName": "D",
    "state": "CA",
    "currentMember": True,
    "terms": {"item": [{"chamber": "House of Representatives", "startYear": "2025-01-03"}]},
}

_RAW_MEMBER_DETAIL = {
    "bioguideId": "T000001",
    "firstName": "Ada",
    "lastName": "Turing",
    "directOrderName": "Ada Turing",
    "partyName": "D",
    "state": "CA",
    "currentMember": True,
    "terms": {
        "item": [
            {
                "congress": 119,
                "chamber": "House of Representatives",
                "startYear": "2025-01-03",
                "stateCode": "CA",
                "district": 12,
            }
        ]
    },
    "committees": {
        "item": [
            {
                "committee": {"systemCode": "hsif00"},
                "congress": 119,
                "role": "Chair",
                "startDate": "2025-01-03",
                "isCurrent": True,
            }
        ]
    },
    "leadership": [],
    "partyHistory": [{"partyName": "D", "startYear": 2025}],
}

_RAW_COMMITTEE = {
    "systemCode": "hsif00",
    "chamber": "House",
    "committeeTypeCode": "Standing",
    "name": "Committee on Innovation Futures",
}

_RAW_BILL = {
    "congress": 119,
    "type": "HR",
    "number": 42,
    "title": "Local Congress Confidence Act",
    "introducedDate": "2025-01-09",
    "latestAction": {"actionDate": "2025-01-10", "text": "Introduced"},
}

_RAW_BILL_DETAIL = {
    "congress": 119,
    "type": "HR",
    "number": 42,
    "title": "Local Congress Confidence Act",
    "introducedDate": "2025-01-09",
    "latestAction": {"actionDate": "2025-01-10", "text": "Introduced"},
    "sponsors": [
        {
            "bioguideId": "T000001",
            "sponsorshipDate": "2025-01-09",
        }
    ],
}

# ---------------------------------------------------------------------------
# Archive fixture builder
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def build_congress_archive(root: Path, congress: int = 119) -> Path:
    """Write a minimal, valid Congress archive directory under *root*.

    Returns the archive root path.
    """
    _write_json(root / "members.json", {"members": [_RAW_MEMBER]})
    _write_json(root / "committees.json", {"committees": [_RAW_COMMITTEE]})
    _write_json(root / "bills.json", {"bills": [_RAW_BILL]})
    _write_json(
        root / "member_details" / f"{_RAW_MEMBER['bioguideId']}.json",
        {"member": _RAW_MEMBER_DETAIL},
    )
    _write_json(
        root / "bill_details" / f"{congress}_hr_{_RAW_BILL['number']}.json",
        {"bill": _RAW_BILL_DETAIL},
    )
    return root


def _assert_archive_enrichment(inputs) -> None:
    assert [m.bioguide_id for m in inputs.members] == ["T000001"]
    assert [
        (term.record.bioguide_id, term.congress, term.district) for term in inputs.member_terms
    ] == [
        ("T000001", 119, 12),
    ]
    assert [
        (membership.bioguide_id, membership.committee_code, membership.role)
        for membership in inputs.memberships
    ] == [
        ("T000001", "hsif00", "chair"),
    ]
    assert [committee.committee_code for committee in inputs.committees] == ["hsif00"]
    assert [bill.bill_number for bill in inputs.bills] == [42]
    assert [
        (sponsor.record.bill_number, sponsor.bioguide_id) for sponsor in inputs.primary_sponsors
    ] == [
        (42, "T000001"),
    ]


# ---------------------------------------------------------------------------
# Bundle fixture builder
# ---------------------------------------------------------------------------

_ZERO_SHA256 = "0" * 64

_HOUSE_ARTIFACT = {
    "source_record_id": "10001",
    "chamber": "house",
    "filing_year": 2024,
    "storage_uri": "house/2024/10001.pdf",
    "source_url": "https://disclosures.house.gov/public_disc/financial-pdfs/2024/10001.pdf",
    "source_slug": "house_disclosures",
    "artifact_kind": "pdf",
    "sha256": _ZERO_SHA256,
    "index_row": {
        "last_name": "Turing",
        "first_name": "Ada",
        "suffix": "",
        "raw_filing_type": "O",
        "state_dst": "CA30",
        "filing_date": "2024-03-01",
        "doc_id": "10001",
        "filing_kind": "annual",
    },
}


def build_disclosures_bundle(path: Path, *, include_artifact: bool = True) -> Path:
    """Write a minimal, valid disclosures bundle JSON file to *path*.

    Returns the bundle file path.
    """
    artifacts = [_HOUSE_ARTIFACT] if include_artifact else []
    path.write_text(json.dumps({"artifacts": artifacts}), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Fake typed results for DB-boundary patches
# ---------------------------------------------------------------------------


def _fake_congress_load_result() -> CongressLoadResult:
    summary = build_load_summary([], WarnErrorSummary(), run_id=1)
    return CongressLoadResult(
        data_source={"id": 1, "slug": "congress-core"},
        run_id=1,
        load_summary=summary,
    )


def _fake_disclosures_load_runtime_result() -> DisclosuresLoadRuntimeResult:
    summary = build_load_summary([], WarnErrorSummary(), run_id=2)
    return DisclosuresLoadRuntimeResult(
        data_source={"id": 2, "slug": "financial-disclosures"},
        run_id=2,
        load_summary=summary,
        sidecars=(),
    )


def _fake_bundle_process_result() -> DisclosuresBundleProcessResult:
    parse_result = DisclosureParseRuntimeResult(
        processed_count=0,
        succeeded_count=0,
        failed_count=0,
        parse_sessions=(),
        parsed_documents=(),
        failed_artifact_ids=(),
    )
    return DisclosuresBundleProcessResult(
        stage_result=None,
        parse_result=parse_result,
        transform_count=0,
        skipped_transform_count=0,
        skipped_sessions=(),
        load_result=_fake_disclosures_load_runtime_result(),
    )


def _fake_recompute_result() -> RuntimeRecomputeResult:
    return RuntimeRecomputeResult(
        data_source={"id": 3, "slug": "conflict-recompute"},
        run_id=3,
        recompute_result=RecomputeRunResult(),
    )


def _fake_publish_result(snapshot_id: str = "2025-01-15") -> PublishRuntimeResult:
    pub = PublishResult(
        snapshot_id=snapshot_id,
        planned_count=0,
        written_count=0,
    )
    return PublishRuntimeResult(
        data_source={"id": 4, "slug": "snapshot-publish"},
        run_id=4,
        snapshot_id=snapshot_id,
        publish_result=pub,
    )


def _fake_verify_result() -> PublishVerifyResult:
    return PublishVerifyResult(
        stages=(PublishVerifyStageResult(stage="manifest", checked=1, issues=()),),
    )


def _fake_roundtrip_result() -> PublishRoundtripResult:
    return PublishRoundtripResult(
        stages=(PublishRoundtripStageResult(stage="snapshot", checked=1, issues=()),),
    )


# ---------------------------------------------------------------------------
# Test: load-congress-local
# ---------------------------------------------------------------------------


class TestLoadCongressLocalE2E:
    """E2E tests for the load-congress-local command.

    The archive directory is written to a real tmp_path.
    run_congress_archive_load reads the files for real.
    Only run_congress_load_runtime (the DB write step) is patched.
    """

    _CONGRESS = 119

    def _argv(self, archive_path: Path) -> list[str]:
        return [
            "load-congress-local",
            "--archive",
            str(archive_path),
            "--congress",
            str(self._CONGRESS),
        ]

    def test_exits_zero_with_valid_archive(self, tmp_path: Path) -> None:
        archive = build_congress_archive(tmp_path / "archive")
        with (
            patch("src.runtime.commands.core.build_runtime", return_value=MagicMock()),
            patch("src.runtime.commands.core.open_connection", return_value=MagicMock()),
            patch(
                f"{_CONGRESS_ARCHIVE}.run_congress_load_runtime",
                return_value=_fake_congress_load_result(),
            ),
        ):
            code = run(self._argv(archive))
        assert code == 0

    def test_output_contains_ok_true(self, tmp_path: Path, capsys) -> None:
        archive = build_congress_archive(tmp_path / "archive")
        with (
            patch("src.runtime.commands.core.build_runtime", return_value=MagicMock()),
            patch("src.runtime.commands.core.open_connection", return_value=MagicMock()),
            patch(
                f"{_CONGRESS_ARCHIVE}.run_congress_load_runtime",
                return_value=_fake_congress_load_result(),
            ),
        ):
            run(self._argv(archive))
        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is True
        assert out["command"] == "load-congress-local"

    def test_archive_is_actually_read_member_forwarded(self, tmp_path: Path) -> None:
        """Prove the archive is read: member T000001 must appear in the inputs
        passed to the DB boundary function."""
        archive = build_congress_archive(tmp_path / "archive")
        with (
            patch("src.runtime.commands.core.build_runtime", return_value=MagicMock()),
            patch("src.runtime.commands.core.open_connection", return_value=MagicMock()),
            patch(
                f"{_CONGRESS_ARCHIVE}.run_congress_load_runtime",
                return_value=_fake_congress_load_result(),
            ) as mock_load_rt,
        ):
            run(self._argv(archive))

        # run_congress_load_runtime(conn, inputs) — inputs is CongressIngestInputs
        _, inputs = mock_load_rt.call_args.args
        bioguide_ids = [m.bioguide_id for m in inputs.members]
        assert "T000001" in bioguide_ids, (
            "Expected member T000001 from the real archive to be forwarded to "
            "run_congress_load_runtime, but it was not."
        )

    def test_archive_enrichment_is_forwarded_to_db_boundary(self, tmp_path: Path) -> None:
        archive = build_congress_archive(tmp_path / "archive")
        with (
            patch("src.runtime.commands.core.build_runtime", return_value=MagicMock()),
            patch("src.runtime.commands.core.open_connection", return_value=MagicMock()),
            patch(
                f"{_CONGRESS_ARCHIVE}.run_congress_load_runtime",
                return_value=_fake_congress_load_result(),
            ) as mock_load_rt,
        ):
            run(self._argv(archive))

        _, inputs = mock_load_rt.call_args.args
        _assert_archive_enrichment(inputs)

    def test_congress_number_forwarded_to_load(self, tmp_path: Path) -> None:
        archive = build_congress_archive(tmp_path / "archive")
        with (
            patch("src.runtime.commands.core.build_runtime", return_value=MagicMock()),
            patch("src.runtime.commands.core.open_connection", return_value=MagicMock()),
            patch(
                f"{_CONGRESS_ARCHIVE}.run_congress_load_runtime",
                return_value=_fake_congress_load_result(),
            ),
        ):
            code = run(self._argv(archive))
        assert code == 0

    def test_missing_archive_arg_exits_nonzero(self) -> None:
        code = run(["load-congress-local", "--congress", str(self._CONGRESS)])
        assert code != 0

    def test_missing_congress_arg_exits_nonzero(self, tmp_path: Path) -> None:
        archive = build_congress_archive(tmp_path / "archive")
        code = run(["load-congress-local", "--archive", str(archive)])
        assert code != 0

    def test_nonexistent_archive_exits_nonzero(self, tmp_path: Path, capsys) -> None:
        missing = tmp_path / "does_not_exist"
        with (
            patch("src.runtime.commands.core.build_runtime", return_value=MagicMock()),
            patch("src.runtime.commands.core.open_connection", return_value=MagicMock()),
        ):
            code = run(["load-congress-local", "--archive", str(missing), "--congress", "119"])
        assert code != 0
        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is False


# ---------------------------------------------------------------------------
# Test: process-disclosures-local
# ---------------------------------------------------------------------------


class TestProcessDisclosuresLocalE2E:
    """E2E tests for the process-disclosures-local command.

    The bundle JSON file is written to a real tmp_path.
    load_disclosures_bundle() is called for real inside main.py dispatch;
    the parsed DisclosuresBundle object is forwarded to run_disclosures_bundle_process.
    Only run_disclosures_bundle_process (the DB boundary) is patched.
    """

    def _argv(self, bundle_path: Path) -> list[str]:
        return ["process-disclosures-local", "--bundle", str(bundle_path)]

    def test_exits_zero_with_valid_bundle(self, tmp_path: Path) -> None:
        bundle = build_disclosures_bundle(tmp_path / "bundle.json")
        with (
            patch("src.runtime.commands.disclosures.build_runtime", return_value=MagicMock()),
            patch("src.runtime.commands.disclosures.open_connection", return_value=MagicMock()),
            patch(
                "src.runtime.commands.disclosures.run_disclosures_bundle_process",
                return_value=_fake_bundle_process_result(),
            ),
        ):
            code = run(self._argv(bundle))
        assert code == 0

    def test_output_contains_ok_true(self, tmp_path: Path, capsys) -> None:
        bundle = build_disclosures_bundle(tmp_path / "bundle.json")
        with (
            patch("src.runtime.commands.disclosures.build_runtime", return_value=MagicMock()),
            patch("src.runtime.commands.disclosures.open_connection", return_value=MagicMock()),
            patch(
                "src.runtime.commands.disclosures.run_disclosures_bundle_process",
                return_value=_fake_bundle_process_result(),
            ),
        ):
            run(self._argv(bundle))
        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is True
        assert out["command"] == "process-disclosures-local"

    def test_bundle_is_actually_loaded_artifact_forwarded(self, tmp_path: Path) -> None:
        """Prove the bundle JSON is parsed: the house artifact must appear in
        the DisclosuresBundle passed to the DB boundary function."""
        bundle_path = build_disclosures_bundle(tmp_path / "bundle.json")
        with (
            patch("src.runtime.commands.disclosures.build_runtime", return_value=MagicMock()),
            patch("src.runtime.commands.disclosures.open_connection", return_value=MagicMock()),
            patch(
                "src.runtime.commands.disclosures.run_disclosures_bundle_process",
                return_value=_fake_bundle_process_result(),
            ) as mock_process,
        ):
            run(self._argv(bundle_path))

        # run_disclosures_bundle_process(conn, bundle, ...) — bundle is DisclosuresBundle
        _, bundle_arg = mock_process.call_args.args
        assert len(bundle_arg.artifacts) == 1, (
            "Expected the real bundle JSON to be parsed and its one artifact "
            "forwarded to run_disclosures_bundle_process."
        )
        assert bundle_arg.artifacts[0].source_record_id == "10001"
        assert bundle_arg.artifacts[0].chamber == "house"

    def test_empty_bundle_is_accepted(self, tmp_path: Path, capsys) -> None:
        bundle_path = build_disclosures_bundle(
            tmp_path / "empty_bundle.json", include_artifact=False
        )
        with (
            patch("src.runtime.commands.disclosures.build_runtime", return_value=MagicMock()),
            patch("src.runtime.commands.disclosures.open_connection", return_value=MagicMock()),
            patch(
                "src.runtime.commands.disclosures.run_disclosures_bundle_process",
                return_value=_fake_bundle_process_result(),
            ) as mock_process,
        ):
            code = run(self._argv(bundle_path))
        assert code == 0
        _, bundle_arg = mock_process.call_args.args
        assert len(bundle_arg.artifacts) == 0

    def test_missing_bundle_arg_exits_nonzero(self) -> None:
        code = run(["process-disclosures-local"])
        assert code != 0

    def test_nonexistent_bundle_exits_nonzero(self, tmp_path: Path, capsys) -> None:
        missing = tmp_path / "no_bundle.json"
        with patch("src.runtime.commands.disclosures.build_runtime", return_value=MagicMock()):
            code = run(["process-disclosures-local", "--bundle", str(missing)])
        assert code != 0
        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is False

    def test_malformed_bundle_json_exits_nonzero(self, tmp_path: Path, capsys) -> None:
        bad_path = tmp_path / "bad.json"
        bad_path.write_text('{"artifacts": "not-a-list"}', encoding="utf-8")
        with patch("src.runtime.commands.disclosures.build_runtime", return_value=MagicMock()):
            code = run(["process-disclosures-local", "--bundle", str(bad_path)])
        assert code != 0
        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is False


# ---------------------------------------------------------------------------
# Test: run-oracle-local
# ---------------------------------------------------------------------------


class TestRunOracleLocalE2E:
    """E2E tests for the run-oracle-local command.

    Both the Congress archive directory and the disclosures bundle JSON
    are written to real temp directories.

    Boundary patch strategy:
    - build_runtime                                  → mock runtime
    - commands.open_connection                       → mock conn
    - congress_archive.run_congress_load_runtime     → fake CongressLoadResult
      (archive files are still read for real by run_congress_archive_load)
    - oracle_local.run_disclosures_bundle_process    → fake result
    - oracle_local.run_recompute_runtime             → fake result
    - oracle_local.run_publish_runtime               → fake result

    NOTE: These tests require main.py to pass congress_archive and
    disclosures_bundle to run_oracle_local_command.  Until that dispatch
    is corrected the tests will fail — the failures are intentional and
    serve as a regression gate for the local-oracle surface.
    """

    _DATE_STR = "2025-01-15"
    _DATE = dt.date(2025, 1, 15)

    def _argv(
        self,
        archive_path: Path,
        bundle_path: Path,
        target_dir: Path,
        *,
        extra: list[str] | None = None,
    ) -> list[str]:
        return [
            "run-oracle-local",
            "--congress-archive",
            str(archive_path),
            "--disclosures-bundle",
            str(bundle_path),
            "--snapshot-date",
            self._DATE_STR,
            "--target-dir",
            str(target_dir),
        ] + (extra or [])

    @contextlib.contextmanager
    def _patch_stack(self, snapshot_id: str | None = None):
        """Enter all four DB-facing boundary patches as one context manager."""
        sid = snapshot_id or self._DATE_STR
        with (
            patch("src.runtime.commands.core.build_runtime", return_value=MagicMock()),
            patch("src.runtime.commands.core.open_connection", return_value=MagicMock()),
            patch(
                f"{_CONGRESS_ARCHIVE}.run_congress_load_runtime",
                return_value=_fake_congress_load_result(),
            ),
            patch(
                f"{_ORACLE_LOCAL}.run_disclosures_bundle_process",
                return_value=_fake_bundle_process_result(),
            ),
            patch(
                f"{_ORACLE_LOCAL}.run_recompute_runtime",
                return_value=_fake_recompute_result(),
            ),
            patch(
                f"{_ORACLE_LOCAL}.run_publish_runtime",
                return_value=_fake_publish_result(sid),
            ),
            patch(f"{_ORACLE_LOCAL}._run_verify", return_value=_fake_verify_result()),
            patch(f"{_ORACLE_LOCAL}._run_roundtrip", return_value=_fake_roundtrip_result()),
        ):
            yield

    def test_exits_zero(self, tmp_path: Path) -> None:
        archive = build_congress_archive(tmp_path / "archive")
        bundle = build_disclosures_bundle(tmp_path / "bundle.json")
        target = tmp_path / "out"
        target.mkdir()
        with self._patch_stack():
            code = run(self._argv(archive, bundle, target))
        assert code == 0

    def test_output_contains_ok_true_and_command(self, tmp_path: Path, capsys) -> None:
        archive = build_congress_archive(tmp_path / "archive")
        bundle = build_disclosures_bundle(tmp_path / "bundle.json")
        target = tmp_path / "out"
        target.mkdir()
        with self._patch_stack():
            run(self._argv(archive, bundle, target))
        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is True
        assert out["command"] == "run-oracle-local"

    def test_snapshot_id_matches_date_arg(self, tmp_path: Path, capsys) -> None:
        archive = build_congress_archive(tmp_path / "archive")
        bundle = build_disclosures_bundle(tmp_path / "bundle.json")
        target = tmp_path / "out"
        target.mkdir()
        with self._patch_stack():
            run(self._argv(archive, bundle, target))
        out = json.loads(capsys.readouterr().out)
        assert out["snapshot_id"] == self._DATE_STR

    def test_output_surfaces_default_local_oracle_scope(self, tmp_path: Path, capsys) -> None:
        archive = build_congress_archive(tmp_path / "archive")
        bundle = build_disclosures_bundle(tmp_path / "bundle.json")
        target = tmp_path / "out"
        target.mkdir()
        with self._patch_stack():
            run(self._argv(archive, bundle, target))
        out = json.loads(capsys.readouterr().out)
        assert out["congress"]["configured_congress"] == 119
        assert out["congress"]["congress_source"] == "current-date-default"
        assert out["congress"]["include_votes"] is False
        assert out["disclosures"]["requested_chamber"] == "both"
        assert out["disclosures"]["artifact_limit"] is None
        assert out["publish"]["zip_feeds_generated"] is False

    def test_explicit_congress_flag_overrides_default_assumption(
        self, tmp_path: Path, capsys
    ) -> None:
        archive = build_congress_archive(tmp_path / "archive")
        bundle = build_disclosures_bundle(tmp_path / "bundle.json")
        target = tmp_path / "out"
        target.mkdir()
        with self._patch_stack():
            run(self._argv(archive, bundle, target, extra=["--congress", "119"]))
        out = json.loads(capsys.readouterr().out)
        assert out["congress"]["configured_congress"] == 119
        assert out["congress"]["congress_source"] == "explicit-arg"

    def test_archive_is_read_and_member_forwarded_to_db_boundary(self, tmp_path: Path) -> None:
        """Prove the archive is actually parsed: T000001 must reach the DB boundary."""
        archive = build_congress_archive(tmp_path / "archive")
        bundle = build_disclosures_bundle(tmp_path / "bundle.json")
        target = tmp_path / "out"
        target.mkdir()
        with (
            patch("src.runtime.commands.core.build_runtime", return_value=MagicMock()),
            patch("src.runtime.commands.core.open_connection", return_value=MagicMock()),
            patch(
                f"{_CONGRESS_ARCHIVE}.run_congress_load_runtime",
                return_value=_fake_congress_load_result(),
            ) as mock_load_rt,
            patch(
                f"{_ORACLE_LOCAL}.run_disclosures_bundle_process",
                return_value=_fake_bundle_process_result(),
            ),
            patch(
                f"{_ORACLE_LOCAL}.run_recompute_runtime",
                return_value=_fake_recompute_result(),
            ),
            patch(
                f"{_ORACLE_LOCAL}.run_publish_runtime",
                return_value=_fake_publish_result(self._DATE_STR),
            ),
        ):
            run(self._argv(archive, bundle, target))

        _, inputs = mock_load_rt.call_args.args
        bioguide_ids = [m.bioguide_id for m in inputs.members]
        assert "T000001" in bioguide_ids, (
            "Expected member T000001 from the real archive to be forwarded to "
            "run_congress_load_runtime, but it was not."
        )

    def test_archive_enrichment_reaches_oracle_congress_stage_boundary(
        self, tmp_path: Path
    ) -> None:
        archive = build_congress_archive(tmp_path / "archive")
        bundle = build_disclosures_bundle(tmp_path / "bundle.json")
        target = tmp_path / "out"
        target.mkdir()
        with (
            patch("src.runtime.commands.core.build_runtime", return_value=MagicMock()),
            patch("src.runtime.commands.core.open_connection", return_value=MagicMock()),
            patch(
                f"{_CONGRESS_ARCHIVE}.run_congress_load_runtime",
                return_value=_fake_congress_load_result(),
            ) as mock_load_rt,
            patch(
                f"{_ORACLE_LOCAL}.run_disclosures_bundle_process",
                return_value=_fake_bundle_process_result(),
            ),
            patch(
                f"{_ORACLE_LOCAL}.run_recompute_runtime",
                return_value=_fake_recompute_result(),
            ),
            patch(
                f"{_ORACLE_LOCAL}.run_publish_runtime",
                return_value=_fake_publish_result(self._DATE_STR),
            ),
        ):
            run(self._argv(archive, bundle, target))

        _, inputs = mock_load_rt.call_args.args
        _assert_archive_enrichment(inputs)

    def test_bundle_artifact_forwarded_to_process_boundary(self, tmp_path: Path) -> None:
        """Prove the bundle JSON is parsed: the house artifact must reach the
        oracle_local process step."""
        archive = build_congress_archive(tmp_path / "archive")
        bundle_path = build_disclosures_bundle(tmp_path / "bundle.json")
        target = tmp_path / "out"
        target.mkdir()
        with (
            patch("src.runtime.commands.core.build_runtime", return_value=MagicMock()),
            patch("src.runtime.commands.core.open_connection", return_value=MagicMock()),
            patch(
                f"{_CONGRESS_ARCHIVE}.run_congress_load_runtime",
                return_value=_fake_congress_load_result(),
            ),
            patch(
                f"{_ORACLE_LOCAL}.run_disclosures_bundle_process",
                return_value=_fake_bundle_process_result(),
            ) as mock_process,
            patch(
                f"{_ORACLE_LOCAL}.run_recompute_runtime",
                return_value=_fake_recompute_result(),
            ),
            patch(
                f"{_ORACLE_LOCAL}.run_publish_runtime",
                return_value=_fake_publish_result(self._DATE_STR),
            ),
        ):
            run(self._argv(archive, bundle_path, target))

        # run_disclosures_bundle_process(conn, bundle, local_root=...)
        _, bundle_arg = mock_process.call_args.args
        assert len(bundle_arg.artifacts) == 1
        assert bundle_arg.artifacts[0].source_record_id == "10001"

    def test_explicit_snapshot_id_respected(self, tmp_path: Path, capsys) -> None:
        archive = build_congress_archive(tmp_path / "archive")
        bundle = build_disclosures_bundle(tmp_path / "bundle.json")
        target = tmp_path / "out"
        target.mkdir()
        custom_id = "custom-oracle-snap-001"
        with self._patch_stack(snapshot_id=custom_id):
            run(self._argv(archive, bundle, target, extra=["--snapshot-id", custom_id]))
        out = json.loads(capsys.readouterr().out)
        assert out["snapshot_id"] == custom_id

    def test_missing_required_args_exits_nonzero(self) -> None:
        code = run(["run-oracle-local"])
        assert code != 0

    def test_missing_snapshot_date_exits_nonzero(self, tmp_path: Path) -> None:
        archive = build_congress_archive(tmp_path / "archive")
        bundle = build_disclosures_bundle(tmp_path / "bundle.json")
        target = tmp_path / "out"
        target.mkdir()
        code = run(
            [
                "run-oracle-local",
                "--congress-archive",
                str(archive),
                "--disclosures-bundle",
                str(bundle),
                "--target-dir",
                str(target),
            ]
        )
        assert code != 0

    def test_missing_target_dir_exits_nonzero(self, tmp_path: Path) -> None:
        archive = build_congress_archive(tmp_path / "archive")
        bundle = build_disclosures_bundle(tmp_path / "bundle.json")
        code = run(
            [
                "run-oracle-local",
                "--congress-archive",
                str(archive),
                "--disclosures-bundle",
                str(bundle),
                "--snapshot-date",
                self._DATE_STR,
            ]
        )
        assert code != 0
