# Harvey/Legora signal audit

**Verdict: no viable signal.** The current Psephos corpus does not contain a nontrivial,
independently checkable legal-change or continual-maintenance target suitable for Harvey/Legora-grade
RL or evaluation. Viewed as a deterministic environment compiler, its 74,714,826 canonical fact
rows collapse to at most six generic data procedures. Only OpenStates normalization has broad
held-out-jurisdiction volume, and **zero** families contain retained legal before state, official
transition evidence, and retained after state. Transfer Eval V0 remains useful plumbing, not a
legal-agent thesis.

No episode bundle was created. The machine-readable measurements and decisions are in
[`harvey-legora-signal-audit.json`](harvey-legora-signal-audit.json).

## Decision standard

A signal was considered viable only if its hidden target was:

1. independently machine-checkable from retained public evidence;
2. derivable without later or future records;
3. not directly present in the candidate input;
4. materially harder than parsing, hashing, lookup, grouping, or set difference; and
5. free of an LLM judge, invented legal rule, or unverifiable source-preference judgment as its
   primary oracle.

This is stricter than asking whether Psephos contains legally themed rows. It asks whether those rows
can reward a reusable legal-work procedure.

## Why these capabilities would matter—if the data supported them

Harvey's [LAB announcement](https://www.harvey.ai/blog/introducing-harveys-legal-agent-benchmark)
describes closed-universe matters, partner-style instructions, work product, and expert atomic
rubrics. Its [Tenet research preview](https://www.harvey.ai/blog/post-training-update-harvey-tenet)
reports asynchronous RL on roughly 1,750 environments and more than 10,000 rollouts, with rewards
based primarily on LLM judging. Harvey also reports separate work on document-review citation quality
and firm-memory search; those results should not be conflated with the Kimi K3 Tenet checkpoint.

Legora's [legal-research](https://legora.com/product/legal-research) and
[Monitors](https://legora.com/product/monitors) pages emphasize primary-source verification,
current-law checking, change detection, and audit trails. That makes temporal change, authority,
and provenance relevant hypotheses. It is **not** evidence that either company wants legislative
data, and the vendor performance claims are not independently validated here.

## Measured corpus reality

This was a read-only audit of the completed Time Machine, the original roughly 77 GB workspace, and
already-local derivatives. No data was downloaded.

| Asset | Measured retained coverage | What is actually available for a verifier |
|---|---:|---|
| Time Machine inputs | 697 hashed artifacts; 18,700,281,610 bytes | Exact artifact hashes, URLs, acquisition times, and canonical relations |
| Canonical tables | 1,601,450 bills; 13,097,175 actions; 1,211,109 rolls; 55,161,451 member votes | Strong normalization/provenance oracle; `amendments=0`, `law_links=0` |
| OpenStates states/DC/territories | 677 sessions, 52 jurisdictions, 1,494,858 bills | 13,097,175 actions, 1,198,902 rolls, and 51,528,307 choices |
| State document/version data | 3,461,306 link rows for 1,459,472 bills | 3,407,477 URLs, but **zero** retained documents or text |
| Genuine federal bill text | 6 GPO/USLM XML files, 129,945 bytes, 58,588 extracted characters | Six different bills, one version each, no issued dates, no version chain |
| BILLSTATUS | 106,592 metadata rows; 87,596 CRS summaries totaling 65,115,494 characters | Titles, subjects, and summaries—not legislative text; no raw BILLSTATUS XML retained |
| CourtListener focal snapshot | 3,740 clusters; 3,734 opinion objects | 3,729 snippets totaling 1,864,240 characters, capped at 500; zero PDFs |
| CourtListener citation aliases | 18,123,788 rows | Citation lookup table; not opinion text or treatment/currentness history |
| LOCUS | 2,207,679 rows across 2,287 jurisdictions | Median 55-character, p99 136-character, max 300-character headings/snippets; one snapshot; CC-BY-NC |
| Congressional Record derivative | 52,004,494 member/day/bill edges across 2,564 session days | Extracted metadata relationships; zero retained speech text |
| LegiScan/public-law/code assets | 0 LegiScan files; 0 public-law or U.S.-Code XML files | Planned catalog entries, not present evidence |

### Temporal truth

All 680 OpenStates ZIP artifacts were observed at exactly
`2026-06-24T23:40:40.306472Z`. The 677 normalized nonfederal sessions are retrospective snapshots,
not successive observations of the same legislative matters. Of 13,097,175 actions, 13,096,996
use the historical event date as `available_at`; that does not prove the local snapshot contained
the row on that historical date. The remaining 179 actions have events after observation and are
conservatively cutoff-filtered. There is also one future-dated version row. No state bill, action,
or version has a non-null `valid_to`.

The cutoff machinery therefore prevents mechanical future inclusion in a query. It cannot turn one
later snapshot into an independently observed history of corrections, publication, or legal effect.
It also does not preserve parent/child closure across all historical `as_of` views, quantified below.

### Text, amendment, and codification truth

- The 3,461,306 state version rows are metadata links. All have null `content_path` and
  `text_content`; their 670 hashes identify enclosing ZIP files, not linked document bytes.
- 852,205 bills have two or more link rows; only 115,363 have two or more dated link rows, and
  84,647 have two or more distinct link dates. These are acquisition leads, not text-version pairs.
- 446,047 action rows across 160,689 bills carry an amendment-related action classification.
  Separately, 318,014 version-link rows across 128,732 bills have amendment-like labels. The
  canonical `amendments` table is empty, and no state amendment text is retained.
- 59,403 `became-law` actions cover 53,032 bills in 29 jurisdictions. A broad chapter/law regex
  finds an identifier-looking string in 30,788 actions across 29,819 bills. The canonical
  `law_links` table is empty, and no enacted or codified statute corpus is retained.
- The six genuine GPO XML files cover six bills at one version apiece. Even the
  `BILLS-116hr1865eas` engrossed-amendment sample lacks the base and next versions needed to grade
  propagation.

## Candidate signal audit

| Candidate | Input and hidden target | Deterministic verifier | Leakage route / naive baseline | Plausible transfer | Decision |
|---|---|---|---|---|---|
| Point-in-time normalization | Raw OpenStates rows + artifact time + cutoff → normalized bills/actions/rolls/votes/provenance | Exact Time Machine semantic projection | Every fact is in the CSV; parser + date comparison | Evidence intake, cutoff discipline, auditable grounding | **Auxiliary only.** Keep Transfer Eval; not legal-change training |
| Continual snapshot maintenance | June 21 API slice + June 24 bulk snapshot → recorded additions/removals/identity changes | Keyed set and field difference | Later snapshot contains every target; withhold it and later human votes are unknowable | Firm-memory update diligence | **Reject.** Generic CDC/ETL |
| Amendment/version propagation | Prior official text + amendment → changed provisions and later official text | Byte/structure diff against a retained later version | Labels and URLs are copyable; text is absent | Redlining, diligence, statutory change tracing | **Reject.** Zero state text pairs and zero federal chains |
| Bill-to-law/codification | Bill/actions/version → law ID, enacted text, affected code sections | Official law/chapter IDs plus statute/code bytes | Regex copies visible chapter prose | Current-law research and updating | **Reject.** No law links or statute target corpus |
| Cross-source conflict resolution | OpenStates + BILLSTATUS overlap → authoritative title and temporal explanation | Independent title-type and publication history | Supplying BILLSTATUS makes copying trivial; withholding it leaves no authority rule | Conflicting-source review | **Reject.** Difference detection is a join; winner has no gold |
| Opinion revision/citation maintenance | Snippet, citation mention, prior opinion → changed text, cited authority, current treatment | Full official before/after opinions and treatment graph | `cites` exposes target IDs; alias resolution is lookup | Citation grounding and authority maintenance | **Reject.** No full opinions or treatment graph |
| Artifact provenance | Immutable bytes → correct URL/hash/time/record anchor | SHA-256 and manifest/FK checks | Hash and copy manifest fields | Grounding and auditability | **Auxiliary only.** Valuable component, not a standalone legal signal |
| LOCUS labels | Short provision heading → function/topic/dimension scores | Dataset-provided labels | Labels sit beside rows; keyword/templates are strong | High-volume review/classification | **Reject.** Derived static labels, no independent legal oracle, non-commercial license |

## Deterministic environment compiler refinement

The compiler framing changes what is worth preserving, but not the verdict. Psephos can compile
hard-scored normalization, provenance, join, and integrity exercises. It cannot currently compile a
diverse family of witnessed legal transitions.

| Diversity measure | Result | Interpretation |
|---|---:|---|
| Strict legal before/event/after families | **0** | No stable entity has retained official before bytes, an official transition/specification, retained official after bytes, and an independent exact witness |
| Relaxed generic deterministic procedures | **≤6** | Row count, jurisdiction, field name, template, cutoff, and mask variants are collapsed |
| Procedures with broad held-out-jurisdiction volume | **1** | OpenStates schema normalization/key construction, already exercised by Transfer Eval |
| Independent-representation alignment procedures | **1** | House vote alignment is a keyed join plus one choice taxonomy map |
| High-value legal-agent families | **0** | No text propagation, codification, legal correction, authority resolution, or continual-law oracle survives |

The six-procedure upper bound is deliberately generous:

1. parse/project fields and rewrite namespaces or composite keys;
2. exact-key join and path composition;
3. group-by tally;
4. keyed or string set difference;
5. cutoff filtering, upsert, and deduplication; and
6. hash, primary-key, foreign-key, and content-claim validation.

Streaming, edge, path, repair, conflict, and program-synthesis packaging does not automatically add
a family. If two tasks require the same operator and differ only in rows, jurisdiction, labels, or
cutoff, they are fake scale.

### Real transition and correction motifs

| Witnessed motif | Exact local evidence | Why it does not survive |
|---|---|---|
| California amendment metadata | Two bills, SB 623 and SB 574, have June 21 metadata, June 22 amendment actions/version-link metadata, and changed June 24 title/subject metadata | Both observations are OpenStates; no amendment/version bytes; the after title is not derivable from the event |
| OpenStates API → bulk | 73 overlapping bills; 309 → 314 rolls; five new California rolls and 56 choices | With both snapshots this is set difference; without the later file the human votes are unknowable |
| Identity-spine catch-up | Six June 10–11 House choices by James Gallagher (`G000607`) are absent from the June edge export, present in June 24 OpenStates with Clerk URLs, and the person/CA-1 term appears in congress-legislators on July 2 | The later identity row diagnoses stale resolution but cannot reconstruct the lost choices; supplying OpenStates makes recovery a copy/join |
| BILLSTATUS derivatives | 106,536 → 106,592 rows; 56 additions and 21 changed common rows | Same-day local derivatives; no raw BILLSTATUS XML, row publication time, or official update event |
| CourtListener revisions | 36 revision-labelled records across 27 dockets; all 36 retain identical capped snippets; three change citation-ID sets | No before/after PDFs; grouping by docket and timestamp verifies metadata only |
| Senate schema fill | 4,945 common rolls; 4,943 merely gain `question`; three old rows disappear | Same upstream transformed twice; official XML and migration event are absent |
| Contract CDC | 140,951 updates, of which 140,492 are enrichment flags; 433 name lookups, 19 bill merges, and seven person joins | Untimestamped build stages, not official/legal transitions |

Recorded correction phrases are real, but still do not supply correction gold. South Carolina has
3,230 `Scrivener's error corrected` actions across 2,198 bills; Wisconsin has 448 `LRB correction`
actions across 422; Texas has 160 corrected-committee-report actions across 152; California has 125
introduced-version and 92 enrolled-version corrections. State version rows retain no documents.
Even in California there is no explicit action→version edge: among the 125 introduced corrections,
44 have no later-dated link, 14 have one, and 67 have multiple. Selecting “the next link” would be a
benchmark-authored heuristic, not an official witness.

### Duplicate representations and exact invariants

- **OpenStates API versus bulk:** all 553 API roll IDs are present in the later bulk archives. Core
  fields are exact for 477 and differ only by CRLF/LF for 76; tally vectors, source sets, and
  `(name, choice)` sets are exact on all 553. The only substantive row-key change is 343 Texas
  person IDs across 86 rolls, collapsing to four inferred substitutions with no official crosswalk.
- **OpenStates versus official-derived House votes:** 1,394 matched 118th/119th-Congress rolls cover
  604,073 OpenStates person-vote rows. After `yes↔yea` and `no↔nay`, 603,645 choices match; 422 are
  uniformly OpenStates `other` versus House-derived `present`, and six Bioguide IDs are absent from
  the official-derived roll. That is one join and one taxonomy map, not 604,073 tasks; the original
  House XML was not retained.
- **Senate serializations:** the v1/v2 files share 4,945 rolls and identical vote arrays containing
  494,469 casts. The v1-derived edge feed has 4,945 rolls and 494,465 casts: it omits three other v1
  rolls (300 casts) and four unusual `present, giving live pair` choices while retaining v1's three
  late rolls. Every edge-roll hash is the SHA-256 of its v1 rich record. This proves direct lineage,
  not independent corroboration.
- **Choice rows versus count rows:** 933,442 OpenStates rolls have both person choices and count rows.
  Grouping choices matches all nonzero option totals on 846,610 (90.70%) and the yes/no cells on
  904,187 (96.87%); 86,832 rolls conflict. Both representations come from the same ZIP, so exact
  cases are one group-by invariant and disagreement has no independent winner.
- **Artifact integrity:** rehashing all 697 inventoried artifacts (18,700,281,610 bytes) finds zero
  missing, size, or SHA-256 mismatches. Canonical PK/FK/provenance checks are also clean, but fact and
  artifact rows were emitted by the same build, so only byte rehashing is independently witnessed.
  Provenance is aggregate-level: 7,259 House roll URLs share one retained aggregate hash, 4,948
  Senate URLs share two, and 692,903 distinct OpenStates roll URLs resolve to 552 ZIP hashes. It does
  not prove that the bytes at each cited record URL were retained.

The base tables are referentially clean; the historical `*_as_of` views are **not graph-closed**.
At cutoff `2024-01-01`, visible children lack 10,103,155 bill parents for actions, 407,347 bill
parents for version links, 898,800 session parents and 896,901 bill parents for rolls, and 29,918,126
person parents for resolved votes. At `2026-06-12`, the corresponding counts are 13,087,082,
501,168, 1,208,125, 1,197,325, and 43,185,144. Vote→roll remains closed because those two tables
share the event clock. Once the state artifacts become available on June 25, state bill paths close,
but 10,800 historical federal rolls still lose expired session parents; people remain unavailable for
3,633,144 federal votes until the July 2 identity-spine observation. This is a real schema-policy
problem, not a legal transition: the row masks work as written, but temporal path episodes need an
explicit snapshot-closure policy before they are safe to compile.

There is one genuine key-shape defect worth retaining as parser supervision: 5,197 OpenStates source
roll IDs are reused across sessions, covering 10,398 conflicting rows (Oklahoma 4,614 groups, North
Carolina 368, New Jersey 186, New Mexico 29). Qualifying the source ID by session repairs it exactly.
That is one composite-key rule. Similarly, 1,391 resolved OpenStates person IDs have multiple names,
but the corpus has no authoritative temporal winner; grouping aliases is valid, “repairing” the name
is not.

### What each Time Machine shape can honestly compile

| Episode shape | Current relations and scale | Honest target | Effective procedure / limit |
|---|---|---|---|
| Edge | 13,097,175 action→bill; 3,461,312 version→bill; 55,161,451 vote→roll; 45,532 term→person edges; zero broken FKs | Preserve exact edge or explicit unresolved key | Parse/project or exact join; no amendment/law edges exist |
| Path | Base tables have 55,161,451 vote→roll→session paths; 54,783,340 also reach a bill; 42,884,464 reach person and bill | Reconstruct a recorded provenance path | Plain FK composition, usually inside one ZIP; current historical `as_of` views are not referentially closed |
| Repair | Session-qualified vote IDs and byte/hash recovery | Apply a known composite-key or provenance rule | One parser/invariant family; unresolved identities and titles lack authoritative targets |
| Conflict | Tally disagreements, 752 federal title conflicts, name variants, duplicate vote representations | Detect keyed agreement/disagreement | Group-by or diff; resolution normally has no authority/time oracle |
| Streaming | Cutoff/dedup over actions, rolls, votes, and version links; 180 future-event exclusions | Bounded row projection at a cutoff | One interval predicate; all state facts come from one retrospective observation, and cross-table paths can break |
| Program synthesis | OpenStates CSV and six genuine GPO XML inputs | Recover a normalization/extraction program | Credible for OpenStates parsing only; six unrelated XML files do not create a scalable second family |

The raw state formats themselves do not provide hidden migration diversity. Across 677 nonfederal
archives, every present core CSV relation has one header schema. `organizations.csv` has two header
shapes (57 old, 358 extended) and is absent in 262; the archives have 13 file-presence signatures.
Those are useful missing-table/optional-column robustness cases, not witnessed schema migrations.

### Hard kill criteria

Kill a proposed family when any of these is true:

- the same durable entity lacks retained official before state, an official event/specification,
  retained official after state, or an independent witness;
- both “witnesses” are serializations from one fetch or outputs from one build lineage;
- the target is a copy, rename, mask, exact join, group-by, regex/template, sort, set difference, or
  hash check already counted as a generic procedure;
- repair requires an invented source hierarchy or has no authoritative winner;
- supplying the later snapshot turns the task into CDC, while withholding it requires predicting a
  discretionary human event;
- historical event dates from one retrospective snapshot are treated as observed history;
- a point-in-time graph is scored without first defining and enforcing parent/child closure;
- optional columns across unrelated sessions are marketed as a schema migration; or
- apparent scale disappears under a procedure-level held-out split.

Do not revive the legal environment-compiler thesis until at least three non-isomorphic families
survive these tests. The current strict count is zero.

## Concrete real episodes

### 1. California SB 10 looks perfect until the oracle is requested

California SB 10 (2023–2024), `ocd-bill/92de10b6-d6a7-4e3a-acb7-6ba73f654fd4`, has:

- 37 actions, including 11 amendment-related actions;
- 10 document/version links, nine dated from introduced through chaptered;
- 10 roll calls and 231 member votes; and
- a final action, `Chaptered by Secretary of State. Chapter 856, Statutes of 2023.`

This is the desired amendment-propagation/codification shape. It is not a usable episode: all ten
documents are remote URLs, with zero retained bytes. The chapter prose is directly copyable, while
the actual textual changes and chaptered statute cannot be verified locally.

### 2. A real three-day update across Arizona, California, and Texas is only a diff

The partial OpenStates API snapshot observed June 21 and the bulk snapshot observed June 24 overlap
on 73 bills: 16 Arizona, 30 California, and 27 Texas.

- API: 309 rolls and 14,792 choices.
- Bulk: 314 rolls and 14,848 choices.
- All 309 common rolls have identical bill, motion, result, and date.
- The later snapshot adds five California rolls and 56 choices: two for SB 1425, plus SB 623,
  AB 706, and SCR 187.
- Across 86 Texas rolls, 343 removed and 343 added person-vote keys collapse by matching
  name/choice to four inferred identity substitutions. No official crosswalk adjudicates them.

If both snapshots are supplied, a keyed set difference scores 100%. If the later snapshot is
withheld, predicting the five new votes means predicting human events. This measures update plumbing,
not continual legal knowledge.

### 3. Federal title changes expose a real ambiguity but no authority oracle

The local OpenStates 117th–119th Congress ZIPs contain 54,047 bills. Exactly 53,724 match
BILLSTATUS by Congress, type, and number:

- 52,967 titles are byte-identical;
- three differ only by case/whitespace and two only by punctuation after normalization;
- 752 remain different strings; and
- 323 later 119th-Congress OpenStates bills are absent from the earlier BILLSTATUS acquisition.

For example, 117th H.R. 4346 is `Supreme Court Security Funding Act of 2022` in OpenStates and
`Chips and Science Act` in BILLSTATUS. That can reflect a legislative vehicle and later title state,
not a bad string. The corpus has no paired federal actions, full version sequence, or effective
title intervals to grade the temporally correct answer. A join finds the conflict; choosing one
source invents a priority rule.

### 4. Court revision evidence stops at the filename

CourtListener has 36 explicitly revision-named records across 27 dockets. `Trump v. CASA, Inc.`,
for example, has revision-labelled records dated June 27 and July 2, 2025. But the local assets retain
only CourtListener's first 500 characters and a remote `local_path` string—not either PDF. A timestamp
sort can identify the later record; no local evidence can verify what changed.

The same failure affects citation maintenance. The raw opinions list 102,043 cited-ID occurrences
covering 46,651 IDs. The bulk alias table has an alias for 34,808 of those IDs (74.61%), covering
65,975 occurrences. For that subset the task is an ID lookup, and the input already exposes the
cited IDs. The uncovered and ambiguous cases lack the full opinions needed for adjudication.

## Why no tiny episode bundle was created

The best-looking bundle would be California SB 10, a revised Supreme Court opinion, or the five-roll
OpenStates update. The first two would omit the evidence needed to verify the claimed legal change;
the third would advertise set difference as legal-agent learning. Packaging any of them would make
the evidence look stronger than it is.

The honest reusable outputs are the Time Machine's aggregate provenance checks and Transfer Eval's
bounded hard behavioral score. The general cutoff views need a graph-closure policy before serving
as a path oracle. These pieces can be auxiliary reward components in a future legal environment;
they do not independently justify post-training on legal change, authority, or continual maintenance.

## What would change the verdict

Do not add another harness. Acquire the missing oracle material first:

- content-addressed official before/after bill and amendment text with actual publication
  observations;
- explicit amendment-to-version and enacted-law identifiers;
- official public-law/chapter bytes and historical statute/code snapshots;
- full before/after court opinions plus citation treatment/currentness data;
- adjudicated authority/title semantics for source conflicts;
- multiple contemporaneous observations of the same legal matter; and
- an explicit, tested parent/child closure policy for point-in-time graph queries.

A revived audit should require performance materially above copy, parser, hash, lookup, regex,
group-by, and set-difference baselines on held-out sources and time periods. Until those data exist,
the measured result is **no viable signal**.

## Evidence and limitations

Canonical counts come from [`build.json`](../data/time_machine/build.json),
[`integrity.json`](../data/time_machine/integrity.json), and read-only DuckDB queries. Legacy checks
streamed the original JSONL, ZIP, Parquet, XML, and BZ2 assets directly. The CourtListener
`fetch_state.json` claims 5,500 written records, while the present raw file contains 3,740; the report
uses the bytes actually present. LOCUS is CC-BY-NC-4.0 and should not be treated as a commercial
training asset without separate rights analysis.

This audit did not fetch missing documents or test a frontier model. It determines whether the
existing local evidence can support the requested reward, not whether a future acquisition could.
