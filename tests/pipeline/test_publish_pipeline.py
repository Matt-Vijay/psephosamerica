"""Tests for src/pipeline/publish_pipeline.py.

Pure unit tests only — no DB, no network.
Filesystem writes go to a tmp_path fixture (pytest-provided temp directory).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.export.writer import PlannedFile
from src.pipeline.publish_pipeline import (
    Planner,
    PublishConfig,
    PublishResult,
    _make_plan_stage,
    _make_verify_stage,
    _make_write_stage,
    run_publish,
)
from src.pipeline.stages import PipelineResult, StageResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _planned_file(path: str = "test/file.json", content: bytes = b'{"x":1}') -> PlannedFile:
    return PlannedFile.from_bytes(path, content)


def _planner(*files: PlannedFile) -> Planner:
    """Return a zero-argument callable that yields the given planned files."""

    def _fn() -> list[PlannedFile]:
        return list(files)

    return _fn


def _empty_planner() -> Planner:
    return _planner()


# ---------------------------------------------------------------------------
# PublishConfig
# ---------------------------------------------------------------------------


class TestPublishConfig:
    def test_is_frozen(self, tmp_path: Path) -> None:
        cfg = PublishConfig(snapshot_id="s", target_dir=tmp_path)
        with pytest.raises((AttributeError, TypeError)):
            cfg.snapshot_id = "changed"  # type: ignore[misc]

    def test_defaults(self, tmp_path: Path) -> None:
        cfg = PublishConfig(snapshot_id="snap", target_dir=tmp_path)
        assert cfg.stop_on_failure is True

    def test_stop_on_failure_override(self, tmp_path: Path) -> None:
        cfg = PublishConfig(snapshot_id="snap", target_dir=tmp_path, stop_on_failure=False)
        assert cfg.stop_on_failure is False


# ---------------------------------------------------------------------------
# PublishResult
# ---------------------------------------------------------------------------


class TestPublishResult:
    def _succeeded_pipeline(self) -> PipelineResult:
        pr = PipelineResult()
        pr.stage_results.append(StageResult(stage_name="s", status="succeeded"))
        return pr

    def _failed_pipeline(self) -> PipelineResult:
        pr = PipelineResult()
        pr.stage_results.append(StageResult(stage_name="s", status="failed"))
        return pr

    def test_succeeded_when_pipeline_ok_and_no_failures(self) -> None:
        result = PublishResult(
            snapshot_id="snap",
            planned_count=2,
            written_count=2,
            verification_failures=[],
            pipeline_result=self._succeeded_pipeline(),
        )
        assert result.succeeded is True

    def test_not_succeeded_when_pipeline_failed(self) -> None:
        result = PublishResult(
            snapshot_id="snap",
            planned_count=2,
            written_count=0,
            verification_failures=[],
            pipeline_result=self._failed_pipeline(),
        )
        assert result.succeeded is False

    def test_not_succeeded_when_verification_failures(self) -> None:
        result = PublishResult(
            snapshot_id="snap",
            planned_count=1,
            written_count=1,
            verification_failures=["test/file.json"],
            pipeline_result=self._succeeded_pipeline(),
        )
        assert result.succeeded is False

    def test_defaults(self) -> None:
        result = PublishResult(snapshot_id="snap", planned_count=0, written_count=0)
        assert result.verification_failures == []
        assert result.pipeline_result.succeeded  # empty PipelineResult is succeeded


# ---------------------------------------------------------------------------
# Stage builders — plan
# ---------------------------------------------------------------------------


class TestPlanStage:
    def test_stores_planned_files_in_context(self) -> None:
        pf = _planned_file()
        stage = _make_plan_stage(_planner(pf))
        ctx: dict[str, Any] = {}
        logs: list[str] = []
        output = stage.fn(ctx, logs)
        assert ctx["planned_files"] == [pf]
        assert output == [pf]

    def test_logs_count(self) -> None:
        stage = _make_plan_stage(_planner(_planned_file(), _planned_file("b/c.json")))
        ctx: dict[str, Any] = {}
        logs: list[str] = []
        stage.fn(ctx, logs)
        assert any("2" in line for line in logs)

    def test_empty_planner(self) -> None:
        stage = _make_plan_stage(_empty_planner())
        ctx: dict[str, Any] = {}
        logs: list[str] = []
        output = stage.fn(ctx, logs)
        assert output == []
        assert ctx["planned_files"] == []

    def test_planner_exception_propagates(self) -> None:
        def bad_planner() -> list[PlannedFile]:
            raise RuntimeError("planner broke")

        stage = _make_plan_stage(bad_planner)
        ctx: dict[str, Any] = {}
        logs: list[str] = []
        with pytest.raises(RuntimeError, match="planner broke"):
            stage.fn(ctx, logs)


# ---------------------------------------------------------------------------
# Stage builders — write
# ---------------------------------------------------------------------------


class TestWriteStage:
    def test_writes_files_to_disk(self, tmp_path: Path) -> None:
        pf = _planned_file("sub/data.json", b'{"ok":true}')
        ctx: dict[str, Any] = {"planned_files": [pf]}
        stage = _make_write_stage(tmp_path)
        stage.fn(ctx, [])
        assert (tmp_path / "sub" / "data.json").exists()
        assert (tmp_path / "sub" / "data.json").read_bytes() == b'{"ok":true}'

    def test_returns_count(self, tmp_path: Path) -> None:
        files = [_planned_file(f"f{i}.json", b"{}") for i in range(3)]
        ctx: dict[str, Any] = {"planned_files": files}
        stage = _make_write_stage(tmp_path)
        result = stage.fn(ctx, [])
        assert result == 3

    def test_stores_written_count_in_context(self, tmp_path: Path) -> None:
        pf = _planned_file()
        ctx: dict[str, Any] = {"planned_files": [pf]}
        stage = _make_write_stage(tmp_path)
        stage.fn(ctx, [])
        assert ctx["written_count"] == 1

    def test_empty_plan_writes_nothing(self, tmp_path: Path) -> None:
        ctx: dict[str, Any] = {"planned_files": []}
        stage = _make_write_stage(tmp_path)
        result = stage.fn(ctx, [])
        assert result == 0
        assert ctx["written_count"] == 0


# ---------------------------------------------------------------------------
# Stage builders — verify
# ---------------------------------------------------------------------------


class TestVerifyStage:
    def _write(self, tmp_path: Path, pf: PlannedFile) -> None:
        dest = tmp_path / pf.path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(pf.content)

    def test_succeeds_when_hashes_match(self, tmp_path: Path) -> None:
        pf = _planned_file()
        self._write(tmp_path, pf)
        ctx: dict[str, Any] = {"planned_files": [pf]}
        stage = _make_verify_stage(tmp_path)
        failures = stage.fn(ctx, [])
        assert failures == []

    def test_fails_when_file_missing(self, tmp_path: Path) -> None:
        pf = _planned_file("missing/file.json")
        ctx: dict[str, Any] = {"planned_files": [pf]}
        stage = _make_verify_stage(tmp_path)
        with pytest.raises(ValueError, match="Verification failed"):
            stage.fn(ctx, [])
        assert "missing/file.json" in ctx["verification_failures"]

    def test_fails_when_hash_mismatch(self, tmp_path: Path) -> None:
        pf = _planned_file("tampered.json", b'{"x":1}')
        dest = tmp_path / pf.path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b'{"x":2}')  # different content
        ctx: dict[str, Any] = {"planned_files": [pf]}
        stage = _make_verify_stage(tmp_path)
        with pytest.raises(ValueError):
            stage.fn(ctx, [])
        assert "tampered.json" in ctx["verification_failures"]

    def test_stores_failures_in_context(self, tmp_path: Path) -> None:
        pf = _planned_file("gone.json")
        ctx: dict[str, Any] = {"planned_files": [pf]}
        stage = _make_verify_stage(tmp_path)
        with pytest.raises(ValueError):
            stage.fn(ctx, [])
        assert ctx["verification_failures"] == ["gone.json"]

    def test_logs_success(self, tmp_path: Path) -> None:
        pf = _planned_file()
        self._write(tmp_path, pf)
        ctx: dict[str, Any] = {"planned_files": [pf]}
        logs: list[str] = []
        stage = _make_verify_stage(tmp_path)
        stage.fn(ctx, logs)
        assert any("verified" in line for line in logs)


# ---------------------------------------------------------------------------
# run_publish — integration over tmp_path
# ---------------------------------------------------------------------------


class TestRunPublish:
    def test_happy_path(self, tmp_path: Path) -> None:
        pf = _planned_file("members/alice.json", b'{"name":"alice"}')
        cfg = PublishConfig(snapshot_id="2026-04-14", target_dir=tmp_path)
        result = run_publish(cfg, _planner(pf))
        assert result.succeeded
        assert result.snapshot_id == "2026-04-14"
        assert result.planned_count == 1
        assert result.written_count == 1
        assert result.verification_failures == []

    def test_written_file_matches_content(self, tmp_path: Path) -> None:
        content = b'{"member":"B001"}'
        pf = _planned_file("members/b001.json", content)
        cfg = PublishConfig(snapshot_id="snap", target_dir=tmp_path)
        run_publish(cfg, _planner(pf))
        assert (tmp_path / "members" / "b001.json").read_bytes() == content

    def test_multiple_files(self, tmp_path: Path) -> None:
        files = [
            _planned_file("members/a.json", b'{"m":"a"}'),
            _planned_file("zip/12345.json", b'{"z":"12345"}'),
            _planned_file("evidence/e1.json", b'{"e":"1"}'),
        ]
        cfg = PublishConfig(snapshot_id="snap", target_dir=tmp_path)
        result = run_publish(cfg, _planner(*files))
        assert result.succeeded
        assert result.planned_count == 3
        assert result.written_count == 3

    def test_empty_snapshot_succeeds(self, tmp_path: Path) -> None:
        cfg = PublishConfig(snapshot_id="snap", target_dir=tmp_path)
        result = run_publish(cfg, _empty_planner())
        assert result.succeeded
        assert result.planned_count == 0
        assert result.written_count == 0

    def test_planner_failure_fails_pipeline(self, tmp_path: Path) -> None:
        def bad() -> list[PlannedFile]:
            raise RuntimeError("planner failure")

        cfg = PublishConfig(snapshot_id="snap", target_dir=tmp_path)
        result = run_publish(cfg, bad)
        assert not result.succeeded
        assert result.pipeline_result.failed
        assert result.planned_count == 0
        assert result.written_count == 0

    def test_planner_failure_skips_write_and_verify(self, tmp_path: Path) -> None:
        def bad() -> list[PlannedFile]:
            raise RuntimeError("broken planner")

        cfg = PublishConfig(snapshot_id="snap", target_dir=tmp_path)
        result = run_publish(cfg, bad)
        stage_statuses = {r.stage_name: r.status for r in result.pipeline_result.stage_results}
        assert stage_statuses["plan"] == "failed"
        assert stage_statuses["write"] == "skipped"
        assert stage_statuses["verify"] == "skipped"

    def test_stop_on_failure_false_continues_after_plan_error(self, tmp_path: Path) -> None:
        def bad() -> list[PlannedFile]:
            raise RuntimeError("broken")

        cfg = PublishConfig(snapshot_id="snap", target_dir=tmp_path, stop_on_failure=False)
        result = run_publish(cfg, bad)
        # write and verify run but fail (KeyError on missing context key)
        stage_statuses = {r.stage_name: r.status for r in result.pipeline_result.stage_results}
        assert stage_statuses["plan"] == "failed"
        # write/verify also failed since context["planned_files"] is absent
        assert stage_statuses["write"] == "failed"
        assert stage_statuses["verify"] == "failed"

    def test_emitter_receives_events(self, tmp_path: Path) -> None:
        events: list[tuple] = []

        class Cap:
            def on_stage_start(self, name: str) -> None:
                events.append(("start", name))

            def on_stage_end(self, result) -> None:
                events.append(("end", result.stage_name, result.status))

        pf = _planned_file()
        cfg = PublishConfig(snapshot_id="snap", target_dir=tmp_path)
        run_publish(cfg, _planner(pf), emitter=Cap())

        stage_names = [e[1] for e in events if e[0] == "start"]
        assert stage_names == ["plan", "write", "verify"]
        assert all(e[2] == "succeeded" for e in events if e[0] == "end")

    def test_no_emitter_runs_fine(self, tmp_path: Path) -> None:
        pf = _planned_file()
        cfg = PublishConfig(snapshot_id="snap", target_dir=tmp_path)
        result = run_publish(cfg, _planner(pf), emitter=None)
        assert result.succeeded

    def test_pipeline_result_included(self, tmp_path: Path) -> None:
        pf = _planned_file()
        cfg = PublishConfig(snapshot_id="snap", target_dir=tmp_path)
        result = run_publish(cfg, _planner(pf))
        assert len(result.pipeline_result.stage_results) == 3
        assert all(r.succeeded for r in result.pipeline_result.stage_results)

    def test_snapshot_id_propagated(self, tmp_path: Path) -> None:
        cfg = PublishConfig(snapshot_id="my-snapshot-id", target_dir=tmp_path)
        result = run_publish(cfg, _empty_planner())
        assert result.snapshot_id == "my-snapshot-id"
