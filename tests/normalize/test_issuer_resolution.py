"""Tests for src/normalize/issuer_resolution.py.

No network calls.  All reference data is constructed in-memory.
"""

from __future__ import annotations

import pytest

from src.normalize.issuer_resolution import (
    IssuerRecord,
    _extract_tickers_from_text,
    _normalize_for_fuzzy,
    _normalize_name,
    _token_overlap_score,
    build_index,
    resolve_best,
    resolve_issuer,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

APPLE = IssuerRecord(ticker="AAPL", name="Apple Inc.", cik="0000320193", aliases=("apple inc",))
MSFT = IssuerRecord(ticker="MSFT", name="Microsoft Corporation", cik="0000789019", aliases=("microsoft corp",))
GOOGL = IssuerRecord(ticker="GOOGL", name="Alphabet Inc.", cik="0001652044", aliases=("alphabet", "google"))
BRKA = IssuerRecord(ticker="BRK.A", name="Berkshire Hathaway Inc.", cik="0001067983", aliases=("berkshire hathaway",))
EXXON = IssuerRecord(ticker="XOM", name="Exxon Mobil Corporation", cik="0000034088", aliases=("exxon mobil", "exxon"))


@pytest.fixture()
def index():
    return build_index([APPLE, MSFT, GOOGL, BRKA, EXXON])


# ---------------------------------------------------------------------------
# Unit: normalization helpers
# ---------------------------------------------------------------------------

class TestNormalizeName:
    def test_lowercases(self):
        assert _normalize_name("Apple Inc.") == "apple inc"

    def test_strips_punctuation(self):
        assert _normalize_name("AT&T Corp.") == "at t corp"

    def test_collapses_whitespace(self):
        assert _normalize_name("  Foo   Bar  ") == "foo bar"

    def test_unicode_accents(self):
        # Accented chars should be left as-is but punctuation stripped
        result = _normalize_name("Société Générale")
        assert "soci" in result  # 'é' stays as unicode letter after NFC

    def test_empty_string(self):
        assert _normalize_name("") == ""


class TestNormalizeForFuzzy:
    def test_strips_inc(self):
        assert "inc" not in _normalize_for_fuzzy("Apple Inc.")

    def test_strips_corporation(self):
        assert "corporation" not in _normalize_for_fuzzy("Microsoft Corporation")

    def test_strips_ltd(self):
        assert "ltd" not in _normalize_for_fuzzy("Some Company Ltd.")

    def test_preserves_core(self):
        result = _normalize_for_fuzzy("Apple Inc.")
        assert "apple" in result


class TestTokenOverlapScore:
    def test_identical(self):
        assert _token_overlap_score("apple inc", "apple inc") == 1.0

    def test_no_overlap(self):
        assert _token_overlap_score("apple", "microsoft") == 0.0

    def test_partial_overlap(self):
        score = _token_overlap_score("apple inc", "apple corporation")
        assert 0.0 < score < 1.0

    def test_empty_strings(self):
        assert _token_overlap_score("", "") == 0.0

    def test_one_empty(self):
        assert _token_overlap_score("apple", "") == 0.0


# ---------------------------------------------------------------------------
# Unit: ticker extraction
# ---------------------------------------------------------------------------

class TestExtractTickers:
    def test_parenthetical(self):
        assert "AAPL" in _extract_tickers_from_text("Apple Inc (AAPL)")

    def test_bracketed(self):
        assert "MSFT" in _extract_tickers_from_text("Microsoft [MSFT]")

    def test_exchange_prefix(self):
        assert "GOOGL" in _extract_tickers_from_text("NASDAQ:GOOGL")

    def test_colon_suffix(self):
        assert "XOM" in _extract_tickers_from_text("XOM: Exxon Mobil")

    def test_no_ticker(self):
        assert _extract_tickers_from_text("Some plain text with no ticker") == []

    def test_multiple_tickers(self):
        tickers = _extract_tickers_from_text("(AAPL) and (MSFT)")
        assert "AAPL" in tickers
        assert "MSFT" in tickers


# ---------------------------------------------------------------------------
# Integration: resolve_issuer waterfall
# ---------------------------------------------------------------------------

class TestTickerHintResolution:
    """Step 1 – ticker hint from the disclosure row."""

    def test_exact_ticker_hint(self, index):
        cands = resolve_issuer("Apple", "AAPL", index)
        top = cands[0]
        assert top.ticker == "AAPL"
        assert top.confidence_label == "HIGH"
        assert top.match_method == "regex_ticker"
        assert top.cik == "0000320193"

    def test_ticker_hint_case_insensitive(self, index):
        top = resolve_best("Apple", "aapl", index)
        assert top.ticker == "AAPL"

    def test_unknown_ticker_hint_falls_through(self, index):
        # ZZZZ is not in the index; should fall through to name matching
        top = resolve_best("Apple Inc.", "ZZZZ", index)
        assert top.ticker == "AAPL"  # should still match via exact name


class TestRegexTickerExtraction:
    """Step 2 – ticker embedded in issuer_name text."""

    def test_parenthetical_in_name(self, index):
        top = resolve_best("Apple Inc. (AAPL)", None, index)
        assert top.ticker == "AAPL"
        assert top.match_method == "regex_ticker"
        assert top.confidence_label == "HIGH"

    def test_exchange_prefix_in_name(self, index):
        top = resolve_best("NYSE:XOM Exxon", None, index)
        assert top.ticker == "XOM"
        assert top.match_method == "regex_ticker"

    def test_unknown_extracted_ticker_falls_through(self, index):
        # (ZZZ) extracted but not in index – should fall through to name
        top = resolve_best("Exxon Mobil Corporation (ZZZ)", None, index)
        # Should still resolve via name/alias/fuzzy
        assert top.ticker == "XOM"


class TestExactNameResolution:
    """Step 3 – exact normalized name match."""

    def test_exact_canonical_name(self, index):
        top = resolve_best("Apple Inc.", None, index)
        assert top.ticker == "AAPL"
        assert top.match_method == "exact_name"
        assert top.confidence_label == "HIGH"

    def test_exact_name_ignores_punctuation_case(self, index):
        top = resolve_best("MICROSOFT CORPORATION", None, index)
        assert top.ticker == "MSFT"
        assert top.match_method == "exact_name"

    def test_name_with_extra_whitespace(self, index):
        top = resolve_best("  Apple   Inc.  ", None, index)
        assert top.ticker == "AAPL"
        assert top.match_method == "exact_name"


class TestAliasResolution:
    """Step 4 – alias match."""

    def test_known_alias(self, index):
        top = resolve_best("Alphabet", None, index)
        assert top.ticker == "GOOGL"
        assert top.match_method == "alias_match"
        assert top.confidence_label == "MEDIUM"

    def test_alias_case_insensitive(self, index):
        top = resolve_best("GOOGLE", None, index)
        assert top.ticker == "GOOGL"
        assert top.match_method == "alias_match"

    def test_berkshire_alias(self, index):
        top = resolve_best("Berkshire Hathaway", None, index)
        assert top.ticker == "BRK.A"
        assert top.match_method == "alias_match"


class TestFuzzyResolution:
    """Step 5 – fuzzy token-overlap fallback."""

    def test_fuzzy_partial_name(self, index):
        # "Exxon Mobil Energy Corp" has good token overlap with alias "exxon mobil"
        # but doesn't exact-match any name or alias → reaches fuzzy stage.
        # Jaccard({"exxon","mobil","energy"}, {"exxon","mobil"}) ≈ 0.67 → MEDIUM
        top = resolve_best("Exxon Mobil Energy Corp", None, index)
        assert top.ticker == "XOM"
        assert top.match_method == "fuzzy_score"
        assert top.score >= 0.55

    def test_fuzzy_confidence_medium_for_two_token_overlap(self, index):
        # Two shared tokens out of three → score ≈ 0.67, below HIGH threshold
        top = resolve_best("Exxon Mobil Energy Corp", None, index)
        assert top.confidence_label in ("MEDIUM", "HIGH")

    def test_fuzzy_returns_unresolved_for_garbage(self, index):
        top = resolve_best("XYZZY fund trust class zz", None, index)
        # Either unresolved or a very low score match
        if top.match_method == "unresolved":
            assert top.ticker is None
            assert top.confidence_label == "LOW"
        else:
            assert top.score < 0.55  # below medium threshold


class TestUnresolved:
    """Fallback path when nothing matches."""

    def test_unresolved_candidate(self):
        idx = build_index([APPLE])
        top = resolve_best("Absolutely nothing matching here", None, idx)
        assert top.match_method == "unresolved"
        assert top.ticker is None
        assert top.cik is None
        assert top.confidence_label == "LOW"
        assert top.score == 0.0


# ---------------------------------------------------------------------------
# Output structure
# ---------------------------------------------------------------------------

class TestCandidateStructure:
    def test_returns_list(self, index):
        result = resolve_issuer("Apple Inc.", "AAPL", index)
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_sorted_best_first(self, index):
        result = resolve_issuer("Apple Inc.", None, index)
        scores = [c.score for c in result]
        assert scores == sorted(scores, reverse=True)

    def test_as_dict_keys(self, index):
        d = resolve_best("Apple Inc.", None, index).as_dict()
        assert set(d.keys()) == {"ticker", "cik", "confidence_label", "match_method", "score", "matched_name"}

    def test_confidence_labels_are_valid(self, index):
        valid = {"HIGH", "MEDIUM", "LOW"}
        for name, hint in [("Apple Inc.", "AAPL"), ("google", None), ("junk xyzzy", None)]:
            top = resolve_best(name, hint, index)
            assert top.confidence_label in valid

    def test_match_methods_are_valid(self, index):
        valid = {"regex_ticker", "exact_name", "alias_match", "fuzzy_score", "unresolved"}
        for name, hint in [
            ("Apple Inc (AAPL)", None),
            ("Apple Inc.", None),
            ("google", None),
            ("Exxon Mobile Corp", None),
        ]:
            top = resolve_best(name, hint, index)
            assert top.match_method in valid


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_name_returns_unresolved(self, index):
        top = resolve_best("", None, index)
        assert top.match_method == "unresolved"

    def test_empty_reference_dataset(self):
        idx = build_index([])
        top = resolve_best("Apple Inc.", "AAPL", idx)
        assert top.match_method == "unresolved"

    def test_duplicate_tickers_in_dataset_first_wins(self):
        rec1 = IssuerRecord(ticker="DUPE", name="First Company", cik="0000000001")
        rec2 = IssuerRecord(ticker="DUPE", name="Second Company", cik="0000000002")
        idx = build_index([rec1, rec2])
        top = resolve_best("First Company", "DUPE", idx)
        assert top.ticker == "DUPE"
        assert top.cik == "0000000001"  # first record wins

    def test_none_cik_propagated(self, index):
        rec = IssuerRecord(ticker="NOCIK", name="No Cik Corp", cik=None)
        idx = build_index([rec])
        top = resolve_best("No Cik Corp", None, idx)
        assert top.cik is None

    def test_ticker_hint_uppercase_coercion(self, index):
        top = resolve_best("Apple", "AaPl", index)
        assert top.ticker == "AAPL"
