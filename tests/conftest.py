"""Root conftest — shared pytest fixtures.

Fixtures here are auto-discovered by every test in the tree.
Only truly cross-cutting fixtures belong here; module-specific
helpers live in tests/support/.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from tests.support.network_guard import NetworkGuard


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    """Alias for tmp_path — use when the test just needs a throwaway dir."""
    return tmp_path


@pytest.fixture(autouse=True)
def _block_network() -> None:
    """Block real outbound network connections in every test.

    Uses socket-level guard so httpx.MockTransport and in-memory
    fixtures continue to work (they never call socket.connect).
    Any test that accidentally tries a real connection will get a
    loud NetworkGuardError instead of hanging or hitting the network.
    """
    guard = NetworkGuard()
    guard.install()
    yield  # type: ignore[misc]
    guard.uninstall()
