# Psephos America

Psephos explores executable benchmarks grounded in real public records, with
traceable inputs and deterministic grading. The research direction is still
open: a runnable prototype is not proof of frontier-model difficulty or useful
training transfer.

## Run the current prototype

**RegPatch** gives a program historical eCFR XML and ordered Federal Register
amendments, then scores its output against the withheld historical successor.
The public demo uses committed source bytes; it needs no network, credentials,
database, or private corpus after installation.

Requirements: Python 3.12+ and Deno 2.x at `/opt/homebrew/bin/deno` or
`/usr/local/bin/deno`.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/psephos-regpatch demo /tmp/psephos-regpatch-demo
.venv/bin/psephos-regpatch grade \
  /tmp/psephos-regpatch-demo/evaluator-episode \
  /tmp/psephos-regpatch-demo/candidate-bundle/submission
```

Use a fresh output directory. The starter copies the base and deliberately
scores below 100; edit `candidate-bundle/submission/` to try another procedure.
See the [operator guide](src/regpatch/README.md) for acquisition, private suites,
measured scores, and limitations, and [TASK.md](src/regpatch/pilot/TASK.md) for
the candidate contract. `psephos-regpatch --help` lists the commands.

## Follow the code

```text
sources.py → compiler.py → evaluation.py → runner.py → grader.py
                 ↑                                      ↑
                 └────────── projection.py ─────────────┘
```

All paths above are under `src/regpatch/`. Source acquisition owns official
bytes and receipts; the compiler admits witnessed transitions; evaluation
packages public inputs and orchestrates execution; the runner isolates the
candidate; the grader compares substantive XML. The compiler and grader share
pure XML projection rules. `corpus.py` adapts the retained private collection
into the same compiler; `cli.py` only dispatches commands.

The exact source bytes, distinct observation/publication/effectiveness clocks,
and candidate/evaluator separation are contracts, not optional plumbing.
`data/regpatch_evaluator/` is ignored and private. Do not distribute the repo or
evaluator store as a candidate workspace: distribute an audited `bundle`.

## Other work in this repository

| Area | Start here | Status |
|---|---|---|
| Local legislative evidence | [Time Machine](docs/time-machine-v1.md) · `python -m src.time_machine --help` | Parquet/DuckDB inventory, build, integrity, point-in-time queries |
| Executable format transfer | [Transfer Eval](src/transfer_eval/README.md) · `python -m src.transfer_eval --help` | Bounded OpenStates normalization pilot, not the project finish line |
| Earlier graph, prediction, ledger, and UI work | [Legacy commands](USAGE.md), [architecture](docs/architecture-domains.md) | Retained for compatibility and research, not current headline claims |
| Feasibility and negative results | [Documentation index](docs/README.md) | Evidence to consult before reopening a discarded approach |

RegPatch's [measurement receipt](src/regpatch/measurements.json) records the
bounded corpus and weak baselines. No current frontier-model evaluation or
training result is claimed. The older political prediction and public-ledger
plans are historical documents, not simultaneous product requirements.

## Development

See [development.md](docs/development.md) for focused verification and legacy
checks. Preserve raw data, source receipts, secrets, and unrelated changes.
Do not run the entire historical test suite for a change isolated to RegPatch.
