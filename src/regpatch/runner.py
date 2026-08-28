"""Fail-closed cleanroom runner for executable RegPatch candidates.

The runner deliberately knows nothing about evaluator targets.  It freezes a
candidate program, freezes only the files named by the public episode manifest,
and recreates both in a fresh temporary workspace for each of two runs.
"""

from __future__ import annotations

import hashlib
import json
import os
import resource
import signal
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

MAX_SUBMISSION_FILES = 64
MAX_SUBMISSION_BYTES = 4 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_INPUT_BYTES = 128 * 1024 * 1024
MAX_RESULT_BYTES = 64 * 1024 * 1024
MAX_PROVENANCE_BYTES = 64 * 1024
MAX_CAPTURE_BYTES = 256 * 1024
WALL_SECONDS = 20
CPU_SECONDS = 12
_TEMP_ROOT = Path("/private/tmp") if Path("/private/tmp").is_dir() else Path("/tmp")
_PRIVATE_MANIFEST_KEYS = {
    "answer",
    "changed_regions",
    "evaluator",
    "expected",
    "oracle",
    "private_receipt",
    "successor_sha256",
    "target",
    "target_path",
    "target_sha256",
}
_PRIVATE_VALUE_MARKERS = (
    "/users/",
    "\\users\\",
    "/.git/",
    "data/time_machine",
    "evaluator/target.xml",
)


@dataclass(frozen=True)
class FrozenFile:
    """One regular file frozen before the untrusted process starts."""

    path: PurePosixPath
    content: bytes
    sha256: str


@dataclass(frozen=True)
class FrozenSubmission:
    """A bounded candidate tree with one root entrypoint."""

    entrypoint: PurePosixPath
    files: tuple[FrozenFile, ...]
    sha256: str


@dataclass(frozen=True)
class FrozenEpisode:
    """The complete candidate-visible portion of an episode."""

    episode_id: str
    manifest: FrozenFile
    base: FrozenFile
    rules: tuple[FrozenFile, ...]
    rule_metadata: tuple[dict[str, object], ...]
    result_path: PurePosixPath
    provenance_path: PurePosixPath
    request: bytes

    @property
    def files(self) -> tuple[FrozenFile, ...]:
        return (self.manifest, self.base, *self.rules)


@dataclass(frozen=True)
class RunCapture:
    """Evaluator-only capture from one fresh candidate run."""

    ok: bool
    returncode: int
    result: bytes | None
    candidate_provenance: dict[str, object] | None
    candidate_provenance_valid: bool
    stdout: bytes
    stderr: str
    timed_out: bool
    violation: str | None
    receipt: dict[str, object]


@dataclass(frozen=True)
class RepeatedRun:
    """Two independent executions of the same frozen submission and episode."""

    first: RunCapture
    second: RunCapture

    @property
    def result_deterministic(self) -> bool:
        return self.first.result is not None and self.first.result == self.second.result

    @property
    def provenance_deterministic(self) -> bool:
        return (
            self.first.candidate_provenance is not None
            and self.first.candidate_provenance == self.second.candidate_provenance
        )

    @property
    def deterministic(self) -> bool:
        return self.result_deterministic and self.provenance_deterministic

    def evaluator_receipt(self) -> dict[str, object]:
        """Return an evaluator-owned receipt without candidate stdout or target bytes."""
        return {
            "schema_version": 1,
            "runner": "psephos-regpatch-deno-v1",
            "deterministic": self.deterministic,
            "result_deterministic": self.result_deterministic,
            "provenance_deterministic": self.provenance_deterministic,
            "runs": [self.first.receipt, self.second.receipt],
        }


class RegPatchRunner:
    """Run local JS/TS candidate trees with only public inputs and output access."""

    def __init__(self, *, probe: bool = True) -> None:
        self.deno = _find_deno().resolve()
        self.version = self._check_version()
        if probe:
            self._probe_boundary()

    def run_twice(self, submission: Path, episode_dir: Path) -> RepeatedRun:
        frozen_submission = self.freeze_submission(submission)
        frozen_episode = self.freeze_episode(episode_dir)
        return self.run_frozen(frozen_submission, frozen_episode)

    def run_frozen(self, submission: FrozenSubmission, episode: FrozenEpisode) -> RepeatedRun:
        first = self._run_once(submission, episode, run_number=1)
        second = self._run_once(submission, episode, run_number=2)
        return RepeatedRun(first, second)

    @staticmethod
    def freeze_submission(path: Path) -> FrozenSubmission:
        """Freeze one entrypoint or a small local source tree without following links."""
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            raise ValueError(f"submission is unavailable: {path}") from exc
        if stat.S_ISLNK(mode):
            raise ValueError("submission must not be a symlink")

        files: tuple[FrozenFile, ...]
        if stat.S_ISREG(mode):
            suffix = path.suffix.lower()
            if suffix not in {".js", ".ts"}:
                raise ValueError("submission entrypoint must end in .js or .ts")
            content = _read_regular_file(path, MAX_SUBMISSION_BYTES, "submission")
            entrypoint = PurePosixPath(f"solution{suffix}")
            files = (FrozenFile(entrypoint, content, _sha256(content)),)
        elif stat.S_ISDIR(mode):
            files = _freeze_tree(path)
            candidates = [
                candidate
                for candidate in (PurePosixPath("solution.ts"), PurePosixPath("solution.js"))
                if any(item.path == candidate for item in files)
            ]
            if len(candidates) != 1:
                raise ValueError(
                    "submission directory must contain exactly one root solution.ts or solution.js"
                )
            entrypoint = candidates[0]
        else:
            raise ValueError("submission must be one regular file or directory")

        digest = hashlib.sha256()
        for item in files:
            encoded = item.path.as_posix().encode("utf-8")
            digest.update(len(encoded).to_bytes(4, "big"))
            digest.update(encoded)
            digest.update(len(item.content).to_bytes(8, "big"))
            digest.update(item.content)
        return FrozenSubmission(entrypoint, files, digest.hexdigest())

    @staticmethod
    def freeze_episode(episode_dir: Path) -> FrozenEpisode:
        """Freeze and validate exactly the public files named by manifest.json."""
        root = episode_dir.resolve()
        if not root.is_dir() or episode_dir.is_symlink():
            raise ValueError("episode must be a non-symlink directory")
        manifest_payload = _read_regular_file(
            root / "manifest.json", MAX_MANIFEST_BYTES, "manifest"
        )
        manifest_value = _json_object(manifest_payload, "manifest")
        _audit_public_manifest(manifest_value)

        if manifest_value.get("schema_version") != 1:
            raise ValueError("manifest schema_version must be 1")
        if manifest_value.get("task_type") != "ecfr_amendatory_patch":
            raise ValueError("manifest task_type must be ecfr_amendatory_patch")
        episode_id = manifest_value.get("episode_id")
        if not isinstance(episode_id, str) or not episode_id or len(episode_id) > 200:
            raise ValueError("manifest episode_id must be a short non-empty string")

        inputs = _required_object(manifest_value, "inputs")
        base_metadata = _required_object(inputs, "base")
        base = _freeze_manifest_input(root, base_metadata, "base")

        raw_rules = inputs.get("rules")
        if not isinstance(raw_rules, list) or not raw_rules:
            raise ValueError("manifest inputs.rules must be a non-empty list")
        rules: list[FrozenFile] = []
        rule_metadata: list[dict[str, object]] = []
        orders: list[int] = []
        for index, raw_rule in enumerate(raw_rules):
            if not isinstance(raw_rule, dict):
                raise TypeError(f"manifest rule {index} must be an object")
            rule = _freeze_manifest_input(root, raw_rule, f"rule {index}")
            order = raw_rule.get("order")
            if not isinstance(order, int) or isinstance(order, bool):
                raise TypeError(f"manifest rule {index} requires integer order")
            document_number = raw_rule.get("document_number")
            if not isinstance(document_number, str) or not document_number:
                raise ValueError(f"manifest rule {index} requires document_number")
            rules.append(rule)
            orders.append(order)
            rule_metadata.append(
                {
                    "order": order,
                    "path": rule.path.as_posix(),
                    "document_number": document_number,
                    "sha256": rule.sha256,
                    "byte_count": len(rule.content),
                }
            )
        if orders != list(range(1, len(orders) + 1)):
            raise ValueError("manifest rule order must be contiguous and start at one")

        paths = [base.path, *(rule.path for rule in rules)]
        if len(paths) != len(set(paths)):
            raise ValueError("manifest input paths must be unique")
        if _paths_overlap(paths):
            raise ValueError("manifest input paths must not contain one another")
        input_bytes = sum(len(item.content) for item in (base, *rules))
        if input_bytes > MAX_INPUT_BYTES:
            raise ValueError(f"episode input exceeds {MAX_INPUT_BYTES} bytes")

        output_contract = _required_object(manifest_value, "output_contract")
        result_path = _public_path(
            output_contract.get("result_path"), "output result", root_name="output"
        )
        provenance_path = _public_path(
            output_contract.get("provenance_path"), "output provenance", root_name="output"
        )
        if result_path == provenance_path:
            raise ValueError("result and provenance paths must differ")
        if _paths_overlap([result_path, provenance_path]):
            raise ValueError("result and provenance paths must not contain one another")

        manifest_file = FrozenFile(
            PurePosixPath("manifest.json"), manifest_payload, _sha256(manifest_payload)
        )
        request_value = {
            "schema_version": 1,
            "task_type": "ecfr_amendatory_patch",
            "episode_id": episode_id,
            "manifest_path": "manifest.json",
            "base": {
                "path": base.path.as_posix(),
                "sha256": base.sha256,
                "byte_count": len(base.content),
            },
            "rules": rule_metadata,
            "output": {
                "result_path": result_path.as_posix(),
                "provenance_path": provenance_path.as_posix(),
            },
        }
        request = (json.dumps(request_value, sort_keys=True, separators=(",", ":")) + "\n").encode()
        return FrozenEpisode(
            episode_id=episode_id,
            manifest=manifest_file,
            base=base,
            rules=tuple(rules),
            rule_metadata=tuple(rule_metadata),
            result_path=result_path,
            provenance_path=provenance_path,
            request=request,
        )

    def _run_once(
        self, submission: FrozenSubmission, episode: FrozenEpisode, *, run_number: int
    ) -> RunCapture:
        with tempfile.TemporaryDirectory(prefix="psephos-regpatch-", dir=_TEMP_ROOT) as root_name:
            root = Path(root_name)
            candidate_root = root / "candidate"
            workspace = root / "workspace"
            capture = root / "capture"
            candidate_root.mkdir()
            workspace.mkdir()
            capture.mkdir()
            _stage_files(candidate_root, submission.files)
            _stage_files(workspace, episode.files)
            output_root = workspace / "output"
            output_root.mkdir(mode=0o700)
            for output_path in (episode.result_path, episode.provenance_path):
                (workspace.joinpath(*output_path.parts)).parent.mkdir(parents=True, exist_ok=True)

            entrypoint = candidate_root.joinpath(*submission.entrypoint.parts).resolve()
            readable = [candidate_root.resolve(), (workspace / "manifest.json").resolve()]
            readable.extend(
                workspace.joinpath(*item.path.parts).resolve()
                for item in (episode.base, *episode.rules)
            )
            result = self._invoke(
                entrypoint=entrypoint,
                cwd=workspace,
                readable=readable,
                output_root=output_root.resolve(),
                request=episode.request,
                capture=capture,
            )
            return _capture_outputs(
                result=result,
                workspace=workspace,
                output_root=output_root,
                submission=submission,
                episode=episode,
                run_number=run_number,
                deno_version=self.version,
            )

    def _invoke(
        self,
        *,
        entrypoint: Path,
        cwd: Path,
        readable: list[Path],
        output_root: Path,
        request: bytes,
        capture: Path,
    ) -> tuple[int, bool, bytes, bytes]:
        if any("," in str(path) for path in readable):
            raise RuntimeError("Deno allow-read path contains a comma")
        allowed_read = ",".join(str(path) for path in readable)
        if "," in str(output_root):
            raise RuntimeError("Deno allow-write path contains a comma")
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
            f"--allow-read={allowed_read}",
            f"--allow-write={output_root}",
            "--deny-net",
            "--deny-env",
            "--deny-run",
            "--deny-ffi",
            "--deny-sys",
            "--seed=0",
            "--v8-flags=--max-old-space-size=256",
            str(entrypoint),
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
                preexec_fn=_limits,  # noqa: PLW1509 - required POSIX sandbox resource limits
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
        stdout_payload = stdout_path.read_bytes()[:MAX_CAPTURE_BYTES]
        stderr_payload = stderr_path.read_bytes()[:MAX_CAPTURE_BYTES]
        return process.returncode, timed_out, stdout_payload, stderr_payload

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
        probe = """
const checks = [];
try { Deno.writeTextFileSync("output/allowed", "ok"); checks.push(true); }
catch (_) { checks.push(false); }
for (const attempt of [
  () => Deno.readTextFileSync(Deno.args[0]),
  () => Deno.env.get("HOME"),
  () => new Deno.Command("/bin/echo").outputSync(),
  () => Deno.writeTextFileSync("outside", "x"),
  () => Deno.systemMemoryInfo(),
]) {
  try { attempt(); checks.push(false); }
  catch (error) { checks.push(error instanceof Deno.errors.NotCapable); }
}
try { await fetch("http://127.0.0.1:8080/"); checks.push(false); }
catch (error) { checks.push(error instanceof Deno.errors.NotCapable); }
try { await import("https://example.invalid/blocked.ts"); checks.push(false); }
catch (_) { checks.push(true); }
console.log(JSON.stringify(checks));
"""
        with tempfile.TemporaryDirectory(prefix="psephos-regpatch-probe-", dir=_TEMP_ROOT) as name:
            root = Path(name)
            allowed = root / "allowed"
            output = allowed / "output"
            allowed.mkdir()
            output.mkdir()
            entrypoint = allowed / "probe.js"
            entrypoint.write_text(probe, encoding="utf-8")
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
                f"--allow-read={entrypoint}",
                f"--allow-write={output}",
                "--deny-net",
                "--deny-env",
                "--deny-run",
                "--deny-ffi",
                "--deny-sys",
                "--seed=0",
                str(entrypoint),
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
            checks = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Deno permission probe failed: {result.stderr}") from exc
        if result.returncode or checks != [True] * 8:
            raise RuntimeError(
                f"Deno permission probe did not fail closed: rc={result.returncode}, {checks}"
            )


def _freeze_tree(root: Path) -> tuple[FrozenFile, ...]:
    frozen: list[FrozenFile] = []
    total = 0

    def visit(directory: Path, relative: PurePosixPath) -> None:
        nonlocal total
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                child_relative = relative / entry.name
                if len(child_relative.as_posix()) > 240:
                    raise ValueError("submission path exceeds 240 characters")
                if ".git" in child_relative.parts:
                    raise ValueError("submission must not contain repository history")
                if entry.is_symlink():
                    raise ValueError(f"submission contains symlink: {child_relative}")
                if entry.is_dir(follow_symlinks=False):
                    visit(Path(entry.path), child_relative)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    raise ValueError(f"submission contains special file: {child_relative}")
                remaining = MAX_SUBMISSION_BYTES - total
                content = _read_regular_file(
                    Path(entry.path), remaining + 1, f"submission file {child_relative}"
                )
                total += len(content)
                if total > MAX_SUBMISSION_BYTES:
                    raise ValueError(f"submission exceeds {MAX_SUBMISSION_BYTES} bytes")
                frozen.append(FrozenFile(child_relative, content, _sha256(content)))
                if len(frozen) > MAX_SUBMISSION_FILES:
                    raise ValueError(f"submission exceeds {MAX_SUBMISSION_FILES} files")

    visit(root, PurePosixPath())
    return tuple(sorted(frozen, key=lambda item: item.path.as_posix()))


def _freeze_manifest_input(root: Path, metadata: dict[str, Any], label: str) -> FrozenFile:
    relative = _public_path(metadata.get("path"), label, root_name="input")
    expected_sha256 = metadata.get("sha256")
    expected_bytes = metadata.get("byte_count")
    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise ValueError(f"manifest {label} sha256 is invalid")
    if (
        not isinstance(expected_bytes, int)
        or isinstance(expected_bytes, bool)
        or expected_bytes < 0
    ):
        raise ValueError(f"manifest {label} byte_count is invalid")
    content = _read_regular_file(
        _safe_episode_path(root, relative, label), MAX_INPUT_BYTES, f"episode {label}"
    )
    actual_sha256 = _sha256(content)
    if len(content) != expected_bytes or actual_sha256 != expected_sha256:
        raise ValueError(f"manifest {label} bytes do not match its receipt")
    return FrozenFile(relative, content, actual_sha256)


def _stage_files(root: Path, files: tuple[FrozenFile, ...]) -> None:
    for item in files:
        target = root.joinpath(*item.path.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(item.content)
        target.chmod(0o444)


def _capture_outputs(
    *,
    result: tuple[int, bool, bytes, bytes],
    workspace: Path,
    output_root: Path,
    submission: FrozenSubmission,
    episode: FrozenEpisode,
    run_number: int,
    deno_version: str,
) -> RunCapture:
    returncode, timed_out, stdout, stderr_bytes = result
    expected_files = {
        PurePosixPath(*episode.result_path.parts[1:]),
        PurePosixPath(*episode.provenance_path.parts[1:]),
    }
    allowed_directories = {PurePosixPath(".")}
    for expected in expected_files:
        allowed_directories.update(expected.parents)
    violation = _audit_output_tree(output_root, expected_files, allowed_directories)

    result_bytes: bytes | None = None
    provenance_payload: bytes | None = None
    if violation is None:
        result_file = workspace.joinpath(*episode.result_path.parts)
        provenance_file = workspace.joinpath(*episode.provenance_path.parts)
        if result_file.exists() or result_file.is_symlink():
            try:
                result_bytes = _read_regular_file(result_file, MAX_RESULT_BYTES, "candidate result")
            except ValueError as exc:
                violation = str(exc)
        if provenance_file.exists() or provenance_file.is_symlink():
            try:
                provenance_payload = _read_regular_file(
                    provenance_file, MAX_PROVENANCE_BYTES, "candidate provenance"
                )
            except ValueError as exc:
                violation = str(exc)

    candidate_provenance: dict[str, object] | None = None
    if provenance_payload is not None:
        try:
            candidate_provenance = _json_object(provenance_payload, "candidate provenance")
        except ValueError:
            candidate_provenance = None
    provenance_valid = _valid_candidate_provenance(candidate_provenance, episode, result_bytes)
    ok = returncode == 0 and not timed_out and violation is None and result_bytes is not None
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    receipt: dict[str, object] = {
        "schema_version": 1,
        "runner": "psephos-regpatch-deno-v1",
        "deno_version": deno_version,
        "run_number": run_number,
        "episode_id": episode.episode_id,
        "manifest_sha256": episode.manifest.sha256,
        "submission_sha256": submission.sha256,
        "input_sha256": {
            "base": episode.base.sha256,
            "rules": [rule.sha256 for rule in episode.rules],
        },
        "returncode": returncode,
        "timed_out": timed_out,
        "run_ok": ok,
        "violation": violation,
        "result_sha256": _sha256(result_bytes) if result_bytes is not None else None,
        "result_byte_count": len(result_bytes) if result_bytes is not None else None,
        "candidate_provenance_sha256": (
            _sha256(provenance_payload) if provenance_payload is not None else None
        ),
        "candidate_provenance_valid": provenance_valid,
        "stdout_sha256": _sha256(stdout),
        "stderr_sha256": _sha256(stderr_bytes),
    }
    return RunCapture(
        ok=ok,
        returncode=returncode,
        result=result_bytes,
        candidate_provenance=candidate_provenance,
        candidate_provenance_valid=provenance_valid,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        violation=violation,
        receipt=receipt,
    )


def _audit_output_tree(
    output_root: Path,
    expected_files: set[PurePosixPath],
    allowed_directories: set[PurePosixPath],
) -> str | None:
    if not output_root.is_dir() or output_root.is_symlink():
        return "candidate removed or replaced the output directory"

    def visit(directory: Path, relative: PurePosixPath) -> str | None:
        with os.scandir(directory) as entries:
            for entry in entries:
                child = relative / entry.name
                if entry.is_symlink():
                    return f"candidate output contains symlink: {child}"
                if entry.is_dir(follow_symlinks=False):
                    if child not in allowed_directories:
                        return f"candidate output contains undeclared directory: {child}"
                    nested = visit(Path(entry.path), child)
                    if nested is not None:
                        return nested
                elif entry.is_file(follow_symlinks=False):
                    if child not in expected_files:
                        return f"candidate output contains undeclared file: {child}"
                else:
                    return f"candidate output contains special file: {child}"
        return None

    return visit(output_root, PurePosixPath("."))


def _valid_candidate_provenance(
    value: dict[str, object] | None,
    episode: FrozenEpisode,
    result: bytes | None,
) -> bool:
    if value is None or result is None:
        return False
    return (
        value.get("schema_version") == 1
        and value.get("episode_id") == episode.episode_id
        and value.get("base_sha256") == episode.base.sha256
        and value.get("rule_sha256s") == [rule.sha256 for rule in episode.rules]
        and value.get("result_sha256") == _sha256(result)
    )


def _audit_public_manifest(value: dict[str, object]) -> None:
    def visit(item: object) -> None:
        if isinstance(item, dict):
            for raw_key, child in item.items():
                key = str(raw_key).lower()
                if key in _PRIVATE_MANIFEST_KEYS:
                    raise ValueError(f"public manifest contains evaluator-only key: {raw_key}")
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, str):
            lowered = item.lower()
            if any(marker in lowered for marker in _PRIVATE_VALUE_MARKERS):
                raise ValueError("public manifest contains a private/local path marker")

    visit(value)


def _required_object(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise TypeError(f"manifest {key} must be an object")
    return value


def _public_path(value: object, label: str, *, root_name: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"{label} path must be a relative POSIX path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or len(path.parts) < 2
        or path.parts[0] != root_name
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"{label} path must stay under {root_name}/")
    return path


def _safe_episode_path(root: Path, relative: PurePosixPath, label: str) -> Path:
    current = root
    for part in relative.parts[:-1]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except OSError as exc:
            raise ValueError(f"episode {label} has a missing parent directory") from exc
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise ValueError(f"episode {label} path crosses a symlink or non-directory")
    return current / relative.name


def _paths_overlap(paths: list[PurePosixPath]) -> bool:
    parts = [path.parts for path in paths]
    return any(left != right and left[: len(right)] == right for left in parts for right in parts)


def _read_regular_file(path: Path, maximum: int, label: str) -> bytes:
    if maximum < 0:
        raise ValueError(f"{label} exceeds its byte limit")
    if not hasattr(os, "O_NOFOLLOW"):
        raise RuntimeError("this runner requires O_NOFOLLOW")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ValueError(f"{label} must be a readable regular file") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"{label} must be a regular file")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(payload) > maximum:
        raise ValueError(f"{label} exceeds {maximum} bytes")
    return payload


def _json_object(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload, parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must be strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be a JSON object")
    return value


def _reject_json_constant(value: str) -> NoReturn:
    raise ValueError(f"invalid JSON constant: {value}")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _find_deno() -> Path:
    for candidate in (Path("/opt/homebrew/bin/deno"), Path("/usr/local/bin/deno")):
        if candidate.exists():
            resolved = candidate.resolve()
            if resolved.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                raise RuntimeError(f"Deno runtime is group/world writable: {resolved}")
            return resolved
    raise RuntimeError("trusted Deno 2.x is required at /opt/homebrew/bin or /usr/local/bin")


def _limits() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_RESULT_BYTES, MAX_RESULT_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (96, 96))


__all__ = [
    "FrozenEpisode",
    "FrozenFile",
    "FrozenSubmission",
    "RegPatchRunner",
    "RepeatedRun",
    "RunCapture",
]
