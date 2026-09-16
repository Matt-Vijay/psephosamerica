"""Optional per-user macOS timer; the worker itself also runs under cron/systemd."""

from __future__ import annotations

import hashlib
import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any

from .refresh import _load, _save, pause
from .store import writer_lock


def identity(root: Path) -> str:
    suffix = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]
    return "org.psephos.refresh." + suffix


def specification(root: Path) -> dict[str, Any]:
    state = _load(root)
    if state is None:
        raise ValueError("Configure refresh before installing its timer")
    if not state["enabled"]:
        raise ValueError("Refresh is paused; configure it before installing its timer")
    log = root.resolve() / "refresh" / "service.log"
    spec = {
        "Label": identity(root),
        "ProgramArguments": [
            sys.executable,
            "-m",
            "psephos.refresh_service",
            str(root.resolve()),
        ],
        "WorkingDirectory": str(root.resolve()),
        "StartInterval": 3600,
        "RunAtLoad": True,
        "ProcessType": "Background",
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }
    if state.get("https_proxy"):
        spec["EnvironmentVariables"] = {"HTTPS_PROXY": state["https_proxy"]}
    return spec


def install(root: Path) -> dict[str, Any]:
    if sys.platform != "darwin":
        raise ValueError("Use cron or a systemd timer to invoke 'psephos --data PATH refresh run'")
    spec = specification(root)
    path = Path.home() / "Library" / "LaunchAgents" / (identity(root) + ".plist")
    payload = plistlib.dumps(spec)
    if path.exists() and path.read_bytes() != payload:
        raise ValueError("Existing timer differs; uninstall it before replacing it")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        stream.write(payload)
    path.chmod(0o600)
    domain = f"gui/{os.getuid()}"
    existing = subprocess.run(
        ["/bin/launchctl", "print", domain + "/" + identity(root)], capture_output=True
    )
    if existing.returncode:
        result = subprocess.run(
            ["/bin/launchctl", "bootstrap", domain, str(path)], capture_output=True, text=True
        )
        if result.returncode:
            raise RuntimeError("Could not load refresh timer: " + result.stderr.strip())
    return {
        "status": "installed",
        "label": identity(root),
        "plist": str(path),
        "wake_interval_seconds": 3600,
    }


def uninstall(root: Path) -> dict[str, Any]:
    if sys.platform != "darwin":
        raise ValueError("Remove the external cron/systemd timer, then pause refresh")
    path = Path.home() / "Library" / "LaunchAgents" / (identity(root) + ".plist")
    if path.exists():
        spec = plistlib.loads(path.read_bytes())
        if spec.get("Label") != identity(root) or spec.get("ProgramArguments", [])[1:] != [
            "-m",
            "psephos.refresh_service",
            str(root.resolve()),
        ]:
            raise ValueError("Timer identity mismatch; will not remove it")
        target = f"gui/{os.getuid()}/" + identity(root)
        loaded = subprocess.run(["/bin/launchctl", "print", target], capture_output=True)
        if not loaded.returncode:
            result = subprocess.run(
                ["/bin/launchctl", "bootout", target], capture_output=True, text=True
            )
            if result.returncode:
                raise RuntimeError("Could not unload timer: " + result.stderr.strip())
        path.unlink()
    pause(root)
    return {"status": "uninstalled", "label": identity(root)}


def supervise(root: Path) -> int:
    """A separate process enforces a hard timeout, including native parser calls."""
    try:
        return subprocess.run(
            [sys.executable, "-m", "psephos.cli", "--data", str(root.resolve()), "refresh", "run"],
            timeout=1800,
        ).returncode
    except subprocess.TimeoutExpired:
        with writer_lock(root):
            state = _load(root)
            if state is not None:
                state["worker_error"] = (
                    "Worker exceeded 30 minutes; terminated with reserved bytes still charged"
                )
                for entry in state["sources"].values():
                    if entry["status"] == "running":
                        entry.update(status="interrupted", error=state["worker_error"])
                _save(root, state)
        return 1


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Run one refresh pass with a hard 30-minute deadline"
    )
    parser.add_argument("data", type=Path)
    raise SystemExit(supervise(parser.parse_args().data))
