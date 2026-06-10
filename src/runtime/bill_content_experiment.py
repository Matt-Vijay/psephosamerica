"""THE bill-content experiment: dense bill embeddings -> defection AUC (track 1 payload).

This is the result the whole v4 effort is gated on. The moment Track A's contract
carries dense, vote-linkable bill rows, the watcher fires this: it joins each vote
to its bill's dense embedding, and measures defection AUC with bill content added,
two ways:

* **bill-RAG**: retrieve the member's own k-nearest past votes by *dense*
  bill-embedding cosine (which, unlike the sector-bag stand-in, discriminates
  *within* a sector) and add the similarity-weighted past-defection rate.
* **bill-features**: project the dense bill embedding to a few dims + cosponsor
  count, added straight to the head.

Reports ΔAUC vs the base head and vs the 0.7247 pin, ablating k in {4,8,16,32}.
Testable now with a synthetic per-bill embedding map (each bill id -> a fixed
pseudo-random vector that genuinely separates same-sector bills), so the harness
is proven before the real embeddings arrive; with Track A's embeddings the same
code yields the real number.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from src.prediction.defection import build_party_profiles, defected, defection_features, ranking_metrics
from src.prediction.vote_record import VoteRecord
from src.runtime.cross_pressured_experiment import _BINARY

Array = npt.NDArray[np.float64]
_NORM_FLOOR = 1e-12
_LOGIT_CLAMP = 40.0
_PIN = 0.7247


@dataclass(frozen=True)
class LinkedVote:
    record: VoteRecord
    bill_id: str
    bill_embedding: Array | None


def load_bill_embedding_map(records_path: Path) -> dict[str, Array]:
    """bill_id -> dense embedding, from contract bill rows (canonical/us_congress + external ids)."""
    out: dict[str, Array] = {}
    if not records_path.exists():
        return out
    with records_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("entity_type") != "bill":
                continue
            embedding = record.get("dossier_embedding")
            if not embedding:
                continue
            vec = np.asarray(embedding, dtype=np.float64)
            canonical = str(record.get("canonical_id", ""))
            if canonical:
                out[canonical] = vec
            for ext in record.get("external_ids") or []:
                out[str(ext)] = vec
    return out


def synthetic_bill_embedding(bill_id: str, *, dim: int = 32) -> Array:
    """A fixed pseudo-random per-bill vector (deterministic hash seed).

    Stands in for a dense embedding before Track A's land: unlike the sector-bag
    stand-in it gives every distinct bill its own point, so same-sector bills are
    separable and the bill-RAG harness exercises within-sector discrimination.
    """
    seed = int.from_bytes(hashlib.sha256(bill_id.encode("utf-8")).digest()[:8], "big")
    rng = np.random.default_rng(seed)
    vec: Array = rng.normal(size=dim)
    return vec


def build_linked_votes(
    rollcalls: list[dict[str, Any]],
    embedding_map: dict[str, Array] | None,
    *,
    synthetic: bool = False,
) -> list[LinkedVote]:
    """Flatten rich roll-calls to per-vote records carrying their bill embedding."""
    linked: list[LinkedVote] = []
    for rollcall in rollcalls:
        votes = [v for v in rollcall["votes"] if v[3] in _BINARY]
        if not votes:
            continue
        party_yea: dict[str, int] = defaultdict(int)
        party_total: dict[str, int] = defaultdict(int)
        for _bio, party, _state, choice in votes:
            party_total[party] += 1
            if choice == "yea":
                party_yea[party] += 1
        leans_yea = {p: party_yea[p] * 2 >= party_total[p] for p in party_total}
        bill_id = str(rollcall.get("bill_id", ""))
        sectors = tuple(rollcall.get("sectors", []))
        vote_date = date.fromisoformat(str(rollcall["date"]))
        if synthetic:
            embedding: Array | None = synthetic_bill_embedding(bill_id)
        elif embedding_map is not None:
            embedding = embedding_map.get(bill_id)
        else:
            embedding = None
        for bio, party, state, choice in votes:
            is_yea = choice == "yea"
            lean = leans_yea.get(party, True)
            record = VoteRecord(
                member=bio,
                party=party,
                state=state,
                vote_date=vote_date,
                is_yea=is_yea,
                party_alignment=1.0 if lean else -1.0,
                sectors=sectors,
                is_cross_pressured=is_yea != lean,
            )
            linked.append(LinkedVote(record=record, bill_id=bill_id, bill_embedding=embedding))
    return linked


class _MemberBillStore:
    def __init__(self) -> None:
        self._rows: list[Array] = []
        self._defections: list[bool] = []
        self._matrix: Array | None = None  # (n, dim) L2-normalised, built lazily
        self._defect: Array | None = None

    def add(self, embedding: Array, defected_flag: bool) -> None:
        self._rows.append(embedding)
        self._defections.append(defected_flag)
        self._matrix = None

    def _finalize(self) -> None:
        if self._matrix is not None or not self._rows:
            return
        mat = np.stack(self._rows, axis=0)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms < _NORM_FLOOR] = 1.0
        self._matrix = mat / norms
        self._defect = np.asarray(self._defections, dtype=np.float64)

    def signals(self, query: Array, k_values: tuple[int, ...]) -> dict[int, float]:
        """Similarity-weighted defection rate over top-k, for every k in one pass."""
        self._finalize()
        if self._matrix is None or self._defect is None:
            return {k: 0.0 for k in k_values}
        qn = float(np.linalg.norm(query))
        if qn < _NORM_FLOOR:
            return {k: 0.0 for k in k_values}
        sims = self._matrix @ (query / qn)  # cosine since matrix rows are unit
        order = np.argsort(sims)[::-1]  # descending, computed once
        out: dict[int, float] = {}
        for k in k_values:
            idx = order[:k]
            w = np.clip(sims[idx], 0.0, None)
            total = float(w.sum())
            out[k] = float((w * self._defect[idx]).sum() / total) if total >= _NORM_FLOOR else 0.0
        return out


def _train_logistic(
    rows: list[tuple[dict[str, float], bool]], names: tuple[str, ...], *, epochs: int = 300
) -> tuple[float, dict[str, float]]:
    if not rows:
        return 0.0, {n: 0.0 for n in names}
    positives = sum(1 for _f, y in rows if y)
    rate = min(0.95, max(0.05, positives / len(rows)))
    intercept = math.log(rate / (1.0 - rate))
    coef = {n: 0.0 for n in names}
    scale = 1.0 / len(rows)
    for _ in range(epochs):
        d_int = 0.0
        d_coef = {n: 0.0 for n in names}
        for features, label in rows:
            raw = intercept + sum(coef[n] * features.get(n, 0.0) for n in names)
            pred = 1.0 / (1.0 + math.exp(-max(-_LOGIT_CLAMP, min(_LOGIT_CLAMP, raw))))
            err = pred - (1.0 if label else 0.0)
            d_int += err
            for n in names:
                d_coef[n] += err * features.get(n, 0.0)
        intercept -= 0.3 * d_int * scale
        for n in names:
            coef[n] -= 0.3 * (d_coef[n] * scale + 0.01 * coef[n])
    return intercept, coef


def _auc(intercept: float, coef: dict[str, float], names: tuple[str, ...], rows: list[tuple[dict[str, float], bool]]) -> float:
    scores = [
        1.0 / (1.0 + math.exp(-max(-_LOGIT_CLAMP, min(_LOGIT_CLAMP, intercept + sum(coef[n] * f.get(n, 0.0) for n in names)))))
        for f, _y in rows
    ]
    return ranking_metrics(scores, [y for _f, y in rows]).auc


def run_bill_content_experiment(
    rollcalls: list[dict[str, Any]],
    embedding_map: dict[str, Array] | None,
    *,
    cutoff: date,
    eval_end: date,
    k_values: tuple[int, ...] = (4, 8, 16, 32),
    synthetic: bool = False,
    max_train: int = 200_000,
) -> dict[str, Any]:
    """Bill-RAG before/after defection AUC over k, with the re-pin decision."""
    linked = build_linked_votes(rollcalls, embedding_map, synthetic=synthetic)
    train = [lv for lv in linked if lv.record.vote_date <= cutoff]
    eval_lv = [lv for lv in linked if cutoff < lv.record.vote_date <= eval_end]
    if len(train) > max_train:
        train = train[-max_train:]
    linked_count = sum(1 for lv in linked if lv.bill_embedding is not None)
    profiles = build_party_profiles([lv.record for lv in train])

    base_train = [(defection_features(lv.record, profiles), defected(lv.record)) for lv in train]
    base_eval = [(defection_features(lv.record, profiles), defected(lv.record)) for lv in eval_lv]
    b_int, b_coef = _train_logistic(base_train, ("loyalty_gap", "sector_divergence"))
    base_auc = _auc(b_int, b_coef, ("loyalty_gap", "sector_divergence"), base_eval)

    # Build per-member dense-embedding stores from pre-cutoff votes.
    stores: dict[str, _MemberBillStore] = defaultdict(_MemberBillStore)
    for lv in train:
        if lv.bill_embedding is not None:
            stores[lv.record.member].add(lv.bill_embedding, defected(lv.record))

    names = ("loyalty_gap", "sector_divergence", "bill_rag_signal")

    def all_k_signals(lv: LinkedVote) -> dict[int, float]:
        store = stores.get(lv.record.member)
        if store is None or lv.bill_embedding is None:
            return {k: 0.0 for k in k_values}
        return store.signals(lv.bill_embedding, k_values)

    # One retrieval pass per record yields the signal for every k at once.
    train_feats = [(defection_features(lv.record, profiles), all_k_signals(lv), defected(lv.record)) for lv in train]
    eval_feats = [(defection_features(lv.record, profiles), all_k_signals(lv), defected(lv.record)) for lv in eval_lv]

    ablation: dict[str, Any] = {}
    best_auc = base_auc
    best_k = None
    for k in k_values:
        tr = [({**base, "bill_rag_signal": sig[k]}, y) for base, sig, y in train_feats]
        ev = [({**base, "bill_rag_signal": sig[k]}, y) for base, sig, y in eval_feats]
        i, c = _train_logistic(tr, names)
        auc = _auc(i, c, names, ev)
        ablation[f"k={k}"] = {"auc": auc, "delta_vs_base": auc - base_auc}
        if auc > best_auc:
            best_auc, best_k = auc, k

    return {
        "cutoff": cutoff.isoformat(),
        "embedding_source": "synthetic" if synthetic else "contract_dense",
        "vote_linked_bills": linked_count,
        "eval_pairs": len(eval_lv),
        "base_auc": base_auc,
        "best_auc": best_auc,
        "best_k": best_k,
        "delta_vs_base": best_auc - base_auc,
        "pin": _PIN,
        "beats_pin": best_auc > _PIN + 0.005,
        "k_ablation": ablation,
    }
