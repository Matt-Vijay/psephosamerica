"""Tests for the network guard itself.

Validates that:
- The guard blocks real socket.connect calls.
- The guard restores the original connect on uninstall.
- The guard works as a context manager.
- Pure filesystem operations are unaffected.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from tests.support.network_guard import NetworkGuard, NetworkGuardError, _ORIGINAL_CONNECT


# ---------------------------------------------------------------------------
# Guard blocks connections
# ---------------------------------------------------------------------------


class TestNetworkGuardBlocks:
    """NetworkGuard.install() makes socket.connect raise NetworkGuardError."""

    def test_install_replaces_connect(self) -> None:
        guard = NetworkGuard()
        guard.install()
        try:
            assert socket.socket.connect is not _ORIGINAL_CONNECT
        finally:
            guard.uninstall()

    def test_blocked_connect_raises(self) -> None:
        guard = NetworkGuard()
        guard.install()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            with pytest.raises(NetworkGuardError, match="real network connection"):
                sock.connect(("127.0.0.1", 80))
            sock.close()
        finally:
            guard.uninstall()

    def test_error_includes_address(self) -> None:
        guard = NetworkGuard()
        guard.install()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            with pytest.raises(NetworkGuardError, match="127.0.0.1"):
                sock.connect(("127.0.0.1", 9999))
            sock.close()
        finally:
            guard.uninstall()


# ---------------------------------------------------------------------------
# Guard restores on uninstall
# ---------------------------------------------------------------------------


class TestNetworkGuardRestore:
    """NetworkGuard.uninstall() restores the original socket.connect."""

    def test_uninstall_restores_connect(self) -> None:
        guard = NetworkGuard()
        guard.install()
        guard.uninstall()
        assert socket.socket.connect is _ORIGINAL_CONNECT

    def test_double_uninstall_is_safe(self) -> None:
        guard = NetworkGuard()
        guard.install()
        guard.uninstall()
        guard.uninstall()  # should not raise
        assert socket.socket.connect is _ORIGINAL_CONNECT

    def test_double_install_is_safe(self) -> None:
        guard = NetworkGuard()
        guard.install()
        guard.install()  # idempotent
        guard.uninstall()
        assert socket.socket.connect is _ORIGINAL_CONNECT


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


class TestNetworkGuardContextManager:
    """NetworkGuard works as a context manager."""

    def test_context_manager_blocks(self) -> None:
        with NetworkGuard():
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            with pytest.raises(NetworkGuardError):
                sock.connect(("127.0.0.1", 80))
            sock.close()

    def test_context_manager_restores(self) -> None:
        with NetworkGuard():
            pass
        assert socket.socket.connect is _ORIGINAL_CONNECT

    def test_context_manager_restores_on_exception(self) -> None:
        try:
            with NetworkGuard():
                raise ValueError("boom")
        except ValueError:
            pass
        assert socket.socket.connect is _ORIGINAL_CONNECT


# ---------------------------------------------------------------------------
# Filesystem operations are unaffected
# ---------------------------------------------------------------------------


class TestFilesystemUnaffected:
    """Pure filesystem ops work fine under the guard."""

    def test_file_write_read_under_guard(self, tmp_path: Path) -> None:
        with NetworkGuard():
            p = tmp_path / "test.txt"
            p.write_text("hello")
            assert p.read_text() == "hello"

    def test_mkdir_under_guard(self, tmp_path: Path) -> None:
        with NetworkGuard():
            d = tmp_path / "subdir"
            d.mkdir()
            assert d.is_dir()

    def test_glob_under_guard(self, tmp_path: Path) -> None:
        with NetworkGuard():
            (tmp_path / "a.txt").write_text("a")
            (tmp_path / "b.txt").write_text("b")
            assert len(list(tmp_path.glob("*.txt"))) == 2
