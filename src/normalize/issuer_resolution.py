"""Deterministic issuer/ticker candidate resolution for disclosure assets.

Implements a waterfall:
  1. regex_ticker   – extract parenthetical/bracketed ticker from raw text
  2. exact_name     – normalized exact match against reference issuer names
  3. alias_match    – match against normalized alias list
  4. fuzzy_score    – token-overlap scoring against all names + aliases

No network calls.  The caller provides an in-memory reference dataset.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal, Sequence

# ---------------------------------------------------------------------------
# Reference dataset types (caller-supplied, no I/O here)
# ---------------------------------------------------------------------------

MatchMethod = Literal["regex_ticker", "exact_name", "alias_match", "fuzzy_score", "unresolved"]
ConfidenceLabel = Literal["HIGH", "MEDIUM", "LOW"]


@dataclass(frozen=True)
class IssuerRecord:
    """``aliases`` must already be normalized (lower-cased, punctuation-stripped)."""

    ticker: str
    name: str
    cik: str | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "ticker", self.ticker.upper().strip())


@dataclass
class IssuerCandidate:
    ticker: str | None
    cik: str | None
    confidence_label: ConfidenceLabel
    match_method: MatchMethod
    score: float  # 0.0–1.0; higher is better
    matched_name: str | None = None  # canonical name that triggered the match

    def as_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "cik": self.cik,
            "confidence_label": self.confidence_label,
            "match_method": self.match_method,
            "score": round(self.score, 4),
            "matched_name": self.matched_name,
        }


# ---------------------------------------------------------------------------
# Internal indices – built once per reference dataset
# ---------------------------------------------------------------------------

@dataclass
class _RefIndex:
    by_ticker: dict[str, IssuerRecord]           # TICKER -> record
    by_norm_name: dict[str, IssuerRecord]         # normalized_name -> record
    by_alias: dict[str, IssuerRecord]             # normalized_alias -> record
    all_records: list[IssuerRecord]

    @staticmethod
    def build(records: Sequence[IssuerRecord]) -> "_RefIndex":
        by_ticker: dict[str, IssuerRecord] = {}
        by_norm_name: dict[str, IssuerRecord] = {}
        by_alias: dict[str, IssuerRecord] = {}

        for rec in records:
            ticker_key = rec.ticker.upper()
            if ticker_key and ticker_key not in by_ticker:
                by_ticker[ticker_key] = rec

            norm = _normalize_name(rec.name)
            if norm and norm not in by_norm_name:
                by_norm_name[norm] = rec

            for alias in rec.aliases:
                norm_alias = _normalize_name(alias)
                if norm_alias and norm_alias not in by_alias:
                    by_alias[norm_alias] = rec

        return _RefIndex(
            by_ticker=by_ticker,
            by_norm_name=by_norm_name,
            by_alias=by_alias,
            all_records=list(records),
        )


# ---------------------------------------------------------------------------
# String normalization helpers
# ---------------------------------------------------------------------------

# Common legal suffixes to strip for fuzzy matching
_SUFFIX_RE = re.compile(
    r"\b(inc\.?|incorporated|corp\.?|corporation|ltd\.?|limited|llc|"
    r"plc|co\.?|company|group|holdings?|fund|trust|etf|class\s+[a-z])\b",
    re.IGNORECASE,
)

# Ticker patterns: (AAPL), [AAPL], AAPL:, :AAPL, NYSE:AAPL, NASDAQ:AAPL
_TICKER_RE = re.compile(
    r"""
    (?:
        [\(\[]\s*([A-Z]{1,5})\s*[\)\]]      # (AAPL) or [AAPL]
        |
        (?:NYSE|NASDAQ|AMEX|BATS|OTC)[:\s]+([A-Z]{1,5})  # NYSE:AAPL
        |
        \b([A-Z]{1,5})\s*:                  # AAPL:
        |
        :\s*([A-Z]{1,5})\b                  # :AAPL
    )
    """,
    re.VERBOSE,
)


def _normalize_name(name: str) -> str:
    """Lower-case, strip accents, remove punctuation, collapse whitespace."""
    # Unicode NFC then decompose for accent stripping
    nfc = unicodedata.normalize("NFC", name)
    # Remove non-alphanumeric except spaces
    cleaned = re.sub(r"[^a-zA-Z0-9\s]", " ", nfc)
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def _normalize_for_fuzzy(name: str) -> str:
    """Normalize then strip common legal suffixes."""
    norm = _normalize_name(name)
    stripped = _SUFFIX_RE.sub(" ", norm)
    return re.sub(r"\s+", " ", stripped).strip()


def _token_overlap_score(a: str, b: str) -> float:
    """Jaccard token overlap between two normalized strings."""
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


def _extract_tickers_from_text(text: str) -> list[str]:
    """Return all uppercase ticker candidates found in ``text``."""
    tickers: list[str] = []
    for m in _TICKER_RE.finditer(text):
        # groups correspond to the four sub-patterns
        ticker = next((g for g in m.groups() if g is not None), None)
        if ticker:
            tickers.append(ticker.upper())
    return tickers


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_index(records: Sequence[IssuerRecord]) -> _RefIndex:
    """Build a lookup index from a reference dataset.  Call once, reuse often."""
    return _RefIndex.build(records)


def resolve_issuer(
    issuer_name: str,
    issuer_ticker_hint: str | None,
    index: _RefIndex,
    *,
    fuzzy_high_threshold: float = 0.85,
    fuzzy_medium_threshold: float = 0.55,
) -> list[IssuerCandidate]:
    """Always returns at least one candidate (method="unresolved" if nothing matches)."""
    candidates: list[IssuerCandidate] = []

    # ------------------------------------------------------------------
    # Step 1: ticker hint from disclosure row (deterministic)
    # ------------------------------------------------------------------
    if issuer_ticker_hint:
        ticker_key = issuer_ticker_hint.upper().strip()
        if ticker_key in index.by_ticker:
            rec = index.by_ticker[ticker_key]
            candidates.append(IssuerCandidate(
                ticker=rec.ticker,
                cik=rec.cik,
                confidence_label="HIGH",
                match_method="regex_ticker",
                score=1.0,
                matched_name=rec.name,
            ))

    # ------------------------------------------------------------------
    # Step 2: regex ticker extraction from the name text
    # ------------------------------------------------------------------
    if not candidates:
        extracted = _extract_tickers_from_text(issuer_name)
        for t in extracted:
            if t in index.by_ticker:
                rec = index.by_ticker[t]
                candidates.append(IssuerCandidate(
                    ticker=rec.ticker,
                    cik=rec.cik,
                    confidence_label="HIGH",
                    match_method="regex_ticker",
                    score=0.98,
                    matched_name=rec.name,
                ))
                break  # first match wins at this stage

    # ------------------------------------------------------------------
    # Step 3: exact normalized name match
    # ------------------------------------------------------------------
    if not candidates:
        norm = _normalize_name(issuer_name)
        if norm in index.by_norm_name:
            rec = index.by_norm_name[norm]
            candidates.append(IssuerCandidate(
                ticker=rec.ticker,
                cik=rec.cik,
                confidence_label="HIGH",
                match_method="exact_name",
                score=0.97,
                matched_name=rec.name,
            ))

    # ------------------------------------------------------------------
    # Step 4: alias match
    # ------------------------------------------------------------------
    if not candidates:
        norm = _normalize_name(issuer_name)
        if norm in index.by_alias:
            rec = index.by_alias[norm]
            candidates.append(IssuerCandidate(
                ticker=rec.ticker,
                cik=rec.cik,
                confidence_label="MEDIUM",
                match_method="alias_match",
                score=0.80,
                matched_name=rec.name,
            ))

    # ------------------------------------------------------------------
    # Step 5: fuzzy token-overlap scoring across all names + aliases
    # ------------------------------------------------------------------
    if not candidates:
        query_fuzzy = _normalize_for_fuzzy(issuer_name)
        best_score = 0.0
        best_rec: IssuerRecord | None = None

        for rec in index.all_records:
            s = _token_overlap_score(query_fuzzy, _normalize_for_fuzzy(rec.name))

            for alias in rec.aliases:
                alias_score = _token_overlap_score(query_fuzzy, _normalize_for_fuzzy(alias))
                if alias_score > s:
                    s = alias_score

            if s > best_score:
                best_score = s
                best_rec = rec

        if best_rec is not None and best_score >= fuzzy_medium_threshold:
            if best_score >= fuzzy_high_threshold:
                confidence: ConfidenceLabel = "HIGH"
            else:
                confidence = "MEDIUM" if best_score >= 0.60 else "LOW"

            candidates.append(IssuerCandidate(
                ticker=best_rec.ticker,
                cik=best_rec.cik,
                confidence_label=confidence,
                match_method="fuzzy_score",
                score=best_score,
                matched_name=best_rec.name,
            ))

    # ------------------------------------------------------------------
    # Fallback: unresolved
    # ------------------------------------------------------------------
    if not candidates:
        candidates.append(IssuerCandidate(
            ticker=None,
            cik=None,
            confidence_label="LOW",
            match_method="unresolved",
            score=0.0,
            matched_name=None,
        ))

    # Sort best first (higher score = better)
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


def resolve_best(
    issuer_name: str,
    issuer_ticker_hint: str | None,
    index: _RefIndex,
    **kwargs,
) -> IssuerCandidate:
    """Convenience wrapper – returns only the top candidate."""
    return resolve_issuer(issuer_name, issuer_ticker_hint, index, **kwargs)[0]
