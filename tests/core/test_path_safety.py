from __future__ import annotations

from pathlib import Path

import pytest

from src.core.path_safety import (
    is_confined_relative_path,
    require_confined_relative_path,
    safe_join_confined,
)


def test_is_confined_relative_path_accepts_nested_relative_paths() -> None:
    assert is_confined_relative_path("house/2024/12345.pdf") is True


@pytest.mark.parametrize(
    "path",
    [
        "",
        ".",
        "   ",
        "/tmp/outside.pdf",
        "../outside.pdf",
        "safe/../outside.pdf",
        r"safe\outside.pdf",
    ],
)
def test_is_confined_relative_path_rejects_unsafe_paths(path: str) -> None:
    assert is_confined_relative_path(path) is False


def test_require_confined_relative_path_returns_valid_path() -> None:
    assert (
        require_confined_relative_path("members.json", label="manifest.members") == "members.json"
    )


def test_require_confined_relative_path_raises_with_label() -> None:
    with pytest.raises(ValueError, match="manifest.members"):
        require_confined_relative_path("../members.json", label="manifest.members")


def test_safe_join_confined_allows_missing_nested_path(tmp_path: Path) -> None:
    assert (
        safe_join_confined(
            tmp_path,
            "members/alice.json",
            label="planned.path",
        )
        == tmp_path / "members" / "alice.json"
    )


def test_safe_join_confined_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "members").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="planned.path"):
        safe_join_confined(tmp_path, "members/alice.json", label="planned.path")
