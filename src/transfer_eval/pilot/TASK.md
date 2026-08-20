# Psephos Transfer Eval V0: point-in-time OpenStates slice

## Capability target

Write one small executable program that recovers a reusable normalization procedure from an
unfamiliar public-record format. Given bounded OpenStates CSV records and a cutoff, reconstruct
the bills, dated actions, roll calls, individual vote choices, and exact source provenance that
were available by that time. Hidden cases change jurisdiction, session, IDs, cutoff, and real
source quirks; copying the public answer will score zero.

Submit exactly one regular `solution.ts` or `solution.js` file, at most 128 KiB. It must use only
Deno/Web standard APIs and must not require packages, network access, environment variables,
subprocesses, FFI, system information, or disk writes.

## Interface

The grader invokes your file with one JSON request on stdin. It stages only the request's `input/`
paths as readable files. Write UTF-8 JSONL records to stdout; diagnostics may go to stderr.
`schema.json` is the exact closed output schema: all fields are required and extra fields fail
schema validation. Output order does not matter.

The request contains:

- `contract_version`, `case_id`, and an ISO-8601 `cutoff` with timezone;
- one or more artifacts with `source_id`, relative `path`, official parent ZIP URL/hash/bytes,
  `available_at`, `jurisdiction_id`, and `session`.

Each artifact path contains byte-faithful OpenStates field values and original headers in the
bounded CSV members `bills.csv`, `bill_actions.csv`, `bill_sources.csv`, `votes.csv`,
`vote_people.csv`, `vote_counts.csv`, `vote_sources.csv`, and sometimes `organizations.csv`.
`README` and `SOURCE.json` tie those extracted, lossless row filters to the inventoried official
parent ZIP. You are not being tested on ZIP/DEFLATE implementation.

Emit:

- one `source` per artifact;
- one `bill` per supplied bill row;
- one `action` per supplied action whose date is eligible;
- one `roll_call` per supplied vote event whose start date is eligible;
- one `member_vote` per supplied vote-person row whose parent roll call is eligible.

## Fully specified normalization rules

- Trim surrounding whitespace. Empty scalar strings become JSON `null` where nullable.
- Preserve exact nonempty upstream IDs. Never fuzzy-match names or invent a person ID.
- A roll-call key is scoped by source, jurisdiction, session, and upstream vote ID. A member-vote
  key additionally includes its exact upstream vote-person ID. The same vote ID may validly occur
  in different sessions.
- Parse list cells as either JSON arrays or Python literal list/tuple syntax. A nonempty scalar is
  a one-item list. Deduplicate and sort list outputs; list order is not meaningful.
- A valid date is the leading local `YYYY-MM-DD` portion of a date or datetime field. Do not shift
  it across timezones.
- If an artifact's `available_at` is after the cutoff, emit nothing from that artifact, including
  its `source`. Otherwise, include identity rows; include actions and rolls only when their event
  date is on or before the cutoff date. Member votes inherit the parent roll's event date.
- Preserve a roll's nonempty `bill_id` even if that bill row is absent. Do not fabricate the bill.
- `person_id` is the trimmed `voter_id` or null; `member_name` is the trimmed `voter_name` or null;
  `choice` is the exact nonempty `option` value.
- Bill chamber is `organization_classification` or null. Roll chamber is the matching
  organization's `classification`, then the linked supplied bill's chamber, then null.
- For a bill or action, `source_urls` is every distinct valid HTTP(S) URL in `bill_sources.csv` for
  that bill, or the official parent artifact URL if none exists. For a roll/member vote, prefer all
  matching `vote_sources.csv` URLs, then the bill URL set, then the parent URL. Sort/deduplicate.
- Use the artifact's request values for `source_id`; use its official URL/hash/availability for the
  `source` record. Facts carry that `source_id` and the direct URL set above.
- Emit each semantic key at most once and produce the same normalized output on repeated fresh runs.

## Development and grading

`case/request.json`, `case/input/`, and `case/dev_expected.jsonl` are the one public development
case. A local unsandboxed convenience run is:

```sh
deno run --allow-read=case/input solution.ts < case/request.json > answer.jsonl
```

The deterministic grader runs the submission twice in fresh Deno capability sandboxes. It scores
closed-schema validity and references, weighted macro key F1, temporal true-positive/true-negative
behavior, normalized field fidelity, exact provenance, and determinism. The weighted continuous
score is multiplied by matched-expected-keys divided by all nonblank output lines, so fabricated or
duplicated records hurt directly. There is no source inspection and no LLM judge.

Component weights are schema 10%, keys 25%, temporal 20%, fidelity 25%, provenance 15%, and
determinism 5%. Key-F1 type weights are source 5%, bill 20%, action 25%, roll call 20%, and member
vote 30%. Schema is the mean of valid-line, unique-key, and valid-parent-reference rates. Both runs
must exit zero; a timeout, resource-limit termination, or other nonzero exit makes that case score 0.

The grader grants read access only to the frozen submission and the current case input. It denies
network, environment, process, import-host, write, FFI, and system capabilities; applies CPU, wall,
file-output, descriptor, and V8-heap limits; and rejects symlinks and special files. Hidden inputs,
expected values, the Time Machine oracle, and repository history are never mounted.

Published bounds are 128 KiB of source, 2 MiB of staged input, 8 MiB each for stdout/stderr,
12 seconds wall time, 8 CPU seconds, 64 file descriptors, and a 128 MiB main V8 old-space limit.

## Scope and limitations

This pilot can show transfer across several real OpenStates CSV variants, cutoff discipline,
provenance care, and resistance to public-answer hardcoding. It cannot establish transfer to a
different legislative format, legal interpretation, long-horizon agent learning, or general
research ability. The runtime boundary trusts Deno rather than a kernel VM and intentionally limits
submissions to JS/TS. Current snapshots also do not prove that old facts were observable before the
recorded artifact acquisition time. All real artifacts precede their cutoffs, so this pilot
empirically tests future-event exclusion but not whole-artifact unavailability. Deno also exposes
worker isolates that are covered by CPU/wall limits but not a kernel-enforced aggregate memory cap.
