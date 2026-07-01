"""Runtime environment preflight commands."""

from __future__ import annotations

import hashlib
import json
import os
import re

from pathlib import Path
from typing import Any

from src.runtime.commands._shared import (
    _DEFAULT_RUNTIME_ENV_REQUIREMENTS,
    _attach_optional_verification_output,
    _is_non_negative_plain_int,
    _is_plain_int,
    _is_sha256_hex,
    _required_string_list,
    _string_list,
    _write_bytes_artifact,
    _write_json_artifact,
)


def _handle_runtime_env_preflight(args: Any) -> dict[str, Any]:
    required_env = _required_string_list(getattr(args, "require_env", None))
    if not required_env:
        required_env = list(_DEFAULT_RUNTIME_ENV_REQUIREMENTS)
    dotenv_path = Path(args.dotenv) if getattr(args, "dotenv", None) is not None else None
    dotenv_keys, dotenv_issues = _read_dotenv_key_presence(dotenv_path)
    checks = [
        _runtime_env_check(name, dotenv_keys=dotenv_keys)
        for name in sorted(dict.fromkeys(required_env))
    ]
    missing_env = [check["name"] for check in checks if not bool(check["visible_to_process"])]
    dotenv_only = [
        check["name"]
        for check in checks
        if bool(check["present_in_dotenv"]) and not bool(check["visible_to_process"])
    ]
    next_actions_by_env = _runtime_env_preflight_next_actions_by_env(
        checks,
        dotenv_path=dotenv_path,
    )
    next_actions = sorted(
        {action for actions in next_actions_by_env.values() for action in actions}
    )
    result: dict[str, Any] = {
        "ok": not missing_env and not dotenv_issues,
        "command": "runtime-env-preflight",
        "checked": len(checks),
        "checks": checks,
        "missing_env": missing_env,
        "missing_env_count": len(missing_env),
        "next_actions": next_actions,
        "next_actions_by_env": next_actions_by_env,
        "dotenv": {
            "path": str(dotenv_path) if dotenv_path is not None else None,
            "exists": dotenv_path.is_file() if dotenv_path is not None else None,
            "dotenv_only": dotenv_only,
            "dotenv_only_count": len(dotenv_only),
            "create_from_template_action": None,
        },
        "issues": dotenv_issues,
        "issue_count": len(dotenv_issues),
        "run_metadata": {
            "command": "runtime-env-preflight",
            "source_state": {
                "checked_env": [check["name"] for check in checks],
                "missing_env": missing_env,
                "dotenv_only": dotenv_only,
                "next_actions": next_actions,
            },
        },
    }
    template_output = (
        Path(args.template_output) if getattr(args, "template_output", None) is not None else None
    )
    if template_output is not None:
        template_sha256 = _write_runtime_env_template(template_output, required_env)
        result["template_output"] = str(template_output)
        result["template_output_sha256"] = template_sha256
        create_dotenv_action = _runtime_env_create_dotenv_from_template_action(
            dotenv_path=dotenv_path,
            template_output=template_output,
            missing_env=missing_env,
        )
        if create_dotenv_action is not None:
            result["dotenv"]["create_from_template_action"] = create_dotenv_action
            result["next_actions"] = sorted(
                {*_string_list(result.get("next_actions")), create_dotenv_action}
            )
            result["run_metadata"]["source_state"]["next_actions"] = result["next_actions"]
    output = Path(args.output) if getattr(args, "output", None) is not None else None
    if output is not None:
        output_sha256 = _write_json_artifact(output, result)
        result["output"] = str(output)
        result["output_sha256"] = output_sha256
    return result


def _write_runtime_env_template(path: Path, required_env: list[str]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Psephos America runtime environment template.",
        "# Fill values locally; do not commit secrets.",
        *[f"{name}=" for name in sorted(dict.fromkeys(required_env))],
        "",
    ]
    encoded = "\n".join(lines).encode("utf-8")
    return _write_bytes_artifact(path, encoded)


def _runtime_env_check(
    name: str,
    *,
    dotenv_keys: set[str],
) -> dict[str, Any]:
    value = os.environ.get(name)
    visible = value is not None and value != ""
    present_in_dotenv = name in dotenv_keys
    if visible:
        source = "process"
    elif present_in_dotenv:
        source = "dotenv_only"
    else:
        source = "absent"
    return {
        "name": name,
        "visible_to_process": visible,
        "present_in_dotenv": present_in_dotenv,
        "source": source,
    }


def _runtime_env_preflight_next_actions_by_env(
    checks: list[dict[str, Any]],
    *,
    dotenv_path: Path | None,
) -> dict[str, list[str]]:
    actions_by_env: dict[str, list[str]] = {}
    dotenv_action = f"source_dotenv:{dotenv_path}" if dotenv_path is not None else None
    for check in checks:
        name = str(check.get("name"))
        if bool(check.get("visible_to_process")):
            continue
        if bool(check.get("present_in_dotenv")) and dotenv_action is not None:
            actions_by_env[name] = [dotenv_action]
        else:
            actions_by_env[name] = [f"set_env:{name}"]
    return actions_by_env


def _runtime_env_create_dotenv_from_template_action(
    *,
    dotenv_path: Path | None,
    template_output: Path,
    missing_env: list[str],
) -> str | None:
    if dotenv_path is None or dotenv_path.is_file() or not missing_env:
        return None
    return f"create_dotenv_from_template:{dotenv_path}:{template_output}"


def _handle_verify_runtime_env_preflight(args: Any) -> dict[str, Any]:
    artifact_path = Path(args.artifact)

    def failure_result(*, issues: list[str]) -> dict[str, Any]:
        return _attach_optional_verification_output(
            args,
            {
                "ok": False,
                "command": "verify-runtime-env-preflight",
                "artifact": str(artifact_path),
                "issues": issues,
                "issue_count": len(issues),
                "quality_gate_failures": [],
                "quality_gate_failure_count": 0,
                "run_metadata": _runtime_env_preflight_verify_run_metadata(
                    args,
                    artifact_path=artifact_path,
                ),
            },
        )

    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return failure_result(issues=[f"failed to load artifact: {exc}"])
    if not isinstance(payload, dict):
        return failure_result(issues=["artifact must be an object"])

    issues: list[str] = []
    quality_gate_failures: list[str] = []
    if payload.get("command") != "runtime-env-preflight":
        issues.append("artifact_command_mismatch")
    issues.extend(_runtime_env_preflight_count_issues(payload))

    checks = payload.get("checks")
    checks_list = checks if isinstance(checks, list) else []
    if not isinstance(checks, list):
        issues.append("checks_missing")
    if _is_non_negative_plain_int(payload.get("checked")) and payload.get("checked") != len(
        checks_list
    ):
        issues.append("checked_count_mismatch")

    missing_env = _string_list(payload.get("missing_env"))
    if _is_non_negative_plain_int(payload.get("missing_env_count")) and payload.get(
        "missing_env_count"
    ) != len(missing_env):
        issues.append("missing_env_count_mismatch")

    dotenv = payload.get("dotenv")
    dotenv_only: list[str] = []
    if isinstance(dotenv, dict):
        dotenv_only = _string_list(dotenv.get("dotenv_only"))
        if _is_non_negative_plain_int(dotenv.get("dotenv_only_count")) and dotenv.get(
            "dotenv_only_count"
        ) != len(dotenv_only):
            issues.append("dotenv_only_count_mismatch")
    else:
        issues.append("dotenv_missing")

    artifact_issues = _string_list(payload.get("issues"))
    if _is_non_negative_plain_int(payload.get("issue_count")) and payload.get("issue_count") != len(
        artifact_issues
    ):
        issues.append("issue_count_mismatch")

    next_actions = _string_list(payload.get("next_actions"))
    next_actions_by_env = _runtime_env_next_actions_by_env_summary(
        payload.get("next_actions_by_env")
    )
    if bool(getattr(args, "require_next_actions", False)) and missing_env:
        for name in missing_env:
            if name not in next_actions_by_env and not any(
                action.endswith(f":{name}") for action in next_actions
            ):
                quality_gate_failures.append(f"missing_next_action:{name}")
        if not next_actions:
            quality_gate_failures.append("missing_next_actions")

    template_sha256 = None
    template_output_sha256_invalid = False
    template_output = payload.get("template_output")
    template_output_sha256 = payload.get("template_output_sha256")
    if bool(getattr(args, "require_template_output", False)):
        if not isinstance(template_output, str) or not template_output:
            quality_gate_failures.append("template_output_missing")
        elif not Path(template_output).is_file():
            quality_gate_failures.append("template_output_file_missing")
        else:
            template_sha256 = hashlib.sha256(Path(template_output).read_bytes()).hexdigest()
            if not isinstance(template_output_sha256, str) or not _is_sha256_hex(
                template_output_sha256
            ):
                template_output_sha256_invalid = True
                quality_gate_failures.append("template_output_sha256_invalid")
            elif template_sha256 != template_output_sha256:
                quality_gate_failures.append("template_output_sha256_mismatch")

    secret_failures: list[str] = []
    if bool(getattr(args, "require_no_secret_literals", False)):
        secret_failures = _runtime_env_preflight_secret_literal_failures(payload)
        quality_gate_failures.extend(secret_failures)

    source_state = {
        "checked": len(checks_list),
        "missing_env_count": len(missing_env),
        "next_action_count": len(next_actions),
        "template_output_present": isinstance(template_output, str) and bool(template_output),
        "template_output_sha256_invalid": template_output_sha256_invalid,
        "secret_literal_count": len(secret_failures),
    }
    result = {
        "ok": not issues and not quality_gate_failures,
        "command": "verify-runtime-env-preflight",
        "artifact": str(artifact_path),
        "artifact_sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
        "checked": 1,
        "missing_env": missing_env,
        "missing_env_count": len(missing_env),
        "template_output": template_output,
        "template_output_sha256": template_sha256,
        "template_output_sha256_invalid": template_output_sha256_invalid,
        "issues": issues,
        "issue_count": len(issues),
        "quality_gate_failures": quality_gate_failures,
        "quality_gate_failure_count": len(quality_gate_failures),
        "run_metadata": _runtime_env_preflight_verify_run_metadata(
            args,
            artifact_path=artifact_path,
            source_state=source_state,
        ),
    }
    return _attach_optional_verification_output(args, result)


def _runtime_env_preflight_count_issues(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for key in ("checked", "missing_env_count", "issue_count"):
        if not _is_plain_int(payload.get(key)):
            issues.append(f"{key} must be an integer")
        elif not _is_non_negative_plain_int(payload.get(key)):
            issues.append(f"{key} must be a non-negative integer")
    dotenv = payload.get("dotenv")
    if isinstance(dotenv, dict) and not _is_plain_int(dotenv.get("dotenv_only_count")):
        issues.append("dotenv.dotenv_only_count must be an integer")
    elif isinstance(dotenv, dict) and not _is_non_negative_plain_int(
        dotenv.get("dotenv_only_count")
    ):
        issues.append("dotenv.dotenv_only_count must be a non-negative integer")
    return issues


def _runtime_env_preflight_secret_literal_failures(
    payload: dict[str, Any],
) -> list[str]:
    serialized = json.dumps(payload, sort_keys=True)
    failures: list[str] = []
    if "sk-" in serialized:
        failures.append("secret_literal:openai_token")
    if re.search(r"\bpostgres(?:ql)?://\S+", serialized):
        failures.append("secret_literal:postgres_dsn")
    return failures


def _runtime_env_preflight_verify_run_metadata(
    args: Any,
    *,
    artifact_path: Path,
    source_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_metadata = {
        "command": "verify-runtime-env-preflight",
        "verification_flags": {
            "require_next_actions": bool(getattr(args, "require_next_actions", False)),
            "require_template_output": bool(getattr(args, "require_template_output", False)),
            "require_no_secret_literals": bool(getattr(args, "require_no_secret_literals", False)),
        },
        "artifact_sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        if artifact_path.is_file()
        else None,
    }
    if source_state is not None:
        run_metadata["source_state"] = source_state
    return run_metadata


def _read_dotenv_key_presence(path: Path | None) -> tuple[set[str], list[str]]:
    if path is None:
        return set(), []
    if not path.is_file():
        return set(), [f"dotenv file not found: {path}"]
    keys: set[str] = set()
    issues: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:  # noqa: BLE001
        return set(), [f"failed to read dotenv file: {exc}"]
    for index, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            issues.append(f"dotenv line {index}: missing '='")
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            issues.append(f"dotenv line {index}: missing key")
            continue
        if value.strip():
            keys.add(key)
    return keys, issues


def _runtime_env_next_actions_by_env_summary(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    return {str(name): _string_list(actions) for name, actions in value.items()}
