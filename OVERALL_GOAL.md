# Psephos America — Overall Goal

**The single, maximalist objective for this codebase.** Everything older in
this repo (the v1 deterministic conflict-of-interest scoring, the
prediction subsystem as currently shipped, the quality-pass work) is now
scaffolding for this. Old "goal" docs and tickets are superseded.

## The product, in one sentence

Predict every public official's vote on every bill — federal, state,
county, city, special-district — with calibrated probability, fully
audited evidence, and a clean correction channel, **scaling by
construction** because the architecture treats federal Congress as one
slice of a homogeneous Person × Bill × Org knowledge graph rather than a
special case.

## Why the current model is insufficient (controlled-experiment evidence)

A causally-clean synthetic House (80 members, 60/40 partisan split,
explicit hidden member-specific sector polarity, strict train/test
cutoff) was run through the three shipping models and a per-member
extension. On the cross-pressured slice (party vs sector pull diverge —
the only votes anyone cares about predicting):

| model                       | acc    | brier  | logloss |
| --------------------------- | ------ | ------ | ------- |
| baseline_member_yea_rate    | 0.5749 | 0.2451 | 0.6833  |
| ontology_signal_model       | 0.5716 | 0.2532 | 0.7007  |
| learned_signal_logistic     | 0.4284 | 0.2518 | 0.6967  |
| per_member (intercept+slope)| 0.6441 | 0.2059 | 0.5917  |

The v1 deterministic global-linear models sit at or below the
"predict-the-global-yea-rate" reference (brier 0.2449). A 401-parameter
per-member model (per-member intercept + per-member sector-polarity
slope + one global party-alignment term, ~60 epochs SGD) lifts overall
accuracy from 55% to 81% and Brier from 0.248 to 0.135 on the same
strict-cutoff windows. The architectural constraint, not the data
plumbing, is what's pinning the metrics.

The v1 "auditable, deterministic" constraint is explicitly dropped going
forward. Auditability is preserved via source-anchored evidence + LLM
explanations of every prediction, not by capping model capacity.

## The right object model

It's a **heterogeneous knowledge graph**, not a tabular ML problem.

Primary node classes:
- **Person** — official, candidate, donor, lobbyist, staffer, journalist.
- **Bill** — at every jurisdiction; includes resolutions, ordinances,
  ballot measures, executive orders where votable.
- **Org** — party, caucus, committee, agency, corporation, union, NGO,
  trade association, PAC, foundation, news outlet.

Secondary node classes:
- **Issue / Sector** — topical taxonomy node.
- **Event** — speech, vote, donation, hearing, endorsement, meeting,
  press release.
- **Artifact** — speech transcript, bill text, news article, FOIA
  release, court filing, meeting minutes, social post.
- **Geography** — district, county, city, state, federal.

Every edge carries `(source_url, content_sha256, first_observed_at,
valid_from, valid_to)`. Every fact is replayable from immutable raw
artifacts to any time-`t` snapshot. The existing source-anchor +
manifest-verified-publishing discipline in this repo is the right
starting point and stays.

Storage:
- **Raw immutable lake**: S3/R2 + Apache Iceberg + Parquet,
  content-addressed (sha256 paths).
- **Graph store**: Neo4j or Memgraph (live), DGL or PyTorch Geometric
  (training).
- **Vector store**: pgvector or Qdrant.
- **Read API**: FastAPI + Postgres serving precomputed snapshots.

## Data — the full catalog

For each public official, the system knows everything that is **legally
and publicly observable about their public conduct**. Not their private
life, not their family beyond what they themselves disclose, not
purchased private data, not anything behind a login the official did
not consent to. Categories:

**Formal record** — every roll call vote, sponsorship, cosponsorship,
committee membership, floor speech (transcribed from video), press
release, public schedule, ethics filing, financial disclosure,
foreign-travel disclosure.

**Money trail** — FEC plus every state campaign-finance authority plus
every municipal disclosure; donors entity-resolved across jurisdictions
(OpenSecrets, FollowTheMoney as starting points). Lobbying registrations
(LDA federal + 50 state equivalents). Bundlers. JFCs. Independent
expenditures. PAC-to-PAC flows.

**Stated positions** — campaign websites (Wayback Machine for
time-resolved versions), candidate questionnaires (Vote Smart, LWV,
advocacy scorecards), debate transcripts, town halls, podcasts, op-eds,
books, academic publications.

**Communications** — every public social-media post (X, Bluesky, Truth,
Facebook page, Instagram, TikTok, YouTube, Threads) with NLP-extracted
stance. Newsletter archives. Substack/Medium output.

**Network signals** — who endorses whom, who appears at whose
fundraisers, staff hiring (the revolving door — chief-of-staff career
paths are highly predictive), faction membership (Freedom Caucus,
Progressive Caucus, Problem Solvers, state equivalents),
trade-association membership, publicly-stated religious affiliation.

**Media footprint** — every news article mentioning them (GDELT,
LexisNexis, local-paper scraping), sentiment + topic extraction, op-eds
about them, hometown paper coverage especially.

**Public records of interests** — business registrations (state SOS),
property records (county assessor), board memberships, charitable
foundation 990s, court filings.

**Constituency context** — district demographics, presidential-vote
margin, recent polling, district-level economic indicators, local events
(factory closures, disasters, prominent local controversies).

**Bill side** — full text + every amendment + committee markup + CBO/JCT
or state-fiscal-analyst score + lobbying disclosures filed *on the
specific bill* + position letters from NGOs, unions, trade groups + news
coverage + Reddit/Twitter sentiment + text-similarity to known model
legislation (ALEC, ALICE, ACLU model bills, ULA uniform laws).

## Entity resolution — the hardest engineering problem

Bigger than the model. "Jane Doe, city councilor" must reliably link to
the Jane Doe on LinkedIn, the Jane M. Doe on FEC, the @janedoe handle,
the law-firm Jane Doe, the property-records Jane Doe, the Wikipedia
stub. Without this every downstream signal fragments.

Tooling:
- **Splink** or **Zingg** for probabilistic record linkage.
- Fine-tuned **bi-encoder transformer** for name + context matching.
- **Human-in-the-loop review queue** for low-confidence matches.
- Canonical ID with full audit trail of all source records merged in.
- Disputed matches persist as separate possibilities rather than forced
  premature collapse.

## Encoding stack — don't engineer features, learn them

**Bill encoder.** Long-context LLM (Claude Sonnet 4.6 / Opus 4.7 class)
ingests full text + amendments + fiscal score + LDA actors → produces
(a) a structured JSON dossier (sectors, beneficiaries, harmed parties,
fiscal scope, ideological valence, similar past bills, coalition
pattern), (b) a dense embedding. Cache aggressively.

**Politician encoder.** Rolling LLM-generated dossier updated daily —
"what does this person believe, who funds them, who they vote with,
what they've said recently on the bill's topic" — with every claim
source-anchored. Embed the dossier. Plus a graph-derived structural
embedding from a **Relational GNN (RGCN / HGT)** trained on the
knowledge graph capturing "who is this person similar to in network
position."

**Context encoder.** Vote timing relative to election cycle, leadership
whip status, procedural vs substantive, news-cycle salience, bill
momentum. Mostly categorical.

## The prediction model

A **cross-attention transformer** over four token streams:
1. Politician dossier tokens + structural embedding.
2. Bill dossier tokens + structural embedding.
3. Context tokens.
4. **Retrieved past-vote tokens**: RAG over the K most similar
   (politician, bill) pairs from history.

Output: yea probability + calibrated uncertainty (Bayesian deep ensemble
across 5 seeds, or evidential deep learning).

Plus an **LLM forecaster as an independent ensemble member**: prompt a
frontier model with the politician's dossier + the bill + retrieved
comparable votes + relevant news, request a probability and reasoning.
Stack with the transformer on a held-out window.

## Training recipe

**Multi-task pretraining.** Predict votes *and* cosponsorship,
donor-receipt, statement-stance valence, committee reassignment, bill
passage, endorsements. Forces embeddings to encode the whole political
object, not one downstream label.

**Strict temporal cutoff at every example.** Every feature carries a
`known_at`. The leakage validators in `src/prediction/backtest.py` are
the model for this and they stay; nothing in the new architecture is
allowed to violate them. Property-fuzz tests already lock the invariant.

**Curriculum.** Easy partisan votes first to stabilize base embeddings,
then mid-difficulty, then close votes with high learning rate. Final
fine-tune on the close-vote slice only.

**Calibration.** Per-politician temperature scaling + isotonic
regression on a held-out window. **Conformal prediction sets** for
honest uncertainty bands.

**Continuous learning.** Every real-world vote becomes a new training
example. Incremental retrain daily; full retrain weekly.

## The thin-record problem (this is the scaling key)

A first-term city councilor has zero votes. The model predicts their
stance anyway, via three mechanisms:

1. **Hierarchical partial pooling.** Their embedding initializes as a
   weighted blend of (their party in their state) + (their endorsers'
   clusters) + (similar politicians by network position). As they
   accumulate votes, the prior weight shrinks.
2. **Stated-position transfer.** Their campaign website, debate answers,
   and questionnaires are *labeled* training data of declared positions;
   train the model from declared-position-text to vote-stance.
3. **Endorsement-network signal.** Endorsed by Sierra Club + WFP-aligned
   funders + local DSA? Strong prior on issue positioning. The most
   data-efficient signal for unknown officials.

## Local-jurisdiction data infrastructure

Mostly an unglamorous ingestion problem, not an ML problem:

- **Granicus, Legistar, CivicClerk, BoardDocs, PrimeGov** host most US
  local-government agendas/minutes. APIs where available; scraping
  fallback; OCR for PDF-only minutes.
- **OpenStates** covers state legislatures comprehensively.
- **MuckRock** for FOIA pipelines.
- Local news: Internet Archive + Newspapers.com APIs, university library
  partnerships where reachable.
- Civic-tech partnerships: Code for America, Sunlight Foundation alumni
  networks, Documenters.org.

Budget heavy data-engineering work before the model gets interesting at
the local level. Half the headcount belongs here.

## Auditability and ethics

Every prediction renders with:
- The top-5 evidence sources that drove it (specific past votes, donor
  relationships, public statements), each with URL + retrieval timestamp
  + content hash.
- A confidence interval + an LLM-generated human-readable explanation.
- A "what would change this prediction" counterfactual.

Guardrails:
- Only public-record data on officials' public conduct.
- No family / private-life data.
- Right-of-correction channel that propagates corrections through the
  lake (the existing `docs/corrections.md` SLA scales up).
- Differential treatment for officials vs candidates vs former
  officials.
- No scraping behind logins. Respect robots.txt for that source.
- The existing immutability + content-hash discipline in
  `db/schema.sql` and the manifest-verified publishing remain.

## Tech stack (concrete)

- **Lake**: S3 / Cloudflare R2 + Apache Iceberg + Parquet,
  content-addressed.
- **Graph**: Neo4j or Memgraph (live serving); PyTorch Geometric / DGL
  for training-time graph ops.
- **Vector**: pgvector or Qdrant.
- **Models**: PyTorch + Hugging Face. Bill-text embedding via fine-tuned
  Llama-3-70B / Qwen-2.5-72B, or hosted Claude/GPT for the heavy
  reasoning steps. LLM gateway via LiteLLM so models are swappable.
- **Orchestration**: Dagster or Prefect (not Airflow — lineage tracking
  matters here).
- **Entity resolution**: Splink + custom transformer matcher.
- **Serving**: FastAPI + Ray Serve for the model, Postgres for the read
  API.
- **Monitoring**: Evidently for drift, Weights & Biases for training,
  calibration dashboards in Grafana.
- **CI gates**: keep the current ruff / ruff-format / bandit /
  compileall / mypy-strict / pytest-with-coverage discipline. Extend to
  add ML evaluation gates (Brier and log-loss must not regress on the
  benchmark slice without explicit approval).

## Definition of done

- Every US public official with a votable record is in the graph with
  a stable canonical ID.
- Every bill at every covered jurisdiction has a full LLM dossier +
  embedding cached, regenerated on amendment.
- The prediction model emits a calibrated probability + uncertainty
  band + 5-source evidence list + LLM explanation for every (Person,
  Bill) pair where the model believes itself qualified to opine.
- Calibration metrics (per-jurisdiction, per-party, per-faction)
  visible on a public dashboard. Brier and log-loss on the
  cross-pressured slice published.
- Corrections SLA honored; correction propagation auditable.
- Public read API with rate limits and citation requirements.
- The whole thing reproducible from raw immutable artifacts to any
  past time `t`.

The existing repo spine — strict cutoff discipline, source-anchored
provenance, immutable manifest-verified publishing, no-leakage
validators — is the rarer half of this work and stays. The model + lake
+ entity-resolution + ingestion-at-scale layers are what gets built on
top.
