"""Shared argument helpers for the operator CLI."""

from __future__ import annotations

import argparse
import datetime


# ---------------------------------------------------------------------------
# Argument type helpers
# ---------------------------------------------------------------------------


def _parse_date(value: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid date '{value}'. Expected YYYY-MM-DD.")
