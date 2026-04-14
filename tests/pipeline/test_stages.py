"""Tests for src/pipeline/stages.py.

Pure function tests only — no DB, no network, no I/O.
"""

from __future__ import annotations

import pytest

from src.pipeline import (
    NullEmitter,
    PipelineResult,
    ProvenanceEmitter,
    StageDefinition,
    StageResult,
    run_pipeline,
    run_stage,
)


# ---------------------------------------------------------------------------
# Shared stage functions
# ---------------------------------------------------------------------------

def _ok_fn(context: dict, logs: list) -> dict:
    logs.append("ok ran")
    return {"status": "ok"}


def _fail_fn(context: dict, logs: list) -> None:
    raise ValueError("intentional failure")


def _trace_fn(context: dict, logs: list) -> list:
    context.setdefault("trace", []).append("trace_fn")
    return context["trace"]


def _ctx_reader_fn(context: dict, logs: list) -> str:
    return context.get("key", "missing")


# ---------------------------------------------------------------------------
# StageDefinition
# ---------------------------------------------------------------------------

class TestStageDefinition:
    def test_is_frozen(self):
        sd = StageDefinition(name="s", fn=_ok_fn)
        with pytest.raises((AttributeError, TypeError)):
            sd.name = "changed"  # type: ignore[misc]

    def test_default_description(self):
        sd = StageDefinition(name="s", fn=_ok_fn)
        assert sd.description == ""

    def test_explicit_description(self):
        sd = StageDefinition(name="s", fn=_ok_fn, description="my stage")
        assert sd.description == "my stage"


# ---------------------------------------------------------------------------
# StageResult properties and summary
# ---------------------------------------------------------------------------

class TestStageResult:
    def test_succeeded_flags(self):
        r = StageResult(stage_name="s", status="succeeded")
        assert r.succeeded is True
        assert r.failed is False

    def test_failed_flags(self):
        r = StageResult(stage_name="s", status="failed")
        assert r.succeeded is False
        assert r.failed is True

    def test_skipped_flags(self):
        r = StageResult(stage_name="s", status="skipped")
        assert r.succeeded is False
        assert r.failed is False

    def test_summary_keys_present(self):
        r = StageResult(stage_name="s", status="succeeded", duration_ms=5.5)
        s = r.summary()
        assert s["stage"] == "s"
        assert s["status"] == "succeeded"
        assert s["error"] is None
        assert s["duration_ms"] == pytest.approx(5.5)
        assert "log_count" in s

    def test_summary_error_stringified(self):
        exc = RuntimeError("boom")
        r = StageResult(stage_name="s", status="failed", error=exc)
        assert "boom" in r.summary()["error"]

    def test_logs_default_empty(self):
        r = StageResult(stage_name="s", status="succeeded")
        assert r.logs == []


# ---------------------------------------------------------------------------
# run_stage
# ---------------------------------------------------------------------------

class TestRunStage:
    def test_success_returns_output(self):
        sd = StageDefinition(name="ok", fn=_ok_fn)
        result = run_stage(sd, context={})
        assert result.succeeded
        assert result.output == {"status": "ok"}
        assert result.error is None

    def test_fn_logs_are_captured(self):
        sd = StageDefinition(name="ok", fn=_ok_fn)
        result = run_stage(sd, context={})
        assert "ok ran" in result.logs

    def test_failure_is_captured_not_raised(self):
        sd = StageDefinition(name="fail", fn=_fail_fn)
        result = run_stage(sd, context={})  # must not raise
        assert result.failed
        assert isinstance(result.error, ValueError)

    def test_failure_appends_error_to_logs(self):
        sd = StageDefinition(name="fail", fn=_fail_fn)
        result = run_stage(sd, context={})
        assert any("intentional failure" in line for line in result.logs)

    def test_duration_non_negative(self):
        sd = StageDefinition(name="ok", fn=_ok_fn)
        result = run_stage(sd, context={})
        assert result.duration_ms >= 0.0

    def test_context_passed_through(self):
        sd = StageDefinition(name="r", fn=_ctx_reader_fn)
        result = run_stage(sd, context={"key": "hello"})
        assert result.output == "hello"

    def test_context_mutated_by_fn(self):
        sd = StageDefinition(name="t", fn=_trace_fn)
        ctx: dict = {}
        run_stage(sd, context=ctx)
        assert ctx["trace"] == ["trace_fn"]

    def test_emitter_receives_start_and_end(self):
        events: list[tuple] = []

        class Cap:
            def on_stage_start(self, name: str) -> None:
                events.append(("start", name))

            def on_stage_end(self, result: StageResult) -> None:
                events.append(("end", result.stage_name, result.status))

        sd = StageDefinition(name="ok", fn=_ok_fn)
        run_stage(sd, context={}, emitter=Cap())
        assert events[0] == ("start", "ok")
        assert events[1] == ("end", "ok", "succeeded")

    def test_emitter_end_called_even_on_failure(self):
        events: list[str] = []

        class Cap:
            def on_stage_start(self, name: str) -> None:
                events.append("start")

            def on_stage_end(self, result: StageResult) -> None:
                events.append("end")

        sd = StageDefinition(name="fail", fn=_fail_fn)
        run_stage(sd, context={}, emitter=Cap())
        assert events == ["start", "end"]

    def test_no_emitter_runs_fine(self):
        sd = StageDefinition(name="ok", fn=_ok_fn)
        result = run_stage(sd, context={}, emitter=None)
        assert result.succeeded


# ---------------------------------------------------------------------------
# run_pipeline
# ---------------------------------------------------------------------------

class TestRunPipeline:
    def _stages(self, pairs: list[tuple]) -> list[StageDefinition]:
        return [StageDefinition(name=n, fn=f) for n, f in pairs]

    def test_all_succeed(self):
        stages = self._stages([("a", _ok_fn), ("b", _ok_fn)])
        pr = run_pipeline(stages, context={})
        assert pr.succeeded
        assert len(pr.stage_results) == 2
        assert all(r.succeeded for r in pr.stage_results)

    def test_stop_on_failure_default(self):
        stages = self._stages([("a", _ok_fn), ("b", _fail_fn), ("c", _ok_fn)])
        pr = run_pipeline(stages, context={})
        assert pr.failed
        statuses = [r.status for r in pr.stage_results]
        assert statuses == ["succeeded", "failed", "skipped"]

    def test_continue_on_failure(self):
        stages = self._stages([("a", _fail_fn), ("b", _ok_fn)])
        pr = run_pipeline(stages, context={}, stop_on_failure=False)
        statuses = [r.status for r in pr.stage_results]
        assert statuses == ["failed", "succeeded"]

    def test_empty_pipeline_succeeds(self):
        pr = run_pipeline([], context={})
        assert pr.succeeded
        assert pr.stage_results == []

    def test_single_failure_marks_pipeline_failed(self):
        stages = self._stages([("x", _fail_fn)])
        pr = run_pipeline(stages, context={})
        assert pr.failed

    def test_summary_counts(self):
        stages = self._stages([("a", _ok_fn), ("b", _fail_fn), ("c", _ok_fn)])
        pr = run_pipeline(stages, context={}, stop_on_failure=True)
        s = pr.summary()
        assert s["status"] == "failed"
        assert s["succeeded"] == 1
        assert s["failed"] == 1
        assert s["skipped"] == 1
        assert s["stage_count"] == 3
        assert len(s["stages"]) == 3

    def test_summary_all_success(self):
        stages = self._stages([("a", _ok_fn), ("b", _ok_fn)])
        pr = run_pipeline(stages, context={})
        s = pr.summary()
        assert s["status"] == "succeeded"
        assert s["failed"] == 0
        assert s["skipped"] == 0

    def test_logs_for_named_stage(self):
        stages = self._stages([("ok", _ok_fn)])
        pr = run_pipeline(stages, context={})
        assert "ok ran" in pr.logs_for("ok")

    def test_logs_for_missing_stage_returns_empty(self):
        pr = run_pipeline([], context={})
        assert pr.logs_for("nonexistent") == []

    def test_emitter_not_called_for_skipped(self):
        started: list[str] = []

        class Cap:
            def on_stage_start(self, name: str) -> None:
                started.append(name)

            def on_stage_end(self, result: StageResult) -> None:
                pass

        stages = self._stages([("a", _ok_fn), ("b", _fail_fn), ("c", _ok_fn)])
        run_pipeline(stages, context={}, emitter=Cap(), stop_on_failure=True)
        assert "c" not in started

    def test_context_shared_across_stages(self):
        def writer(ctx: dict, logs: list) -> None:
            ctx["written"] = True

        def reader(ctx: dict, logs: list) -> bool:
            return ctx.get("written", False)

        stages = [
            StageDefinition(name="w", fn=writer),
            StageDefinition(name="r", fn=reader),
        ]
        ctx: dict = {}
        pr = run_pipeline(stages, context=ctx)
        assert pr.succeeded
        assert pr.stage_results[1].output is True

    def test_pipeline_result_status_aggregation(self):
        pr = PipelineResult()
        assert pr.succeeded  # empty → succeeded
        pr.stage_results.append(StageResult(stage_name="x", status="failed"))
        assert pr.failed


# ---------------------------------------------------------------------------
# ProvenanceEmitter protocol
# ---------------------------------------------------------------------------

class TestProvenanceEmitterProtocol:
    def test_null_emitter_satisfies_protocol(self):
        assert isinstance(NullEmitter(), ProvenanceEmitter)

    def test_custom_class_satisfies_protocol(self):
        class MyEmitter:
            def on_stage_start(self, stage_name: str) -> None:
                pass

            def on_stage_end(self, result: StageResult) -> None:
                pass

        assert isinstance(MyEmitter(), ProvenanceEmitter)

    def test_incomplete_class_fails_protocol(self):
        class Partial:
            def on_stage_start(self, stage_name: str) -> None:
                pass
            # missing on_stage_end

        assert not isinstance(Partial(), ProvenanceEmitter)
