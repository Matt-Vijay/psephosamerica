"""LOCUS ordinance analyses — the cross-jurisdiction flagship (V8 #4).

Track A landed the **LOCUS v1 corpus** (2,207,679 municipal/county ordinance
provisions across thousands of US jurisdictions) as ``data/exports/locus``.
Each provision carries, in the content sidecar
(``ordinance_content.jsonl``): the raw ``text``, the LOCUS ``topic`` and legal
``function``, the canonical ``jurisdiction_id`` (e.g. ``us-ca-city-oakland``),
and four continuous ``dimension_scores`` — ``opacity``, ``paternalism``,
``enforcement_discretion``, ``problem_salience``.

This is exactly the substrate the LOCUS authors stopped at (text + scores). The
analyses here add the **cross-jurisdiction comparison** on top — the thing
LOCUS itself did not do:

* :func:`opacity_paternalism_map` — per-jurisdiction mean opacity / paternalism
  / enforcement-discretion (the "opacity/paternalism maps").
* :func:`near_duplicate_ordinances` — model-legislation diffusion: ordinances
  whose **text** is near-identical *across different jurisdictions*, found with
  MinHash + LSH banding over token shingles (numpy-only, no embeddings needed —
  so it works on the ``pending`` LOCUS rows today, before enrichment).
* :func:`topic_diffusion` — how a LOCUS topic spreads across jurisdictions.

All findings are cited (LOCUS ``source_url`` + ``content_sha256``), honor the
CC-BY-NC-4.0 attribution, and stream the 2.2M-row sidecar without loading it all
into memory. Pure stdlib + numpy.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

LOCUS_CONTENT = Path("data/exports/locus/ordinance_content.jsonl")
LOCUS_RECORDS = Path("data/exports/locus/records.jsonl")
LOCUS_SOURCE_URL = "https://huggingface.co/datasets/LocalLaws/LOCUS-v1"
LOCUS_LICENSE = "CC-BY-NC-4.0 (non-commercial)"

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_DIMENSIONS = ("opacity", "paternalism", "enforcement_discretion", "problem_salience")


@dataclass(frozen=True)
class Ordinance:
    """One LOCUS provision: id, jurisdiction, topic, text, dimension scores."""

    canonical_id: str
    jurisdiction_id: str
    topic: str | None
    function: str | None
    text: str
    dimensions: dict[str, float]

    @property
    def citation(self) -> dict[str, str | None]:
        return {
            "source_url": LOCUS_SOURCE_URL,
            "content_sha256": hashlib.sha256(self.text.encode("utf-8")).hexdigest(),
            "license": LOCUS_LICENSE,
            "jurisdiction_id": self.jurisdiction_id,
        }


def _parse_dimensions(raw: object) -> dict[str, float]:
    if isinstance(raw, dict):
        return {k: float(v) for k, v in raw.items() if _is_number(v)}
    if isinstance(raw, str) and raw:
        try:
            parsed = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return {}
        if isinstance(parsed, dict):
            return {k: float(v) for k, v in parsed.items() if _is_number(v)}
    return {}


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def iter_ordinances(path: Path = LOCUS_CONTENT, *, limit: int | None = None) -> Iterator[Ordinance]:
    """Stream LOCUS ordinances from the content sidecar (memory-bounded)."""
    if not path.exists():
        return
    count = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            yield Ordinance(
                canonical_id=str(record.get("canonical_id", "")),
                jurisdiction_id=str(record.get("jurisdiction_id", "") or "unknown"),
                topic=_opt_str(record.get("topic")),
                function=_opt_str(record.get("function")),
                text=str(record.get("text", "")),
                dimensions=_parse_dimensions(record.get("dimension_scores")),
            )
            count += 1
            if limit is not None and count >= limit:
                return


def _opt_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text and text != "None" else None


# -- opacity / paternalism map -----------------------------------------


@dataclass(frozen=True)
class JurisdictionDimensions:
    """Per-jurisdiction mean dimension scores over its ordinances (cited corpus)."""

    jurisdiction_id: str
    ordinance_count: int
    means: dict[str, float]


def opacity_paternalism_map(
    path: Path = LOCUS_CONTENT,
    *,
    limit: int | None = None,
    min_ordinances: int = 25,
    top: int = 50,
    rank_by: str = "opacity",
) -> list[JurisdictionDimensions]:
    """Mean opacity/paternalism/etc. per jurisdiction, ranked by ``rank_by``.

    This is the "opacity / paternalism map": which jurisdictions write the most
    opaque or paternalistic law, aggregated from LOCUS's per-provision scores.
    """
    sums: dict[str, dict[str, float]] = defaultdict(lambda: dict.fromkeys(_DIMENSIONS, 0.0))
    counts: dict[str, int] = defaultdict(int)
    for ordinance in iter_ordinances(path, limit=limit):
        counts[ordinance.jurisdiction_id] += 1
        bucket = sums[ordinance.jurisdiction_id]
        for dim in _DIMENSIONS:
            if dim in ordinance.dimensions:
                bucket[dim] += ordinance.dimensions[dim]
    results: list[JurisdictionDimensions] = []
    for jurisdiction, n in counts.items():
        if n < min_ordinances:
            continue
        means = {dim: sums[jurisdiction][dim] / n for dim in _DIMENSIONS}
        results.append(
            JurisdictionDimensions(jurisdiction_id=jurisdiction, ordinance_count=n, means=means)
        )
    key = rank_by if rank_by in _DIMENSIONS else "opacity"
    results.sort(key=lambda r: (-r.means.get(key, 0.0), r.jurisdiction_id))
    return results[:top]


# -- cross-jurisdiction near-duplicate (MinHash + LSH) -----------------


def _shingles(text: str, *, k: int = 5) -> frozenset[int]:
    """Hashed k-word shingles of the text (for MinHash Jaccard estimation)."""
    tokens = _TOKEN_RE.findall(text.lower())
    if len(tokens) < k:
        joined = " ".join(tokens)
        return frozenset({_hash_token(joined)}) if joined else frozenset()
    return frozenset(_hash_token(" ".join(tokens[i : i + k])) for i in range(len(tokens) - k + 1))


def _hash_token(token: str) -> int:
    return int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "big")


def _minhash(shingles: frozenset[int], *, num_perm: int, seeds: np.ndarray) -> np.ndarray:
    """MinHash signature: per-permutation minimum of (shingle XOR seed)."""
    if not shingles:
        return np.full(num_perm, np.iinfo(np.uint64).max, dtype=np.uint64)
    arr = np.fromiter(shingles, dtype=np.uint64, count=len(shingles))
    # signature[p] = min over shingles of (shingle XOR seed[p])
    xored = np.bitwise_xor(arr[None, :], seeds[:, None])
    signature: np.ndarray = xored.min(axis=1)
    return signature


@dataclass(frozen=True)
class OrdinanceDuplicate:
    """A near-duplicate ordinance pair across jurisdictions (estimated Jaccard)."""

    jaccard: float
    a: dict[str, object]
    b: dict[str, object]
    cross_jurisdiction: bool


@dataclass
class _Candidate:
    canonical_id: str
    jurisdiction_id: str
    topic: str | None
    text: str
    signature: np.ndarray


def near_duplicate_ordinances(
    path: Path = LOCUS_CONTENT,
    *,
    limit: int | None = 50000,
    num_perm: int = 64,
    bands: int = 16,
    threshold: float = 0.7,
    max_pairs: int = 200,
    cross_jurisdiction_only: bool = True,
    min_tokens: int = 12,
    seed: int = 1234567,
) -> list[OrdinanceDuplicate]:
    """Find near-duplicate ordinance *text* across jurisdictions via MinHash+LSH.

    This is the model-legislation-diffusion flagship that works **today** on the
    ``pending`` LOCUS rows (no embeddings required): the same ordinance text
    adopted by many cities. LSH banding keeps it near-linear instead of O(n^2);
    candidate pairs are confirmed by estimated MinHash Jaccard >= ``threshold``.

    ``limit`` bounds how many provisions are scanned (the full corpus is 2.2M);
    raise it for completeness, lower for a fast demo.
    """
    rng = np.random.default_rng(seed)
    seeds = rng.integers(1, np.iinfo(np.uint64).max, size=num_perm, dtype=np.uint64)
    rows_per_band = max(1, num_perm // bands)

    candidates: list[_Candidate] = []
    buckets: dict[tuple[int, bytes], list[int]] = defaultdict(list)
    for ordinance in iter_ordinances(path, limit=limit):
        if len(_TOKEN_RE.findall(ordinance.text)) < min_tokens:
            continue
        signature = _minhash(_shingles(ordinance.text), num_perm=num_perm, seeds=seeds)
        index = len(candidates)
        candidates.append(
            _Candidate(
                canonical_id=ordinance.canonical_id,
                jurisdiction_id=ordinance.jurisdiction_id,
                topic=ordinance.topic,
                text=ordinance.text,
                signature=signature,
            )
        )
        for band in range(bands):
            chunk = signature[band * rows_per_band : (band + 1) * rows_per_band]
            buckets[(band, chunk.tobytes())].append(index)

    seen_pairs: set[tuple[int, int]] = set()
    duplicates: list[OrdinanceDuplicate] = []
    for members in buckets.values():
        if len(members) < 2:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                pair = (a, b) if a < b else (b, a)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                ca, cb = candidates[a], candidates[b]
                cross = ca.jurisdiction_id != cb.jurisdiction_id
                if cross_jurisdiction_only and not cross:
                    continue
                jaccard = float((ca.signature == cb.signature).mean())
                if jaccard < threshold:
                    continue
                duplicates.append(
                    OrdinanceDuplicate(
                        jaccard=jaccard,
                        a=_candidate_brief(ca),
                        b=_candidate_brief(cb),
                        cross_jurisdiction=cross,
                    )
                )
                if len(duplicates) >= max_pairs * 4:
                    break
    duplicates.sort(key=lambda d: -d.jaccard)
    return duplicates[:max_pairs]


def _candidate_brief(candidate: _Candidate) -> dict[str, object]:
    return {
        "canonical_id": candidate.canonical_id,
        "jurisdiction_id": candidate.jurisdiction_id,
        "topic": candidate.topic,
        "text_excerpt": candidate.text[:160],
        "citation": {
            "source_url": LOCUS_SOURCE_URL,
            "content_sha256": hashlib.sha256(candidate.text.encode("utf-8")).hexdigest(),
            "license": LOCUS_LICENSE,
        },
    }


# -- topic diffusion ----------------------------------------------------


@dataclass(frozen=True)
class TopicDiffusion:
    """How widely a LOCUS topic spreads across jurisdictions."""

    topic: str
    jurisdiction_count: int
    ordinance_count: int
    example_jurisdictions: tuple[str, ...] = field(default=())


def topic_diffusion(
    path: Path = LOCUS_CONTENT, *, limit: int | None = None, top: int = 30
) -> list[TopicDiffusion]:
    """Rank LOCUS topics by how many distinct jurisdictions legislate them."""
    juris_by_topic: dict[str, set[str]] = defaultdict(set)
    count_by_topic: dict[str, int] = defaultdict(int)
    for ordinance in iter_ordinances(path, limit=limit):
        if ordinance.topic is None:
            continue
        juris_by_topic[ordinance.topic].add(ordinance.jurisdiction_id)
        count_by_topic[ordinance.topic] += 1
    results = [
        TopicDiffusion(
            topic=topic,
            jurisdiction_count=len(juris),
            ordinance_count=count_by_topic[topic],
            example_jurisdictions=tuple(sorted(juris)[:5]),
        )
        for topic, juris in juris_by_topic.items()
    ]
    results.sort(key=lambda r: (-r.jurisdiction_count, -r.ordinance_count, r.topic))
    return results[:top]


__all__ = [
    "Ordinance",
    "JurisdictionDimensions",
    "OrdinanceDuplicate",
    "TopicDiffusion",
    "LOCUS_CONTENT",
    "LOCUS_SOURCE_URL",
    "LOCUS_LICENSE",
    "iter_ordinances",
    "opacity_paternalism_map",
    "near_duplicate_ordinances",
    "topic_diffusion",
]
