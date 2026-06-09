"""Tests for the LLM forecaster path (stub completer; no API key needed)."""

from __future__ import annotations

import pytest

from src.prediction.anthropic_forecaster import (
    AnthropicForecaster,
    build_prompt,
    llm_forecaster_available,
    parse_forecast,
)
from src.prediction.ensemble_forecaster import (
    HeldoutVote,
    NamedForecaster,
    fit_stacked_forecaster,
)


def test_parse_forecast_json() -> None:
    result = parse_forecast('{"probability_yea": 0.82, "reasoning": "party + sector"}')
    assert result.probability_yea == 0.82
    assert "party" in result.reasoning


def test_parse_forecast_robust_to_prose() -> None:
    result = parse_forecast("I think the probability is 0.31 because ...")
    assert abs(result.probability_yea - 0.31) < 1e-9


def test_parse_forecast_clamps_and_rejects_garbage() -> None:
    assert parse_forecast('{"probability_yea": 1.5}').probability_yea == 1.0
    with pytest.raises(ValueError):
        parse_forecast("no number here")


def test_forecaster_satisfies_protocol_and_uses_context() -> None:
    seen: dict[str, str] = {}

    def context_provider(person: str, bill: str) -> str:
        seen["ctx"] = f"{person}/{bill}"
        return "dossier: votes with party; bill: energy permitting"

    def complete(prompt: str) -> str:
        assert "forecaster" in prompt and "energy permitting" in prompt
        return '{"probability_yea": 0.7, "reasoning": "energy-aligned"}'

    forecaster = AnthropicForecaster(context_provider=context_provider, complete=complete)
    prob = forecaster.forecast(canonical_person_id="bioguide:A000001", canonical_bill_id="bill:1")
    assert prob == 0.7
    assert seen["ctx"] == "bioguide:A000001/bill:1"


def test_llm_forecaster_integrates_into_stacked_ensemble() -> None:
    # The LLM member stacks with a model member through the held-out window.
    def ctx(_p: str, _b: str) -> str:
        return "ctx"

    def complete(_prompt: str) -> str:
        return '{"probability_yea": 0.6, "reasoning": "x"}'

    llm = AnthropicForecaster(context_provider=ctx, complete=complete)

    class _Model:
        def forecast(self, *, canonical_person_id: str, canonical_bill_id: str) -> float:
            return 0.55

    members = [
        NamedForecaster(name="transformer", forecaster=_Model()),
        NamedForecaster(name="llm", forecaster=llm),
    ]
    holdout = [HeldoutVote(f"p{i}", f"b{i}", i % 2 == 0) for i in range(8)]
    stacked = fit_stacked_forecaster(members, holdout, epochs=20, learning_rate=0.1)
    assert set(stacked.stack.weights) == {"transformer", "llm"}
    prob = stacked.forecast(canonical_person_id="p0", canonical_bill_id="b0")
    assert 0.0 <= prob <= 1.0


def test_build_prompt_mentions_pair() -> None:
    prompt = build_prompt("bioguide:A000001", "bill:hr1", "context here")
    assert "bioguide:A000001" in prompt and "bill:hr1" in prompt


def test_availability_reflects_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm_forecaster_available() is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert llm_forecaster_available() is True
