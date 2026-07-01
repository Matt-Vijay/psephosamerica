"""Runtime mock helpers for test_main.py and friends.

Provides a lightweight mock runtime and common patch shortcuts so
every test file doesn't have to reinvent them.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


_MOD = "src.runtime.main"


def mock_runtime() -> MagicMock:
    """Return a MagicMock shaped like PsephosAmericaRuntime.

    Has .context (MagicMock) and .publish_root (MagicMock) pre-set.
    """
    rt = MagicMock()
    rt.context = MagicMock()
    return rt


def patch_as_json_passthrough(module: str = _MOD):
    """Patch as_json so it returns its first arg unchanged (dict→dict).

    Use as a context manager::

        with patch_as_json_passthrough():
            result = run(["status"])
    """
    return patch(f"{module}.as_json", side_effect=lambda obj: obj)
