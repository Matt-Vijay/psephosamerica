"""Test-side network guard — blocks real outbound connections.

Installs a socket-level guard that raises ``NetworkGuardError`` whenever
a test tries to open a real TCP/UDP connection.  The guard is designed to
coexist with httpx.MockTransport and other in-memory fixtures because
those never call ``socket.socket.connect``.

Usage — as a pytest fixture (auto-applied via conftest)::

    @pytest.fixture(autouse=True)
    def _block_network(block_network):
        yield

Or applied directly::

    guard = NetworkGuard()
    guard.install()
    ...
    guard.uninstall()

Design constraints:
- No ``sys.modules`` tricks.
- Pure filesystem tests are unaffected (no socket calls).
- Failures are loud and immediate.
"""

from __future__ import annotations

import socket


class NetworkGuardError(Exception):
    """Raised when a test tries to make a real network connection."""


_ORIGINAL_CONNECT = socket.socket.connect


class NetworkGuard:
    """Context-manager / install-uninstall guard that blocks socket.connect."""

    def __init__(self) -> None:
        self._active = False

    # -- public API -----------------------------------------------------------

    def install(self) -> None:
        if self._active:
            return
        socket.socket.connect = _blocked_connect  # type: ignore[assignment]
        self._active = True

    def uninstall(self) -> None:
        if not self._active:
            return
        socket.socket.connect = _ORIGINAL_CONNECT  # type: ignore[assignment]
        self._active = False

    # -- context-manager protocol ---------------------------------------------

    def __enter__(self) -> NetworkGuard:
        self.install()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.uninstall()


def _blocked_connect(self: socket.socket, address: object) -> None:  # noqa: ANN001
    raise NetworkGuardError(
        f"Test tried to open a real network connection to {address!r}. "
        "All tests must run offline — use httpx.MockTransport or "
        "in-memory fixtures instead of real HTTP calls."
    )
