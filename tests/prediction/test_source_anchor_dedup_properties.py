"""Property tests: prediction source-anchor dedup is deterministic.

Published source anchors must be stable and reproducible regardless of input
order, so dedup must be order-independent, idempotent, and yield a canonical
(sorted, identity-unique) result. Seeded stdlib randomness (no hypothesis).
"""

from __future__ import annotations

import random

from src.export.contracts import SourceAnchor
from src.prediction.source_anchors import (
    _identity_sort_key,
    dedupe_prediction_source_anchors,
    source_anchor_identity,
)

_TYPES = [
    "financial_disclosure",
    "fec_contribution",
    "vote_event",
    "legislative_bill",
    "legislative_vote",
]
_URLS = [None, "https://example.test/a", "https://efdsearch.senate.gov/paper/1/"]
_LABELS = ["L", "a longer label", "x"]


def _rand_anchor(rng: random.Random) -> SourceAnchor:
    source_type = rng.choice(_TYPES)
    kwargs: dict[str, object] = {
        "source_type": source_type,
        "source_id": rng.choice(["a", "b", "c", "d"]),
        "url": rng.choice(_URLS),
        "label": rng.choice(_LABELS),
    }
    if source_type in ("legislative_bill", "legislative_vote") and rng.random() < 0.7:
        kwargs.update(
            jurisdiction_id="us_congress",
            legislative_body_id="us_congress_house",
            legislative_session_id="congress_119_session_1",
        )
    return SourceAnchor(**kwargs)  # type: ignore[arg-type]


def test_dedup_is_order_independent_idempotent_and_canonical() -> None:
    rng = random.Random(2024)
    for _ in range(500):
        anchors = [_rand_anchor(rng) for _ in range(rng.randint(0, 12))]
        once = dedupe_prediction_source_anchors(anchors)

        shuffled = anchors[:]
        rng.shuffle(shuffled)
        assert dedupe_prediction_source_anchors(shuffled) == once  # order-independent
        assert dedupe_prediction_source_anchors(once) == once  # idempotent

        identities = [source_anchor_identity(a) for a in once]
        assert len(identities) == len(set(identities))  # no duplicate identities
        # Canonical order uses the implementation's None-safe identity key.
        assert identities == sorted(identities, key=_identity_sort_key)


def test_dedup_prefers_official_url_bearing_anchor() -> None:
    # Same identity, different quality: the official URL-bearing anchor must win.
    plain = SourceAnchor(source_type="financial_disclosure", source_id="fd-1", url=None, label="x")
    official = SourceAnchor(
        source_type="financial_disclosure",
        source_id="fd-1",
        url="https://efdsearch.senate.gov/search/view/paper/1/",
        label="2024 disclosure",
    )
    result = dedupe_prediction_source_anchors([plain, official])
    assert len(result) == 1
    assert result[0].url == "https://efdsearch.senate.gov/search/view/paper/1/"


def test_dedup_handles_legislative_anchors_with_mixed_jurisdiction_context() -> None:
    # Regression: two legislative anchors sharing a source_id, one WITH portable
    # jurisdiction context and one WITHOUT (None fields), produced distinct
    # identity tuples whose sort compared None < str and raised TypeError.
    with_context = SourceAnchor(
        source_type="legislative_bill",
        source_id="hr-1",
        url="https://www.congress.gov/bill/119th-congress/house-bill/1",
        label="HR 1",
        jurisdiction_id="us_congress",
        legislative_body_id="us_congress_house",
        legislative_session_id="congress_119_session_1",
    )
    without_context = SourceAnchor(
        source_type="legislative_bill",
        source_id="hr-1",
        url=None,
        label="HR 1 (no context)",
    )
    result = dedupe_prediction_source_anchors([with_context, without_context])
    # Distinct identities -> both retained, and the call does not raise.
    assert len(result) == 2
    assert {source_anchor_identity(a) for a in result} == {
        source_anchor_identity(with_context),
        source_anchor_identity(without_context),
    }
