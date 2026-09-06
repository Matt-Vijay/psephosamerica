from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.core.files import sha256_file, write_bytes_atomic, write_text_atomic


def test_hash_is_streamed_and_matches_exact_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    payload = bytes(range(256)) * 20
    path = tmp_path / "source"
    path.write_bytes(payload)

    def no_whole_file_read(self: Path) -> bytes:
        raise AssertionError("hashing must not load the whole file")

    monkeypatch.setattr(Path, "read_bytes", no_whole_file_read)
    assert sha256_file(path, chunk_size=127) == hashlib.sha256(payload).hexdigest()
    for invalid_size in (0, -1):
        with pytest.raises(ValueError, match="chunk_size must be positive"):
            sha256_file(path, chunk_size=invalid_size)


@pytest.mark.parametrize("text", [False, True])
def test_atomic_replace_preserves_bytes_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: bool
):
    path = tmp_path / "artifact"
    path.write_bytes(b"old")

    def write() -> None:
        if text:
            write_text_atomic(path, "new café\n")
        else:
            write_bytes_atomic(path, b"new caf\xc3\xa9\n")

    def reject_replace(self: Path, target: Path) -> Path:
        assert self != path and target == path
        raise OSError("replace failed")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", reject_replace)
        with pytest.raises(OSError, match="replace failed"):
            write()
    assert path.read_bytes() == b"old"
    assert list(tmp_path.iterdir()) == [path]

    write()
    assert path.read_text(encoding="utf-8") == "new café\n"
    assert list(tmp_path.iterdir()) == [path]

    with pytest.raises(FileNotFoundError):
        write_bytes_atomic(tmp_path / "missing-parent" / "artifact", b"content")
