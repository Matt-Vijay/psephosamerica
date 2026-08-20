# Legislature replay feasibility audit

**Verdict: KILL the broad thesis on the existing corpus.** A large slice can replay vote
*arithmetic*, but not legislative procedure. The one clean deterministic output is a per-option
group-by over member choices. Pass/fail needs rule and roster state that the corpus does not carry;
action labels are mostly recorded or text-leaked outputs; and subsequent actions are discretionary
human events. Building another environment now would rename normalization as “replay.”

## Scope and method

This was a read-only audit of the existing Time Machine V1 DuckDB/Parquet build and the inventoried
local OpenStates ZIPs. No data was fetched or rewritten. Measurements used:

- all 677 normalized state/DC/territory session ZIPs (680 inventoried ZIPs including three
  OpenStates U.S.-Congress snapshots);
- canonical `tm.actions`, `tm.roll_calls`, and `tm.member_votes` for corpus-wide counts;
- direct streaming of raw `votes`, `vote_people`, `vote_counts`, and action CSVs;
- heterogeneous session checks in CA, GA, MN, MO, NC, NE, NY, TX, UT, VA, and WI;
- deliberately naive baselines: always-pass, per-option group-by, `yes > no`, keyword/template
  parsing, repeat-last, and leave-one-jurisdiction-out current-action-to-next-action mode.

For the majority baseline, affirmative choices are `yes`, `yea`, or `guilty`; negative choices are
`no`, `nay`, or `not guilty`; other/absent choices do not affect the comparison.
The next-action baselines use the 2023–24 CA, GA, MN, NC, and VA sessions plus MO 2024, TX 2023,
and UT 2024, with each jurisdiction withheld in turn.

## What the corpus can and cannot replay

| Candidate split | Real coverage / behavior | Assessment |
|---|---|---|
| Member choices → option totals | 51,528,307 choice rows; 1,054,229/1,198,902 rolls have member rows | Deterministic when complete, but only a group-by |
| Tallies + motion → pass/fail | `yes > no` matches 1,035,492/1,054,229 covered rolls (98.223%) | Near-trivial majority baseline; the 18,737 failures need missing rules or contain bad labels |
| Action text → action class | 5,888,660/13,097,175 actions (44.96%) have no class; a small keyword set finds an advertised class in 4,694,311/7,208,515 classified rows (65.12%) | Classification/templating task, not procedural replay |
| Classified history → next action | Leave-one-jurisdiction-out modal next class scores 38.01% on 382,078 sampled transitions; repeat-last scores 22.51% | Not deterministic; scheduling, referral, amendment, and executive choices intervene |
| History → final withheld action | Modal baseline scores 23.17% across 39,411 sampled bills | No unique replayable future |
| Actions → authoritative current bill status | The corpus has no independent state-status table | A grader would be testing its own invented reducer |
| Votes/actions → amendment, enacted text, or statute state | State amendments and law links are empty; 3,461,306 state version rows are metadata/links and zero are retained full text | No output oracle |

The arithmetic slice is large by row count but not meaningful as a legislature-replay thesis. In
six sessions with both person rows and reported counts (CA 2023–24, GA 2025–26, MN and NC
2023–24, NE 2025–26, and VA 2025), grouping choices
reproduced yes/no totals on 38,206/38,220 rolls (99.963%) and every option category on
36,739/38,220 (96.125%). The remainder is mainly source taxonomy/data quality—such as Georgia
unnamed `other` seats and North Carolina `abstain` versus `not voting`—rather than procedure.

The outcome baseline is also heavily imbalanced: always predicting `pass` already scores 95.755%
on rolls with member rows. Majority arithmetic raises accuracy to 98.223%, but fail recall is only
67.79% (balanced accuracy 83.68%). Cross-jurisdiction behavior exposes the missing semantics:

| Jurisdiction | Rolls with member rows | `yes > no` accuracy |
|---|---:|---:|
| Georgia | 10,106 | 100.00% |
| Virginia | 69,391 | 100.00% |
| Minnesota | 2,387 | 99.92% |
| North Carolina | 24,707 | 99.47% |
| Texas | 8,666 | 94.55% |
| Tennessee | 55,615 | 87.79% |
| Nebraska | 7,006 | 59.46% |

Nebraska supplies facially contradictory gold: motions saying an amendment was “adopted” or a
motion “prevailed,” with 41–45 yes and zero no, are labeled `fail`. Missouri snapshots contain
rolls with no member/count tables whose motion prose embeds `AYES`, `NOES`, and `PRESENT`; many
rows say “Adopted” or “Passed” while the stored result is `fail`. Those are not learnable
procedural exceptions from the supplied state. They are output-bearing prose and/or source errors.

## Missing specification and state

No inventoried ZIP contains a rule, quorum, threshold, calendar, procedural-specification, or
standalone membership/roster file. Time Machine has no state legislative term rows. In particular,
the available records do not establish:

- the effective chamber/committee rule version and motion-specific threshold;
- quorum, vacancies, eligibility, attendance state, or “majority of membership” denominator;
- committee composition, presiding-officer/tie-break behavior, or veto/override rules;
- normalized motion semantics distinguishing passage, amendment, reconsideration, suspension,
  referral, cloture, and special-threshold questions;
- an authoritative state lifecycle/status output independent of action descriptions;
- contemporaneous publication state.

All 680 OpenStates artifacts were first observed locally at the same timestamp,
`2026-06-24T23:40:40.306472Z`. They are retrospective current snapshots, not successive snapshots
of what was knowable as a legislature unfolded.

## Leakage and ambiguity

The untouched format gives away nearly every plausible target:

- all 1,198,902 raw state vote rows contain `result`; 1,002,458 also contain the source motion
  classification;
- 497/677 state archives contain `vote_counts`, and only 482/677 contain the full
  votes/counts/people trio;
- 259,747/1,198,902 motions contain simple outcome words such as “adopted,” “prevailed,” “failed,”
  or “rejected”; a crude regex agrees with the stored result on 93.92% of those rows;
- 1,076,054/1,198,902 rolls have a same-bill, same-date action, and 817,537 have a same-day
  passage/failure classification;
- only 26,244/1,198,902 raw rolls carry `bill_action_id`. In a heterogeneous sample, same-bill/date
  matching produced multiple candidate actions for 56.10% of rolls;
- action rows directly contain `classification` and `order`; 64.49% of actions in an independent
  eight-session sample shared bill and date with another action, so date does not recover ordering.

A fair task would therefore have to remove `result`, `vote_counts`, outcome-bearing motion text,
same-day/downstream actions, action classifications/order, and other future records. After that
redaction, the current corpus lacks the rules and causal state needed to compute the nontrivial
answers.

## One tiny honest example

Georgia HB 579 House roll `ocd-vote/fa93ea63-ef3a-4b99-8402-25f92aaf18fe` contains 180 individual
choice rows. A deterministic aggregation yields:

```json
{"yes": 158, "no": 2, "other": 20}
```

That exactly matches `vote_counts`. It does **not** independently justify source result `pass`
without supplying a majority/threshold rule. Retaining the same-day action
`House Passed/Adopted By Substitute` leaks that result anyway. This example proves tally
bookkeeping and simultaneously shows why it is not the thesis.

## Decision rule

Do not build a legislature-replay environment from this corpus. Keep tally recomputation as an
integrity check or ordinary normalization evaluation.

Reconsider only after acquiring effective official rule specifications, event-time rosters and
vacancies, quorum/tie-break context, normalized motion types, explicit vote-to-action links,
contemporaneous snapshots, and independently adjudicated non-leaky outputs. A revived task should
beat always-pass, group-by, majority, regex/template, and repeat-last baselines by a material margin
across several jurisdictions and motion types. The present corpus fails every one of those go
conditions.
