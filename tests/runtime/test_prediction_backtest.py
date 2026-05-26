"""Unit tests for runtime prediction-backtest verify helpers.

These pin invariants that are otherwise only exercised indirectly through the
CLI dispatch tests. The most important one is that the runtime verify command
computes prediction event keys the *same* way the contract validator does:
if the two ever drift, the verify gate would raise spurious "event_key
mismatch" errors on artifacts the contract itself accepts.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.prediction.backtest import _event_key
from src.runtime.prediction_backtest import _expected_prediction_event_key


def _contract_event_key(prediction: SimpleNamespace) -> str:
    """Mirror exactly how the contract validator derives the expected key."""
    return _event_key(
        jurisdiction_id=prediction.jurisdiction_id,
        legislative_body_id=str(
            prediction.legislative_body_id or f"us_congress_{prediction.chamber}"
        ),
        legislative_session_id=prediction.legislative_session_id,
        chamber=prediction.chamber,
        congress=prediction.congress,
        session_number=prediction.session_number,
        roll_call_number=prediction.roll_call_number,
    )


@pytest.mark.parametrize(
    "prediction",
    [
        # Congress, fully populated.
        SimpleNamespace(
            jurisdiction_id="us_congress",
            legislative_body_id="us_congress_house",
            legislative_session_id="congress_119_session_1",
            chamber="house",
            congress=119,
            session_number=1,
            roll_call_number=123,
        ),
        # Congress with a None legislative_body_id (schema-valid: the contract
        # only requires the field for non-Congress predictions).
        SimpleNamespace(
            jurisdiction_id="us_congress",
            legislative_body_id=None,
            legislative_session_id=None,
            chamber="senate",
            congress=118,
            session_number=2,
            roll_call_number=7,
        ),
        # Portable non-Congress jurisdiction with an explicit body id (the only
        # state the contract allows for non-Congress).
        SimpleNamespace(
            jurisdiction_id="us_state_ca",
            legislative_body_id="us_state_ca_assembly",
            legislative_session_id="ca_2025_regular",
            chamber="assembly",
            congress=0,
            session_number=0,
            roll_call_number=42,
        ),
        # Non-Congress with a missing session id, exercising the session fallback.
        SimpleNamespace(
            jurisdiction_id="us_state_ny",
            legislative_body_id="us_state_ny_senate",
            legislative_session_id=None,
            chamber="senate",
            congress=5,
            session_number=3,
            roll_call_number=9,
        ),
    ],
)
def test_runtime_event_key_matches_contract(prediction: SimpleNamespace) -> None:
    assert _expected_prediction_event_key(prediction) == _contract_event_key(prediction)
