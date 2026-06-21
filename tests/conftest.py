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


@pytest.fixture(autouse=True)
def _isolate_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the GraphRAG LLM path offline and fast by default.

    The repo's gitignored ``.env`` carries a live OPENROUTER_API_KEY, and
    ``GraphRagAnswerer.from_environment`` loads it. Without isolation, any test
    that builds an answerer from the environment would pick the live backend,
    hit the (guarded) network, and burn real backoff sleeps. We neutralise the
    .env loader and clear the keys so tests are deterministic; tests that
    exercise the live fallback chain inject their own poster + sleep patches.
    """
    monkeypatch.setattr("src.query.graph_rag.load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Belt-and-suspenders: never actually sleep during a test.
    monkeypatch.setattr("src.query.graph_rag.time.sleep", lambda *a, **k: None)
