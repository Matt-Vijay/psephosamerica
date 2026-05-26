from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4


def write_json_artifact(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temp_path.write_bytes(encoded)
        temp_path.replace(path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
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
