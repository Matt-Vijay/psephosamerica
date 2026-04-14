"""Scoring helpers for Open Pact.

Public API re-exported from sub-modules for convenient import.
"""

from src.scoring.deltas import diff_many_members, diff_one_member, filter_changed
from src.scoring.snapshots import (
    build_snapshot_row,
    build_snapshot_rows,
    clamp,
    compute_dimension_scores,
    compute_score_total,
    group_deltas_by_dimension,
)

__all__ = [
    "build_snapshot_row",
    "build_snapshot_rows",
    "clamp",
    "compute_dimension_scores",
    "compute_score_total",
    "diff_many_members",
    "diff_one_member",
    "filter_changed",
    "group_deltas_by_dimension",
]
