# Psephos Transfer Eval V0

**Pilot thesis:** an agent that has recovered a real public-record procedure should preserve
identity, time, vote meaning, and provenance on unseen session exports; exact executable agreement
on those behaviors gives a hard, continuous reward.

This directory is the complete one-task pilot. It is intentionally not a benchmark framework.
The private American Legislative Time Machine V1 is the oracle; candidates receive only one small
public bundle of raw CSV rows, a contract, a starter, and a development answer.

## Zero to a score

From the repository root, with project dependencies and Deno 2.x installed:

```sh
# Self-contained fixture/reference validation (no private data required).
python -m src.transfer_eval validate --without-oracle

# Optional live proof against a local private Time Machine V1 build.
python -m src.transfer_eval validate --oracle data/time_machine

# Create the only directory that a candidate agent is allowed to see.
python -m src.transfer_eval bundle /tmp/psephos-transfer-public

# Edit /tmp/psephos-transfer-public/solution.ts, then get a development score.
python -m src.transfer_eval grade /tmp/psephos-transfer-public/solution.ts --split public

# Freeze/hash that same file and evaluate the three unseen cases.
python -m src.transfer_eval grade /tmp/psephos-transfer-public/solution.ts --split hidden
```

`bundle` fails if the destination exists and audits an explicit public-file allow-list. The agent
must be launched inside that fresh non-git bundle—not in this grader repository. `grade` validates
the single file, copies and SHA-256 freezes its bytes, then runs that copy twice per case.

The exact trusted answer for a grader-side case can be inspected with:

```sh
python -m src.transfer_eval reference src/transfer_eval/pilot/cases/public/ga_hb579_cutoff
```

## One task, four cases

All fixtures preserve original CSV headers, values, and row order. `SOURCE.json` records the exact
official parent ZIP URL, byte count, inventory SHA-256, observation time, original member names,
and a digest over the extracted lossless row filter.

| Split | Real source behavior | Eligible / raw scored records |
|---|---|---:|
| Public Georgia 2025–26 | future effective-date action, null people, `other`, Python-list cells | 254 / 255 |
| Hidden Minnesota 2025–26 | different session/date shapes and future effective date | 299 / 300 |
| Hidden North Carolina 2023 + 2025 | one upstream roll ID reused for different events | 316 / 316 |
| Hidden Missouri 2017 special | no organizations table and a roll linked to an absent bill | 68 / 68 |

These are cases of exactly one normalization task, not four task families. The fixed fixture set is
about 0.5 MiB; no corpus expansion or redownload is involved.

## Environment and reward

The runner uses Deno's permission model because macOS Seatbelt cannot be nested in the current
execution environment and no container runtime is present. It gives the candidate read access only
to its frozen source and the current staged input. Network, environment, subprocess, write, FFI,
system, and remote-import capabilities are denied. CPU, wall-clock, stdout/stderr file size, file
descriptor, and main V8 heap limits apply. Inputs and submissions must be bounded regular files; every
run gets a fresh unrelated temporary directory. This is materially stronger than cwd/env scrubbing,
but it is a capability sandbox, not a kernel VM, and it intentionally accepts only JS/TS. The host
trusts a Deno binary installed at a fixed system path; candidate CLI arguments cannot replace it.

Each case produces six `[0,1]` components:

- closed-schema parse/uniqueness/reference validity: 10%;
- weighted macro semantic-key F1: 25%;
- cutoff inclusion and exclusion: 20%;
- normalized field fidelity: 25%;
- source URL/hash/availability fidelity: 15%;
- exact two-run determinism: 5%.

The weighted score is multiplied by `matched expected keys / all nonblank output lines`. Thus a
precise partial procedure earns partial credit, while duplicates and fabricated facts lower the
score. Scoring examines output behavior only. It uses no implementation heuristic and no LLM judge.

## Measured evidence

The focused verification currently establishes:

- all four fixture/reference projections match `tm.*_as_of(cutoff)` in Time Machine V1;
- the independent gold executable scores **100.0** on public and every hidden case;
- a submission hardcoding the public source record scores **0.0** on a hidden case;
- direct and `node:fs` reads of repository history, public expected output, and the DuckDB oracle
  are denied, as are network, environment, process, write, and system attempts;
- six focused test executions cover score truth, a future false record, hostile contracts,
  isolation/resource limits, atomic publishing, hidden-output exfiltration, hardcoding, plus one
  real public and one real hidden oracle run.

No frontier-agent baseline has been run. The 100 is a trusted test implementation, not evidence
that the task is easy or hard for a general agent.

`pilot/oracle_receipt.json` records the per-table comparison and SHA-256 identities of the V1
catalog, build report, and passing integrity report used for that check. The compact receipt and
fixtures travel with the repository; rerunning the live oracle check additionally requires the
private local Time Machine Parquet/catalog directory.

## Fairness audit

- Every scored field is present in supplied rows/request metadata and specified in `TASK.md`.
- Keys are upstream semantic composites; hashed V1 implementation IDs are never required.
- Missing people/chambers and dangling bills must stay null/dangling; fuzzy joins are neither
  requested nor rewarded.
- URL sets, local-date handling, list parsing, artifact availability, and vote inheritance are
  explicit; the scorer does not depend on row order or reference implementation structure.
- Hidden cases vary jurisdiction, session, cutoff, and source quirks without changing the contract.
- Validation compares both to the supplied bounded rows and to the private oracle.

## What this does and does not establish

The pilot demonstrates an executable cleanroom loop, a hard continuous reward, honest point-in-time
behavior, real provenance, and at least one anti-hardcoding check. It does **not** demonstrate
transfer to another legislative format, legal reasoning, learning across a task distribution,
frontier-agent competence, or broad anti-shortcut robustness. Because this repository contains the
grader, it must never be the candidate workspace; only the audited public bundle is cleanroom-safe.
The admitted artifacts all predate their cutoffs, so future-event exclusion is measured but
whole-artifact unavailability is not. Worker isolates remain an honest denial-of-service gap: CPU
and wall time are bounded, but aggregate worker memory/thread count lacks a kernel-enforced cap.

For a Mechanize-grade artifact, the next work is thesis-level rather than more plumbing: sharpen the
one-sentence research claim, package the zero-to-score path for a neutral host, keep the independent
reference/oracle and cleanroom boundary auditable, retain the hard continuous reward, run credible
frontier baselines with traces and budgets, and add anti-shortcut evidence stronger than one
hardcoding control. Scaling to more tasks should happen only if those results justify it.
