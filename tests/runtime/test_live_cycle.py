from __future__ import annotations

from typing import Any

from src.runtime.live_cycle import STAGES, TickReport, run_loop, run_tick


def test_run_tick_collects_stage_results() -> None:
    report = run_tick({"a": lambda: {"n": 1}, "b": lambda: {"m": 2}})
    assert report.ok
    assert report.results == {"a": {"n": 1}, "b": {"m": 2}}


def test_run_tick_contains_stage_failure() -> None:
    def boom() -> dict[str, Any]:
        raise RuntimeError("upstream down")

    report = run_tick({"bad": boom, "good": lambda: {"n": 1}})
    assert not report.ok
    assert report.errors == {"bad": "RuntimeError: upstream down"}
    assert report.results == {"good": {"n": 1}}  # the other stage still ran


def test_run_loop_paces_and_emits() -> None:
    delays: list[float] = []
    lines: list[str] = []
    counter = {"n": 0}

    def stage() -> dict[str, Any]:
        counter["n"] += 1
        return {"tick": counter["n"]}

    reports = run_loop(
        ticks=3,
        interval_seconds=900.0,
        stages={"s": stage},
        sleep=delays.append,
        emit=lines.append,
    )
    assert len(reports) == 3 and all(r.ok for r in reports)
    assert delays == [900.0, 900.0]  # paced between ticks, not before the first
    assert lines[0].startswith("tick 1/3") and "'tick': 1" in lines[0]


def test_run_loop_reports_errors_in_summary() -> None:
    lines: list[str] = []

    def boom() -> dict[str, Any]:
        raise ValueError("x")

    run_loop(ticks=1, interval_seconds=0, stages={"bad": boom}, emit=lines.append)
    assert "ERRORS" in lines[0] and "bad: ValueError: x" in lines[0]


def test_default_stages_registered() -> None:
    assert set(STAGES) == {"senate", "markets"}
    assert TickReport().ok  # empty report is ok


def test_real_stages_delegate_to_runners(monkeypatch) -> None:
    import src.runtime.live_cycle as mod

    class _Progress:
        votes_new = 3

    class _EdgeReport:
        edges_written = 7

    class _MarketReport:
        markets_total = 10
        markets_linked = 2
        prices_written = 5
        deltas_written = 1

    calls: dict[str, dict] = {}

    def fake_backfill(**kwargs) -> _Progress:
        calls["backfill"] = kwargs
        return _Progress()

    def fake_export(**kwargs) -> _EdgeReport:
        calls["export"] = kwargs
        return _EdgeReport()

    def fake_snapshot(**kwargs) -> _MarketReport:
        calls["snapshot"] = kwargs
        return _MarketReport()

    monkeypatch.setattr(mod, "backfill_senate_votes", fake_backfill)
    monkeypatch.setattr(mod, "export_senate_vote_edges", fake_export)
    monkeypatch.setattr(mod, "snapshot_markets", fake_snapshot)

    report = run_tick()
    assert report.ok
    assert report.results["senate"] == {"new_rollcalls": 3, "new_edges": 7}
    assert report.results["markets"] == {"markets": 10, "linked": 2, "prices_new": 5, "deltas": 1}
    assert calls["backfill"]["congresses"] == [119]
    assert calls["snapshot"]["include_closed"] is False
