"""Local, deterministic text vectors; no model download or paid API.

Indexing and query-time retrieval share the feature-hashing implementation.
This lightweight representation is not a learned semantic model.
"""

from src.core.text_hash import DEFAULT_DIM, FeatureHashEmbedder as LocalTextEmbedder


def default_text_embedder() -> LocalTextEmbedder:
    return LocalTextEmbedder(dim=DEFAULT_DIM)
