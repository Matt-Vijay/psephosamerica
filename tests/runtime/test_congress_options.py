"""Tests for src/runtime/congress_options.py."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from src.runtime.congress_options import CongressLoadOptions


class TestCongressLoadOptionsDefaults:
    def test_include_votes_defaults_false(self) -> None:
        opts = CongressLoadOptions(congress=119)
        assert opts.include_votes is False

    def test_house_vote_year_defaults_none(self) -> None:
        opts = CongressLoadOptions(congress=119)
        assert opts.house_vote_year is None

    def test_senate_session_defaults_none(self) -> None:
        opts = CongressLoadOptions(congress=119)
        assert opts.senate_session is None


class TestCongressLoadOptionsExplicitValues:
    def test_congress_stored(self) -> None:
        opts = CongressLoadOptions(congress=118)
        assert opts.congress == 118

    def test_include_votes_stored(self) -> None:
        opts = CongressLoadOptions(congress=119, include_votes=True)
        assert opts.include_votes is True

    def test_house_vote_year_stored(self) -> None:
        opts = CongressLoadOptions(congress=119, house_vote_year=2025)
        assert opts.house_vote_year == 2025

    def test_senate_session_stored(self) -> None:
        opts = CongressLoadOptions(congress=119, senate_session=1)
        assert opts.senate_session == 1

    def test_all_fields_together(self) -> None:
        opts = CongressLoadOptions(
            congress=119,
            include_votes=True,
            house_vote_year=2025,
            senate_session=2,
        )
        assert opts.congress == 119
        assert opts.include_votes is True
        assert opts.house_vote_year == 2025
        assert opts.senate_session == 2


class TestCongressLoadOptionsImmutability:
    def test_frozen_congress(self) -> None:
        opts = CongressLoadOptions(congress=119)
        with pytest.raises(FrozenInstanceError):
            opts.congress = 120  # type: ignore[misc]

    def test_frozen_include_votes(self) -> None:
        opts = CongressLoadOptions(congress=119)
        with pytest.raises(FrozenInstanceError):
            opts.include_votes = True  # type: ignore[misc]

    def test_frozen_house_vote_year(self) -> None:
        opts = CongressLoadOptions(congress=119, house_vote_year=2025)
        with pytest.raises(FrozenInstanceError):
            opts.house_vote_year = 2026  # type: ignore[misc]

    def test_frozen_senate_session(self) -> None:
        opts = CongressLoadOptions(congress=119, senate_session=1)
        with pytest.raises(FrozenInstanceError):
            opts.senate_session = 2  # type: ignore[misc]


class TestCongressLoadOptionsEquality:
    def test_equal_when_fields_match(self) -> None:
        a = CongressLoadOptions(congress=119, include_votes=True, house_vote_year=2025)
        b = CongressLoadOptions(congress=119, include_votes=True, house_vote_year=2025)
        assert a == b

    def test_not_equal_on_congress_diff(self) -> None:
        a = CongressLoadOptions(congress=118)
        b = CongressLoadOptions(congress=119)
        assert a != b

    def test_not_equal_on_include_votes_diff(self) -> None:
        a = CongressLoadOptions(congress=119, include_votes=False)
        b = CongressLoadOptions(congress=119, include_votes=True)
        assert a != b
