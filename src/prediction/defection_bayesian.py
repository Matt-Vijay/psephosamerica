"""10-seed Bayesian uncertainty for the defection head + content-addressed checkpoints (v4 #10).

The defection head's fit is deterministic given its data, so epistemic uncertainty
comes from *data* resampling: train the head on ``n_seeds`` bootstrap resamples of
the training set (each seeded), then for any (member, bill) report the mean and
standard deviation of the predicted defection probability across the ensemble --
the Bayesian posterior spread. We also report the AUC mean ± std across seeds, so
the headline ranking metric carries an uncertainty band.

Every pinned ensemble is **content-addressed**: its canonical JSON is hashed
(sha256) and stored at ``sha256/<aa>/<bb>/<full>.json``, so an identical ensemble
maps to an identical path and a checkpoint can be verified by recomputing the
hash. This mirrors the repo's existing ``runtime/checkpoint.py`` scheme for the
defection model specifically.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from src.prediction.defection import (
    PartyProfiles,
    defected,
    defection_features,
    ranking_metrics,
)
from src.prediction.defection_head import DefectionHead, train_defection_head
from src.prediction.vote_record import VoteRecord


def bootstrap_seed_ensemble(
    train: list[VoteRecord],
    profiles: PartyProfiles,
    *,
    n_seeds: int = 10,
    learning_rate: float = 0.3,
    epochs: int = 300,
    l2: float = 0.01,
) -> list[DefectionHead]:
    """Train ``n_seeds`` defection heads on seeded bootstrap resamples of ``train``."""
    if n_seeds <= 0:
        raise ValueError("n_seeds must be positive")
    if not train:
        return [train_defection_head(train, profiles)]
    heads: list[DefectionHead] = []
    n = len(train)
    for seed in range(n_seeds):
        rng = np.random.default_rng(seed)
        idx = rng.integers(0, n, size=n)
        resample = [train[int(i)] for i in idx]
        heads.append(
            train_defection_head(
                resample, profiles, learning_rate=learning_rate, epochs=epochs, l2=l2
            )
        )
    return heads


@dataclass(frozen=True)
class EnsemblePrediction:
    mean: float
    std: float


def ensemble_predict(heads: list[DefectionHead], features: dict[str, float]) -> EnsemblePrediction:
    """Mean + std of defection probability across the seed ensemble."""
    probs = [head.probability(features) for head in heads]
    arr = np.asarray(probs, dtype=np.float64)
    return EnsemblePrediction(mean=float(arr.mean()), std=float(arr.std()))


@dataclass(frozen=True)
class EnsembleAuc:
    mean_auc: float
    std_auc: float
    per_seed_auc: list[float]
    n_seeds: int

    def as_dict(self) -> dict[str, object]:
        return {
            "mean_auc": self.mean_auc,
            "std_auc": self.std_auc,
            "per_seed_auc": self.per_seed_auc,
            "n_seeds": self.n_seeds,
        }


def ensemble_auc(
    heads: list[DefectionHead], eval_records: list[VoteRecord], profiles: PartyProfiles
) -> EnsembleAuc:
    """Per-seed defection AUC with mean ± std (uncertainty band on the headline metric)."""
    labels = [defected(r) for r in eval_records]
    per_seed: list[float] = []
    for head in heads:
        scores = [head.probability(defection_features(r, profiles)) for r in eval_records]
        per_seed.append(ranking_metrics(scores, labels).auc)
    arr = np.asarray(per_seed, dtype=np.float64)
    return EnsembleAuc(
        mean_auc=float(arr.mean()),
        std_auc=float(arr.std()),
        per_seed_auc=per_seed,
        n_seeds=len(heads),
    )


def _canonical_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def content_address(payload: dict[str, object]) -> str:
    """sha256 of the canonical JSON of a checkpoint payload."""
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def ensemble_payload(heads: list[DefectionHead], *, model_name: str) -> dict[str, object]:
    """A serializable, deterministic payload for the seed ensemble."""
    return {
        "model_name": model_name,
        "n_seeds": len(heads),
        "heads": [
            {"intercept": head.intercept, "coefficients": dict(sorted(head.coefficients.items()))}
            for head in heads
        ],
    }


def checkpoint_path(root: Path, digest: str) -> Path:
    """Content-addressed path ``sha256/<aa>/<bb>/<full>.json``."""
    return root / "sha256" / digest[:2] / digest[2:4] / f"{digest}.json"


def pin_checkpoint(root: Path, payload: dict[str, object]) -> tuple[str, Path]:
    """Write the payload to its content-addressed path; return (digest, path)."""
    digest = content_address(payload)
    path = checkpoint_path(root, digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return digest, path


def verify_checkpoint(path: Path, expected_digest: str) -> bool:
    """Recompute the content address of a pinned checkpoint and compare."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return content_address(payload) == expected_digest
