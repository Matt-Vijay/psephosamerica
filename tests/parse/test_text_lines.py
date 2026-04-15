"""Tests for text_lines helpers.

All tests are pure (no network, no filesystem, no DB).
"""

from __future__ import annotations

import pytest

from src.parse.disclosures.text_lines import (
    SECTION_HEADERS,
    PageLines,
    drop_empty,
    flatten_lines,
    pages_to_line_lists,
    slice_section,
    split_page,
)
import src.parse.disclosures.text_tokens as _tokens


# ---------------------------------------------------------------------------
# PageLines
# ---------------------------------------------------------------------------


class TestPageLines:
    def test_stores_page_number_and_lines(self) -> None:
        pl = PageLines(page_number=1, lines=("foo", "bar"))
        assert pl.page_number == 1
        assert pl.lines == ("foo", "bar")

    def test_empty_lines_allowed(self) -> None:
        pl = PageLines(page_number=2, lines=())
        assert pl.lines == ()

    def test_is_immutable(self) -> None:
        pl = PageLines(page_number=1, lines=("x",))
        with pytest.raises((AttributeError, TypeError)):
            pl.page_number = 2  # type: ignore[misc]


# ---------------------------------------------------------------------------
# split_page
# ---------------------------------------------------------------------------


class TestSplitPage:
    def test_single_line(self) -> None:
        pl = split_page(1, "hello")
        assert pl.lines == ("hello",)
        assert pl.page_number == 1

    def test_multi_line(self) -> None:
        pl = split_page(2, "line one\nline two\nline three")
        assert pl.lines == ("line one", "line two", "line three")

    def test_empty_text(self) -> None:
        pl = split_page(1, "")
        assert pl.lines == ()

    def test_preserves_order(self) -> None:
        text = "\n".join(str(i) for i in range(5))
        pl = split_page(3, text)
        assert pl.lines == ("0", "1", "2", "3", "4")

    def test_blank_lines_preserved(self) -> None:
        pl = split_page(1, "a\n\nb")
        assert pl.lines == ("a", "", "b")

    def test_returns_page_lines(self) -> None:
        pl = split_page(1, "x")
        assert isinstance(pl, PageLines)


# ---------------------------------------------------------------------------
# pages_to_line_lists
# ---------------------------------------------------------------------------


class TestPagesToLineLists:
    def test_empty_input(self) -> None:
        result = pages_to_line_lists([])
        assert result == ()

    def test_page_numbers_are_one_indexed(self) -> None:
        result = pages_to_line_lists(["first", "second"])
        assert result[0].page_number == 1
        assert result[1].page_number == 2

    def test_lines_are_split_per_page(self) -> None:
        result = pages_to_line_lists(["a\nb", "c\nd"])
        assert result[0].lines == ("a", "b")
        assert result[1].lines == ("c", "d")

    def test_returns_tuple(self) -> None:
        result = pages_to_line_lists(["x"])
        assert isinstance(result, tuple)

    def test_single_page(self) -> None:
        result = pages_to_line_lists(["only page"])
        assert len(result) == 1
        assert result[0].page_number == 1


# ---------------------------------------------------------------------------
# flatten_lines
# ---------------------------------------------------------------------------


class TestFlattenLines:
    def test_empty_sequence(self) -> None:
        assert flatten_lines([]) == ()

    def test_single_page(self) -> None:
        pl = PageLines(page_number=1, lines=("a", "b"))
        assert flatten_lines([pl]) == ("a", "b")

    def test_multiple_pages_in_order(self) -> None:
        pages = [
            PageLines(page_number=1, lines=("a", "b")),
            PageLines(page_number=2, lines=("c",)),
            PageLines(page_number=3, lines=("d", "e")),
        ]
        assert flatten_lines(pages) == ("a", "b", "c", "d", "e")

    def test_empty_page_contributes_nothing(self) -> None:
        pages = [
            PageLines(page_number=1, lines=("x",)),
            PageLines(page_number=2, lines=()),
            PageLines(page_number=3, lines=("y",)),
        ]
        assert flatten_lines(pages) == ("x", "y")

    def test_returns_tuple(self) -> None:
        result = flatten_lines([PageLines(page_number=1, lines=("a",))])
        assert isinstance(result, tuple)

    def test_page_order_determines_line_order(self) -> None:
        pages = [
            PageLines(page_number=1, lines=("first",)),
            PageLines(page_number=2, lines=("second",)),
        ]
        result = flatten_lines(pages)
        assert result.index("first") < result.index("second")


# ---------------------------------------------------------------------------
# drop_empty
# ---------------------------------------------------------------------------


class TestDropEmpty:
    def test_removes_empty_strings(self) -> None:
        assert drop_empty(["a", "", "b"]) == ("a", "b")

    def test_removes_whitespace_only_lines(self) -> None:
        assert drop_empty(["x", "   ", "\t", "y"]) == ("x", "y")

    def test_all_empty_returns_empty_tuple(self) -> None:
        assert drop_empty(["", " ", "\n"]) == ()

    def test_no_empty_lines_unchanged(self) -> None:
        assert drop_empty(["a", "b", "c"]) == ("a", "b", "c")

    def test_empty_input(self) -> None:
        assert drop_empty([]) == ()

    def test_returns_tuple(self) -> None:
        result = drop_empty(["a"])
        assert isinstance(result, tuple)

    def test_preserves_order(self) -> None:
        lines = ["first", "", "second", "  ", "third"]
        assert drop_empty(lines) == ("first", "second", "third")

    def test_line_with_content_and_surrounding_space_kept(self) -> None:
        # strip() is used to decide; the original line is retained unchanged.
        assert drop_empty(["  text  "]) == ("  text  ",)


# ---------------------------------------------------------------------------
# slice_section
# ---------------------------------------------------------------------------


class TestSliceSection:
    def test_header_not_found_returns_empty(self) -> None:
        lines = ["part i", "some data", "part ii", "more data"]
        assert slice_section(lines, "schedule a") == ()

    def test_basic_slice(self) -> None:
        lines = ["preamble", "schedule a", "row 1", "row 2", "schedule b", "other"]
        result = slice_section(lines, "schedule a")
        assert result == ("row 1", "row 2")

    def test_header_line_itself_excluded(self) -> None:
        lines = ["schedule a", "data line"]
        result = slice_section(lines, "schedule a")
        assert "schedule a" not in result

    def test_slice_to_end_of_stream(self) -> None:
        lines = ["part i", "line a", "line b"]
        result = slice_section(lines, "part i")
        assert result == ("line a", "line b")

    def test_stops_at_next_known_header(self) -> None:
        lines = ["schedule a", "asset line", "schedule b", "should not appear"]
        result = slice_section(lines, "schedule a")
        assert "should not appear" not in result
        assert result == ("asset line",)

    def test_empty_section_between_headers(self) -> None:
        lines = ["schedule a", "schedule b"]
        result = slice_section(lines, "schedule a")
        assert result == ()

    def test_case_insensitive_header_match(self) -> None:
        lines = ["Schedule A", "data"]
        result = slice_section(lines, "schedule a")
        assert result == ("data",)

    def test_header_with_leading_trailing_whitespace(self) -> None:
        lines = ["  schedule a  ", "data"]
        result = slice_section(lines, "schedule a")
        assert result == ("data",)

    def test_only_first_occurrence_triggers(self) -> None:
        lines = ["schedule a", "first", "schedule a", "second"]
        result = slice_section(lines, "schedule a")
        # second "schedule a" stops the section if it's in known_headers.
        # "schedule a" IS in SECTION_HEADERS so it terminates the first slice.
        assert result == ("first",)

    def test_custom_known_headers(self) -> None:
        lines = ["intro", "section x", "content", "section y", "end"]
        custom = frozenset({"section x", "section y"})
        result = slice_section(lines, "section x", known_headers=custom)
        assert result == ("content",)

    def test_returns_tuple(self) -> None:
        lines = ["schedule a", "row"]
        result = slice_section(lines, "schedule a")
        assert isinstance(result, tuple)

    def test_section_headers_constant_is_frozenset(self) -> None:
        assert isinstance(SECTION_HEADERS, frozenset)

    def test_preserves_line_content(self) -> None:
        lines = ["part ii", "  some value  \t", "another line"]
        result = slice_section(lines, "part ii")
        assert result == ("  some value  \t", "another line")


# ---------------------------------------------------------------------------
# Canonical token source
# ---------------------------------------------------------------------------


class TestSectionHeadersCanonicalSource:
    def test_section_headers_is_canonical(self) -> None:
        """SECTION_HEADERS re-exported from text_lines must be the text_tokens object."""
        assert SECTION_HEADERS is _tokens.SECTION_HEADERS

    def test_section_headers_subset_of_detection_headers(self) -> None:
        assert SECTION_HEADERS <= _tokens.DETECTION_HEADERS

    def test_ptr_field_tokens_not_in_section_headers(self) -> None:
        for token in ("transaction", "owner", "asset", "amount", "date"):
            assert token not in SECTION_HEADERS

    def test_schedule_tokens_present(self) -> None:
        for token in ("schedule a", "schedule b", "schedule c", "schedule d"):
            assert token in SECTION_HEADERS

    def test_part_tokens_present(self) -> None:
        for token in ("part i", "part ii", "part iii", "part iv", "part v",
                      "part vi", "part vii", "part viii", "part ix"):
            assert token in SECTION_HEADERS
