from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.runtime.json_artifacts import (
    attach_optional_verification_output,
    write_json_artifact,
)


class _Args:
    def __init__(self, output: Path | None) -> None:
        self.output = output


def test_write_json_artifact_writes_deterministic_bytes_and_sha(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "artifact.json"
    payload = {"b": 2, "a": 1}

    digest = write_json_artifact(output, payload)

    expected = json.dumps(payload, sort_keys=True).encode("utf-8")
    assert output.read_bytes() == expected
    assert digest == hashlib.sha256(expected).hexdigest()
    assert list(output.parent.iterdir()) == [output]


def test_write_json_artifact_replaces_existing_file(tmp_path: Path) -> None:
    output = tmp_path / "artifact.json"
    output.write_text("old", encoding="utf-8")

    write_json_artifact(output, {"ok": True})

    assert json.loads(output.read_text(encoding="utf-8")) == {"ok": True}
    assert list(tmp_path.iterdir()) == [output]


def test_write_json_artifact_does_not_write_directly_to_final_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output = tmp_path / "artifact.json"
    original_write_bytes = Path.write_bytes

    def guarded_write_bytes(path: Path, data: bytes) -> int:
        if path == output:
            raise AssertionError("direct final-path write")
        return original_write_bytes(path, data)

    monkeypatch.setattr(Path, "write_bytes", guarded_write_bytes)

    write_json_artifact(output, {"ok": True})

    assert json.loads(output.read_text(encoding="utf-8")) == {"ok": True}


def test_attach_optional_verification_output_writes_and_annotates_result(
    tmp_path: Path,
) -> None:
    output = tmp_path / "artifact.json"
    result = {"ok": True}

    annotated = attach_optional_verification_output(_Args(output), result)

    assert annotated is result
    assert annotated["output"] == str(output)
    assert annotated["output_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert json.loads(output.read_text(encoding="utf-8")) == {"ok": True}


def test_attach_optional_verification_output_noops_without_output() -> None:
    result = {"ok": True}

    assert attach_optional_verification_output(_Args(None), result) is result
    assert result == {"ok": True}
