"""Root conftest — shared pytest fixtures.

Fixtures here are auto-discovered by every test in the tree.
Only truly cross-cutting fixtures belong here; module-specific
helpers live in tests/support/.
"""

from __future__ import annotations

import pytest
from pathlib import Path


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    """Alias for tmp_path — use when the test just needs a throwaway dir."""
    return tmp_path
