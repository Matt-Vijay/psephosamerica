"""Query vectors use the same feature space as indexed graph text."""

from src.core.text_hash import DEFAULT_DIM, FeatureHashEmbedder as QueryEmbedder

__all__ = ["QueryEmbedder", "DEFAULT_DIM"]
