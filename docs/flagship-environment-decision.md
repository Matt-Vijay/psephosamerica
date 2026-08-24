# Flagship environment decision and acceptance gates

**Status:** FROZEN before implementation
**Decision date:** 2026-08-23
**Scope:** the current local corpus and Transfer Eval V0

This document is the pre-implementation gate. A change to the thesis, candidate-visible surface,
oracle boundary, reward, or exit criteria requires new measured evidence and an explicit amendment
here; implementation work must not silently weaken the gates.

## Decision

**KILL the proposed legislature-procedure and publication-order incident flagship on the current
corpus.** Do not wrap the Time Machine in persistent scaffolding and call that a long-horizon RL
environment. The data do not contain a private correct replay of legislative procedure or of an
evolving public-record service.

**PIVOT: harden/package the strongest honest executable-transfer artifact.** That artifact is
Transfer Eval V0: one bounded OpenStates normalization task with a deterministic behavioral score,
one public case, and three evaluator-side cases. Packaging it well is useful. It does not satisfy the
flagship north star, and passing every gate in this document does not turn it into a flagship.

The frozen research claim is:

> Given bounded real OpenStates CSV slices, their artifact receipt, and a cutoff, a candidate
> executable can be scored exactly for preserving semantic identity, cutoff behavior, normalized
> fields, and provenance on unseen session slices that obey the same published contract.

No legal interpretation, procedural outcome inference, continual maintenance, or long-horizon agent
claim is part of that sentence.

## Evidence that fixes the decision

| Question | Measured answer | Consequence |
|---|---|---|
| Does the corpus witness legislative transition families with official before state, transition evidence, official after state, and an independent exact witness? | **0** strict families. The broader compiler audit finds only one broadly instantiated procedure: OpenStates normalization/key construction. | Legislature replay is killed, not deferred behind more harness code. See the [signal audit](harvey-legora-signal-audit.md) and its [machine-readable measurements](harvey-legora-signal-audit.json). |
| Is state history an observed publication stream? | No. All 680 inventoried OpenStates artifacts were first observed at `2026-06-24T23:40:40.306472Z`; the 677 normalized nonfederal sessions are retrospective snapshots. | Historical event dates cannot be relabeled as contemporary observation times or publication-order batches. See the [replay feasibility audit](legislature-replay-feasibility.md#missing-specification-and-state) and [Time Machine clock contract](time-machine-v1.md#point-in-time-semantics). |
| Can current `as_of` views serve as a correct historical graph replay? | No. Base tables are referentially closed, but the audited historical `*_as_of` views are not parent/child closed because relations use different availability and validity clocks. | No path-replay or persistent incident oracle may be built on those views until a closure policy is specified and tested. See [Temporal path closure](harvey-legora-signal-audit.md#temporal-path-closure). |
| Can votes determine legislative procedure? | No. `yes > no` already matches 98.223% of covered results, while the exceptions require rules, roster/quorum state, or correction truth that is not retained. Later actions remain discretionary. | Tallying is an integrity check, not a procedure environment. See [Legislature replay feasibility](legislature-replay-feasibility.md). |
| What does Transfer Eval actually contain? | Four slices of the same task: **939 raw records, 937 cutoff-eligible records** (254/255 public; 299/300, 316/316, and 68/68 hidden). The fixed fixtures are about 0.5 MiB. | It is real-data executable transfer, not task-family diversity. See [Transfer Eval V0](../src/transfer_eval/README.md#one-task-four-cases). |
| What has scored 100? | The trusted test implementation scores **100.0** on the public case and all hidden cases. This is a reference check, not an agent baseline. | It proves attainability and scorer consistency only. See [Measured evidence](../src/transfer_eval/README.md#measured-evidence). |
| How long and stateful is the current episode? | A local 2026-08-23 run of the trusted implementation with Deno 2.8.3 took 0.67 s public and 1.58 s hidden. Each case starts in a fresh temporary directory, writes no durable state, and runs twice. | The current task is stateless and under two seconds on that measured host, not a roughly 100k-token persistent rollout. Runtime is a host-specific measurement, not an SLA. See the [runner](../src/transfer_eval/runner.py). |

No frontier-agent baseline has been run as of this freeze. The repository says so explicitly; an
unrun baseline must never be reported as a result.

## Frozen product contract

The product to be hardened is one evaluator package, not a benchmark framework or a family
generator.

1. `bundle` creates a fresh, non-git candidate directory and refuses to overwrite an existing path.
   Its exact allow-list is `TASK.md`, `schema.json`, `solution.ts`, one public request, its public
   development answer, and the losslessly filtered public source rows and receipt.
2. The candidate edits one regular JavaScript or TypeScript file of at most 128 KiB. It reads the
   request on stdin and the explicitly mounted case inputs, then emits closed-schema JSONL on
   stdout. The normative task details remain in [`pilot/TASK.md`](../src/transfer_eval/pilot/TASK.md).
3. Public development grading uses the one public Georgia slice. Evaluation freezes the exact
   candidate bytes by SHA-256 and grades those same bytes against the three unseen slices.
4. Every case is a fresh process and fresh temporary directory. There is no writable persistent
   service, cross-case memory, interactive tool loop, or hidden state transition. Adding empty
   persistence plumbing is outside scope.
5. The grader evaluates output behavior, not source style. It uses exact keys, fields, clocks,
   provenance, integrity, and repeated-run determinism; an LLM judge is not part of the primary or
   secondary reward.
6. Inputs remain byte-faithful extracts of already-local immutable OpenStates artifacts. Raw
   evidence is not mutated, the roughly 77 GB source tree is not copied, and metadata links are not
   called full text. The provenance and text distinctions in [Time Machine V1](time-machine-v1.md)
   are normative.
7. A row after the cutoff may be present specifically to test exclusion. That is a deterministic
   filtering input, not permission to claim that the candidate predicts a future event or that the
   retrospective artifact was historically available.

## Public/private boundary

“Private” means unavailable to the candidate process and candidate workspace. It does not mean
that a neutral evaluator cannot inspect or audit it.

| Surface | Candidate-visible | Evaluator-only |
|---|---|---|
| Instructions and schema | Task contract, output schema, resource limits, starter | Scorer implementation and hidden report details |
| Data | One public request, source receipt, losslessly filtered public CSV rows, public development answer | Three hidden requests and CSV slices; reference projections; private Time Machine Parquet/catalog directory |
| Executable | Candidate's single frozen `.js` or `.ts` file | Trusted reference implementation, Python grader, fixed trusted Deno binary |
| Receipts | Public source URLs, hashes, byte counts, and observation metadata | Live oracle files; exact expected hidden JSONL. The committed [`oracle_receipt.json`](../src/transfer_eval/pilot/oracle_receipt.json) may disclose hashes, counts, and pass/fail comparisons but never hidden answers. |
| Feedback | Full public component/case score | Hidden feedback is aggregate only: no case names, raw input, expected rows, stderr, parser errors, or per-case values |

The evaluator repository itself is never the candidate workspace. The candidate must be launched
inside the audited output of `bundle`; repository history, hidden fixtures, grader source, trusted
solution, oracle, and other worktree files must not be mounted. A score produced while the candidate
can inspect this repository is invalid.

The live Time Machine is an independent hidden witness with respect to candidate execution, but the
reference projection and Time Machine share source lineage. Documentation must say “oracle-validated
normalization,” not imply independent legal adjudication.

## Threat model and required controls

Assume a hostile submission attempts direct and `node:` filesystem reads, path traversal, symlinks,
special files, environment or credential reads, subprocesses, remote/local network access, remote
imports, FFI/system calls, writes, nondeterminism, output floods, CPU/memory/file-descriptor denial of
service, public-answer hardcoding, source-specific branching, duplicates, fabricated records, and
hidden-input exfiltration through diagnostics.

The release must demonstrate all of these controls:

- audit an exact public-bundle and per-case file allow-list; reject symlinks, special files, path
  escape, oversize inputs, and oversize submissions before execution;
- copy and hash-freeze the candidate bytes before grading, then use only that copy;
- mount only the frozen executable and current staged input; deny network, environment, process,
  write, FFI, system, and remote/import-host capabilities;
- pin and verify a trusted Deno 2.x executable, use a deterministic seed, and bound wall time, CPU,
  stdout/stderr, file descriptors, staged input, and the main V8 heap;
- run every case twice and award a case zero when either run times out, is resource-terminated, or
  exits nonzero;
- prevent hidden reports from reproducing case IDs, input bytes, expected bytes, stderr, or detailed
  parser errors; and
- test that a hardcoded public record scores zero on a hidden case and that false, future,
  duplicated, and malformed records reduce reward.

The trusted computing base is the neutral host OS, the fixed Deno binary, the Python grader, the
case/reference builders, and the private oracle custodian. A Deno vulnerability, malicious host,
microarchitectural side channel, or evaluator operator is out of scope. Deno worker isolates remain
a known denial-of-service gap because aggregate worker memory/thread count is not kernel-enforced.
Repeated adaptive submissions to the hidden service also require an external attempt/rate policy;
the runner alone cannot prevent leaderboard hill-climbing. These limitations must remain prominent,
not be converted into “secure sandbox” claims.

## Score and hard gates

For each case, the continuous base score is the weighted sum below. The implementation in
[`score.py`](../src/transfer_eval/score.py) is normative and must stay independently tested.

| Component | Weight | Exact subject |
|---|---:|---|
| Schema | 10% | closed-schema parsing, unique semantic keys, valid parent references |
| Keys | 25% | record-type-weighted macro semantic-key F1 |
| Temporal | 20% | geometric mean of eligible inclusion and future/ineligible exclusion |
| Fidelity | 25% | exact normalized non-provenance fields on matched keys |
| Provenance | 15% | exact source ID, URL set, content hash, and availability fields |
| Determinism | 5% | identical canonical output from two successful runs |

The base is multiplied by:

```text
matched expected semantic keys / all nonblank output lines
```

This multiplier makes fabricated and duplicate output directly costly. Scores are averaged equally
across cases; row volume does not let one jurisdiction dominate.

The following are hard gates, not optional score components:

- A timeout, resource termination, or nonzero exit in either repeated run makes that case **0**.
- A score of **100** requires every component to equal 1, every eligible key and exact field to be
  correct, every ineligible/future record to be absent, no extra or duplicate nonblank lines, and
  identical repeated output.
- A run may be called **oracle-validated** only when fixture projections equal the live private Time
  Machine and the catalog, build report, integrity report, and committed oracle-receipt hashes all
  agree. `--without-oracle` is self-contained validation, not live-oracle validation.
- If the public bundle allow-list, case receipts, submission freeze, capability probe, or hidden
  redaction check fails, no candidate score may be released.
- A trusted reference score is never reported as a model or agent baseline.

## One-command verification and zero-to-score path

These are the frozen release interfaces. The implementation is accepted only when each command
performs the listed checks; the existence of a command name alone is not evidence that every gate is
wired into it. Commands assume the documented Python dependencies and Deno 2.x are installed.

**Fast, self-contained verification (no private Time Machine):**

```sh
python -m src.transfer_eval validate --without-oracle
```

It must audit every committed fixture and bundle boundary, run the focused scorer/sandbox/security
checks, run the trusted implementation twice on every case, and prove 100.0 per case. On the declared
reference host it must finish in at most 60 seconds and print its measured duration and exact runtime
versions.

**Full evaluator verification:**

```sh
python -m src.transfer_eval validate --oracle data/time_machine
```

It must include every fast check, compare all four projections to the live private oracle, rehash the
catalog/build/integrity files, validate the oracle receipt, run every required weak baseline, and
emit a machine-readable summary. On the declared reference host it must finish in at most 10 minutes;
the report must record actual wall time rather than imply portability of the local under-two-second
grade measurement.

**Fresh bundle to a score:**

```sh
python -m src.transfer_eval bundle /tmp/psephos-transfer-public
python -m src.transfer_eval grade /tmp/psephos-transfer-public/solution.ts --split public
python -m src.transfer_eval grade /tmp/psephos-transfer-public/solution.ts --split hidden
```

The hidden command belongs on the neutral evaluator only. A release smoke test must execute this
path from a newly created destination, capture the bundle and submission hashes, and refuse an
existing destination. Full setup instructions remain in the [Transfer Eval README](../src/transfer_eval/README.md#zero-to-a-score).

## Reference and baseline gates

Before release:

1. The trusted reference must score 100.0 on the public case and on each hidden case, with all six
   components equal to 1, both executions successful, no timeout, and identical output. Its exact
   source hash, Deno version, host description, and wall time must be recorded. It stays evaluator
   side.
2. At least these executable weak strategies must be checked in the same sandbox: empty output;
   public-answer hardcoding; source-record-only copying; raw/pass-through or field-copy
   normalization; no-cutoff filtering; unqualified upstream IDs that collide across sessions; and a
   Georgia/public-source-specific parser. A strategy may be combined only when its failure mode
   remains identifiable.
3. Each baseline record must include source and artifact hashes, exact command, runtime versions,
   case/component scores, wall time, and stdout report. A baseline is a result only after that record
   is committed. At minimum, public hardcoding must remain 0 on a hidden case, no-cutoff must lose
   temporal reward, and collision-unsafe IDs must fail the reused-ID case.
4. If any weak strategy reaches 100 on all hidden cases, release stops until the shortcut is removed
   or the claim is narrowed again. Low weak-baseline scores do not prove frontier difficulty.
5. A frontier baseline is conditional on quota/availability. If run, record exact model, model
   snapshot when available, reasoning effort, tools, budget, candidate-visible files, transcript,
   submission hash, retries, runtime, and public/hidden scores. Otherwise state exactly “not run”; do
   not estimate a score or advertise difficulty.

Regex, vote majority, and group-by baselines are relevant to the killed procedure thesis and are
already measured in the [replay feasibility audit](legislature-replay-feasibility.md). They are not
pretended to be meaningful candidate programs for this normalization contract. Copying,
pass-through, cutoff omission, collision-unsafe keys, hardcoding, and source-specific parsing are
the appropriate shortcut controls here.

## Allowed and forbidden claims

Allowed after the corresponding gates pass:

- “one bounded executable-transfer task over real, immutable OpenStates source slices”;
- “deterministic continuous behavioral score without an LLM judge”;
- “exactly 937 eligible records out of 939 raw records in the fixed four-case pilot”;
- “trusted reference scores 100.0,” explicitly identified as a reference implementation;
- “candidate process is capability-restricted under the documented Deno threat model”;
- “tests transfer across sessions and documented quirks under one OpenStates CSV contract”; and
- “oracle-validated” only for a run that passed the live-oracle and receipt gate.

Forbidden on the current evidence:

- “flagship RL environment,” “Mechanize-grade environment,” “long-horizon,” “100k-token task,”
  “persistent incident response,” or “production system recovery”;
- “legislative procedure replay,” “legal reasoning,” “current-law maintenance,” “amendment
  propagation,” “codification,” or “authority resolution”;
- “four tasks,” “task distribution,” or diversity obtained by multiplying rows, jurisdictions,
  cutoffs, masks, or filenames under the same procedure;
- “contemporaneous history” based on historical event dates in one retrospective snapshot;
- “point-in-time graph replay” while `as_of` parent/child closure is unresolved;
- “independent legal oracle” for projections derived from the same source lineage;
- “secure sandbox” without the Deno, host, worker, and adaptive-query qualifications;
- treating OpenStates version metadata as retained legislative text; or
- calling an unrun model, hypothetical script, or trusted gold implementation a baseline result.

## Exact exit criteria

The pivot program is complete only when all of the following are true:

- this kill/pivot decision is prominent in the package and no active documentation contradicts it;
- the candidate task contract, schema, public fixture, and scorer agree byte-for-byte on every field,
  clock, ID, null/dangling case, and provenance rule;
- the candidate bundle is created atomically from an exact allow-list and contains no repository,
  hidden, oracle, grader, credential, or answer artifact beyond the intentional public development
  answer;
- fast and full one-command validation satisfy the behaviors and runtime bounds above on a recorded
  reference host;
- the trusted reference passes 100.0 per case and the live oracle receipt passes;
- focused tests cover score truth, cutoff false records, collisions, hostile schemas/contracts,
  determinism, resource limits, file and capability isolation, atomic publishing, hidden-output
  redaction, and hardcoding;
- all required weak baselines have actually run and their machine-readable reports are committed;
- a fresh-directory bundle-to-public-score-to-hidden-score smoke test passes without network access;
- the threat model, reward decomposition, provenance receipt, measured runtimes, baseline status,
  boundaries, and known weaknesses are understandable without reading implementation code;
- no redundant framework, synthetic task multiplication, copied bulk corpus, or unused persistent
  service was added; and
- repository tests are green, status contains no unexplained generated material, and coherent local
  commits exist. Nothing is pushed.

Completion means a defensible packaged pilot plus a documented kill decision. It does **not** mean
the original flagship requirement was met.

## Smallest acquisition that can reopen the flagship decision

The minimum useful acquisition is **one complete, contemporaneously captured official legislative
publication stream for one jurisdiction**, not another retrospective bulk snapshot. Retain every
byte-changing observation in publication order for stable bill/action/vote/member identifiers,
including full official bill and amendment text, response timestamps and headers, correction or
deletion/tombstone notices, and an authoritative end-state snapshot. Store immutable bytes and a
content-addressed manifest separately from any candidate bundle.

Continue the capture until it contains at least three non-isomorphic, independently witnessed
before/event/after transition families across held-out time partitions; row count alone is not a go
signal. If procedural results will be graded, the same acquisition must also retain the effective
rules, event-time rosters/vacancies, quorum and threshold state, normalized motion type, and explicit
vote-to-action links. Before any incident task is admitted, specify and test one parent/child
referential-closure policy and prove that the private replay can be reconstructed without future
observations.

Only then rerun the kill criteria in the [signal audit](harvey-legora-signal-audit.md#what-would-change-the-verdict).
If fewer than three families survive copy, parser, hash, lookup, regex, group-by, majority, and set-
difference baselines, this decision remains KILL.
