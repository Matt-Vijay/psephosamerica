from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from src.parse.disclosures.models import Chamber, Filing, FilingType
from src.parse.disclosures.parse_result import ParseResult, ParserMeta
from src.parse.disclosures.parser_dispatch import parse_disclosure_pages


def _filing(*, chamber: Chamber, filing_type: FilingType) -> Filing:
    return Filing(
        member_bioguide_id="A000001",
        chamber=chamber,
        filing_year=2024,
        filing_type=filing_type,
        filed_at=date(2024, 5, 1),
        source_record_id="DOC1",
    )


def _result(name: str) -> ParseResult:
    return ParseResult(
        filing=_filing(chamber=Chamber.HOUSE, filing_type=FilingType.ANNUAL),
        holdings=(),
        transactions=(),
        outside_positions=(),
        meta=ParserMeta(parser_name=name),
    )


class TestDispatchRoutes:
    def test_house_ptr_calls_house_ptr_parser(self) -> None:
        pages = ["ptr page"]
        filing = _filing(chamber=Chamber.HOUSE, filing_type=FilingType.PTR)
        result = _result("house_ptr")

        with patch(
            "src.parse.disclosures.parser_dispatch.house_ptr_text.parse_house_ptr",
            return_value=result,
        ) as mock_parse:
            parsed = parse_disclosure_pages(pages, filing)

        mock_parse.assert_called_once_with(pages, member_bioguide_id="A000001")
        assert parsed is result

    def test_house_annual_calls_house_annual_parser(self) -> None:
        pages = ["annual page"]
        filing = _filing(chamber=Chamber.HOUSE, filing_type=FilingType.ANNUAL)
        result = _result("house_annual")

        with patch(
            "src.parse.disclosures.parser_dispatch.house_annual_text.parse_house_annual",
            return_value=result,
        ) as mock_parse:
            parsed = parse_disclosure_pages(pages, filing)

        mock_parse.assert_called_once_with(pages, filing)
        assert parsed is result

    def test_senate_ptr_calls_senate_parser(self) -> None:
        pages = ["senate ptr"]
        filing = _filing(chamber=Chamber.SENATE, filing_type=FilingType.PTR)
        result = _result("senate_ptr")

        with patch(
            "src.parse.disclosures.parser_dispatch.senate_text.parse_senate_text",
            return_value=result,
        ) as mock_parse:
            parsed = parse_disclosure_pages(pages, filing)

        mock_parse.assert_called_once_with(pages, filing)
        assert parsed is result

    def test_senate_annual_calls_senate_parser(self) -> None:
        pages = ["senate annual"]
        filing = _filing(chamber=Chamber.SENATE, filing_type=FilingType.ANNUAL)
        result = _result("senate_annual")

        with patch(
            "src.parse.disclosures.parser_dispatch.senate_text.parse_senate_text",
            return_value=result,
        ) as mock_parse:
            parsed = parse_disclosure_pages(pages, filing)

        mock_parse.assert_called_once_with(pages, filing)
        assert parsed is result


class TestDispatchIsolation:
    def test_house_ptr_does_not_call_other_parsers(self) -> None:
        filing = _filing(chamber=Chamber.HOUSE, filing_type=FilingType.PTR)

        with (
            patch(
                "src.parse.disclosures.parser_dispatch.house_ptr_text.parse_house_ptr",
                return_value=_result("house_ptr"),
            ),
            patch(
                "src.parse.disclosures.parser_dispatch.house_annual_text.parse_house_annual",
            ) as mock_house_annual,
            patch(
                "src.parse.disclosures.parser_dispatch.senate_text.parse_senate_text",
            ) as mock_senate,
        ):
            parse_disclosure_pages(["x"], filing)

        mock_house_annual.assert_not_called()
        mock_senate.assert_not_called()

    def test_house_annual_does_not_call_other_parsers(self) -> None:
        filing = _filing(chamber=Chamber.HOUSE, filing_type=FilingType.ANNUAL)

        with (
            patch(
                "src.parse.disclosures.parser_dispatch.house_annual_text.parse_house_annual",
                return_value=_result("house_annual"),
            ),
            patch(
                "src.parse.disclosures.parser_dispatch.house_ptr_text.parse_house_ptr",
            ) as mock_house_ptr,
            patch(
                "src.parse.disclosures.parser_dispatch.senate_text.parse_senate_text",
            ) as mock_senate,
        ):
            parse_disclosure_pages(["x"], filing)

        mock_house_ptr.assert_not_called()
        mock_senate.assert_not_called()

    def test_senate_does_not_call_house_parsers(self) -> None:
        filing = _filing(chamber=Chamber.SENATE, filing_type=FilingType.PTR)

        with (
            patch(
                "src.parse.disclosures.parser_dispatch.senate_text.parse_senate_text",
                return_value=_result("senate"),
            ),
            patch(
                "src.parse.disclosures.parser_dispatch.house_ptr_text.parse_house_ptr",
            ) as mock_house_ptr,
            patch(
                "src.parse.disclosures.parser_dispatch.house_annual_text.parse_house_annual",
            ) as mock_house_annual,
        ):
            parse_disclosure_pages(["x"], filing)

        mock_house_ptr.assert_not_called()
        mock_house_annual.assert_not_called()


class TestAmendmentRejected:
    def test_amendment_raises(self) -> None:
        filing = _filing(chamber=Chamber.HOUSE, filing_type=FilingType.AMENDMENT)

        with pytest.raises(ValueError, match="AMENDMENT"):
            parse_disclosure_pages(["x"], filing)

    def test_error_message_guides_caller(self) -> None:
        filing = _filing(chamber=Chamber.SENATE, filing_type=FilingType.AMENDMENT)

        with pytest.raises(ValueError, match="underlying type"):
            parse_disclosure_pages(["x"], filing)


class TestEmptyPages:
    def test_empty_pages_forwarded_to_house_ptr(self) -> None:
        filing = _filing(chamber=Chamber.HOUSE, filing_type=FilingType.PTR)

        with patch(
            "src.parse.disclosures.parser_dispatch.house_ptr_text.parse_house_ptr",
            return_value=_result("house_ptr"),
        ) as mock_parse:
            parse_disclosure_pages([], filing)

        mock_parse.assert_called_once_with([], member_bioguide_id="A000001")

    def test_empty_pages_forwarded_to_senate(self) -> None:
        filing = _filing(chamber=Chamber.SENATE, filing_type=FilingType.ANNUAL)

        with patch(
            "src.parse.disclosures.parser_dispatch.senate_text.parse_senate_text",
            return_value=_result("senate"),
        ) as mock_parse:
            parse_disclosure_pages([], filing)

        mock_parse.assert_called_once_with([], filing)


class TestDispatchDeterminism:
    """Verify that every valid (chamber, filing_type) pair routes exactly once
    and that the routing key is based on enum identity, not string values."""

    @pytest.mark.parametrize(
        "chamber,filing_type,expected_patch",
        [
            (
                Chamber.HOUSE,
                FilingType.PTR,
                "src.parse.disclosures.parser_dispatch.house_ptr_text.parse_house_ptr",
            ),
            (
                Chamber.HOUSE,
                FilingType.ANNUAL,
                "src.parse.disclosures.parser_dispatch.house_annual_text.parse_house_annual",
            ),
            (
                Chamber.SENATE,
                FilingType.PTR,
                "src.parse.disclosures.parser_dispatch.senate_text.parse_senate_text",
            ),
            (
                Chamber.SENATE,
                FilingType.ANNUAL,
                "src.parse.disclosures.parser_dispatch.senate_text.parse_senate_text",
            ),
        ],
    )
    def test_each_combo_dispatches_exactly_once(
        self,
        chamber: Chamber,
        filing_type: FilingType,
        expected_patch: str,
    ) -> None:
        filing = _filing(chamber=chamber, filing_type=filing_type)
        with patch(expected_patch, return_value=_result("mock")) as mock_fn:
            parse_disclosure_pages(["page"], filing)
        assert mock_fn.call_count == 1

    def test_all_four_combos_are_registered(self) -> None:
        """All valid (chamber × filing_type) pairs dispatch without ValueError."""
        combos = [
            (Chamber.HOUSE, FilingType.PTR),
            (Chamber.HOUSE, FilingType.ANNUAL),
            (Chamber.SENATE, FilingType.PTR),
            (Chamber.SENATE, FilingType.ANNUAL),
        ]
        for chamber, filing_type in combos:
            filing = _filing(chamber=chamber, filing_type=filing_type)
            with (
                patch(
                    "src.parse.disclosures.parser_dispatch.house_ptr_text.parse_house_ptr",
                    return_value=_result("x"),
                ),
                patch(
                    "src.parse.disclosures.parser_dispatch.house_annual_text.parse_house_annual",
                    return_value=_result("x"),
                ),
                patch(
                    "src.parse.disclosures.parser_dispatch.senate_text.parse_senate_text",
                    return_value=_result("x"),
                ),
            ):
                # Should not raise
                parse_disclosure_pages(["page"], filing)
