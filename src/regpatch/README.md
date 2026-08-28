# Psephos RegPatch

Psephos RegPatch gives an executable agent a historical eCFR XML state and one
or more ordered Federal Register rule XML documents, then deterministically
scores the resulting XML against the withheld historical eCFR successor.

This is an evaluator/operator guide. The candidate-facing contract is
[`pilot/TASK.md`](pilot/TASK.md).

## Zero to a public score

Requirements are Python 3.12+, Deno 2.x at the runner's fixed trusted path
(`/opt/homebrew/bin/deno` or `/usr/local/bin/deno`), and no private corpus:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e .
deno --version
.venv/bin/psephos-regpatch demo /private/tmp/psephos-regpatch-demo
.venv/bin/psephos-regpatch grade \
  /private/tmp/psephos-regpatch-demo/evaluator-episode \
  /private/tmp/psephos-regpatch-demo/candidate-bundle/submission
```

`demo` re-hashes and compiles the committed 47 CFR 73 public episode, creates
an audited candidate bundle, executes all five weak baselines twice in fresh
sandboxes, and writes `demo-report.json`. The starter is deliberately weak, so
its score is below 100. Edit only `candidate-bundle/submission/`, then rerun the
last command.

The same steps are available separately:

```bash
.venv/bin/psephos-regpatch seed /private/tmp/regpatch-episode
.venv/bin/psephos-regpatch bundle \
  /private/tmp/regpatch-episode /private/tmp/regpatch-bundle
.venv/bin/psephos-regpatch grade \
  /private/tmp/regpatch-episode /private/tmp/regpatch-bundle/submission
```

## Empty-store acquisition path

The live path uses only allowlisted Federal Register and eCFR endpoints. It is
metadata-first and derives snapshot dates from observed eCFR version records,
not from Federal Register publication dates:

```bash
.venv/bin/psephos-regpatch probe /private/tmp/regpatch-store \
  --start-date 2024-01-01 --end-date 2024-01-31 --max-candidates 30

.venv/bin/psephos-regpatch version-probe /private/tmp/regpatch-store \
  --title 5 --part 2634 \
  --start-date 2024-01-01 --end-date 2024-01-31

.venv/bin/psephos-regpatch acquire /private/tmp/regpatch-store \
  /private/tmp/regpatch-store/probe.json 0 \
  /private/tmp/regpatch-store/versions-title-5-part-2634-page-1.json 0
```

`acquire` prints the compiler-ready `source_spec` path. Continue with:

```bash
.venv/bin/psephos-regpatch compile SOURCE_SPEC /private/tmp/regpatch-evaluator
.venv/bin/psephos-regpatch bundle \
  /private/tmp/regpatch-evaluator /private/tmp/regpatch-candidate
.venv/bin/psephos-regpatch grade \
  /private/tmp/regpatch-evaluator /private/tmp/regpatch-candidate/submission
```

The store is content-addressed and resumable. Reusing one immutable URL under a
different logical role creates another receipt without another fetch. Both raw
transfer bytes and decoded retained bytes are capped. The compiler keeps source
URLs, safe response headers, acquisition times, byte counts, SHA-256 values,
document numbers, citations, and the distinct publication, legal-effective,
eCFR-amendment, and eCFR-issue clocks.

## Private suite

The evaluator corpus belongs under the ignored
`data/regpatch_evaluator/` boundary. It contains successor bytes, hidden IDs,
and causal receipts and must never be copied into a candidate bundle or public
Git history.

```bash
.venv/bin/psephos-regpatch corpus-audit \
  --store data/regpatch_evaluator/substantive
.venv/bin/psephos-regpatch corpus-build /private/tmp/regpatch-suite \
  --store data/regpatch_evaluator/substantive
.venv/bin/psephos-regpatch suite-baselines /private/tmp/regpatch-suite
```

Candidate bundles contain only the public manifest, base XML, ordered rule XML,
task contract, starter, and their hashes. The Deno runner allows reads only from
those frozen files, writes only to `output/`, denies network/environment/process
access, uses a fresh workspace, and requires two deterministic runs. It also
rejects symlinks, undeclared files, local paths, private hashes, Git history,
and evaluator markers.

## What is scored

The grader safely parses bounded XML and continuously scores:

- node and citation identity precision/recall;
- exact substantive agreement in changed regions;
- exact preservation of unaffected regions;
- numbering, hierarchy, cross-references, tables, and authority citations;
- evaluator-verified provenance and two-run determinism.

Fabrication, dropped unaffected provisions, duplicate nodes, malformed XML,
missing provenance, and nondeterminism reduce reward. eCFR editorial `CITA` and
pending-amendment/correction `XREF` nodes are excluded because opaque editorial
attributes are not derivable from candidate inputs. `CITA` remains usable
evaluator-side as causal admission evidence. Table normalization ignores
wrapper/CSS presentation changes but preserves ordered cell text, captions,
spans, and hierarchy; HTML and CALS-style row/cell structures are supported.

## Measured V0 evidence

The retained evaluator audit and suite build currently measure:

- 24 metadata candidates in the capped probe, 23 beyond the seed;
- 12/12 corrected substantive windows accepted, plus one independent-rule
  composition: 1 public and 12 evaluator-only episodes;
- 60 re-hashed official artifacts, 48,570,537 bytes, 17 Federal Register
  documents, 16 agency names, and seven CFR titles;
- eight compiler operation shapes;
- two rule-plus-correction chains, explicitly **not** counted as independent
  composition;
- one genuine same-day composition with three non-correction primary rules,
  nine disjoint substantive section contributions, and full changed-region
  coverage;
- one rejected longer-window attempt with seven unsupplied causal witnesses,
  proving that omitted collisions fail closed.

All 13 trusted targets score 100. The measured all-episode weak baselines are:

| Procedure | Score |
|---|---:|
| Copy base | 69.0825 |
| Copy Federal Register replacement text | 0.0000 |
| Naive regex patcher | 65.6232 |
| Public-answer hardcode | 71.3932 |
| Destructive partial replacement | 14.2630 |

These values are reproduced by `suite-baselines`; its `all` result is the
episode-count-weighted aggregation of the already executed public and hidden
splits, not a redundant third execution. The aggregate machine-readable receipt
is [`measurements.json`](measurements.json); it contains no hidden IDs or target
hashes.

## Source and clock semantics

FederalRegister.gov XML is a machine-readable rendition of a Federal Register
document; the official edition is the linked PDF/GovInfo publication. eCFR is
an authoritative but unofficial editorial compilation. An observed eCFR
amendment/incorporation date is not asserted to be the rule's legal-effective
date.

Rule execution order is observed eCFR amendment date first, then Federal
Register publication date, volume, and page as deterministic tie-breakers. One
retained Federal Register JSON record omits the requested part even though its
same-title rule XML has exact `REGTEXT`, `SECTNO`, and `AMDPAR` targets. That
source defect is admitted only after an exact successor citation-delta match and
is explicitly receipted; JSON records lacking the requested CFR title still
fail.

## Honest limits

- Thirteen episodes prove a working observed-transition family, not frontier
  difficulty or thousand-episode scale.
- The automatic probe expands version windows and acquires individual source
  quartets; automatic grouping of every same-window multi-rule collision is not
  complete.
- CITA-delta causality is used only when no substantive XREF transition exists,
  and every changed region and supplied rule must still map exactly.
- Presentation-neutral table projection is deliberately bounded; it is not a
  general XML equivalence engine.
- The environment tests structured long-context patch execution. It does not
  establish legal reasoning, legal correctness, or legal effectiveness.
