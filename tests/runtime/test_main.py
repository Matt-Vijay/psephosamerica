"""Tests for src/runtime/main.py.

No subprocesses.  No live DB.
All runtime construction, command execution, and I/O are mocked at their
module-level import paths.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.runtime.main import run

_MOD = "src.runtime.main"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_runtime() -> MagicMock:
    rt = MagicMock()
    rt.context = MagicMock()
    return rt


def _patch_as_json_passthrough():
    """Patch as_json to return its first argument unchanged (dict→dict)."""
    return patch(f"{_MOD}.as_json", side_effect=lambda obj: obj)


# ---------------------------------------------------------------------------
# No-command / help path
# ---------------------------------------------------------------------------


class TestNoCommand:
    def test_returns_parse_exit_code_when_no_subcommand(self) -> None:
        code = run([])
        assert code == 2

    def test_does_not_call_build_runtime_on_empty_argv(self) -> None:
        with patch(f"{_MOD}.build_runtime") as mock_build:
            run([])
        mock_build.assert_not_called()


# ---------------------------------------------------------------------------
# status command
# ---------------------------------------------------------------------------


class TestStatusCommand:
    _STATUS_PAYLOAD = {
        "summary": {"ingestion_run_count": 0, "parse_run_count": 0, "data_source_count": 0},
        "latest_ingestion_run": {"id": None, "run_type": None, "status": None, "data_source": None},
        "latest_artifact": {"id": None, "artifact_kind": None, "data_source": None},
    }

    def test_returns_0_on_success(self) -> None:
        rt = _mock_runtime()
        conn = MagicMock()

        with (
            patch(f"{_MOD}.build_runtime", return_value=rt),
            patch(f"{_MOD}.open_runtime_connection", return_value=conn),
            patch(f"{_MOD}.get_runtime_status_summary", return_value=self._STATUS_PAYLOAD),
            _patch_as_json_passthrough(),
            patch("builtins.print"),
        ):
            code = run(["status"])

        assert code == 0

    def test_passes_connection_to_get_runtime_status_summary(self) -> None:
        rt = _mock_runtime()
        conn = MagicMock()

        with (
            patch(f"{_MOD}.build_runtime", return_value=rt),
            patch(f"{_MOD}.open_runtime_connection", return_value=conn),
            patch(f"{_MOD}.get_runtime_status_summary", return_value={}) as mock_status,
            _patch_as_json_passthrough(),
            patch("builtins.print"),
        ):
            run(["status"])

        mock_status.assert_called_once_with(conn, limit=20)

    def test_opens_connection_from_runtime(self) -> None:
        rt = _mock_runtime()
        conn = MagicMock()

        with (
            patch(f"{_MOD}.build_runtime", return_value=rt),
            patch(f"{_MOD}.open_runtime_connection", return_value=conn) as mock_open,
            patch(f"{_MOD}.get_runtime_status_summary", return_value={}),
            _patch_as_json_passthrough(),
            patch("builtins.print"),
        ):
            run(["status"])

        mock_open.assert_called_once_with(rt)

    def test_prints_status_result_as_json(self) -> None:
        rt = _mock_runtime()
        conn = MagicMock()

        with (
            patch(f"{_MOD}.build_runtime", return_value=rt),
            patch(f"{_MOD}.open_runtime_connection", return_value=conn),
            patch(f"{_MOD}.get_runtime_status_summary", return_value=self._STATUS_PAYLOAD),
            _patch_as_json_passthrough(),
            patch("builtins.print") as mock_print,
        ):
            run(["status"])

        printed = mock_print.call_args[0][0]
        assert printed["ok"] is True
        assert printed["command"] == "status"
        assert printed["summary"] == self._STATUS_PAYLOAD["summary"]

    def test_returns_1_and_prints_error_on_exception(self) -> None:
        rt = _mock_runtime()
        conn = MagicMock()

        with (
            patch(f"{_MOD}.build_runtime", return_value=rt),
            patch(f"{_MOD}.open_runtime_connection", return_value=conn),
            patch(f"{_MOD}.get_runtime_status_summary", side_effect=RuntimeError("db down")),
            _patch_as_json_passthrough(),
            patch("builtins.print") as mock_print,
        ):
            code = run(["status"])

        assert code == 1
        printed_arg = mock_print.call_args[0][0]
        assert printed_arg["ok"] is False
        assert "db down" in printed_arg["error"]


# ---------------------------------------------------------------------------
# recompute command
# ---------------------------------------------------------------------------


class TestRecomputeCommand:
    _DATE_STR = "2024-06-01"
    _DATE = dt.date(2024, 6, 1)

    def _recompute_result(self) -> MagicMock:
        return MagicMock()

    def _summary(self, run_id: int = 42) -> dict:
        return {"run_id": run_id, "rule_fires": 3, "evidence_cards": 2}

    def test_returns_0_on_success(self) -> None:
        rt = _mock_runtime()

        with (
            patch(f"{_MOD}.build_runtime", return_value=rt),
            patch(f"{_MOD}.recompute_snapshot", return_value=self._recompute_result()),
            patch(f"{_MOD}.summarize_recompute_result", return_value=self._summary()),
            _patch_as_json_passthrough(),
            patch("builtins.print"),
        ):
            code = run(["recompute", "--snapshot-date", self._DATE_STR])

        assert code == 0

    def test_passes_context_and_date_to_recompute_snapshot(self) -> None:
        rt = _mock_runtime()
        recompute_result = self._recompute_result()

        with (
            patch(f"{_MOD}.build_runtime", return_value=rt),
            patch(f"{_MOD}.recompute_snapshot", return_value=recompute_result) as mock_cmd,
            patch(f"{_MOD}.summarize_recompute_result", return_value=self._summary()),
            _patch_as_json_passthrough(),
            patch("builtins.print"),
        ):
            run(["recompute", "--snapshot-date", self._DATE_STR])

        mock_cmd.assert_called_once_with(rt.context, self._DATE)

    def test_summarizes_recompute_result(self) -> None:
        rt = _mock_runtime()
        recompute_result = self._recompute_result()

        with (
            patch(f"{_MOD}.build_runtime", return_value=rt),
            patch(f"{_MOD}.recompute_snapshot", return_value=recompute_result),
            patch(f"{_MOD}.summarize_recompute_result", return_value=self._summary()) as mock_summarize,
            _patch_as_json_passthrough(),
            patch("builtins.print"),
        ):
            run(["recompute", "--snapshot-date", self._DATE_STR])

        mock_summarize.assert_called_once_with(recompute_result)

    def test_prints_result_with_ok_and_command(self) -> None:
        rt = _mock_runtime()

        with (
            patch(f"{_MOD}.build_runtime", return_value=rt),
            patch(f"{_MOD}.recompute_snapshot", return_value=self._recompute_result()),
            patch(f"{_MOD}.summarize_recompute_result", return_value=self._summary(run_id=99)),
            _patch_as_json_passthrough(),
            patch("builtins.print") as mock_print,
        ):
            run(["recompute", "--snapshot-date", self._DATE_STR])

        printed = mock_print.call_args[0][0]
        assert printed["ok"] is True
        assert printed["command"] == "recompute"
        assert printed["run_id"] == 99

    def test_returns_1_on_recompute_failure(self) -> None:
        rt = _mock_runtime()

        with (
            patch(f"{_MOD}.build_runtime", return_value=rt),
            patch(f"{_MOD}.recompute_snapshot", side_effect=RuntimeError("recompute failed")),
            _patch_as_json_passthrough(),
            patch("builtins.print") as mock_print,
        ):
            code = run(["recompute", "--snapshot-date", self._DATE_STR])

        assert code == 1
        printed = mock_print.call_args[0][0]
        assert printed["ok"] is False
        assert "recompute failed" in printed["error"]

    def test_missing_date_flag_defaults_to_today(self) -> None:
        rt = _mock_runtime()

        with (
            patch(f"{_MOD}.build_runtime", return_value=rt),
            patch(f"{_MOD}.recompute_snapshot", return_value=self._recompute_result()) as mock_cmd,
            patch(f"{_MOD}.summarize_recompute_result", return_value=self._summary()),
            _patch_as_json_passthrough(),
            patch("builtins.print"),
        ):
            code = run(["recompute"])

        assert code == 0
        assert mock_cmd.call_args.args[0] is rt.context
        assert isinstance(mock_cmd.call_args.args[1], dt.date)


# ---------------------------------------------------------------------------
# publish command
# ---------------------------------------------------------------------------


class TestPublishCommand:
    _DATE_STR = "2024-06-01"
    _DATE = dt.date(2024, 6, 1)
    _TARGET_DIR = Path("/tmp/snapshots")
    _ZIP_BUNDLE_PATH = Path("/tmp/zip_bundle.json")

    def _publish_result(self) -> MagicMock:
        return MagicMock()

    def _summary(self, run_id: int = 7, snapshot_id: str = "2024-06-01") -> dict:
        return {"run_id": run_id, "snapshot_id": snapshot_id, "succeeded": True}

    def _argv(self) -> list[str]:
        return [
            "publish",
            "--snapshot-date", self._DATE_STR,
            "--out-dir", str(self._TARGET_DIR),
            "--zip-bundle", str(self._ZIP_BUNDLE_PATH),
        ]

    def test_returns_0_on_success(self) -> None:
        rt = _mock_runtime()
        zip_inputs = MagicMock()

        with (
            patch(f"{_MOD}.build_runtime", return_value=rt),
            patch(f"{_MOD}.load_zip_bundle", return_value=zip_inputs),
            patch(f"{_MOD}.publish_snapshot", return_value=self._publish_result()),
            patch(f"{_MOD}.summarize_publish_result", return_value=self._summary()),
            _patch_as_json_passthrough(),
            patch("builtins.print"),
        ):
            code = run(self._argv())

        assert code == 0

    def test_loads_zip_bundle_from_path(self) -> None:
        rt = _mock_runtime()
        zip_inputs = MagicMock()

        with (
            patch(f"{_MOD}.build_runtime", return_value=rt),
            patch(f"{_MOD}.load_zip_bundle", return_value=zip_inputs) as mock_load,
            patch(f"{_MOD}.publish_snapshot", return_value=self._publish_result()),
            patch(f"{_MOD}.summarize_publish_result", return_value=self._summary()),
            _patch_as_json_passthrough(),
            patch("builtins.print"),
        ):
            run(self._argv())

        mock_load.assert_called_once_with(self._ZIP_BUNDLE_PATH)

    def test_passes_all_args_to_publish_snapshot(self) -> None:
        rt = _mock_runtime()
        zip_inputs = MagicMock()
        publish_result = self._publish_result()

        with (
            patch(f"{_MOD}.build_runtime", return_value=rt),
            patch(f"{_MOD}.load_zip_bundle", return_value=zip_inputs),
            patch(f"{_MOD}.publish_snapshot", return_value=publish_result) as mock_cmd,
            patch(f"{_MOD}.summarize_publish_result", return_value=self._summary()),
            _patch_as_json_passthrough(),
            patch("builtins.print"),
        ):
            run(self._argv())

        mock_cmd.assert_called_once_with(rt.context, self._DATE, self._TARGET_DIR, zip_inputs)

    def test_summarizes_publish_result(self) -> None:
        rt = _mock_runtime()
        zip_inputs = MagicMock()
        publish_result = self._publish_result()

        with (
            patch(f"{_MOD}.build_runtime", return_value=rt),
            patch(f"{_MOD}.load_zip_bundle", return_value=zip_inputs),
            patch(f"{_MOD}.publish_snapshot", return_value=publish_result),
            patch(f"{_MOD}.summarize_publish_result", return_value=self._summary()) as mock_summarize,
            _patch_as_json_passthrough(),
            patch("builtins.print"),
        ):
            run(self._argv())

        mock_summarize.assert_called_once_with(publish_result)

    def test_prints_result_with_ok_and_command(self) -> None:
        rt = _mock_runtime()
        zip_inputs = MagicMock()

        with (
            patch(f"{_MOD}.build_runtime", return_value=rt),
            patch(f"{_MOD}.load_zip_bundle", return_value=zip_inputs),
            patch(f"{_MOD}.publish_snapshot", return_value=self._publish_result()),
            patch(f"{_MOD}.summarize_publish_result", return_value=self._summary(run_id=55, snapshot_id="2024-06-01")),
            _patch_as_json_passthrough(),
            patch("builtins.print") as mock_print,
        ):
            run(self._argv())

        printed = mock_print.call_args[0][0]
        assert printed["ok"] is True
        assert printed["command"] == "publish"
        assert printed["run_id"] == 55
        assert printed["snapshot_id"] == "2024-06-01"

    def test_returns_1_when_zip_bundle_missing(self) -> None:
        rt = _mock_runtime()

        with (
            patch(f"{_MOD}.build_runtime", return_value=rt),
            patch(f"{_MOD}.load_zip_bundle", side_effect=FileNotFoundError("no such file")),
            _patch_as_json_passthrough(),
            patch("builtins.print") as mock_print,
        ):
            code = run(self._argv())

        assert code == 1
        printed = mock_print.call_args[0][0]
        assert printed["ok"] is False

    def test_returns_1_on_publish_failure(self) -> None:
        rt = _mock_runtime()
        zip_inputs = MagicMock()

        with (
            patch(f"{_MOD}.build_runtime", return_value=rt),
            patch(f"{_MOD}.load_zip_bundle", return_value=zip_inputs),
            patch(f"{_MOD}.publish_snapshot", side_effect=RuntimeError("snapshot mismatch")),
            _patch_as_json_passthrough(),
            patch("builtins.print") as mock_print,
        ):
            code = run(self._argv())

        assert code == 1
        printed = mock_print.call_args[0][0]
        assert "snapshot mismatch" in printed["error"]

    def test_missing_zip_bundle_causes_nonzero_exit(self) -> None:
        code = run(["publish", "--snapshot-date", self._DATE_STR])
        assert code != 0


# ---------------------------------------------------------------------------
# load-disclosures command
# ---------------------------------------------------------------------------


class TestLoadDisclosuresCommand:
    _YEAR = 2024

    def _artifact_result(
        self,
        slug: str,
        run_id: int,
        discovered: int = 10,
        stored: int = 10,
        local_root: str | None = "/tmp/artifacts",
    ) -> MagicMock:
        result = MagicMock()
        result.run_id = run_id
        result.data_source = {"slug": slug}
        result.discovered_count = discovered
        result.stored_count = stored
        result.local_root = Path(local_root) if local_root is not None else None
        return result

    def test_returns_0_on_success_single_chamber(self) -> None:
        rt = _mock_runtime()
        conn = MagicMock()
        summary = self._artifact_result("house-disclosures", 1)
        with (
            patch(f"{_MOD}.build_runtime", return_value=rt),
            patch(f"{_MOD}.open_runtime_connection", return_value=conn),
            patch(f"{_MOD}.run_disclosure_artifact_ingest", side_effect=[summary, summary]),
            patch(f"{_MOD}.local_artifact_root"),
            _patch_as_json_passthrough(),
            patch("builtins.print"),
        ):
            code = run(["load-disclosures", "--chamber", "house", "--year", str(self._YEAR)])

        assert code == 0

    def test_senate_calls_ingest_once(self) -> None:
        rt = _mock_runtime()
        conn = MagicMock()
        summary = self._artifact_result("senate-disclosures", 2)
        with (
            patch(f"{_MOD}.build_runtime", return_value=rt),
            patch(f"{_MOD}.open_runtime_connection", return_value=conn),
            patch(f"{_MOD}.run_disclosure_artifact_ingest", return_value=summary) as mock_ingest,
            patch(f"{_MOD}.local_artifact_root"),
            _patch_as_json_passthrough(),
            patch("builtins.print"),
        ):
            run(["load-disclosures", "--chamber", "senate", "--year", str(self._YEAR)])

        assert mock_ingest.call_count == 1
        assert mock_ingest.call_args.args[0] is conn
        assert mock_ingest.call_args.kwargs["chamber"] == "senate"
        assert mock_ingest.call_args.kwargs["year"] == self._YEAR

    def test_house_calls_ingest_twice_for_annual_and_ptr(self) -> None:
        rt = _mock_runtime()
        conn = MagicMock()
        annual = self._artifact_result("house-disclosures", 1)
        ptr = self._artifact_result("house-disclosures", 2)
        with (
            patch(f"{_MOD}.build_runtime", return_value=rt),
            patch(f"{_MOD}.open_runtime_connection", return_value=conn),
            patch(f"{_MOD}.run_disclosure_artifact_ingest", side_effect=[annual, ptr]) as mock_ingest,
            patch(f"{_MOD}.local_artifact_root"),
            _patch_as_json_passthrough(),
            patch("builtins.print"),
        ):
            run(["load-disclosures", "--chamber", "house", "--year", str(self._YEAR)])

        assert mock_ingest.call_count == 2
        assert [call.kwargs["filing_kind"] for call in mock_ingest.call_args_list] == ["annual", "ptr"]

    def test_both_returns_combined_summary(self) -> None:
        rt = _mock_runtime()
        conn = MagicMock()
        results = [
            self._artifact_result("house-disclosures", 1, discovered=5, stored=5),
            self._artifact_result("house-disclosures", 2, discovered=8, stored=8),
            self._artifact_result("senate-disclosures", 3, discovered=6, stored=6),
        ]
        with (
            patch(f"{_MOD}.build_runtime", return_value=rt),
            patch(f"{_MOD}.open_runtime_connection", return_value=conn),
            patch(f"{_MOD}.run_disclosure_artifact_ingest", side_effect=results),
            patch(f"{_MOD}.local_artifact_root"),
            _patch_as_json_passthrough(),
            patch("builtins.print") as mock_print,
        ):
            run(["load-disclosures", "--chamber", "both", "--year", str(self._YEAR)])

        printed = mock_print.call_args[0][0]
        assert printed["ok"] is True
        assert printed["command"] == "load-disclosures"
        assert "house" in printed
        assert "senate" in printed

    def test_missing_year_defaults_to_current_year(self) -> None:
        import datetime as dt

        rt = _mock_runtime()
        conn = MagicMock()
        annual = self._artifact_result("house-disclosures", 1)
        ptr = self._artifact_result("house-disclosures", 2)
        with (
            patch(f"{_MOD}.build_runtime", return_value=rt),
            patch(f"{_MOD}.open_runtime_connection", return_value=conn),
            patch(f"{_MOD}.run_disclosure_artifact_ingest", side_effect=[annual, ptr]) as mock_ingest,
            patch(f"{_MOD}.local_artifact_root"),
            _patch_as_json_passthrough(),
            patch("builtins.print"),
        ):
            run(["load-disclosures", "--chamber", "house"])

        called_year = mock_ingest.call_args.kwargs["year"]
        assert called_year == dt.date.today().year

    def test_uses_local_artifact_root_as_default(self) -> None:
        default_root = Path("/tmp/artifacts")
        rt = _mock_runtime()
        conn = MagicMock()
        annual = self._artifact_result("house-disclosures", 1)
        ptr = self._artifact_result("house-disclosures", 2)
        with (
            patch(f"{_MOD}.build_runtime", return_value=rt),
            patch(f"{_MOD}.open_runtime_connection", return_value=conn),
            patch(f"{_MOD}.run_disclosure_artifact_ingest", side_effect=[annual, ptr]) as mock_ingest,
            patch(f"{_MOD}.local_artifact_root", return_value=default_root),
            _patch_as_json_passthrough(),
            patch("builtins.print"),
        ):
            run(["load-disclosures", "--chamber", "house", "--year", str(self._YEAR)])

        assert mock_ingest.call_args.kwargs["local_root"] == default_root

    def test_respects_explicit_local_root(self) -> None:
        rt = _mock_runtime()
        conn = MagicMock()
        summary = self._artifact_result("senate-disclosures", 1)
        with (
            patch(f"{_MOD}.build_runtime", return_value=rt),
            patch(f"{_MOD}.open_runtime_connection", return_value=conn),
            patch(f"{_MOD}.run_disclosure_artifact_ingest", return_value=summary) as mock_ingest,
            _patch_as_json_passthrough(),
            patch("builtins.print"),
        ):
            run(
                [
                    "load-disclosures",
                    "--chamber",
                    "senate",
                    "--year",
                    str(self._YEAR),
                    "--local-root",
                    "/tmp/custom-artifacts",
                ]
            )

        assert mock_ingest.call_args.kwargs["local_root"] == Path("/tmp/custom-artifacts")

    def test_returns_1_on_exception(self) -> None:
        rt = _mock_runtime()
        conn = MagicMock()
        with (
            patch(f"{_MOD}.build_runtime", return_value=rt),
            patch(f"{_MOD}.open_runtime_connection", return_value=conn),
            patch(f"{_MOD}.run_disclosure_artifact_ingest", side_effect=RuntimeError("portal down")),
            patch(f"{_MOD}.local_artifact_root"),
            _patch_as_json_passthrough(),
            patch("builtins.print") as mock_print,
        ):
            code = run(["load-disclosures", "--chamber", "house", "--year", str(self._YEAR)])

        assert code == 1
        printed = mock_print.call_args[0][0]
        assert printed["ok"] is False
        assert "portal down" in printed["error"]

    def test_single_chamber_result_contains_year(self) -> None:
        rt = _mock_runtime()
        conn = MagicMock()
        summary = self._artifact_result("senate-disclosures", 1)
        with (
            patch(f"{_MOD}.build_runtime", return_value=rt),
            patch(f"{_MOD}.open_runtime_connection", return_value=conn),
            patch(f"{_MOD}.run_disclosure_artifact_ingest", return_value=summary),
            patch(f"{_MOD}.local_artifact_root"),
            _patch_as_json_passthrough(),
            patch("builtins.print") as mock_print,
        ):
            run(["load-disclosures", "--chamber", "senate", "--year", str(self._YEAR)])

        printed = mock_print.call_args[0][0]
        assert printed["year"] == self._YEAR
        assert printed["senate"]["stored"] == 10


# ---------------------------------------------------------------------------
# build_runtime is called exactly once per dispatch
# ---------------------------------------------------------------------------


class TestRuntimeBuiltOnce:
    def test_status_builds_runtime_once(self) -> None:
        rt = _mock_runtime()

        with (
            patch(f"{_MOD}.build_runtime", return_value=rt) as mock_build,
            patch(f"{_MOD}.open_runtime_connection", return_value=MagicMock()),
            patch(f"{_MOD}.get_runtime_status_summary", return_value={}),
            _patch_as_json_passthrough(),
            patch("builtins.print"),
        ):
            run(["status"])

        assert mock_build.call_count == 1

    def test_recompute_builds_runtime_once(self) -> None:
        rt = _mock_runtime()

        with (
            patch(f"{_MOD}.build_runtime", return_value=rt) as mock_build,
            patch(f"{_MOD}.recompute_snapshot", return_value=MagicMock()),
            patch(f"{_MOD}.summarize_recompute_result", return_value={}),
            _patch_as_json_passthrough(),
            patch("builtins.print"),
        ):
            run(["recompute", "--snapshot-date", "2024-01-01"])

        assert mock_build.call_count == 1
