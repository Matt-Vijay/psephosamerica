from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from src.graph.provenance import ProvenanceEnvelope

_SHA = "a" * 64


def _envelope(**overrides: object) -> ProvenanceEnvelope:
    base: dict[str, object] = {
        "source_url": "https://www.congress.gov/bill/118/hr/1",
        "content_sha256": _SHA,
        "first_observed_at": datetime(2024, 1, 10, 12, 0, tzinfo=UTC),
        "valid_from": date(2024, 1, 1),
        "valid_to": None,
        "known_at": datetime(2024, 1, 5, 0, 0, tzinfo=UTC),
    }
    base.update(overrides)
    return ProvenanceEnvelope(**base)  # type: ignore[arg-type]


# ── Happy path ─────────────────────────────────────────────────────


def test_minimal_open_interval_envelope_is_valid() -> None:
    env = _envelope()
    assert env.is_open() is True
    assert env.valid_to is None


def test_closed_interval_envelope_is_valid() -> None:
    env = _envelope(valid_to=date(2024, 6, 1))
    assert env.is_open() is False
    assert env.valid_to == date(2024, 6, 1)


def test_model_is_frozen() -> None:
    env = _envelope()
    with pytest.raises(ValidationError):
        env.source_url = "https://example.com"  # type: ignore[misc]


# ── content_sha256 validation ──────────────────────────────────────


def test_sha256_is_lowercased_and_stripped() -> None:
    env = _envelope(content_sha256="  " + ("A" * 64) + "  ")
    assert env.content_sha256 == "a" * 64


@pytest.mark.parametrize(
    "bad",
    ["", "abc", "a" * 63, "a" * 65, "g" * 64, "z" * 64],
)
def test_sha256_must_be_64_hex_chars(bad: str) -> None:
    with pytest.raises(ValidationError, match="content_sha256"):
        _envelope(content_sha256=bad)


def test_non_string_sha_passes_through_to_type_check() -> None:
    # The before-validator only normalizes strings; non-strings fall through
    # to pydantic's type validation.
    with pytest.raises(ValidationError, match="content_sha256"):
        _envelope(content_sha256=12345)


# ── source_url validation ──────────────────────────────────────────


def test_source_url_is_stripped() -> None:
    env = _envelope(source_url="  https://example.gov/x  ")
    assert env.source_url == "https://example.gov/x"


def test_non_string_source_url_passes_through_to_type_check() -> None:
    with pytest.raises(ValidationError, match="source_url"):
        _envelope(source_url=42)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "ftp://example.com/x",
        "file:///etc/passwd",
        "example.com/x",
        "javascript:alert(1)",
    ],
)
def test_source_url_must_be_http_s(bad: str) -> None:
    with pytest.raises(ValidationError, match="source_url"):
        _envelope(source_url=bad)


# ── timezone / known_at leakage invariants ─────────────────────────


def test_naive_first_observed_at_is_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        _envelope(first_observed_at=datetime(2024, 1, 10, 12, 0))


def test_naive_known_at_is_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        _envelope(known_at=datetime(2024, 1, 5, 0, 0))


def test_non_utc_timestamps_are_normalized_to_utc() -> None:
    eastern = timezone(timedelta(hours=-5))
    env = _envelope(
        known_at=datetime(2024, 1, 5, 7, 0, tzinfo=eastern),
        first_observed_at=datetime(2024, 1, 10, 7, 0, tzinfo=eastern),
    )
    assert env.known_at == datetime(2024, 1, 5, 12, 0, tzinfo=UTC)
    assert env.first_observed_at == datetime(2024, 1, 10, 12, 0, tzinfo=UTC)
    assert env.known_at.tzinfo is UTC


def test_known_at_after_first_observed_at_is_rejected() -> None:
    # You cannot observe a fact before it was knowable.
    with pytest.raises(ValidationError, match="known_at"):
        _envelope(
            known_at=datetime(2024, 1, 11, 0, 0, tzinfo=UTC),
            first_observed_at=datetime(2024, 1, 10, 12, 0, tzinfo=UTC),
        )


def test_known_at_equal_to_first_observed_at_is_allowed() -> None:
    stamp = datetime(2024, 1, 10, 12, 0, tzinfo=UTC)
    env = _envelope(known_at=stamp, first_observed_at=stamp)
    assert env.known_at == env.first_observed_at


# ── valid interval invariants ──────────────────────────────────────


def test_valid_to_before_valid_from_is_rejected() -> None:
    with pytest.raises(ValidationError, match="valid_to"):
        _envelope(valid_from=date(2024, 6, 1), valid_to=date(2024, 1, 1))


def test_valid_to_equal_to_valid_from_is_rejected() -> None:
    # A half-open [from, to) interval of zero width covers nothing — reject it.
    with pytest.raises(ValidationError, match="valid_to"):
        _envelope(valid_from=date(2024, 6, 1), valid_to=date(2024, 6, 1))


# ── covers() — bitemporal valid-time query ─────────────────────────


def test_covers_open_interval_includes_everything_after_valid_from() -> None:
    env = _envelope(valid_from=date(2024, 1, 1), valid_to=None)
    assert env.covers(date(2024, 1, 1)) is True
    assert env.covers(date(2030, 1, 1)) is True
    assert env.covers(date(2023, 12, 31)) is False


def test_covers_closed_interval_is_half_open() -> None:
    env = _envelope(valid_from=date(2024, 1, 1), valid_to=date(2024, 6, 1))
    assert env.covers(date(2024, 1, 1)) is True
    assert env.covers(date(2024, 5, 31)) is True
    assert env.covers(date(2024, 6, 1)) is False  # exclusive upper bound
    assert env.covers(date(2023, 12, 31)) is False


# ── known_as_of() — the strict-cutoff leakage gate ─────────────────


def test_known_as_of_is_inclusive_lower_bound() -> None:
    env = _envelope(known_at=datetime(2024, 1, 5, 0, 0, tzinfo=UTC))
    assert env.known_as_of(datetime(2024, 1, 5, 0, 0, tzinfo=UTC)) is True
    assert env.known_as_of(datetime(2024, 1, 6, 0, 0, tzinfo=UTC)) is True
    assert env.known_as_of(datetime(2024, 1, 4, 23, 59, tzinfo=UTC)) is False


def test_known_as_of_rejects_naive_cutoff() -> None:
    env = _envelope()
    with pytest.raises(ValueError, match="timezone-aware"):
        env.known_as_of(datetime(2024, 1, 6, 0, 0))


def test_known_as_of_compares_across_timezones() -> None:
    env = _envelope(known_at=datetime(2024, 1, 5, 12, 0, tzinfo=UTC))
    eastern = timezone(timedelta(hours=-5))
    # 2024-01-05 07:00 EST == 2024-01-05 12:00 UTC == known_at exactly.
    assert env.known_as_of(datetime(2024, 1, 5, 7, 0, tzinfo=eastern)) is True
    assert env.known_as_of(datetime(2024, 1, 5, 6, 59, tzinfo=eastern)) is False


# ── content_address() — content-addressed lake path ────────────────


def test_content_address_is_sharded_by_sha_prefix() -> None:
    env = _envelope(content_sha256="ab12" + "c" * 60)
    assert env.content_address() == f"sha256/ab/12/ab12{'c' * 60}"


def test_content_address_round_trips_full_digest() -> None:
    env = _envelope()
    assert env.content_address().endswith(_SHA)
