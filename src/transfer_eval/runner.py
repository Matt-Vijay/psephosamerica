"""Fail-closed Deno capability sandbox for one JS/TS candidate file."""

from __future__ import annotations

import json
import os
import resource
import signal
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from src.transfer_eval.cases import case_input_files
from src.transfer_eval.contract import ParsedOutput, parse_jsonl

MAX_SUBMISSION_BYTES = 128 * 1024
MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
WALL_SECONDS = 12
CPU_SECONDS = 8
_TEMP_ROOT = Path("/private/tmp") if Path("/private/tmp").is_dir() else Path("/tmp")


@dataclass(frozen=True)
class RunCapture:
    ok: bool
    returncode: int
    stdout: bytes
    stderr: str
    parsed: ParsedOutput
    timed_out: bool = False


@dataclass(frozen=True)
class RepeatedRun:
    first: RunCapture
    second: RunCapture


class DenoRunner:
    """Run an immutable single-file submission with no ambient capabilities."""

    def __init__(self, *, probe: bool = True) -> None:
        self.deno = _find_deno().resolve()
        self.version = self._check_version()
        if probe:
            self._probe_boundary()

    def run_twice(self, submission: Path, case_dir: Path) -> RepeatedRun:
        payload, suffix = self.read_submission(submission)
        return self.run_frozen(payload, suffix, case_dir)

    def run_frozen(self, payload: bytes, suffix: str, case_dir: Path) -> RepeatedRun:
        if suffix not in {".js", ".ts"} or len(payload) > MAX_SUBMISSION_BYTES:
            raise ValueError("invalid frozen submission")
        request, inputs = _read_case(case_dir)
        return RepeatedRun(
            self._run_once(payload, suffix, request, inputs),
            self._run_once(payload, suffix, request, inputs),
        )

    def _run_once(
        self,
        payload: bytes,
        suffix: str,
        request: bytes,
        inputs: tuple[tuple[Path, bytes], ...],
    ) -> RunCapture:
        with tempfile.TemporaryDirectory(prefix="psephos-candidate-", dir=_TEMP_ROOT) as stage_name:
            with tempfile.TemporaryDirectory(
                prefix="psephos-capture-", dir=_TEMP_ROOT
            ) as capture_name:
                stage, capture = Path(stage_name), Path(capture_name)
                entry = stage / f"solution{suffix}"
                entry.write_bytes(payload)
                input_dir = stage / "input"
                for relative, content in inputs:
                    target = input_dir / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                return self._invoke(entry, input_dir, stage, request, capture)

    def _invoke(
        self,
        entry: Path,
        input_dir: Path,
        cwd: Path,
        request: bytes,
        capture: Path,
    ) -> RunCapture:
        entry = entry.resolve()
        input_dir = input_dir.resolve()
        cwd = cwd.resolve()
        if any("," in str(path) for path in (entry, input_dir)):
            raise RuntimeError("Deno allow-read path contains a comma")
        stdout_path, stderr_path = capture / "stdout", capture / "stderr"
        command = [
            str(self.deno),
            "run",
            "--no-prompt",
            "--no-config",
            "--no-lock",
            "--no-code-cache",
            "--cached-only",
            "--no-npm",
            "--no-remote",
            "--deny-import",
            f"--allow-read={entry},{input_dir}",
            "--deny-write",
            "--deny-net",
            "--deny-env",
            "--deny-run",
            "--deny-ffi",
            "--deny-sys",
            "--seed=0",
            "--v8-flags=--max-old-space-size=128",
            str(entry),
        ]
        environment = {
            "PATH": str(self.deno.parent),
            "NO_COLOR": "1",
            "DENO_NO_UPDATE_CHECK": "1",
        }
        timed_out = False
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
                preexec_fn=_limits,
            )
            try:
                process.communicate(request, timeout=WALL_SECONDS)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=3)
        stdout_payload = stdout_path.read_bytes()[:MAX_OUTPUT_BYTES]
        stderr_payload = stderr_path.read_bytes()[: 64 * 1024].decode("utf-8", errors="replace")
        parsed = parse_jsonl(stdout_payload)
        return RunCapture(
            process.returncode == 0 and not timed_out,
            process.returncode,
            stdout_payload,
            stderr_payload,
            parsed,
            timed_out,
        )

    def _check_version(self) -> str:
        if not self.deno.is_file() or not os.access(self.deno, os.X_OK):
            raise RuntimeError(f"Deno executable unavailable: {self.deno}")
        result = subprocess.run(
            [str(self.deno), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            env={"PATH": str(self.deno.parent), "DENO_NO_UPDATE_CHECK": "1"},
        )
        first = result.stdout.splitlines()[0] if result.stdout else ""
        if result.returncode or not first.startswith("deno 2."):
            raise RuntimeError(f"Deno 2.x is required; received {first or result.stderr!r}")
        return first.removeprefix("deno ").strip()

    def _probe_boundary(self) -> None:
        """Exercise denied read/env/run/net/write/sys before accepting submissions."""
        probe = """
const denied = [];
for (const attempt of [
  () => Deno.readTextFileSync(Deno.args[0]),
  () => Deno.env.get("HOME"),
  () => new Deno.Command("/bin/echo").outputSync(),
  () => Deno.writeTextFileSync("blocked", "x"),
  () => Deno.systemMemoryInfo(),
]) {
  try { attempt(); denied.push(false); } catch (error) {
    denied.push(error instanceof Deno.errors.NotCapable);
  }
}
try { await fetch("http://127.0.0.1:8080/"); denied.push(false); }
catch (error) { denied.push(error instanceof Deno.errors.NotCapable); }
console.log(JSON.stringify(denied));
"""
        with tempfile.TemporaryDirectory(prefix="psephos-probe-", dir=_TEMP_ROOT) as root_name:
            root = Path(root_name)
            allowed = root / "allowed"
            allowed.mkdir()
            entry = allowed / "probe.js"
            entry.write_text(probe, encoding="utf-8")
            canary = root / "denied-canary"
            canary.write_text("secret", encoding="utf-8")
            command = [
                str(self.deno),
                "run",
                "--no-prompt",
                "--no-config",
                "--no-lock",
                "--no-code-cache",
                "--cached-only",
                "--no-npm",
                "--no-remote",
                "--deny-import",
                f"--allow-read={entry}",
                "--deny-write",
                "--deny-net",
                "--deny-env",
                "--deny-run",
                "--deny-ffi",
                "--deny-sys",
                "--seed=0",
                str(entry),
                str(canary),
            ]
            result = subprocess.run(
                command,
                cwd=allowed,
                env={"PATH": str(self.deno.parent), "DENO_NO_UPDATE_CHECK": "1"},
                check=False,
                capture_output=True,
                text=True,
                timeout=8,
            )
        try:
            denied = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Deno permission probe failed: {result.stderr}") from exc
        if result.returncode or denied != [True] * 6:
            raise RuntimeError(
                f"Deno permission probe did not fail closed: rc={result.returncode}, {denied}"
            )

    @staticmethod
    def read_submission(path: Path) -> tuple[bytes, str]:
        suffix = path.suffix.lower()
        if suffix not in {".js", ".ts"}:
            raise ValueError("submission must end in .js or .ts")
        if not hasattr(os, "O_NOFOLLOW"):
            raise RuntimeError("this runner requires O_NOFOLLOW")
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as exc:
            raise ValueError("submission must be one regular file") from exc
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("submission must be one regular file")
            chunks = []
            remaining = MAX_SUBMISSION_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
        finally:
            os.close(descriptor)
        if len(payload) > MAX_SUBMISSION_BYTES:
            raise ValueError("submission exceeds 128 KiB")
        return payload, suffix


def _find_deno() -> Path:
    for candidate in (Path("/opt/homebrew/bin/deno"), Path("/usr/local/bin/deno")):
        if candidate.exists():
            resolved = candidate.resolve()
            if resolved.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                raise RuntimeError(f"Deno runtime is group/world writable: {resolved}")
            return resolved
    raise RuntimeError("trusted Deno 2.x is required at /opt/homebrew/bin or /usr/local/bin")


def _read_case(case_dir: Path) -> tuple[bytes, tuple[tuple[Path, bytes], ...]]:
    files = case_input_files(case_dir)
    request = (case_dir / "request.json").read_bytes()
    if len(request) > 256 * 1024:
        raise ValueError("case request exceeds 256 KiB")
    total = 0
    inputs = []
    input_root = (case_dir / "input").resolve()
    for item in files:
        content = item.read_bytes()
        total += len(content)
        if total > MAX_INPUT_BYTES:
            raise ValueError(f"input exceeds {MAX_INPUT_BYTES} bytes")
        inputs.append((item.relative_to(input_root), content))
    return request, tuple(inputs)


def _limits() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))


__all__ = ["DenoRunner", "RepeatedRun", "RunCapture"]
