# Incident-lab research summary

**Status:** measured research, not a runnable environment or frozen task contract.

The interrupted incident-lab exploration produced one reusable artifact: a small,
reproducible New Mexico fixture that preserves a real cross-session identifier
collision without claiming which source association is historically authoritative.
Generic patch parsing, receipt, and process-supervision prototypes were not connected
to a task runner or grader and are intentionally excluded from the repository.

## Corpus boundary

Measurements taken on 2026-08-23 found:

- 81,358,741,443 bytes across 996 source-data files.
- A 2,144,841,806-byte Time Machine build with 74,714,826 fact rows and 697
  source artifacts.
- Zero duplicate durable keys, broken relations, artifact-hash mismatches, or
  temporal hard failures in the measured build.
- The Time Machine is legislative, not an eight-domain connected graph. Judicial
  and spending outputs are disconnected guards; USASpending is incomplete.
- Historical `as_of` projection is not parent-closed. At the 2024-01-01 cutoff,
  10,103,155 visible actions lacked visible bills and 29,918,126 resolved votes
  lacked visible people.
- The retained archives are retrospective snapshots. Event time must not be
  presented as source availability time.

These measurements support a narrow legislative incident, not a broad political
reasoning environment.

## Concrete fixture

The retained fixture is under
`src/incident_lab/pilot/cases/prototype/nm_reassociation/input`.
Its builder and auditor live in
`src/incident_lab/prototype/nm_reassociation_fixture.py`.

It pins two New Mexico OpenStates parents:

| Archive | Bytes | SHA-256 |
| --- | ---: | --- |
| 2024 regular session | 3,865,019 | `494f1680b245747df91af34504bcfdb08af080d22136dd977d34be6e205564f3` |
| 2024 first special session | 179,822 | `00453d1dee1aa34ad5100d635692e57fbfef45af79ce8cfbcb7ed5b5c45e7758` |

Across those parents, 11 source roll-call identifiers map to 22 canonical rolls
and 1,316 member-vote rows. The compact committed extract contains 18 files and
19,259 bytes with SHA-256
`c54606d1adc5ce8bef87a302641b14ed0766e71eb679fe6d093d1df152917136`.

The sharp example is
`ocd-vote/a5a38c48-675e-4dc9-bb67-c06337a2bce8`. Both source claims share an
event date, motion, result, and source URL, but differ in session, bill, title,
source artifact, and content hash. The fixture preserves both claims. It does not
invent a winner.

An independent source projection matched all 1,338 measured semantic rows in the
Time Machine. That proves preservation of source claims, not independent external
confirmation of their historical meaning.

## Admissible task claim

A future environment may grade whether a candidate:

1. Preserves both session-scoped source claims and their artifact lineage.
2. Produces deterministic, dependency-closed temporal projections.
3. Repairs key scoping without collapsing conflicting records.
4. Detects labeled synthetic faults through independently recomputed invariants.

It must not grade which bill association is historically true without an
independent authority. It must also reject tasks solvable by a single group-by,
parser tweak, latest-record rule, or copied expected output.

## Execution boundary

The measured Mac host can support a bounded local feasibility run only. A future
runner should accept a size-bounded allow-listed patch, apply it to a fresh public
stage, scrub the environment, deny network and outside writes, bound output and
resources, kill the process group on timeout, and compare two fresh runs.

The existing Deno runner does not make time, randomness, staging paths, memory,
threads, or process counts deterministic. No container or microVM runtime was
available during measurement. Hostile multi-tenant execution therefore remains
out of scope.

## Next implementation gate

Do not add more generic harness infrastructure until one vertical slice has:

- a candidate-visible request and a private expected product;
- an independent scorer that separates session-scope, always-latest, hardcoded,
  and partial-repair baselines;
- exact public/private artifact boundaries;
- two reproducible fresh runs; and
- a measured non-trivial baseline gap.

The prototype fixture audit is the current executable gate:

```sh
.venv/bin/pytest -q tests/incident_lab/test_nm_reassociation_fixture.py
```
