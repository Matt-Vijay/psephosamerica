from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from src.core.files import write_bytes_atomic


def write_json_artifact(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    write_bytes_atomic(path, encoded)
    return hashlib.sha256(encoded).hexdigest()


def attach_optional_verification_output(
    args: Any,
    result: dict[str, Any],
) -> dict[str, Any]:
    output_arg = getattr(args, "output", None)
    if output_arg is None:
        return result
    output = Path(output_arg)
    output_sha256 = write_json_artifact(output, result)
    result["output"] = str(output)
    result["output_sha256"] = output_sha256
    return result
