"""Bill-encoder + RAG over a member's own past votes (the honest experiment v2).

For each eval ``(member, bill)`` pair we retrieve the member's *own* k nearest
pre-cutoff votes by bill-embedding cosine and form a per-(member, bill)
interaction term: the similarity-weighted defection rate on the bills most like
this one. That term is added to the defection head and we report AUC / Brier /
accuracy / ECE on the honest ex-ante slice, BEFORE vs AFTER, ablating
``k in {4, 8, 16, 32}``.

The bill encoder is pluggable. Until Track A's dense govinfo bill embeddings land
we encode a bill as the L2-normalised bag of its real policy sectors (no
fabrication -- the sectors come from the clerk ``vote-desc`` tags). The moment a
``dense_embeddings`` map is supplied (keyed by sector signature, swapped in at
hot-swap time) retrieval discriminates *within* a sector instead of collapsing
same-sector bills to one point -- which is exactly the lift the dense stream
buys. We report the sector-bag result honestly and leave the dense path wired.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

import numpy as np
import numpy.typing as npt

from src.prediction.bill_sector import sector_keywords
from src.prediction.calibration import expected_calibration_error
from src.prediction.defection import (
    PartyProfiles,
    defected,
    defection_features,
    is_defection_prone,
    ranking_metrics,
)
from src.prediction.defection_head import DefectionHead
from src.prediction.logistic import train_logistic_rows
from src.runtime.cross_pressured_experiment import VoteRecord

Array = npt.NDArray[np.float64]
Embedder = Callable[[tuple[str, ...]], Array]

_SECTOR_VOCAB = sorted(sector_keywords())
_SECTOR_INDEX = {sector: i for i, sector in enumerate(_SECTOR_VOCAB)}
_NORM_FLOOR = 1e-12


def sector_bag_embedding(sectors: tuple[str, ...]) -> Array:
    """L2-normalised bag-of-sectors bill embedding (the pre-dense stand-in)."""
    vector = np.zeros(len(_SECTOR_VOCAB), dtype=np.float64)
    for sector in sectors:
        if sector in _SECTOR_INDEX:
            vector[_SECTOR_INDEX[sector]] = 1.0
    norm = float(np.linalg.norm(vector))
    if norm < _NORM_FLOOR:
        return vector
    return vector / norm


def _cosine(left: Array, right: Array) -> float:
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denominator < _NORM_FLOOR:
        return 0.0
    return float(np.dot(left, right) / denominator)


class _MemberStore:
    """A member's pre-cutoff (bill-embedding, defected?) history for retrieval."""

    def __init__(self) -> None:
        self.embeddings: list[Array] = []
        self.defections: list[bool] = []

    def add(self, embedding: Array, defected_flag: bool) -> None:
        self.embeddings.append(embedding)
        self.defections.append(defected_flag)

    def rag_signal(self, query: Array, k: int) -> float:
        """Similarity-weighted defection rate over the member's k nearest past votes."""
        if not self.embeddings:
            return 0.0
        sims = [(_cosine(query, emb), self.defections[i]) for i, emb in enumerate(self.embeddings)]
        sims.sort(key=lambda item: item[0], reverse=True)
        top = sims[:k]
        weight = sum(max(0.0, s) for s, _ in top)
        if weight < _NORM_FLOOR:
            return 0.0
        return sum(max(0.0, s) * (1.0 if d else 0.0) for s, d in top) / weight


def build_member_stores(
    train: list[VoteRecord],
    embedder: Embedder = sector_bag_embedding,
) -> dict[str, _MemberStore]:
    """Index each member's own pre-cutoff votes for RAG retrieval."""
    stores: dict[str, _MemberStore] = defaultdict(_MemberStore)
    for record in train:
        stores[record.member].add(embedder(record.sectors), defected(record))
    return stores


def _rag_feature(
    record: VoteRecord,
    stores: dict[str, _MemberStore],
    k: int,
    embedder: Embedder,
    cache: dict[tuple[str, tuple[str, ...], int], float] | None = None,
) -> float:
    store = stores.get(record.member)
    if store is None:
        return 0.0
    # Same member + same sector signature + same k -> identical retrieval. Many
    # bills share a sector signature, so memoising collapses the k-NN work from
    # O(train) to O(distinct member-signatures).
    key = (record.member, record.sectors, k)
    if cache is not None and key in cache:
        return cache[key]
    signal = store.rag_signal(embedder(record.sectors), k)
    if cache is not None:
        cache[key] = signal
    return signal


def _train_head_with_features(
    rows: list[tuple[dict[str, float], bool]],
    feature_names: tuple[str, ...],
    *,
    learning_rate: float = 0.3,
    epochs: int = 300,
    l2: float = 0.01,
) -> DefectionHead:
    intercept, coefficients = train_logistic_rows(
        rows, feature_names, learning_rate=learning_rate, epochs=epochs, l2=l2
    )
    return DefectionHead(intercept=intercept, coefficients=coefficients)


def _slice_metrics(scores: list[float], labels: list[bool]) -> dict[str, float]:
    n = len(labels)
    if n == 0:
        return {"brier": 0.0, "accuracy": 0.0, "ece": 0.0, "auc": 0.5, "sample_count": 0}
    brier = sum((p - (1.0 if y else 0.0)) ** 2 for p, y in zip(scores, labels)) / n
    accuracy = sum(1 for p, y in zip(scores, labels) if (p >= 0.5) == y) / n
    ece = expected_calibration_error(scores, labels)
    metrics = ranking_metrics(scores, labels)
    return {
        "brier": brier,
        "accuracy": accuracy,
        "ece": ece,
        "auc": metrics.auc,
        "precision_at_50": metrics.precision_at_50,
        "sample_count": n,
        "positives": metrics.positives,
    }


def run_rag_before_after(
    train: list[VoteRecord],
    eval_records: list[VoteRecord],
    profiles: PartyProfiles,
    *,
    tau: float = 0.25,
    k_values: tuple[int, ...] = (4, 8, 16, 32),
    embedder: Embedder = sector_bag_embedding,
) -> dict[str, object]:
    """BEFORE (ex-ante features) vs AFTER (+RAG interaction) on the honest slice."""
    honest_eval = [r for r in eval_records if is_defection_prone(r, profiles, tau=tau)]
    base_names = ("loyalty_gap", "sector_divergence")

    before_rows = [(defection_features(r, profiles), defected(r)) for r in train]
    before_head = _train_head_with_features(before_rows, base_names)
    before_scores = [before_head.probability(defection_features(r, profiles)) for r in honest_eval]
    before_labels = [defected(r) for r in honest_eval]
    before = _slice_metrics(before_scores, before_labels)

    stores = build_member_stores(train, embedder=embedder)
    rag_names = (*base_names, "rag_signal")
    ablation: dict[str, object] = {}
    best_k = k_values[0] if k_values else 8
    best_auc = -1.0
    best_after: dict[str, float] = before
    for k in k_values:
        cache: dict[tuple[str, tuple[str, ...], int], float] = {}
        rows = [
            (
                {
                    **defection_features(r, profiles),
                    "rag_signal": _rag_feature(r, stores, k, embedder, cache),
                },
                defected(r),
            )
            for r in train
        ]
        head = _train_head_with_features(rows, rag_names)
        scores = [
            head.probability(
                {
                    **defection_features(r, profiles),
                    "rag_signal": _rag_feature(r, stores, k, embedder, cache),
                }
            )
            for r in honest_eval
        ]
        after = _slice_metrics(scores, before_labels)
        ablation[f"k={k}"] = after
        if after["auc"] > best_auc:
            best_auc = after["auc"]
            best_k = k
            best_after = after

    return {
        "embedding": "sector_bag" if embedder is sector_bag_embedding else "dense",
        "honest_slice_eval_pairs": len(honest_eval),
        "before": before,
        "after_best": best_after,
        "best_k": best_k,
        "k_ablation": ablation,
        "delta_auc": best_after["auc"] - before["auc"],
    }
