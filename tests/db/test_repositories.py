"""Unit tests for connection-helper behavior in src/db/repositories.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.db.repositories import fetch_all, rollback_if_available


def test_rollback_if_available_invokes_rollback_when_callable() -> None:
    conn = MagicMock()
    rollback_if_available(conn)
    conn.rollback.assert_called_once_with()


def test_rollback_if_available_is_noop_without_callable_rollback() -> None:
    # An object whose ``rollback`` attribute is absent/None must not raise.
    rollback_if_available(object())


def test_fetch_all_recovers_test_connection_and_reraises_on_error() -> None:
    conn = MagicMock()
    conn.cursor.side_effect = RuntimeError("cursor boom")
    with patch("src.db.repositories._recover_test_connection") as recover:
        with pytest.raises(RuntimeError, match="cursor boom"):
            fetch_all(conn, "SELECT 1", ())
    recover.assert_called_once_with(conn)
