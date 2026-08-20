from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import src.transfer_eval.cli as cli_module
import src.transfer_eval.runner as runner_module
from src.transfer_eval.cases import PILOT_CASES, PilotCase, audit_case, case_path
from src.transfer_eval.contract import canonical_output, parse_jsonl, semantic_key
from src.transfer_eval.reference import ReferenceSlice, derive_reference, validate_oracle
from src.transfer_eval.runner import MAX_OUTPUT_BYTES, DenoRunner
from src.transfer_eval.score import score_case

ROOT = Path(__file__).parents[1]
GOLD = ROOT / "tests/fixtures/transfer_eval_solution.ts"
ORACLE = ROOT / "data/time_machine"


def test_score_truth_and_future_false_record_penalty() -> None:
    reference = derive_reference(case_path(PILOT_CASES[0]))
    perfect = parse_jsonl(canonical_output(reference.eligible).encode())
    perfect_score = score_case(reference, perfect, perfect)
    assert perfect_score.overall == 100.0
    assert set(perfect_score.components.values()) == {1.0}

    eligible_keys = {semantic_key(row) for row in reference.eligible}
    future = next(row for row in reference.universe if semantic_key(row) not in eligible_keys)
    polluted_payload = canonical_output((*reference.eligible, future)).encode()
    polluted = parse_jsonl(polluted_payload)
    polluted_score = score_case(reference, polluted, polluted)
    assert polluted_score.components["temporal"] == 0.0
    assert polluted_score.false_record_factor == pytest.approx(254 / 255)
    assert polluted_score.overall < perfect_score.overall

    failed = score_case(reference, perfect, perfect, first_ok=False)
    assert failed.overall == 0.0
    assert set(failed.components.values()) == {0.0}

    empty = ReferenceSlice("empty", reference.cutoff, (), reference.universe)
    no_output = parse_jsonl(b"")
    assert score_case(empty, no_output, no_output).overall == 100.0

    wrong_actions = []
    for row in reference.eligible:
        changed = dict(row)
        if row["record_type"] == "action":
            changed.update(
                organization_id=None,
                description="wrong",
                classifications=["wrong"],
                event_date="1900-01-01",
            )
        wrong_actions.append(changed)
    wrong = parse_jsonl(canonical_output(tuple(wrong_actions)).encode())
    assert score_case(reference, wrong, wrong).components["fidelity"] < 0.8

    invalid = json.loads(json.dumps(reference.eligible[0]))
    invalid["not_in_contract"] = True
    parsed = parse_jsonl((json.dumps(invalid) + "\n").encode())
    assert parsed.valid_lines == 0
    assert "extra=['not_in_contract']" in parsed.errors[0]
    assert parse_jsonl(b'{"record_type":[]}\n').valid_lines == 0
    nested = ("[" * 2000 + "0" + "]" * 2000 + "\n").encode()
    assert parse_jsonl(nested).valid_lines == 0


def test_deno_runner_denies_oracle_history_network_and_ambient_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = tmp_path / "solution.ts"
    candidate.write_text(
        """
const request = JSON.parse(await new Response(Deno.stdin.readable).text());
const denied = [];
for (const path of [
  "/Users/matthew/Documents/ChatGPT/coding projs/psephosamerica/.git/HEAD",
  "/Users/matthew/Documents/ChatGPT/coding projs/psephosamerica/data/time_machine/time_machine.duckdb",
  "/Users/matthew/Documents/ChatGPT/coding projs/psephosamerica/src/transfer_eval/pilot/cases/public/ga_hb579_cutoff/dev_expected.jsonl",
]) {
  try { Deno.readTextFileSync(path); denied.push(false); }
  catch (error) { denied.push(error instanceof Deno.errors.NotCapable); }
}
try { const fs = await import("node:fs"); fs.readFileSync("/etc/passwd"); denied.push(false); }
catch (error) { denied.push(error instanceof Deno.errors.NotCapable); }
for (const attempt of [
  () => Deno.env.get("HOME"),
  () => new Deno.Command("/bin/echo").outputSync(),
  () => Deno.writeTextFileSync("leak", "x"),
  () => Deno.systemMemoryInfo(),
]) {
  try { attempt(); denied.push(false); }
  catch (error) { denied.push(error instanceof Deno.errors.NotCapable); }
}
try { await fetch("http://127.0.0.1:8080/"); denied.push(false); }
catch (error) { denied.push(error instanceof Deno.errors.NotCapable); }
console.error("DENIED=" + JSON.stringify(denied));
for (const artifact of request.artifacts) console.log(JSON.stringify({
  record_type: "source", source_id: artifact.source_id, source_url: artifact.source_url,
  content_sha256: artifact.content_sha256, available_at: artifact.available_at,
}));
""",
        encoding="utf-8",
    )
    runner = DenoRunner()
    runs = runner.run_twice(candidate, case_path(PILOT_CASES[0]))
    assert runs.first.ok and runs.second.ok
    assert "DENIED=[true,true,true,true,true,true,true,true,true]" in runs.first.stderr
    assert runs.first.parsed.valid_lines == 1

    symlink = tmp_path / "linked.ts"
    symlink.symlink_to(candidate)
    with pytest.raises(ValueError, match="regular file"):
        runner.run_twice(symlink, case_path(PILOT_CASES[0]))

    contaminated = tmp_path / "contaminated"
    shutil.copytree(case_path(PILOT_CASES[0]), contaminated)
    (contaminated / "input/expected.jsonl").write_text("secret", encoding="utf-8")
    with pytest.raises(ValueError, match="allow-list"):
        audit_case(contaminated)
    with pytest.raises(ValueError, match="allow-list"):
        runner.run_twice(candidate, contaminated)

    linked_input = tmp_path / "linked-input"
    linked_input.mkdir()
    shutil.copy2(case_path(PILOT_CASES[0]) / "request.json", linked_input / "request.json")
    (linked_input / "input").symlink_to(
        case_path(PILOT_CASES[0]) / "input", target_is_directory=True
    )
    with pytest.raises(ValueError, match="non-symlink directory"):
        runner.run_twice(candidate, linked_input)

    flood = tmp_path / "flood.js"
    flood.write_text(f'console.log("x".repeat({MAX_OUTPUT_BYTES + 1024}));\n', encoding="utf-8")
    flooded = runner.run_twice(flood, case_path(PILOT_CASES[0]))
    assert not flooded.first.ok and not flooded.second.ok
    assert len(flooded.first.stdout) <= MAX_OUTPUT_BYTES

    loop = tmp_path / "loop.js"
    loop.write_text("while (true) {}\n", encoding="utf-8")
    with monkeypatch.context() as context:
        context.setattr(runner_module, "WALL_SECONDS", 0.2)
        timed = runner.run_twice(loop, case_path(PILOT_CASES[0]))
    assert timed.first.timed_out and timed.second.timed_out
    assert not timed.first.ok and not timed.second.ok


def test_bundle_publishes_atomically_and_hidden_report_cannot_echo_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rejected = tmp_path / "rejected"
    with monkeypatch.context() as context:
        context.setattr(
            cli_module,
            "audit_public_bundle",
            lambda _: (_ for _ in ()).throw(ValueError("audit failure")),
        )
        with pytest.raises(ValueError, match="audit failure"):
            cli_module.main(["bundle", str(rejected)])
    assert not rejected.exists()

    published = tmp_path / "public"
    assert cli_module.main(["bundle", str(published)]) == 0
    assert published.is_dir() and not (published / ".git").exists()
    capsys.readouterr()

    exfil = tmp_path / "exfil.js"
    exfil.write_text(
        """
const request = JSON.parse(await new Response(Deno.stdin.readable).text());
const text = await Deno.readTextFile(request.artifacts[0].path + "/bills.csv");
console.log(JSON.stringify({record_type: text}));
console.error(text);
""",
        encoding="utf-8",
    )
    assert cli_module.main(["grade", str(exfil), "--split", "hidden"]) == 0
    report = capsys.readouterr().out
    assert "Veterinary medicine" not in report
    assert "mn_hf3718_cutoff" not in report
    assert '"errors"' not in report and '"stderr"' not in report
    assert '"overall"' in report and '"cases"' not in report


def test_public_answer_hardcoding_scores_zero_on_hidden(tmp_path: Path) -> None:
    public_source = derive_reference(case_path(PILOT_CASES[0])).eligible[0]
    candidate = tmp_path / "solution.js"
    candidate.write_text(
        f"console.log({json.dumps(json.dumps(public_source))});\n", encoding="utf-8"
    )
    hidden = PILOT_CASES[1]
    reference = derive_reference(case_path(hidden))
    runs = DenoRunner().run_twice(candidate, case_path(hidden))
    score = score_case(
        reference,
        runs.first.parsed,
        runs.second.parsed,
        first_ok=runs.first.ok,
        second_ok=runs.second.ok,
    )
    assert runs.first.ok and runs.second.ok
    assert score.overall == 0.0
    assert score.false_record_factor == 0.0


@pytest.mark.parametrize("case", (PILOT_CASES[0], PILOT_CASES[2]))
def test_gold_program_matches_real_public_and_hidden_oracle(case: PilotCase) -> None:
    case_dir = case_path(case)
    oracle = validate_oracle(case_dir, ORACLE)
    assert oracle["status"] == "PASS"
    reference = derive_reference(case_dir)
    runs = DenoRunner().run_twice(GOLD, case_dir)
    score = score_case(
        reference,
        runs.first.parsed,
        runs.second.parsed,
        first_ok=runs.first.ok,
        second_ok=runs.second.ok,
    )
    assert score.overall == 100.0
    assert set(score.components.values()) == {1.0}
