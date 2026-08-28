from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.regpatch.baselines import BASELINE_NAMES, write_baseline
from src.regpatch.runner import RegPatchRunner

BASE = b"""<?xml version="1.0" encoding="UTF-8"?>
<ROOT><DIV8 N="73.622"><HEAD>73.622</HEAD><TABLE>
<TR><TD>Wittenberg</TD><TD>31</TD></TR>
</TABLE></DIV8><DIV8 N="73.623"><P>unaffected</P></DIV8></ROOT>
"""
RULE = """<?xml version="1.0" encoding="UTF-8"?>
<RULE><REGTEXT TITLE="47" PART="73">
<AMDPAR>2. In 73.622(j), amend the table by:</AMDPAR>
<AMDPAR>a. Adding an entry for “Shawano”.</AMDPAR>
<AMDPAR>b. Removing the entry for “Wittenberg”.</AMDPAR>
<SECTION><SECTNO>73.622</SECTNO><TABLE><TR><TD>Shawano</TD><TD>31</TD></TR></TABLE></SECTION>
</REGTEXT></RULE>
""".encode()
TARGET = b"""<?xml version="1.0" encoding="UTF-8"?>
<ROOT><DIV8 N="73.622"><HEAD>73.622</HEAD><XREF>89 FR 6024</XREF><TABLE>
<TR><TD>Shawano</TD><TD>31</TD></TR>
</TABLE></DIV8><DIV8 N="73.623"><P>unaffected</P></DIV8></ROOT>
"""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_episode(
    root: Path,
    *,
    episode_id: str = "public-seed",
    base: bytes = BASE,
    rule: bytes = RULE,
) -> Path:
    episode = root / episode_id
    (episode / "input/rules").mkdir(parents=True)
    (episode / "evaluator").mkdir()
    (episode / "input/base.xml").write_bytes(base)
    (episode / "input/rules/01-2024-01928.xml").write_bytes(rule)
    # This canary is evaluator-only and must never be staged into the sandbox.
    (episode / "evaluator/target.xml").write_bytes(TARGET)
    manifest = {
        "schema_version": 1,
        "episode_id": episode_id,
        "task_type": "ecfr_amendatory_patch",
        "scope": {
            "title": 47,
            "parts": ["73"],
            "sections": ["73.622"],
            "granularity": "part",
        },
        "window": {"base_date": "2024-01-30", "successor_date": "2024-01-31"},
        "clocks": {
            "rules": [
                {
                    "order": 1,
                    "document_number": "2024-01928",
                    "publication_date": "2024-01-31",
                    "ecfr_incorporation_date": "2024-01-31",
                    "legal_effective_date": "2024-03-01",
                }
            ],
            "note": "incorporation is not legal effectiveness",
        },
        "inputs": {
            "base": {
                "path": "input/base.xml",
                "source_url": "https://www.ecfr.gov/api/versioner/v1/full/2024-01-30/title-47.xml",
                "sha256": _sha256(base),
                "byte_count": len(base),
            },
            "rules": [
                {
                    "order": 1,
                    "path": "input/rules/01-2024-01928.xml",
                    "document_number": "2024-01928",
                    "source_url": "https://www.federalregister.gov/documents/full_text/xml/2024/01/31/2024-01928.xml",
                    "sha256": _sha256(rule),
                    "byte_count": len(rule),
                }
            ],
        },
        "output_contract": {
            "result_path": "output/result.xml",
            "provenance_path": "output/provenance.json",
        },
    }
    (episode / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return episode


def _write_multifile_candidate(root: Path) -> Path:
    candidate = root / "candidate"
    candidate.mkdir()
    (candidate / "helper.ts").write_text(
        """
export async function digest(payload) {
  const raw = await crypto.subtle.digest("SHA-256", payload);
  return Array.from(new Uint8Array(raw), (b) => b.toString(16).padStart(2, "0")).join("");
}
""",
        encoding="utf-8",
    )
    (candidate / "solution.ts").write_text(
        """
import { digest } from "./helper.ts";
const request = JSON.parse(await new Response(Deno.stdin.readable).text());
const denied = [];
for (const attempt of [
  () => Deno.readTextFileSync("evaluator/target.xml"),
  () => Deno.readTextFileSync("/etc/passwd"),
  () => Deno.env.get("HOME"),
  () => new Deno.Command("/bin/echo").outputSync(),
  () => Deno.writeTextFileSync("escape.xml", "no"),
  () => Deno.systemMemoryInfo(),
]) {
  try { attempt(); denied.push(false); }
  catch (error) { denied.push(error instanceof Deno.errors.NotCapable); }
}
try { await fetch("http://127.0.0.1:8080/"); denied.push(false); }
catch (error) { denied.push(error instanceof Deno.errors.NotCapable); }
console.log(JSON.stringify(denied));
const result = await Deno.readFile(request.base.path);
await Deno.writeFile(request.output.result_path, result);
await Deno.writeTextFile(request.output.provenance_path, JSON.stringify({
  schema_version: 1,
  episode_id: request.episode_id,
  base_sha256: request.base.sha256,
  rule_sha256s: request.rules.map((rule) => rule.sha256),
  result_sha256: await digest(result),
}) + "\\n");
""",
        encoding="utf-8",
    )
    return candidate


def test_multifile_runner_isolates_target_and_receipts_two_deterministic_runs(
    tmp_path: Path,
) -> None:
    episode = _write_episode(tmp_path)
    candidate = _write_multifile_candidate(tmp_path)
    runner = RegPatchRunner()
    runs = runner.run_twice(candidate, episode)

    assert runs.first.ok and runs.second.ok
    assert json.loads(runs.first.stdout) == [True] * 7
    assert runs.first.result == BASE == runs.second.result
    assert runs.first.candidate_provenance_valid
    assert runs.second.candidate_provenance_valid
    assert runs.deterministic
    receipt = runs.evaluator_receipt()
    assert receipt["deterministic"] is True
    receipt_runs = receipt["runs"]
    assert isinstance(receipt_runs, list)
    assert all(isinstance(item, dict) for item in receipt_runs)
    assert receipt_runs[0]["submission_sha256"] == receipt_runs[1]["submission_sha256"]
    serialized = json.dumps(receipt).lower()
    assert "target.xml" not in serialized
    assert "evaluator/" not in serialized


def test_runner_fails_closed_on_links_private_manifest_and_undeclared_output(
    tmp_path: Path,
) -> None:
    episode = _write_episode(tmp_path)
    runner = RegPatchRunner()

    candidate = _write_multifile_candidate(tmp_path)
    linked = candidate / "linked.ts"
    linked.symlink_to(candidate / "helper.ts")
    with pytest.raises(ValueError, match="symlink"):
        runner.run_twice(candidate, episode)
    linked.unlink()

    rules_directory = episode / "input/rules"
    moved_rules = episode / "evaluator/rules-canary"
    rules_directory.rename(moved_rules)
    rules_directory.symlink_to(moved_rules, target_is_directory=True)
    with pytest.raises(ValueError, match="crosses a symlink"):
        runner.run_twice(candidate, episode)
    rules_directory.unlink()
    moved_rules.rename(rules_directory)

    manifest_path = episode / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["target_sha256"] = _sha256(TARGET)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="evaluator-only key"):
        runner.run_twice(candidate, episode)
    del manifest["target_sha256"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    (candidate / "solution.ts").write_text(
        """
const request = JSON.parse(await new Response(Deno.stdin.readable).text());
const result = await Deno.readFile(request.base.path);
await Deno.writeFile(request.output.result_path, result);
await Deno.writeTextFile("output/undeclared", "leak");
""",
        encoding="utf-8",
    )
    runs = runner.run_twice(candidate, episode)
    assert not runs.first.ok and not runs.second.ok
    assert "undeclared file" in (runs.first.violation or "")

    (candidate / "solution.ts").write_text(
        """
import { digest } from "./helper.ts";
const request = JSON.parse(await new Response(Deno.stdin.readable).text());
const result = new TextEncoder().encode("<R>" + Date.now() + "</R>");
await Deno.writeFile(request.output.result_path, result);
await Deno.writeTextFile(request.output.provenance_path, JSON.stringify({
  schema_version: 1,
  episode_id: request.episode_id,
  base_sha256: request.base.sha256,
  rule_sha256s: request.rules.map((rule) => rule.sha256),
  result_sha256: await digest(result),
}) + "\\n");
""",
        encoding="utf-8",
    )
    nondeterministic = runner.run_twice(candidate, episode)
    assert nondeterministic.first.ok and nondeterministic.second.ok
    assert nondeterministic.first.candidate_provenance_valid
    assert nondeterministic.second.candidate_provenance_valid
    assert not nondeterministic.result_deterministic
    assert not nondeterministic.deterministic


def test_all_five_baselines_are_executable_and_public_hardcode_does_not_generalize(
    tmp_path: Path,
) -> None:
    episode = _write_episode(tmp_path)
    hidden = _write_episode(tmp_path, episode_id="held-out-seed")
    runner = RegPatchRunner()
    results: dict[str, bytes] = {}

    for name in BASELINE_NAMES:
        destination = tmp_path / f"baseline-{name}"
        if name == "public-hardcode":
            write_baseline(
                name,
                destination,
                public_episode_id="public-seed",
                public_target=TARGET,
            )
        else:
            write_baseline(name, destination)
        runs = runner.run_twice(destination, episode)
        assert runs.first.ok and runs.second.ok, (name, runs.first.stderr)
        assert runs.first.candidate_provenance_valid
        assert runs.deterministic
        assert runs.first.result is not None
        results[name] = runs.first.result

    assert results["copy-before"] == BASE
    assert b"REGPATCH_RULE_TEXT" in results["copy-rule-replacement"]
    assert b"Shawano" in results["naive-regex"]
    assert results["naive-regex"] != TARGET
    assert results["public-hardcode"] == TARGET
    assert b"<SECTION>" in results["damaging-partial"]
    assert results["damaging-partial"] != TARGET

    hardcode = tmp_path / "baseline-public-hardcode"
    hidden_runs = runner.run_twice(hardcode, hidden)
    assert hidden_runs.first.result == BASE
    assert hidden_runs.first.result != TARGET
