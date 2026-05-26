"""Tests for src/runtime/main.py.

No subprocesses.  No live DB.
All runtime construction, command execution, and I/O are mocked at their
module-level import paths.
"""

from __future__ import annotations

import datetime as dt
import json
import runpy
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.runtime.main import COMMAND_REGISTRY, run

_MAIN_MOD = "src.runtime.main"
_MOD = "src.runtime.commands"

# ---------------------------------------------------------------------------
# Shared harness — collapses the build/open/json/print patch stack
# ---------------------------------------------------------------------------


def _mock_runtime() -> MagicMock:
    rt = MagicMock()
    rt.context = MagicMock()
    return rt


@contextmanager
def _run_harness():
    """Patch the four seams common to every run() test.

    Yields a namespace with rt, conn, mock_build, mock_open_conn, and
    mock_print.  Command-specific patches are added by each test.
    """
    rt = _mock_runtime()
    conn = MagicMock()
    with (
        patch(f"{_MOD}.build_runtime", return_value=rt) as mock_build,
        patch(f"{_MOD}.open_runtime_connection", return_value=conn) as mock_open_conn,
        patch(f"{_MAIN_MOD}.as_json", side_effect=lambda obj: obj),
        patch("builtins.print") as mock_print,
    ):
        yield SimpleNamespace(
            rt=rt,
            conn=conn,
            mock_build=mock_build,
            mock_open_conn=mock_open_conn,
            mock_print=mock_print,
        )


def _printed(h) -> dict:
    """Return the first positional arg passed to print()."""
    return h.mock_print.call_args[0][0]


@contextmanager
def _run_module_as_main():
    existing = sys.modules.pop("src.runtime.main", None)
    try:
        yield
    finally:
        if existing is not None:
            sys.modules["src.runtime.main"] = existing


@contextmanager
def _fresh_runtime_module_import():
    removed: dict[str, object] = {}
    prefixes = ("src.runtime", "src.api", "src.pipeline.publish_snapshot_run")
    for name in list(sys.modules):
        if name == "src" or any(
            name == prefix or name.startswith(f"{prefix}.") for prefix in prefixes
        ):
            removed[name] = sys.modules.pop(name)
    try:
        yield
    finally:
        sys.modules.update(removed)


# ---------------------------------------------------------------------------
# No-command / help path
# ---------------------------------------------------------------------------


class TestNoCommand:
    def test_returns_parse_exit_code_when_no_subcommand(self) -> None:
        code = run([])
        assert code == 2

    def test_boolean_parse_exit_code_fails_closed(self) -> None:
        with patch(f"{_MAIN_MOD}.parse_args", side_effect=SystemExit(False)):
            code = run([])

        assert code == 1

    def test_does_not_call_build_runtime_on_empty_argv(self) -> None:
        with patch(f"{_MOD}.build_runtime") as mock_build:
            run([])
        mock_build.assert_not_called()


# ---------------------------------------------------------------------------
# status command
# ---------------------------------------------------------------------------


class TestStatusCommand:
    _STATUS_PAYLOAD = {
        "summary": {
            "ingestion_run_count": 1,
            "parse_run_count": 1,
            "data_source_count": 2,
            "source_artifact_count": 1,
        },
        "ingestion_runs": [
            {
                "id": 11,
                "run_type": "load-congress",
                "status": "succeeded",
                "data_source_slug": "congress-gov-api",
            }
        ],
        "parse_runs": [
            {
                "id": 22,
                "parser_name": "house-pdf",
                "status": "succeeded",
            }
        ],
        "data_sources": [
            {
                "slug": "congress-gov-api",
                "name": "Congress.gov API",
                "source_kind": "official",
            },
            {
                "slug": "house-disclosures",
                "name": "House Disclosures",
                "source_kind": "official",
            },
        ],
        "source_artifacts": [
            {
                "id": 33,
                "artifact_kind": "pdf",
                "data_source_slug": "house-disclosures",
            }
        ],
    }

    def test_returns_0_on_success(self) -> None:
        with _run_harness():
            with patch(f"{_MOD}.get_runtime_status", return_value=self._STATUS_PAYLOAD):
                code = run(["status"])
        assert code == 0

    def test_passes_connection_to_get_runtime_status(self) -> None:
        with _run_harness() as h:
            with patch(f"{_MOD}.get_runtime_status", return_value={}) as mock_status:
                run(["status"])
        mock_status.assert_called_once_with(h.conn, limit=20)

    def test_opens_connection_from_runtime(self) -> None:
        with _run_harness() as h:
            with patch(f"{_MOD}.get_runtime_status", return_value={}):
                run(["status"])
        h.mock_open_conn.assert_called_once_with(h.rt)

    def test_prints_status_result_as_json(self) -> None:
        with _run_harness() as h:
            with patch(f"{_MOD}.get_runtime_status", return_value=self._STATUS_PAYLOAD):
                run(["status"])
        printed = _printed(h)
        assert printed["ok"] is True
        assert printed["command"] == "status"
        assert printed["summary"] == self._STATUS_PAYLOAD["summary"]
        assert printed["latest_parse_run"] == {
            "id": 22,
            "parser_name": "house-pdf",
            "status": "succeeded",
        }
        assert printed["active_data_sources"] == [
            {
                "slug": "congress-gov-api",
                "name": "Congress.gov API",
                "source_kind": "official",
            },
            {
                "slug": "house-disclosures",
                "name": "House Disclosures",
                "source_kind": "official",
            },
        ]

    def test_returns_1_and_prints_error_on_exception(self) -> None:
        with _run_harness() as h:
            with patch(f"{_MOD}.get_runtime_status", side_effect=RuntimeError("db down")):
                code = run(["status"])
        assert code == 1
        printed = _printed(h)
        assert printed["ok"] is False
        assert "db down" in printed["error"]


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
        with _run_harness():
            with (
                patch(f"{_MOD}.recompute_snapshot", return_value=self._recompute_result()),
                patch(f"{_MOD}.summarize_recompute_result", return_value=self._summary()),
            ):
                code = run(["recompute", "--snapshot-date", self._DATE_STR])
        assert code == 0

    def test_passes_context_and_date_to_recompute_snapshot(self) -> None:
        with _run_harness() as h:
            with (
                patch(
                    f"{_MOD}.recompute_snapshot", return_value=self._recompute_result()
                ) as mock_cmd,
                patch(f"{_MOD}.summarize_recompute_result", return_value=self._summary()),
            ):
                run(["recompute", "--snapshot-date", self._DATE_STR])
        mock_cmd.assert_called_once_with(h.rt.context, self._DATE)

    def test_summarizes_recompute_result(self) -> None:
        recompute_result = self._recompute_result()
        with _run_harness():
            with (
                patch(f"{_MOD}.recompute_snapshot", return_value=recompute_result),
                patch(
                    f"{_MOD}.summarize_recompute_result", return_value=self._summary()
                ) as mock_summarize,
            ):
                run(["recompute", "--snapshot-date", self._DATE_STR])
        mock_summarize.assert_called_once_with(recompute_result)

    def test_prints_result_with_ok_and_command(self) -> None:
        with _run_harness() as h:
            with (
                patch(f"{_MOD}.recompute_snapshot", return_value=self._recompute_result()),
                patch(f"{_MOD}.summarize_recompute_result", return_value=self._summary(run_id=99)),
            ):
                run(["recompute", "--snapshot-date", self._DATE_STR])
        printed = _printed(h)
        assert printed["ok"] is True
        assert printed["command"] == "recompute"
        assert printed["run_id"] == 99

    def test_returns_1_on_recompute_failure(self) -> None:
        with _run_harness() as h:
            with patch(f"{_MOD}.recompute_snapshot", side_effect=RuntimeError("recompute failed")):
                code = run(["recompute", "--snapshot-date", self._DATE_STR])
        assert code == 1
        printed = _printed(h)
        assert printed["ok"] is False
        assert "recompute failed" in printed["error"]

    def test_missing_date_flag_defaults_to_today(self) -> None:
        with _run_harness() as h:
            with (
                patch(
                    f"{_MOD}.recompute_snapshot", return_value=self._recompute_result()
                ) as mock_cmd,
                patch(f"{_MOD}.summarize_recompute_result", return_value=self._summary()),
            ):
                code = run(["recompute"])
        assert code == 0
        assert mock_cmd.call_args.args[0] is h.rt.context
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
            "--snapshot-date",
            self._DATE_STR,
            "--out-dir",
            str(self._TARGET_DIR),
            "--zip-bundle",
            str(self._ZIP_BUNDLE_PATH),
        ]

    @contextmanager
    def _publish_env(self, zip_inputs=None, publish_result=None, summary=None):
        """Patch the publish-specific seams: load_zip_bundle, publish_snapshot, summarize."""
        zip_inputs = zip_inputs or MagicMock()
        publish_result = publish_result or self._publish_result()
        summary = summary or self._summary()
        with (
            patch(f"{_MOD}.load_zip_bundle", return_value=zip_inputs) as mock_load_zip,
            patch(f"{_MOD}.publish_snapshot", return_value=publish_result) as mock_cmd,
            patch(f"{_MOD}.summarize_publish_result", return_value=summary) as mock_summarize,
        ):
            yield SimpleNamespace(
                zip_inputs=zip_inputs,
                mock_load_zip=mock_load_zip,
                mock_cmd=mock_cmd,
                mock_summarize=mock_summarize,
            )

    def test_returns_0_on_success(self) -> None:
        with _run_harness():
            with self._publish_env():
                code = run(self._argv())
        assert code == 0

    def test_loads_zip_bundle_from_path(self) -> None:
        with _run_harness():
            with self._publish_env() as pub:
                run(self._argv())
        pub.mock_load_zip.assert_called_once_with(self._ZIP_BUNDLE_PATH)

    def test_passes_all_args_to_publish_snapshot(self) -> None:
        zip_inputs = MagicMock()
        with _run_harness() as h:
            with self._publish_env(zip_inputs=zip_inputs) as pub:
                run(self._argv())
        pub.mock_cmd.assert_called_once_with(h.rt.context, self._DATE, self._TARGET_DIR, zip_inputs)

    def test_summarizes_publish_result(self) -> None:
        publish_result = self._publish_result()
        with _run_harness():
            with self._publish_env(publish_result=publish_result) as pub:
                run(self._argv())
        pub.mock_summarize.assert_called_once_with(publish_result)

    def test_prints_result_with_ok_and_command(self) -> None:
        with _run_harness() as h:
            with self._publish_env(summary=self._summary(run_id=55, snapshot_id="2024-06-01")):
                run(self._argv())
        printed = _printed(h)
        assert printed["ok"] is True
        assert printed["command"] == "publish"
        assert printed["run_id"] == 55
        assert printed["snapshot_id"] == "2024-06-01"

    def test_returns_1_when_zip_bundle_missing(self) -> None:
        with _run_harness() as h:
            with patch(f"{_MOD}.load_zip_bundle", side_effect=FileNotFoundError("no such file")):
                code = run(self._argv())
        assert code == 1
        printed = _printed(h)
        assert printed["ok"] is False

    def test_returns_1_on_publish_failure(self) -> None:
        with _run_harness() as h:
            with (
                patch(f"{_MOD}.load_zip_bundle", return_value=MagicMock()),
                patch(f"{_MOD}.publish_snapshot", side_effect=RuntimeError("snapshot mismatch")),
            ):
                code = run(self._argv())
        assert code == 1
        assert "snapshot mismatch" in _printed(h)["error"]

    def test_missing_zip_bundle_causes_nonzero_exit(self) -> None:
        code = run(["publish", "--snapshot-date", self._DATE_STR])
        assert code != 0

    def test_unsucceeded_publish_returns_nonzero(self) -> None:
        summary = self._summary() | {"succeeded": False}
        with _run_harness() as h:
            with self._publish_env(summary=summary):
                code = run(self._argv())
        assert code == 1
        assert _printed(h)["ok"] is False


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

    @contextmanager
    def _disclosure_env(self, ingest_rv=None, ingest_side_effect=None):
        """Patch local_artifact_root and run_disclosure_artifact_ingest."""
        kw = {}
        if ingest_side_effect is not None:
            kw["side_effect"] = ingest_side_effect
        elif ingest_rv is not None:
            kw["return_value"] = ingest_rv
        with (
            patch(f"{_MOD}.run_disclosure_artifact_ingest", **kw) as mock_ingest,
            patch(f"{_MOD}.local_artifact_root") as mock_root,
        ):
            yield SimpleNamespace(mock_ingest=mock_ingest, mock_root=mock_root)

    def test_returns_0_on_success_single_chamber(self) -> None:
        summary = self._artifact_result("house-disclosures", 1)
        with _run_harness():
            with self._disclosure_env(ingest_side_effect=[summary, summary]):
                code = run(["load-disclosures", "--chamber", "house", "--year", str(self._YEAR)])
        assert code == 0

    def test_senate_calls_ingest_once(self) -> None:
        summary = self._artifact_result("senate-disclosures", 2)
        with _run_harness():
            with self._disclosure_env(ingest_rv=summary) as d:
                run(["load-disclosures", "--chamber", "senate", "--year", str(self._YEAR)])
        assert d.mock_ingest.call_count == 1
        assert d.mock_ingest.call_args.kwargs["chamber"] == "senate"
        assert d.mock_ingest.call_args.kwargs["year"] == self._YEAR

    def test_house_calls_ingest_twice_for_annual_and_ptr(self) -> None:
        annual = self._artifact_result("house-disclosures", 1)
        ptr = self._artifact_result("house-disclosures", 2)
        with _run_harness():
            with self._disclosure_env(ingest_side_effect=[annual, ptr]) as d:
                run(["load-disclosures", "--chamber", "house", "--year", str(self._YEAR)])
        assert d.mock_ingest.call_count == 2
        assert [c.kwargs["filing_kind"] for c in d.mock_ingest.call_args_list] == ["annual", "ptr"]

    def test_both_returns_combined_summary(self) -> None:
        results = [
            self._artifact_result("house-disclosures", 1, discovered=5, stored=5),
            self._artifact_result("house-disclosures", 2, discovered=8, stored=8),
            self._artifact_result("senate-disclosures", 3, discovered=6, stored=6),
        ]
        with _run_harness() as h:
            with self._disclosure_env(ingest_side_effect=results):
                run(["load-disclosures", "--chamber", "both", "--year", str(self._YEAR)])
        printed = _printed(h)
        assert printed["ok"] is True
        assert printed["command"] == "load-disclosures"
        assert "house" in printed
        assert "senate" in printed

    def test_missing_year_defaults_to_current_year(self) -> None:
        annual = self._artifact_result("house-disclosures", 1)
        ptr = self._artifact_result("house-disclosures", 2)
        with _run_harness():
            with self._disclosure_env(ingest_side_effect=[annual, ptr]) as d:
                run(["load-disclosures", "--chamber", "house"])
        called_year = d.mock_ingest.call_args.kwargs["year"]
        assert called_year == dt.date.today().year

    def test_uses_local_artifact_root_as_default(self) -> None:
        default_root = Path("/tmp/artifacts")
        annual = self._artifact_result("house-disclosures", 1)
        ptr = self._artifact_result("house-disclosures", 2)
        with _run_harness():
            with self._disclosure_env(ingest_side_effect=[annual, ptr]) as d:
                d.mock_root.return_value = default_root
                run(["load-disclosures", "--chamber", "house", "--year", str(self._YEAR)])
        assert d.mock_ingest.call_args.kwargs["local_root"] == default_root

    def test_respects_explicit_local_root(self) -> None:
        summary = self._artifact_result("senate-disclosures", 1)
        with _run_harness():
            with patch(
                f"{_MOD}.run_disclosure_artifact_ingest", return_value=summary
            ) as mock_ingest:
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
        with _run_harness() as h:
            with self._disclosure_env(ingest_side_effect=RuntimeError("portal down")):
                code = run(["load-disclosures", "--chamber", "house", "--year", str(self._YEAR)])
        assert code == 1
        printed = _printed(h)
        assert printed["ok"] is False
        assert "portal down" in printed["error"]

    def test_single_chamber_result_contains_year(self) -> None:
        summary = self._artifact_result("senate-disclosures", 1)
        with _run_harness() as h:
            with self._disclosure_env(ingest_rv=summary):
                run(["load-disclosures", "--chamber", "senate", "--year", str(self._YEAR)])
        printed = _printed(h)
        assert printed["year"] == self._YEAR
        assert printed["senate"]["stored"] == 10


# ---------------------------------------------------------------------------
# parse-disclosures command
# ---------------------------------------------------------------------------


class TestParseDisclosuresCommand:
    _DEFAULT_ARGV = ["parse-disclosures"]

    def _parse_result(self) -> MagicMock:
        return MagicMock()

    def _summary(self) -> dict:
        return {"processed": 10, "succeeded": 8, "failed": 2}

    @contextmanager
    def _parse_env(self, parse_result=None, summary=None):
        """Patch parse-disclosures-specific seams."""
        parse_result = parse_result or self._parse_result()
        summary = summary or self._summary()
        with (
            patch(f"{_MOD}.run_disclosure_parse_runtime", return_value=parse_result) as mock_parse,
            patch(
                f"{_MOD}.summarize_parse_disclosures_result", return_value=summary
            ) as mock_summarize,
            patch(f"{_MOD}.local_artifact_root", return_value=MagicMock()) as mock_root,
        ):
            yield SimpleNamespace(
                parse_result=parse_result,
                mock_parse=mock_parse,
                mock_summarize=mock_summarize,
                mock_root=mock_root,
            )

    def test_returns_0_on_success(self) -> None:
        with _run_harness():
            with self._parse_env():
                code = run(self._DEFAULT_ARGV)
        assert code == 0

    def test_passes_connection_to_run_disclosure_parse_runtime(self) -> None:
        with _run_harness() as h:
            with self._parse_env() as p:
                run(self._DEFAULT_ARGV)
        assert p.mock_parse.call_args.args[0] is h.conn

    def test_both_chamber_maps_to_none(self) -> None:
        with _run_harness():
            with self._parse_env() as p:
                run(["parse-disclosures", "--chamber", "both"])
        assert p.mock_parse.call_args.kwargs["chamber"] is None

    def test_house_chamber_forwarded(self) -> None:
        with _run_harness():
            with self._parse_env() as p:
                run(["parse-disclosures", "--chamber", "house"])
        assert p.mock_parse.call_args.kwargs["chamber"] == "house"

    def test_limit_forwarded(self) -> None:
        with _run_harness():
            with self._parse_env() as p:
                run(["parse-disclosures", "--limit", "25"])
        assert p.mock_parse.call_args.kwargs["limit"] == 25

    def test_explicit_local_root_forwarded(self) -> None:
        with _run_harness():
            with (
                patch(
                    f"{_MOD}.run_disclosure_parse_runtime", return_value=self._parse_result()
                ) as mock_parse,
                patch(f"{_MOD}.summarize_parse_disclosures_result", return_value=self._summary()),
            ):
                run(["parse-disclosures", "--local-root", "/tmp/artifacts"])
        assert mock_parse.call_args.kwargs["local_root"] == Path("/tmp/artifacts")

    def test_uses_local_artifact_root_as_default(self) -> None:
        default_root = Path("/default/artifacts")
        with _run_harness():
            with self._parse_env() as p:
                p.mock_root.return_value = default_root
                run(self._DEFAULT_ARGV)
        assert p.mock_parse.call_args.kwargs["local_root"] == default_root

    def test_prints_result_with_ok_and_command(self) -> None:
        with _run_harness() as h:
            with self._parse_env():
                run(self._DEFAULT_ARGV)
        printed = _printed(h)
        assert printed["ok"] is True
        assert printed["command"] == "parse-disclosures"
        assert printed["processed"] == 10
        assert printed["succeeded"] == 8
        assert printed["failed"] == 2

    def test_summarizes_parse_result(self) -> None:
        parse_result = self._parse_result()
        with _run_harness():
            with self._parse_env(parse_result=parse_result) as p:
                run(self._DEFAULT_ARGV)
        p.mock_summarize.assert_called_once_with(parse_result)

    def test_returns_1_on_exception(self) -> None:
        with _run_harness() as h:
            with (
                patch(
                    f"{_MOD}.run_disclosure_parse_runtime", side_effect=RuntimeError("disk full")
                ),
                patch(f"{_MOD}.local_artifact_root", return_value=MagicMock()),
            ):
                code = run(self._DEFAULT_ARGV)
        assert code == 1
        printed = _printed(h)
        assert printed["ok"] is False
        assert "disk full" in printed["error"]


# ---------------------------------------------------------------------------
# build_runtime is called exactly once per dispatch
# ---------------------------------------------------------------------------


class TestRuntimeBuiltOnce:
    def test_status_builds_runtime_once(self) -> None:
        with _run_harness() as h:
            with patch(f"{_MOD}.get_runtime_status", return_value={}):
                run(["status"])
        assert h.mock_build.call_count == 1

    def test_recompute_builds_runtime_once(self) -> None:
        with _run_harness() as h:
            with (
                patch(f"{_MOD}.recompute_snapshot", return_value=MagicMock()),
                patch(f"{_MOD}.summarize_recompute_result", return_value={}),
            ):
                run(["recompute", "--snapshot-date", "2024-01-01"])
        assert h.mock_build.call_count == 1


# ---------------------------------------------------------------------------
# process-disclosures command
# ---------------------------------------------------------------------------


class TestProcessDisclosuresCommand:
    _DEFAULT_ARGV = ["process-disclosures"]

    def _process_result(self) -> MagicMock:
        return MagicMock()

    def _summary(self) -> dict:
        return {
            "parse": {"processed": 5, "succeeded": 4, "failed": 1},
            "transformed": 3,
            "load": {"run_id": 99, "ok": True, "counts": {}},
        }

    @contextmanager
    def _process_env(self, process_result=None, summary=None):
        """Patch process-disclosures-specific seams."""
        process_result = process_result or self._process_result()
        summary = summary or self._summary()
        with (
            patch(
                f"{_MOD}.run_disclosures_parse_load_runtime", return_value=process_result
            ) as mock_run,
            patch(
                f"{_MOD}.summarize_process_disclosures_result", return_value=summary
            ) as mock_summarize,
            patch(f"{_MOD}.local_artifact_root", return_value=MagicMock()) as mock_root,
        ):
            yield SimpleNamespace(
                process_result=process_result,
                mock_run=mock_run,
                mock_summarize=mock_summarize,
                mock_root=mock_root,
            )

    def test_returns_0_on_success(self) -> None:
        with _run_harness():
            with self._process_env():
                code = run(self._DEFAULT_ARGV)
        assert code == 0

    def test_calls_parse_load_runtime_with_connection(self) -> None:
        with _run_harness() as h:
            with self._process_env() as p:
                run(self._DEFAULT_ARGV)
        p.mock_run.assert_called_once()
        assert p.mock_run.call_args.args[0] is h.conn

    def test_both_chamber_maps_to_none_for_parse(self) -> None:
        with _run_harness():
            with self._process_env() as p:
                run(["process-disclosures", "--chamber", "both"])
        assert p.mock_run.call_args.kwargs["chamber"] is None

    def test_house_chamber_forwarded(self) -> None:
        with _run_harness():
            with self._process_env() as p:
                run(["process-disclosures", "--chamber", "house"])
        assert p.mock_run.call_args.kwargs["chamber"] == "house"

    def test_limit_forwarded(self) -> None:
        with _run_harness():
            with self._process_env() as p:
                run(["process-disclosures", "--limit", "20"])
        assert p.mock_run.call_args.kwargs["limit"] == 20

    def test_explicit_local_root_forwarded(self) -> None:
        with _run_harness():
            with (
                patch(
                    f"{_MOD}.run_disclosures_parse_load_runtime",
                    return_value=self._process_result(),
                ) as mock_run,
                patch(f"{_MOD}.summarize_process_disclosures_result", return_value=self._summary()),
            ):
                run(["process-disclosures", "--local-root", "/tmp/artifacts"])
        assert mock_run.call_args.kwargs["local_root"] == Path("/tmp/artifacts")

    def test_uses_local_artifact_root_as_default(self) -> None:
        default_root = Path("/default/artifacts")
        with _run_harness():
            with self._process_env() as p:
                p.mock_root.return_value = default_root
                run(self._DEFAULT_ARGV)
        assert p.mock_run.call_args.kwargs["local_root"] == default_root

    def test_prints_result_with_ok_and_command(self) -> None:
        with _run_harness() as h:
            with self._process_env():
                run(self._DEFAULT_ARGV)
        printed = _printed(h)
        assert printed["ok"] is True
        assert printed["command"] == "process-disclosures"
        assert "parse" in printed
        assert "load" in printed

    def test_summarizes_combined_result(self) -> None:
        process_result = self._process_result()
        with _run_harness():
            with self._process_env(process_result=process_result) as p:
                run(self._DEFAULT_ARGV)
        called_result = p.mock_summarize.call_args[0][0]
        assert called_result is process_result

    def test_returns_1_on_parse_exception(self) -> None:
        with _run_harness() as h:
            with (
                patch(
                    f"{_MOD}.run_disclosures_parse_load_runtime",
                    side_effect=RuntimeError("parse failed"),
                ),
                patch(f"{_MOD}.local_artifact_root", return_value=MagicMock()),
            ):
                code = run(self._DEFAULT_ARGV)
        assert code == 1
        printed = _printed(h)
        assert printed["ok"] is False
        assert "parse failed" in printed["error"]

    def test_returns_1_on_process_exception(self) -> None:
        with _run_harness() as h:
            with (
                patch(
                    f"{_MOD}.run_disclosures_parse_load_runtime",
                    side_effect=RuntimeError("db write failed"),
                ),
                patch(f"{_MOD}.local_artifact_root", return_value=MagicMock()),
            ):
                code = run(self._DEFAULT_ARGV)
        assert code == 1
        printed = _printed(h)
        assert printed["ok"] is False
        assert "db write failed" in printed["error"]


# ---------------------------------------------------------------------------
# load-congress command
# ---------------------------------------------------------------------------


class TestLoadCongressCommand:
    _CONGRESS = 119

    def _load_result(self) -> MagicMock:
        result = MagicMock()
        result.run_id = 1
        result.data_source = {"slug": "congress-gov"}
        ls = MagicMock()
        ls.ok = True
        ls.total_inserted = 5
        ls.total_updated = 0
        ls.total_skipped = 0
        ls.total_rejected = 0
        ls.warn_error.warning_count = 0
        ls.warn_error.error_count = 0
        ls.table_results = []
        result.load_summary = ls
        return result

    def _summary(self) -> dict:
        return {"run_id": 1, "data_source": "congress-gov", "ok": True, "counts": {}}

    def _argv(self, extra: list[str] | None = None) -> list[str]:
        base = ["load-congress", "--congress", str(self._CONGRESS)]
        return base + (extra or [])

    @contextmanager
    def _congress_env(self, load_result=None, summary=None):
        """Patch congress-specific seams."""
        load_result = load_result or self._load_result()
        summary = summary or self._summary()
        with (
            patch(f"{_MOD}.load_congress", return_value=load_result) as mock_run,
            patch(f"{_MOD}.summarize_load_result", return_value=summary) as mock_summarize,
        ):
            yield SimpleNamespace(
                mock_run=mock_run,
                mock_summarize=mock_summarize,
            )

    def test_returns_0_on_success(self) -> None:
        with _run_harness():
            with self._congress_env():
                code = run(self._argv())
        assert code == 0

    def test_prints_result_with_ok_and_command(self) -> None:
        with _run_harness() as h:
            with self._congress_env():
                run(self._argv())
        printed = _printed(h)
        assert printed["ok"] is True
        assert printed["command"] == "load-congress"

    def test_passes_runtime_context(self) -> None:
        with _run_harness() as h:
            with self._congress_env() as c:
                run(self._argv())
        assert c.mock_run.call_args.args[0] is h.rt.context

    def test_vote_flags_default_to_off(self) -> None:
        with _run_harness():
            with self._congress_env() as c:
                run(self._argv())
        options = c.mock_run.call_args.args[1]
        assert options.include_votes is False
        assert options.house_vote_year is None
        assert options.senate_session is None

    def test_include_votes_flag_forwarded(self) -> None:
        with _run_harness():
            with self._congress_env() as c:
                run(self._argv(["--include-votes"]))
        assert c.mock_run.call_args.args[1].include_votes is True

    def test_house_vote_year_forwarded(self) -> None:
        with _run_harness():
            with self._congress_env() as c:
                run(self._argv(["--house-vote-year", "2024"]))
        assert c.mock_run.call_args.args[1].include_votes is True
        assert c.mock_run.call_args.args[1].house_vote_year == 2024

    def test_senate_session_forwarded(self) -> None:
        with _run_harness():
            with self._congress_env() as c:
                run(self._argv(["--senate-session", "2"]))
        assert c.mock_run.call_args.args[1].include_votes is True
        assert c.mock_run.call_args.args[1].senate_session == 2

    def test_all_vote_flags_forwarded_together(self) -> None:
        with _run_harness():
            with self._congress_env() as c:
                run(
                    self._argv(
                        ["--include-votes", "--house-vote-year", "2023", "--senate-session", "1"]
                    )
                )
        options = c.mock_run.call_args.args[1]
        assert options.include_votes is True
        assert options.house_vote_year == 2023
        assert options.senate_session == 1

    def test_congress_number_forwarded(self) -> None:
        with _run_harness():
            with self._congress_env() as c:
                run(["load-congress", "--congress", "118"])
        assert c.mock_run.call_args.args[1].congress == 118

    def test_missing_congress_defaults_to_current(self) -> None:
        with _run_harness():
            with self._congress_env() as c:
                run(["load-congress"])
        options = c.mock_run.call_args.args[1]
        assert isinstance(options.congress, int)
        assert options.congress > 0

    def test_returns_1_on_exception(self) -> None:
        with _run_harness() as h:
            with patch(f"{_MOD}.load_congress", side_effect=RuntimeError("api down")):
                code = run(self._argv())
        assert code == 1
        printed = _printed(h)
        assert printed["ok"] is False
        assert "api down" in printed["error"]


# ---------------------------------------------------------------------------
# load-congress-local command
# ---------------------------------------------------------------------------


class TestLoadCongressLocalCommand:
    _CONGRESS = 119
    _ARCHIVE_PATH = Path("/tmp/congress_119.json")

    def _load_result(self) -> MagicMock:
        result = MagicMock()
        result.run_id = 1
        result.data_source = {"slug": "congress-gov-api"}
        ls = MagicMock()
        ls.ok = True
        ls.total_inserted = 5
        ls.total_updated = 0
        ls.total_skipped = 0
        ls.total_rejected = 0
        ls.warn_error.warning_count = 0
        ls.warn_error.error_count = 0
        ls.table_results = []
        result.load_summary = ls
        return result

    def _summary(self) -> dict:
        return {"run_id": 1, "data_source": "congress-gov-api", "ok": True, "counts": {}}

    def _argv(self) -> list[str]:
        return [
            "load-congress-local",
            "--archive",
            str(self._ARCHIVE_PATH),
            "--congress",
            str(self._CONGRESS),
        ]

    @contextmanager
    def _congress_local_env(self, load_result=None, summary=None):
        load_result = load_result or self._load_result()
        summary = summary or self._summary()
        with (
            patch(f"{_MOD}.load_congress_local", return_value=load_result) as mock_run,
            patch(f"{_MOD}.summarize_load_result", return_value=summary) as mock_summarize,
        ):
            yield SimpleNamespace(mock_run=mock_run, mock_summarize=mock_summarize)

    def test_returns_0_on_success(self) -> None:
        with _run_harness():
            with self._congress_local_env():
                code = run(self._argv())
        assert code == 0

    def test_prints_result_with_ok_and_command(self) -> None:
        with _run_harness() as h:
            with self._congress_local_env():
                run(self._argv())
        printed = _printed(h)
        assert printed["ok"] is True
        assert printed["command"] == "load-congress-local"

    def test_passes_archive_path(self) -> None:
        with _run_harness():
            with self._congress_local_env() as c:
                run(self._argv())
        assert c.mock_run.call_args.args[1] == self._ARCHIVE_PATH

    def test_passes_congress_number_in_options(self) -> None:
        with _run_harness():
            with self._congress_local_env() as c:
                run(self._argv())
        options = c.mock_run.call_args.args[2]
        assert options.congress == self._CONGRESS

    def test_include_votes_defaults_to_false(self) -> None:
        with _run_harness():
            with self._congress_local_env() as c:
                run(self._argv())
        options = c.mock_run.call_args.args[2]
        assert options.include_votes is False

    def test_passes_runtime_context(self) -> None:
        with _run_harness() as h:
            with self._congress_local_env() as c:
                run(self._argv())
        assert c.mock_run.call_args.args[0] is h.rt.context

    def test_summarizes_load_result(self) -> None:
        load_result = self._load_result()
        with _run_harness():
            with self._congress_local_env(load_result=load_result) as c:
                run(self._argv())
        c.mock_summarize.assert_called_once_with(load_result)

    def test_returns_1_on_exception(self) -> None:
        with _run_harness() as h:
            with patch(
                f"{_MOD}.load_congress_local", side_effect=RuntimeError("bundle parse failed")
            ):
                code = run(self._argv())
        assert code == 1
        printed = _printed(h)
        assert printed["ok"] is False
        assert "bundle parse failed" in printed["error"]

    def test_missing_archive_arg_causes_nonzero_exit(self) -> None:
        code = run(["load-congress-local", "--congress", str(self._CONGRESS)])
        assert code != 0

    def test_missing_congress_arg_causes_nonzero_exit(self) -> None:
        code = run(["load-congress-local", "--archive", str(self._ARCHIVE_PATH)])
        assert code != 0


# ---------------------------------------------------------------------------
# process-disclosures-local command
# ---------------------------------------------------------------------------


class TestProcessDisclosuresLocalCommand:
    _BUNDLE_PATH = Path("/tmp/disclosures_bundle.json")

    def _process_result(self) -> MagicMock:
        return MagicMock()

    def _summary(self) -> dict:
        return {
            "parse": {"processed": 5, "succeeded": 4, "failed": 1},
            "transformed": 3,
            "load": {"run_id": 99, "ok": True, "counts": {}},
        }

    def _argv(self, extra: list[str] | None = None) -> list[str]:
        base = ["process-disclosures-local", "--bundle", str(self._BUNDLE_PATH)]
        return base + (extra or [])

    @contextmanager
    def _process_local_env(self, process_result=None, summary=None, bundle=None):
        process_result = process_result or self._process_result()
        summary = summary or self._summary()
        bundle = bundle or MagicMock()
        with (
            patch(f"{_MOD}.load_disclosures_bundle", return_value=bundle) as mock_load,
            patch(f"{_MOD}.process_disclosures_local", return_value=process_result) as mock_run,
            patch(
                f"{_MOD}.summarize_disclosures_bundle_process_result", return_value=summary
            ) as mock_summarize,
        ):
            yield SimpleNamespace(
                mock_load=mock_load,
                mock_run=mock_run,
                mock_summarize=mock_summarize,
            )

    def test_returns_0_on_success(self) -> None:
        with _run_harness():
            with self._process_local_env():
                code = run(self._argv())
        assert code == 0

    def test_prints_result_with_ok_and_command(self) -> None:
        with _run_harness() as h:
            with self._process_local_env():
                run(self._argv())
        printed = _printed(h)
        assert printed["ok"] is True
        assert printed["command"] == "process-disclosures-local"

    def test_loads_bundle_from_path(self) -> None:
        with _run_harness():
            with self._process_local_env() as p:
                run(self._argv())
        p.mock_load.assert_called_once_with(self._BUNDLE_PATH)

    def test_passes_loaded_bundle_to_process(self) -> None:
        bundle = MagicMock()
        with _run_harness():
            with self._process_local_env(bundle=bundle) as p:
                run(self._argv())
        assert p.mock_run.call_args.args[1] is bundle

    def test_passes_runtime_context(self) -> None:
        with _run_harness() as h:
            with self._process_local_env() as p:
                run(self._argv())
        assert p.mock_run.call_args.args[0] is h.rt.context

    def test_summarizes_result(self) -> None:
        process_result = self._process_result()
        with _run_harness():
            with self._process_local_env(process_result=process_result) as p:
                run(self._argv())
        p.mock_summarize.assert_called_once_with(process_result)

    def test_missing_bundle_arg_causes_nonzero_exit(self) -> None:
        code = run(["process-disclosures-local"])
        assert code != 0

    def test_returns_1_on_load_exception(self) -> None:
        with _run_harness() as h:
            with patch(
                f"{_MOD}.load_disclosures_bundle", side_effect=FileNotFoundError("no bundle")
            ):
                code = run(self._argv())
        assert code == 1
        printed = _printed(h)
        assert printed["ok"] is False
        assert "no bundle" in printed["error"]

    def test_returns_1_on_process_exception(self) -> None:
        with _run_harness() as h:
            with (
                patch(f"{_MOD}.load_disclosures_bundle", return_value=MagicMock()),
                patch(f"{_MOD}.process_disclosures_local", side_effect=RuntimeError("disk error")),
            ):
                code = run(self._argv())
        assert code == 1
        printed = _printed(h)
        assert printed["ok"] is False
        assert "disk error" in printed["error"]


# ---------------------------------------------------------------------------
# run-oracle-local command
# ---------------------------------------------------------------------------


class TestRunOracleLocalCommand:
    _DATE_STR = "2025-01-15"
    _DATE = dt.date(2025, 1, 15)
    _CONGRESS_ARCHIVE = Path("/tmp/congress_119.json")
    _DISCLOSURES_BUNDLE = Path("/tmp/disclosures_bundle.json")
    _TARGET_DIR = Path("/tmp/oracle-out")

    def _oracle_result(self) -> MagicMock:
        r = MagicMock()
        r.snapshot_id = self._DATE_STR
        r.congress = {"run_id": 0}
        r.disclosures = {"run_id": 1, "source_slug": "financial-disclosures"}
        r.recompute = {"run_id": 2, "rule_fires": 3}
        r.publish = {"run_id": 3, "snapshot_id": self._DATE_STR, "succeeded": True}
        r.verify = {"ok": True}
        return r

    def _verify_result(self) -> MagicMock:
        r = MagicMock()
        r.ok = True
        r.total_checked = 4
        r.total_errors = 0
        r.total_warnings = 0
        r.stages = []
        return r

    def _summary(self) -> dict:
        return {
            "snapshot_id": self._DATE_STR,
            "congress": {},
            "disclosures": {},
            "recompute": {},
            "publish": {},
            "verify": {},
        }

    def _argv(self, extra: list[str] | None = None) -> list[str]:
        base = [
            "run-oracle-local",
            "--congress-archive",
            str(self._CONGRESS_ARCHIVE),
            "--disclosures-bundle",
            str(self._DISCLOSURES_BUNDLE),
            "--snapshot-date",
            self._DATE_STR,
            "--target-dir",
            str(self._TARGET_DIR),
        ]
        return base + (extra or [])

    def _roundtrip_summary(self) -> dict:
        return {
            "ok": True,
            "total_checked": 4,
            "total_errors": 0,
            "total_warnings": 0,
            "stages": [],
        }

    @contextmanager
    def _oracle_env(self, oracle_result=None, summary=None, bundle=None):
        oracle_result = oracle_result or self._oracle_result()
        summary = summary or self._summary()
        bundle = bundle or MagicMock()
        with (
            patch(f"{_MOD}.load_disclosures_bundle", return_value=bundle) as mock_load_bundle,
            patch(f"{_MOD}.run_oracle_local_command", return_value=oracle_result) as mock_oracle,
            patch(
                f"{_MOD}.summarize_local_oracle_run_result", return_value=summary
            ) as mock_summarize,
        ):
            yield SimpleNamespace(
                mock_load_bundle=mock_load_bundle,
                mock_oracle=mock_oracle,
                mock_summarize=mock_summarize,
                bundle=bundle,
            )

    def test_returns_0_on_success(self) -> None:
        with _run_harness():
            with self._oracle_env():
                code = run(self._argv())
        assert code == 0

    def test_prints_result_with_ok_and_command(self) -> None:
        with _run_harness() as h:
            with self._oracle_env():
                run(self._argv())
        printed = _printed(h)
        assert printed["ok"] is True
        assert printed["command"] == "run-oracle-local"

    def test_passes_runtime_context(self) -> None:
        with _run_harness() as h:
            with self._oracle_env() as o:
                run(self._argv())
        assert o.mock_oracle.call_args.args[0] is h.rt.context

    def test_congress_archive_path_forwarded(self) -> None:
        with _run_harness():
            with self._oracle_env() as o:
                run(self._argv())
        assert o.mock_oracle.call_args.args[1] == self._CONGRESS_ARCHIVE

    def test_loads_disclosures_bundle_from_path(self) -> None:
        with _run_harness():
            with self._oracle_env() as o:
                run(self._argv())
        o.mock_load_bundle.assert_called_once_with(self._DISCLOSURES_BUNDLE)

    def test_disclosures_bundle_forwarded(self) -> None:
        bundle = MagicMock()
        with _run_harness():
            with self._oracle_env(bundle=bundle) as o:
                run(self._argv())
        assert o.mock_oracle.call_args.args[2] is bundle

    def test_snapshot_date_in_options(self) -> None:
        with _run_harness():
            with self._oracle_env() as o:
                run(self._argv())
        options = o.mock_oracle.call_args.args[3]
        assert options.snapshot_date == self._DATE

    def test_target_dir_in_options(self) -> None:
        with _run_harness():
            with self._oracle_env() as o:
                run(self._argv())
        options = o.mock_oracle.call_args.args[3]
        assert options.target_dir == self._TARGET_DIR

    def test_both_chamber_maps_to_none_in_options(self) -> None:
        with _run_harness():
            with self._oracle_env() as o:
                run(self._argv(["--chamber", "both"]))
        options = o.mock_oracle.call_args.args[3]
        assert options.congress_options.chamber is None

    def test_house_chamber_in_options(self) -> None:
        with _run_harness():
            with self._oracle_env() as o:
                run(self._argv(["--chamber", "house"]))
        options = o.mock_oracle.call_args.args[3]
        assert options.congress_options.chamber == "house"

    def test_limit_in_congress_options(self) -> None:
        with _run_harness():
            with self._oracle_env() as o:
                run(self._argv(["--limit", "5"]))
        options = o.mock_oracle.call_args.args[3]
        assert options.congress_options.limit == 5

    def test_explicit_congress_and_artifact_root_forwarded(self) -> None:
        with _run_harness():
            with self._oracle_env() as o:
                run(self._argv(["--congress", "118", "--artifact-root", "/tmp/artifacts"]))
        options = o.mock_oracle.call_args.args[3]
        assert options.congress_options.congress == 118
        assert options.congress_options.congress_source == "explicit-arg"
        assert options.artifact_root == Path("/tmp/artifacts")

    def test_explicit_snapshot_id_in_options(self) -> None:
        with _run_harness():
            with self._oracle_env() as o:
                run(self._argv(["--snapshot-id", "custom-snap-123"]))
        options = o.mock_oracle.call_args.args[3]
        assert options.snapshot_id == "custom-snap-123"

    def test_default_snapshot_id_is_none(self) -> None:
        with _run_harness():
            with self._oracle_env() as o:
                run(self._argv())
        options = o.mock_oracle.call_args.args[3]
        assert options.snapshot_id is None

    def test_summarizes_oracle_result(self) -> None:
        oracle_result = self._oracle_result()
        with _run_harness():
            with self._oracle_env(oracle_result=oracle_result) as o:
                run(self._argv())
        o.mock_summarize.assert_called_once_with(oracle_result)

    def test_returns_1_on_exception(self) -> None:
        with _run_harness() as h:
            with (
                patch(f"{_MOD}.load_disclosures_bundle", return_value=MagicMock()),
                patch(
                    f"{_MOD}.run_oracle_local_command", side_effect=RuntimeError("oracle failed")
                ),
            ):
                code = run(self._argv())
        assert code == 1
        printed = _printed(h)
        assert printed["ok"] is False
        assert "oracle failed" in printed["error"]

    def test_missing_required_args_causes_nonzero_exit(self) -> None:
        code = run(["run-oracle-local"])
        assert code != 0

    def test_verify_included_in_oracle_output(self) -> None:
        summary = self._summary() | {
            "verify": {
                "ok": True,
                "total_checked": 6,
                "total_errors": 0,
                "total_warnings": 0,
                "stages": [],
            }
        }
        with _run_harness() as h:
            with self._oracle_env(summary=summary):
                run(self._argv())
        printed = _printed(h)
        assert "verify" in printed
        assert printed["verify"]["ok"] is True
        assert printed["verify"]["total_checked"] == 6

    def test_verify_errors_surfaced_in_output(self) -> None:
        summary = self._summary() | {
            "verify": {
                "ok": False,
                "total_checked": 6,
                "total_errors": 2,
                "total_warnings": 0,
                "stages": [],
            }
        }
        with _run_harness() as h:
            with self._oracle_env(summary=summary):
                run(self._argv())
        printed = _printed(h)
        assert printed["verify"]["ok"] is False
        assert printed["verify"]["total_errors"] == 2

    def test_returns_nonzero_when_verify_fails(self) -> None:
        summary = self._summary() | {
            "verify": {
                "ok": False,
                "total_checked": 6,
                "total_errors": 2,
                "total_warnings": 0,
                "stages": [],
            }
        }
        with _run_harness() as h:
            with self._oracle_env(summary=summary):
                code = run(self._argv())
        assert code == 1
        assert _printed(h)["ok"] is False

    def test_returns_nonzero_when_congress_stage_fails(self) -> None:
        summary = self._summary() | {
            "congress": {
                "load_ok": False,
            }
        }
        with _run_harness() as h:
            with self._oracle_env(summary=summary):
                code = run(self._argv())
        assert code == 1
        assert _printed(h)["ok"] is False

    def test_returns_nonzero_when_disclosures_stage_fails(self) -> None:
        summary = self._summary() | {
            "disclosures": {
                "load_ok": False,
            }
        }
        with _run_harness() as h:
            with self._oracle_env(summary=summary):
                code = run(self._argv())
        assert code == 1
        assert _printed(h)["ok"] is False

    def test_roundtrip_included_in_oracle_output(self) -> None:
        roundtrip = {
            "ok": True,
            "total_checked": 5,
            "total_errors": 0,
            "total_warnings": 0,
            "stages": [],
        }
        summary = self._summary() | {"roundtrip": roundtrip}
        with _run_harness() as h:
            with self._oracle_env(summary=summary):
                run(self._argv())
        printed = _printed(h)
        assert "roundtrip" in printed
        assert printed["roundtrip"]["ok"] is True
        assert printed["roundtrip"]["total_checked"] == 5

    def test_roundtrip_comes_from_oracle_summary(self) -> None:
        with _run_harness():
            with self._oracle_env() as o:
                run(self._argv())
        o.mock_summarize.assert_called_once_with(o.mock_oracle.return_value)

    def test_roundtrip_errors_surfaced_in_output(self) -> None:
        roundtrip = {
            "ok": False,
            "total_checked": 3,
            "total_errors": 1,
            "total_warnings": 0,
            "stages": [],
        }
        summary = self._summary() | {"roundtrip": roundtrip}
        with _run_harness() as h:
            with self._oracle_env(summary=summary):
                run(self._argv())
        printed = _printed(h)
        assert printed["roundtrip"]["ok"] is False
        assert printed["roundtrip"]["total_errors"] == 1

    def test_returns_nonzero_when_roundtrip_fails(self) -> None:
        roundtrip = {
            "ok": False,
            "total_checked": 3,
            "total_errors": 1,
            "total_warnings": 0,
            "stages": [],
        }
        summary = self._summary() | {"roundtrip": roundtrip}
        with _run_harness() as h:
            with self._oracle_env(summary=summary):
                code = run(self._argv())
        assert code == 1
        assert _printed(h)["ok"] is False

    def test_output_contains_both_verify_and_roundtrip(self) -> None:
        roundtrip = {
            "ok": True,
            "total_checked": 4,
            "total_errors": 0,
            "total_warnings": 0,
            "stages": [],
        }
        summary = self._summary() | {"verify": {"ok": True, "stages": []}, "roundtrip": roundtrip}
        with _run_harness() as h:
            with self._oracle_env(summary=summary):
                run(self._argv())
        printed = _printed(h)
        assert "verify" in printed
        assert "roundtrip" in printed


# ---------------------------------------------------------------------------
# verify-publish command
# ---------------------------------------------------------------------------


class TestVerifyPublishCommand:
    _PUBLISH_ROOT = Path("/tmp/snapshot")

    def _verify_result(self) -> MagicMock:
        r = MagicMock()
        r.ok = True
        r.total_checked = 10
        r.total_errors = 0
        r.total_warnings = 0
        r.stages = []
        return r

    def _argv(self) -> list[str]:
        return ["verify-publish", "--publish-root", str(self._PUBLISH_ROOT)]

    @contextmanager
    def _verify_env(self, verify_result=None):
        verify_result = verify_result or self._verify_result()
        with patch(f"{_MOD}.verify_publish_local", return_value=verify_result) as mock_verify:
            yield SimpleNamespace(mock_verify=mock_verify, verify_result=verify_result)

    def test_returns_0_on_success(self) -> None:
        with _run_harness():
            with self._verify_env():
                code = run(self._argv())
        assert code == 0

    def test_passes_publish_root_to_verify(self) -> None:
        with _run_harness():
            with self._verify_env() as v:
                run(self._argv())
        v.mock_verify.assert_called_once_with(self._PUBLISH_ROOT)

    def test_prints_result_with_command_and_summary_fields(self) -> None:
        with _run_harness() as h:
            with self._verify_env():
                run(self._argv())
        printed = _printed(h)
        assert printed["command"] == "verify-publish"
        assert "total_checked" in printed
        assert "total_errors" in printed
        assert "total_warnings" in printed
        assert "stages" in printed

    def test_ok_reflects_verify_result_when_passing(self) -> None:
        with _run_harness() as h:
            with self._verify_env():
                run(self._argv())
        assert _printed(h)["ok"] is True

    def test_ok_reflects_verify_result_when_failing(self) -> None:
        failing = self._verify_result()
        failing.ok = False
        with _run_harness() as h:
            with self._verify_env(verify_result=failing):
                code = run(self._argv())
        assert code == 1
        assert _printed(h)["ok"] is False

    def test_stages_serialized_in_output(self) -> None:
        result = self._verify_result()
        stage = MagicMock()
        stage.stage = "manifest"
        stage.checked = 3
        stage.ok = True
        stage.error_count = 0
        stage.warning_count = 0
        result.stages = [stage]
        with _run_harness() as h:
            with self._verify_env(verify_result=result):
                run(self._argv())
        printed = _printed(h)
        assert printed["stages"] == [
            {"stage": "manifest", "checked": 3, "ok": True, "errors": 0, "warnings": 0}
        ]

    def test_does_not_call_build_runtime(self) -> None:
        with _run_harness() as h:
            with self._verify_env():
                run(self._argv())
        h.mock_build.assert_not_called()

    def test_returns_1_on_exception(self) -> None:
        with _run_harness() as h:
            with patch(f"{_MOD}.verify_publish_local", side_effect=RuntimeError("corrupt tree")):
                code = run(self._argv())
        assert code == 1
        printed = _printed(h)
        assert printed["ok"] is False
        assert "corrupt tree" in printed["error"]

    def test_missing_publish_root_causes_nonzero_exit(self) -> None:
        code = run(["verify-publish"])
        assert code != 0

    def test_failing_verify_writes_human_summary_to_stderr(self, tmp_path: Path, capsys) -> None:
        empty_root = tmp_path / "empty"
        empty_root.mkdir()

        code = run(["verify-publish", "--publish-root", str(empty_root)])

        captured = capsys.readouterr()
        printed = json.loads(captured.out)
        assert code == 1
        assert printed["ok"] is False
        assert "verify-publish failed" in captured.err


# ---------------------------------------------------------------------------
# verify-publish-roundtrip command
# ---------------------------------------------------------------------------


class TestVerifyPublishRoundtripCommand:
    _PUBLISH_ROOT = Path("/tmp/snapshot-roundtrip")

    def _verify_result(self) -> MagicMock:
        r = MagicMock()
        r.ok = True
        r.total_checked = 8
        r.total_errors = 0
        r.total_warnings = 0
        r.stages = []
        return r

    def _argv(self) -> list[str]:
        return ["verify-publish-roundtrip", "--publish-root", str(self._PUBLISH_ROOT)]

    @contextmanager
    def _roundtrip_env(self, verify_result=None, roundtrip_summary=None):
        verify_result = verify_result or self._verify_result()
        roundtrip_summary = roundtrip_summary or {
            "ok": True,
            "total_checked": 8,
            "total_errors": 0,
            "total_warnings": 0,
            "stages": [],
        }
        with (
            patch(
                f"{_MOD}.verify_publish_roundtrip_local", return_value=verify_result
            ) as mock_verify,
            patch(
                f"{_MOD}.summarize_publish_roundtrip_result", return_value=roundtrip_summary
            ) as mock_summarize,
        ):
            yield SimpleNamespace(
                mock_verify=mock_verify,
                mock_summarize=mock_summarize,
                verify_result=verify_result,
            )

    def test_returns_0_on_success(self) -> None:
        with _run_harness():
            with self._roundtrip_env():
                code = run(self._argv())
        assert code == 0

    def test_prints_command_key(self) -> None:
        with _run_harness() as h:
            with self._roundtrip_env():
                run(self._argv())
        printed = _printed(h)
        assert printed["command"] == "verify-publish-roundtrip"

    def test_passes_publish_root_to_verify(self) -> None:
        with _run_harness() as h:
            with self._roundtrip_env() as r:
                run(self._argv())
        r.mock_verify.assert_called_once_with(h.rt.context, self._PUBLISH_ROOT)

    def test_result_nested_under_roundtrip_key(self) -> None:
        roundtrip_summary = {
            "ok": True,
            "total_checked": 12,
            "total_errors": 0,
            "total_warnings": 0,
            "stages": [],
        }
        with _run_harness() as h:
            with self._roundtrip_env(roundtrip_summary=roundtrip_summary):
                run(self._argv())
        printed = _printed(h)
        assert "roundtrip" in printed
        assert printed["roundtrip"]["total_checked"] == 12

    def test_ok_reflects_verify_result_when_passing(self) -> None:
        with _run_harness() as h:
            with self._roundtrip_env():
                run(self._argv())
        assert _printed(h)["ok"] is True

    def test_ok_reflects_verify_result_when_failing(self) -> None:
        failing = self._verify_result()
        failing.ok = False
        with _run_harness() as h:
            with self._roundtrip_env(verify_result=failing):
                code = run(self._argv())
        assert code == 1
        assert _printed(h)["ok"] is False

    def test_summarize_called_with_verify_result(self) -> None:
        verify_result = self._verify_result()
        with _run_harness():
            with self._roundtrip_env(verify_result=verify_result) as r:
                run(self._argv())
        r.mock_summarize.assert_called_once_with(verify_result)

    def test_builds_runtime_for_db_backed_roundtrip(self) -> None:
        with _run_harness() as h:
            with self._roundtrip_env():
                run(self._argv())
        h.mock_build.assert_called_once()

    def test_returns_1_on_exception(self) -> None:
        with _run_harness() as h:
            with patch(
                f"{_MOD}.verify_publish_roundtrip_local", side_effect=RuntimeError("broken tree")
            ):
                code = run(self._argv())
        assert code == 1
        printed = _printed(h)
        assert printed["ok"] is False
        assert "broken tree" in printed["error"]

    def test_missing_publish_root_causes_nonzero_exit(self) -> None:
        code = run(["verify-publish-roundtrip"])
        assert code != 0

    def test_failing_roundtrip_writes_human_summary_to_stderr(self, capsys) -> None:
        issue = MagicMock()
        issue.message = "manifest mismatch"
        issue.severity = "error"
        issue.stage = "snapshot"
        issue.path = "snapshots/2024-06-01/manifest.json"

        failing = self._verify_result()
        failing.ok = False
        failing.total_errors = 1
        failing.all_issues.return_value = [issue]

        runtime = SimpleNamespace(context=MagicMock(name="ctx"))
        with (
            patch(f"{_MOD}.build_runtime", return_value=runtime),
            patch("src.runtime.commands.open_connection", return_value=MagicMock(name="conn")),
            patch("src.runtime.commands._verify_roundtrip", return_value=failing),
        ):
            code = run(self._argv())

        captured = capsys.readouterr()
        printed = json.loads(captured.out)
        assert code == 1
        assert printed["ok"] is False
        assert "verify-publish-roundtrip failed" in captured.err


# ---------------------------------------------------------------------------
# Command registry
# ---------------------------------------------------------------------------


class TestCommandRegistry:
    """Tests for the COMMAND_REGISTRY dispatch table."""

    _EXPECTED_COMMANDS = frozenset(
        {
            "bootstrap-db",
            "runtime-env-preflight",
            "verify-runtime-env-preflight",
            "status",
            "load-congress",
            "materialize-fec-bulk-files",
            "materialize-member-fec-crosswalk",
            "materialize-public-statement-rss",
            "materialize-public-statement-rows",
            "load-fec-local",
            "load-member-fec-crosswalk-local",
            "verify-fec-inputs",
            "load-disclosures",
            "parse-disclosures",
            "process-disclosures",
            "recompute",
            "verify-public-statement-rows",
            "publish",
            "load-congress-local",
            "process-disclosures-local",
            "run-oracle-local",
            "plan-history-backfill",
            "check-history-backfill-inputs",
            "write-congress-archive-manifest",
            "materialize-congress-archive",
            "materialize-history-backfill-inputs",
            "materialize-disclosures-bundle",
            "materialize-bill-semantics",
            "verify-bill-semantics",
            "verify-bill-semantics-plan",
            "run-history-launch-local",
            "run-history-backfill-local",
            "aggregate-history",
            "prediction-backtest",
            "prediction-input-inventory",
            "verify-prediction-input-inventory",
            "prediction-eval-report",
            "prediction-eval-window-plan",
            "verify-prediction-eval-window-plan",
            "prediction-eval-window-summary",
            "verify-prediction-eval-window-summary",
            "verify-prediction-eval-window-run",
            "prediction-source-url-audit",
            "verify-prediction-source-url-audit",
            "verify-prediction-eval-manifest",
            "verify-prediction-backtest",
            "verify-prediction-benchmark",
            "verify-prediction-backfill-plan",
            "prediction-offline-readiness-summary",
            "verify-prediction-offline-readiness-summary",
            "verify-prediction-resume-script",
            "verify-prediction-operator-handoff",
            "verify-prediction-operator-runbook",
            "prediction-operator-status",
            "verify-prediction-operator-status",
            "prediction-operator-packet-manifest",
            "verify-prediction-operator-packet-manifest",
            "prediction-operator-packet-export",
            "verify-prediction-operator-packet-export",
            "verify-prediction-operator-packet-directory",
            "prediction-operator-resume-plan",
            "verify-prediction-operator-resume-plan",
            "verify-prediction-operator-resume-run",
            "verify-publish",
            "verify-publish-roundtrip",
            "verify-history-aggregate",
        }
    )

    def test_registry_contains_all_commands(self) -> None:
        assert set(COMMAND_REGISTRY.keys()) == self._EXPECTED_COMMANDS

    def test_registry_values_are_callable(self) -> None:
        for name, handler in COMMAND_REGISTRY.items():
            assert callable(handler), f"{name!r} handler is not callable"

    def test_unknown_command_returns_error(self) -> None:
        with _run_harness() as h:
            with patch(f"{_MAIN_MOD}.parse_args") as mock_parse:
                mock_parse.return_value = SimpleNamespace(command="no-such-command")
                code = run(["no-such-command"])
        assert code == 1
        printed = _printed(h)
        assert printed["ok"] is False
        assert "unknown command" in printed["error"]

    def test_dispatch_routes_to_correct_handler(self) -> None:
        """Each registry entry is invoked when its command name matches."""
        for cmd_name in self._EXPECTED_COMMANDS:
            handler = COMMAND_REGISTRY[cmd_name]
            assert handler.__name__.startswith("_handle_"), (
                f"Registry entry {cmd_name!r} points to {handler.__name__!r}, "
                f"expected a _handle_* function"
            )


# ---------------------------------------------------------------------------
# Module entrypoint
# ---------------------------------------------------------------------------


class TestModuleEntrypoint:
    def test_module_invocation_exits_nonzero_for_verify_publish_failures(self) -> None:
        with (
            patch.object(
                sys, "argv", ["python3", "verify-publish", "--publish-root", "/tmp/snapshot"]
            ),
            patch(
                "src.runtime.cli.parse_args", return_value=SimpleNamespace(command="verify-publish")
            ),
            patch(
                "src.runtime.commands.dispatch_command",
                return_value={"ok": False, "command": "verify-publish", "total_errors": 1},
            ),
            patch("src.runtime.output.as_json", side_effect=lambda obj: obj),
            patch("builtins.print"),
            _run_module_as_main(),
            pytest.raises(SystemExit) as excinfo,
        ):
            runpy.run_module("src.runtime.main", run_name="__main__", alter_sys=True)

        assert excinfo.value.code == 1

    def test_module_invocation_exits_nonzero_for_verify_publish_roundtrip_failures(self) -> None:
        with (
            patch.object(
                sys,
                "argv",
                ["python3", "verify-publish-roundtrip", "--publish-root", "/tmp/snapshot"],
            ),
            patch(
                "src.runtime.cli.parse_args",
                return_value=SimpleNamespace(command="verify-publish-roundtrip"),
            ),
            patch(
                "src.runtime.commands.dispatch_command",
                return_value={
                    "ok": False,
                    "command": "verify-publish-roundtrip",
                    "roundtrip": {"ok": False},
                },
            ),
            patch("src.runtime.output.as_json", side_effect=lambda obj: obj),
            patch("builtins.print"),
            _run_module_as_main(),
            pytest.raises(SystemExit) as excinfo,
        ):
            runpy.run_module("src.runtime.main", run_name="__main__", alter_sys=True)

        assert excinfo.value.code == 1

    def test_module_invocation_exits_nonzero_for_verify_history_aggregate_failures(self) -> None:
        with (
            patch.object(
                sys,
                "argv",
                ["python3", "verify-history-aggregate", "--publish-root", "/tmp/history"],
            ),
            patch(
                "src.runtime.cli.parse_args",
                return_value=SimpleNamespace(command="verify-history-aggregate"),
            ),
            patch(
                "src.runtime.commands.dispatch_command",
                return_value={
                    "ok": False,
                    "command": "verify-history-aggregate",
                    "total_errors": 1,
                },
            ),
            patch("src.runtime.output.as_json", side_effect=lambda obj: obj),
            patch("builtins.print"),
            _run_module_as_main(),
            pytest.raises(SystemExit) as excinfo,
        ):
            runpy.run_module("src.runtime.main", run_name="__main__", alter_sys=True)

        assert excinfo.value.code == 1

    def test_module_bootstrap_help_exposes_current_dry_run_text(self, capsys) -> None:
        with (
            patch.object(sys, "argv", ["python3", "bootstrap-db", "--help"]),
            _run_module_as_main(),
            pytest.raises(SystemExit) as excinfo,
        ):
            runpy.run_module("src.runtime.main", run_name="__main__", alter_sys=True)

        captured = capsys.readouterr()
        assert excinfo.value.code == 0
        assert "db/schema.sql" in captured.out
        assert "Show the bootstrap plan without applying schema SQL." in captured.out

    def test_module_help_survives_fresh_import_graph(self, capsys) -> None:
        with (
            patch.object(sys, "argv", ["python3", "--help"]),
            _fresh_runtime_module_import(),
            pytest.raises(SystemExit) as excinfo,
        ):
            runpy.run_module("src.runtime.main", run_name="__main__", alter_sys=True)

        captured = capsys.readouterr()
        assert excinfo.value.code == 0
        assert "plan-history-backfill" in captured.out
