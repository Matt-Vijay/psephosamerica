"""LLM forecaster ensemble member (Anthropic), via the Forecaster Protocol.

OVERALL_GOAL.md's independent ensemble member: a frontier LLM prompted with the
politician dossier, the bill, and retrieved comparable votes, returning a yea
probability + structured reasoning, stacked with the model on a held-out window.

This implements that as a ``Forecaster`` (``forecast(person, bill) -> float``)
with two injected boundaries kept out of the model: a ``context_provider`` that
assembles the prompt context for a (person, bill) pair, and a ``complete``
callable that runs the LLM (the real one POSTs to the Anthropic Messages API via
httpx when ``ANTHROPIC_API_KEY`` is set -- no SDK dependency added). The
probability is parsed from the model's structured reply. With no key the path is
fully exercised via an injected stub completer, and the forecaster drops into
``ensemble_forecaster.StackedForecaster`` unchanged.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass

ContextProvider = Callable[[str, str], str]
Completer = Callable[[str], str]

_PROMPT_TEMPLATE = (
    "You are a legislative vote forecaster. Given the context below, return ONLY a "
    'JSON object {{"probability_yea": <0..1>, "reasoning": "<one sentence>"}}.\n\n'
    "Context for ({person}, {bill}):\n{context}\n"
)


@dataclass(frozen=True)
class LlmForecastResult:
    probability_yea: float
    reasoning: str


def build_prompt(person: str, bill: str, context: str) -> str:
    """Assemble the forecaster prompt for a (person, bill) pair."""
    return _PROMPT_TEMPLATE.format(person=person, bill=bill, context=context)


def parse_forecast(response_text: str) -> LlmForecastResult:
    """Parse the LLM reply into a probability + reasoning (robust to extra prose)."""
    match = re.search(r"\{.*\}", response_text, re.DOTALL)
    if match:
        try:
            payload = json.loads(match.group(0))
            probability = float(payload["probability_yea"])
            reasoning = str(payload.get("reasoning", ""))
            return LlmForecastResult(
                probability_yea=min(1.0, max(0.0, probability)), reasoning=reasoning
            )
        except (ValueError, KeyError, TypeError):
            pass
    number = re.search(r"0?\.\d+|[01]\.0+|\b[01]\b", response_text)
    if number is None:
        raise ValueError("could not parse a probability from the LLM response")
    return LlmForecastResult(
        probability_yea=min(1.0, max(0.0, float(number.group(0)))),
        reasoning=response_text.strip()[:200],
    )


@dataclass(frozen=True)
class AnthropicForecaster:
    """An LLM ensemble member satisfying the Forecaster Protocol."""

    context_provider: ContextProvider
    complete: Completer

    def forecast(self, *, canonical_person_id: str, canonical_bill_id: str) -> float:
        context = self.context_provider(canonical_person_id, canonical_bill_id)
        prompt = build_prompt(canonical_person_id, canonical_bill_id, context)
        return parse_forecast(self.complete(prompt)).probability_yea


def anthropic_api_completer(
    *,
    model: str = "claude-opus-4-8",
    max_tokens: int = 256,
) -> Completer:
    """A real completer that POSTs to the Anthropic Messages API (requires a key).

    Uses httpx (already a dependency); raises if ``ANTHROPIC_API_KEY`` is unset.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set; inject a stub completer instead")

    import httpx

    def complete(prompt: str) -> str:
        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60.0,
        )
        response.raise_for_status()
        blocks = response.json().get("content", [])
        return "".join(block.get("text", "") for block in blocks)

    return complete


def llm_forecaster_available() -> bool:
    """Whether a real LLM forecaster can run (an Anthropic key is present)."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))
