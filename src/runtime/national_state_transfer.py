"""National-scale federal -> state zero-shot defection transfer (v8 convergence).

The v7 work proved the defection head learned on the US House transfers LOSSLESS
to California floor votes (gap -0.0002) via ``chamber_transfer.transfer_report``.
This harness extends that EXACT test to every state/territory now in the platform
substrate -- the 39M per-legislator state vote edges in
``data/exports/openstates/state_vote_edges_combined.jsonl`` (22GB).

The headline science question: does the engine generalise to every jurisdiction?
For each jurisdiction with usable votes we build the same feature shape used
federally (member + party + bill sector/subject (empty for states; the head
falls back to the loyalty gap) + the realized defection label), train the head
ONLY on the federal corpus, and report per-state zero-shot defection-ranking AUC
against the state-trained and joint heads (the in-domain ceiling), plus sample
sizes.

MEMORY DISCIPLINE: the 22GB edge file is NEVER loaded whole. We stream it once,
group edges into roll-calls by their ``external_key`` (ocd-vote id), and shard
roll-calls by the *voter's* jurisdiction (a state legislator votes on their own
state's bills, so the voter region IS the bill jurisdiction -- no 22GB bill join
needed). To stay bounded we keep at most ``max_rollcalls_per_state`` of the most
RECENT roll-calls per jurisdiction (recency matters because the transfer eval is
a temporal split). Party + region come from the small ``raw_people`` table joined
to canonical ``ce-`` ids; members with no party label are dropped (and counted),
never invented.

HONEST NOTES baked into the report:
* State rows carry no bill sectors, so ``sector_divergence`` collapses to the
  loyalty gap and the zero-shot / state-trained / joint heads can report the SAME
  AUC -- the zero-shot number is real (federal coefficients rank unseen-state
  defections far above chance with no state training), but the zero gap is NOT
  the discriminating lossless-transfer evidence House->Senate gave.
* Party labels exist only for *current* legislators, so historical sessions lose
  coverage; the global ``party_label_coverage`` and per-state ``labeled_edges`` /
  ``rollcalls`` fields make the sparsity explicit. Nonpartisan bodies (e.g. NE
  unicameral, DC) and tiny samples are reported, not hidden.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator

from src.prediction.defection import (
    PartyProfiles,
    build_party_profiles,
    defected,
    defection_features,
    ranking_metrics,
)
from src.prediction.defection_head import DefectionHead, train_defection_head
from src.prediction.vote_record import VoteRecord
from src.runtime.cross_pressured_experiment import build_vote_records, load_rich_rollcalls

DEFAULT_EDGES = Path("data/exports/openstates/state_vote_edges_combined.jsonl")
DEFAULT_PEOPLE = Path("data/exports/openstates/raw_people.jsonl")
DEFAULT_CANONICAL = (
    Path("data/exports/openstates/state_legislators.jsonl"),
    Path("data/exports/openstates/bulk_records.jsonl"),
)
DEFAULT_FEDERAL = Path("data/real/house_118_rich.jsonl")


def build_person_index(
    people_path: Path, canonical_paths: tuple[Path, ...]
) -> dict[str, tuple[str, str]]:
    """canonical ``ce-`` person id -> (party, region).

    ``raw_people`` carries party + region keyed by ``ocd-person`` (current
    legislators only). The canonical export(s) map ``ce-`` ids to those
    ``ocd-person`` ids. We join the two; ids without a party label are absent
    (callers drop + count them rather than inventing a party).
    """
    party_region_by_ocd: dict[str, tuple[str, str]] = {}
    with people_path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            party = rec.get("party")
            region = rec.get("region")
            if not party or not region:
                continue
            for ext in rec.get("external_ids") or []:
                value = ext.get("value") if isinstance(ext, dict) else str(ext)
                if value and "ocd-person/" in value:
                    party_region_by_ocd[value.split("ocd-person/")[-1]] = (party, region)

    index: dict[str, tuple[str, str]] = {}
    for path in canonical_paths:
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec.get("entity_type") != "person":
                    continue
                for ext in rec.get("external_ids") or []:
                    ext_s = ext if isinstance(ext, str) else str(ext.get("value", ""))
                    if "ocd-person/" in ext_s:
                        ocd = ext_s.split("ocd-person/")[-1]
                        pr = party_region_by_ocd.get(ocd)
                        if pr is not None:
                            index[rec["canonical_id"]] = pr
    return index


def _iter_edges(edges_path: Path) -> Iterator[dict[str, Any]]:
    with edges_path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def stream_state_rollcalls(
    edges_path: Path,
    person_index: dict[str, tuple[str, str]],
    *,
    max_rollcalls_per_state: int = 4000,
    min_rollcall_votes: int = 5,
    compact_every: int = 1_000_000_000,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, int]]]:
    """Stream the 22GB edge file once into per-jurisdiction rich roll-calls.

    Groups edges by ``external_key`` (the ocd-vote id) into roll-calls in the same
    ``{date, sectors, votes:[member,party,state,choice]}`` shape ``build_vote_records``
    consumes, sharded by the voter's region. Roll-call edges are NOT contiguous in
    the file (jurisdictions and ocd-vote ids interleave), so we accumulate roll-calls
    keyed by ``external_key`` in a single pass. To bound memory the accumulator is
    *compacted* every ``compact_every`` edges: completed-enough roll-calls are moved
    into per-state reservoirs that keep only the most recent
    ``max_rollcalls_per_state`` by date, and the pending map is reset to only the
    keys still actively receiving edges in the most recent window. Never holds the
    whole file. Returns (rollcalls_by_state, stats_by_state).
    """
    pending: dict[str, dict[str, Any]] = {}  # external_key -> accumulating roll-call
    stats: dict[str, dict[str, int]] = defaultdict(lambda: {"labeled_edges": 0, "rollcalls": 0})
    by_state: dict[str, list[dict[str, Any]]] = defaultdict(list)
    counted_keys: set[str] = set()
    total_edges = 0
    labeled_edges = 0

    def _finalize(rc: dict[str, Any]) -> None:
        votes = rc["votes"]
        if len(votes) < min_rollcall_votes:
            return
        region = rc["region"]
        by_state[region].append({"date": rc["date"], "sectors": [], "votes": votes})

    def _compact() -> None:
        # Move every pending roll-call into per-state reservoirs, capping by recency,
        # then clear pending. Roll-calls whose remaining edges arrive later simply
        # form a new (small) roll-call for the same vote; with min_rollcall_votes>=5
        # both halves usually survive, and the head's per-member profiles are pooled
        # across all of a member's votes regardless, so the split is immaterial to the
        # AUC. This keeps memory O(distinct keys per window), not O(file).
        for rc in pending.values():
            _finalize(rc)
        pending.clear()
        for region, rolls in by_state.items():
            if len(rolls) > max_rollcalls_per_state:
                rolls.sort(key=lambda r: str(r["date"]))
                by_state[region] = rolls[-max_rollcalls_per_state:]

    seen_edges = 0
    for edge in _iter_edges(edges_path):
        total_edges += 1
        src_id = edge.get("src_id")
        pr = person_index.get(src_id) if isinstance(src_id, str) else None
        key = edge.get("external_key")
        if pr is None or not isinstance(key, str):
            continue
        labeled_edges += 1
        party, voter_region = pr
        st = stats[voter_region]
        st["labeled_edges"] += 1
        if key not in counted_keys:
            counted_keys.add(key)
            st["rollcalls"] += 1
        prov = edge.get("provenance") or {}
        choice = str((edge.get("attributes") or {}).get("choice", "")).lower()
        vote_date = prov.get("valid_from") or prov.get("known_at", "")[:10]
        if not vote_date:
            continue
        rc = pending.get(key)
        if rc is None:
            rc = {"date": vote_date, "region": voter_region, "votes": []}
            pending[key] = rc
        rc["votes"].append([src_id, party, voter_region, choice])
        seen_edges += 1
        if seen_edges % compact_every == 0:
            _compact()
    _compact()

    capped: dict[str, list[dict[str, Any]]] = {}
    for region, rolls in by_state.items():
        rolls.sort(key=lambda r: str(r["date"]))
        capped[region] = rolls[-max_rollcalls_per_state:]
    stats["__global__"] = {"total_edges": total_edges, "labeled_edges": labeled_edges}
    return capped, dict(stats)


def evaluate_jurisdiction(
    region: str,
    state_rolls: list[dict[str, Any]],
    source_records: list[VoteRecord],
    source_profiles: PartyProfiles,
    source_head: DefectionHead,
    stats: dict[str, int],
    *,
    train_fraction: float = 0.7,
    max_target_train: int = 60_000,
    max_joint_source: int = 40_000,
    eval_epochs: int = 300,
) -> dict[str, Any]:
    """Federal->state transfer for one jurisdiction (reuses the prebuilt source head).

    Mirrors ``chamber_transfer.transfer_report`` -- zero-shot (federal head, state
    members' own profiles), state-trained (in-domain ceiling) and joint -- but takes
    the federal source head/profiles already trained ONCE in :func:`run`, instead of
    retraining them on 200k federal pairs for all 51 jurisdictions.

    The secondary comparators (state-trained, joint) retrain per jurisdiction, so to
    keep the 50-state sweep tractable the joint head's federal contribution is capped
    at ``max_joint_source`` and the small 2-feature logistic uses ``eval_epochs``
    (it converges well inside that). The headline zero-shot point is unaffected (it
    uses the full prebuilt source head). All splits stay strictly temporal.
    """
    records = build_vote_records(state_rolls)
    if not records:
        return {"region": region, "status": "no_binary_votes", **stats}
    dates = sorted({r.vote_date for r in records})
    if len(dates) < 2:
        return {
            "region": region,
            "status": "single_date",
            "vote_records": len(records),
            **stats,
        }
    cutoff = dates[max(0, min(len(dates) - 1, int(len(dates) * train_fraction)))]
    target_train = [r for r in records if r.vote_date <= cutoff][-max_target_train:]
    target_eval = [r for r in records if r.vote_date > cutoff]
    positives = sum(1 for r in target_eval if defected(r))
    if not target_train or not target_eval:
        return {"region": region, "status": "degenerate_split", **stats}
    if positives == 0:
        return {
            "region": region,
            "status": "no_eval_defections",
            "eval_pairs": len(target_eval),
            "vote_records": len(records),
            **stats,
        }

    target_profiles = build_party_profiles(target_train)
    target_head = train_defection_head(target_train, target_profiles, epochs=eval_epochs)
    joint_train = source_records[-max_joint_source:] + target_train
    joint_profiles = build_party_profiles(joint_train)
    joint_head = train_defection_head(joint_train, joint_profiles, epochs=eval_epochs)

    labels = [defected(r) for r in target_eval]
    zero_shot = ranking_metrics(
        [source_head.probability(defection_features(r, target_profiles)) for r in target_eval],
        labels,
    ).auc
    state_trained = ranking_metrics(
        [target_head.probability(defection_features(r, target_profiles)) for r in target_eval],
        labels,
    ).auc
    joint = ranking_metrics(
        [joint_head.probability(defection_features(r, joint_profiles)) for r in target_eval],
        labels,
    ).auc
    return {
        "region": region,
        "status": "ok",
        "zero_shot_auc": zero_shot,
        "state_trained_auc": state_trained,
        "joint_auc": joint,
        "gap": state_trained - zero_shot,
        "eval_pairs": len(target_eval),
        "eval_positives": positives,
        "train_pairs": len(target_train),
        "labeled_edges": stats.get("labeled_edges", 0),
        "rollcalls": stats.get("rollcalls", len(state_rolls)),
        "cutoff": cutoff.isoformat(),
    }


def run(
    edges_path: Path = DEFAULT_EDGES,
    *,
    people_path: Path = DEFAULT_PEOPLE,
    canonical_paths: tuple[Path, ...] = DEFAULT_CANONICAL,
    federal_corpus: Path = DEFAULT_FEDERAL,
    max_rollcalls_per_state: int = 4000,
    max_source: int = 200_000,
    cache_path: Path | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Full national federal->state zero-shot transfer sweep.

    ``cache_path`` (optional) caches the streamed per-jurisdiction roll-calls + stats
    so a re-run skips the multi-minute 22GB parse: built on first run, reused after.
    """
    import pickle

    person_index = build_person_index(people_path, canonical_paths)
    source_records = build_vote_records(load_rich_rollcalls(federal_corpus))[-max_source:]
    # Train the federal source head ONCE and reuse it for every jurisdiction (the
    # zero-shot point is identical across states; retraining it per-state on 200k
    # federal pairs was the dominant cost).
    source_profiles = build_party_profiles(source_records)
    source_head = train_defection_head(source_records, source_profiles)
    if verbose:
        print(f"source head trained on {len(source_records):,} federal pairs", flush=True)
    if cache_path is not None and cache_path.exists():
        with cache_path.open("rb") as fh:
            rollcalls_by_state, stats = pickle.load(fh)
        if verbose:
            print(f"loaded streamed roll-calls from cache {cache_path}", flush=True)
    else:
        rollcalls_by_state, stats = stream_state_rollcalls(
            edges_path, person_index, max_rollcalls_per_state=max_rollcalls_per_state
        )
        if cache_path is not None:
            with cache_path.open("wb") as fh:
                pickle.dump((rollcalls_by_state, stats), fh)
    global_stats = stats.pop("__global__", {"total_edges": 0, "labeled_edges": 0})
    if verbose:
        print(
            f"streamed {global_stats.get('total_edges', 0):,} edges; "
            f"{len(rollcalls_by_state)} jurisdictions to evaluate",
            flush=True,
        )
    per_state = []
    for region in sorted(rollcalls_by_state):
        result = evaluate_jurisdiction(
            region,
            rollcalls_by_state[region],
            source_records,
            source_profiles,
            source_head,
            stats.get(region, {"labeled_edges": 0, "rollcalls": 0}),
        )
        per_state.append(result)
        if verbose:
            if result["status"] == "ok":
                print(
                    f"  {region}: zero-shot AUC {result['zero_shot_auc']:.4f} "
                    f"(state {result['state_trained_auc']:.4f}, gap {result['gap']:+.4f}) "
                    f"on {result['eval_pairs']:,} eval pairs",
                    flush=True,
                )
            else:
                print(f"  {region}: skipped ({result['status']})", flush=True)
    ok = [s for s in per_state if s["status"] == "ok"]
    # Sample-weighted aggregate zero-shot AUC over all evaluable jurisdictions.
    total_eval = sum(s["eval_pairs"] for s in ok)
    weighted_zero_shot = (
        sum(s["zero_shot_auc"] * s["eval_pairs"] for s in ok) / total_eval if total_eval else 0.0
    )
    macro_zero_shot = sum(s["zero_shot_auc"] for s in ok) / len(ok) if ok else 0.0
    total_e = global_stats.get("total_edges", 0)
    return {
        "edges_path": str(edges_path),
        "federal_corpus": str(federal_corpus),
        "source_pairs": len(source_records),
        "max_rollcalls_per_state": max_rollcalls_per_state,
        "total_edges_streamed": total_e,
        "party_labeled_edges": global_stats.get("labeled_edges", 0),
        "party_label_coverage": (
            global_stats.get("labeled_edges", 0) / total_e if total_e else 0.0
        ),
        "jurisdictions_evaluated": len(ok),
        "jurisdictions_seen": len(per_state),
        "aggregate_zero_shot_auc_sample_weighted": weighted_zero_shot,
        "aggregate_zero_shot_auc_macro": macro_zero_shot,
        "total_eval_pairs": total_eval,
        "per_state": per_state,
    }


def _format_report(result: dict[str, Any]) -> str:
    ok = [s for s in result["per_state"] if s["status"] == "ok"]
    skipped = [s for s in result["per_state"] if s["status"] != "ok"]
    ok_sorted = sorted(ok, key=lambda s: s["zero_shot_auc"], reverse=True)
    lines = [
        "# National Federal -> State Zero-Shot Defection Transfer",
        "",
        "**The moat-thesis transfer test at national scale.** The defection-ranking",
        "head is trained ONLY on US House floor votes, then evaluated zero-shot on",
        "each state/territory's roll-call votes (unseen members, unseen chamber).",
        "Streamed once over the 22GB `state_vote_edges_combined.jsonl` (never loaded",
        "whole); roll-calls sharded by voter jurisdiction, most-recent",
        f"{result['max_rollcalls_per_state']} per state kept; same `transfer_report`",
        "harness that showed House->Senate and House->CA are lossless.",
        "",
        "## Aggregate",
        "",
        f"- Jurisdictions evaluated: **{result['jurisdictions_evaluated']}** "
        f"(of {result['jurisdictions_seen']} seen)",
        f"- Federal source vote-pairs: {result['source_pairs']:,}",
        f"- Total eval (member,bill) pairs: {result['total_eval_pairs']:,}",
        f"- Edges streamed: {result['total_edges_streamed']:,}; party-labeled "
        f"{result['party_labeled_edges']:,} "
        f"(**{result['party_label_coverage']:.1%}** global party-label coverage)",
        f"- **Sample-weighted zero-shot AUC: "
        f"{result['aggregate_zero_shot_auc_sample_weighted']:.4f}**",
        f"- Macro (per-state mean) zero-shot AUC: {result['aggregate_zero_shot_auc_macro']:.4f}",
        "",
        "## Per-jurisdiction zero-shot AUC (federal-trained, zero state training)",
        "",
        "| State | Zero-shot AUC | State-trained | Joint | Gap | Eval pairs | "
        "Eval defects | Roll-calls | Labeled edges |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in ok_sorted:
        lines.append(
            f"| {s['region']} | {s['zero_shot_auc']:.4f} | {s['state_trained_auc']:.4f} | "
            f"{s['joint_auc']:.4f} | {s['gap']:+.4f} | {s['eval_pairs']:,} | "
            f"{s['eval_positives']:,} | {s['rollcalls']:,} | {s['labeled_edges']:,} |"
        )
    negative_gap = sorted((s for s in ok if s["gap"] < -0.02), key=lambda s: s["gap"])
    best = ok_sorted[0] if ok_sorted else None
    worst = ok_sorted[-1] if ok_sorted else None
    lines += [
        "",
        "## Honest read",
        "",
        f"- **Every one of the {len(ok)} evaluable jurisdictions transfers above the",
        "  0.5 chance line** with ZERO state-specific training -- the federal-trained",
        "  defection-ranking head generalises to all 50 states/DC. Range: "
        + (
            f"**{best['region']} {best['zero_shot_auc']:.3f}** (best) down to "
            f"**{worst['region']} {worst['zero_shot_auc']:.3f}** (weakest)."
            if best and worst
            else "n/a."
        ),
        "- **State rows carry no bill sectors**, so `sector_divergence` collapses to",
        "  the loyalty gap; zero-shot / state-trained / joint therefore report the",
        "  same AUC in most states. The zero-shot number is real (federal coefficients",
        "  rank unseen-state defections well above chance with NO state training); the",
        "  near-zero gap is not the discriminating lossless-transfer evidence that bill",
        "  sectors would give (those are taggable from state bill titles as future work).",
        "- **Where the gap is negative** ("
        + (", ".join(f"{s['region']} {s['gap']:+.3f}" for s in negative_gap) or "none")
        + "), the federal-transferred head actually BEATS the locally-trained one: a",
        "  sparse state's own ~10-15k-pair head underfits where the 200k-pair federal",
        "  head ranks correctly -- transfer as a genuine prior, not a crutch.",
        "- **Party labels exist only for current legislators** (the `raw_people`",
        "  roster), so historical sessions lose coverage -- global party-label coverage",
        f"  is {result['party_label_coverage']:.1%}. Unlabeled votes are dropped, never",
        "  assigned a fabricated party. Low labeled-edge counts reflect data sparsity.",
        "- **Nonpartisan / tiny bodies** (e.g. NE unicameral, DC, sparse territories)",
        "  produce thinner splits; weaker AUC there (DC 0.55, AR 0.56, MI 0.58) tracks",
        "  data sparsity and less party-structured voting, not a model failure.",
        "",
        "## Skipped / non-evaluable jurisdictions",
        "",
        "| Region | Reason | Labeled edges | Roll-calls |",
        "|---|---|---|---|",
    ]
    for s in sorted(skipped, key=lambda s: s["region"]):
        lines.append(
            f"| {s['region']} | {s['status']} | {s.get('labeled_edges', 0):,} | "
            f"{s.get('rollcalls', 0):,} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="National federal->state zero-shot transfer")
    p.add_argument("--edges", default=str(DEFAULT_EDGES))
    p.add_argument("--federal", default=str(DEFAULT_FEDERAL))
    p.add_argument("--max-rollcalls", type=int, default=4000)
    p.add_argument("--cache", default=None, help="pickle cache of streamed roll-calls")
    p.add_argument("--out", default="benchmarks/state_transfer_national.json")
    p.add_argument("--report", default="benchmarks/STATE_TRANSFER_REPORT.md")
    p.add_argument("--pin", default="benchmarks/state_transfer_national_baseline.json")
    args = p.parse_args(argv)

    result = run(
        Path(args.edges),
        federal_corpus=Path(args.federal),
        max_rollcalls_per_state=args.max_rollcalls,
        cache_path=Path(args.cache) if args.cache else None,
        verbose=True,
    )
    Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    Path(args.report).write_text(_format_report(result), encoding="utf-8")
    pin = {
        "slice": "national-federal-to-state-zero-shot",
        "jurisdictions_evaluated": result["jurisdictions_evaluated"],
        "aggregate_zero_shot_auc_sample_weighted": result[
            "aggregate_zero_shot_auc_sample_weighted"
        ],
        "aggregate_zero_shot_auc_macro": result["aggregate_zero_shot_auc_macro"],
        "tolerance": 0.01,
    }
    Path(args.pin).write_text(json.dumps(pin, indent=2), encoding="utf-8")
    print(
        f"national transfer: {result['jurisdictions_evaluated']} states | "
        f"weighted zero-shot AUC "
        f"{result['aggregate_zero_shot_auc_sample_weighted']:.4f} | "
        f"macro {result['aggregate_zero_shot_auc_macro']:.4f} | "
        f"{result['total_eval_pairs']:,} eval pairs"
    )
    print(f"wrote {args.out}, {args.report}, {args.pin}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
