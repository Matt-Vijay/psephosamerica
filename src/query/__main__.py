"""Ask-anything CLI for the Psephos America connected graph (V8 deliverable #1).

A clean, dependency-free entrypoint over the GraphRAG reasoning layer and the
analytical lenses. Run:

    python -m src.query ask "Who funds the senators who voted on the budget?"

The ``ask`` command builds the in-memory graph from Track A's exports, runs
GraphRAG retrieval + grounding, and prints a **cited** answer. With no
``ANTHROPIC_API_KEY`` it serves the deterministic, never-hallucinating grounded
stub; with a key set it upgrades to a live ``claude-opus-4-8`` answer (adaptive
thinking) over the same cited evidence — no code path change, just the key.

Other commands surface the lenses from the terminal:

    python -m src.query lens copied-bills        # BILLSTATUS-dossier similarity
    python -m src.query lens accountability       # per-official opacity ranking
    python -m src.query lens jurisdictions        # per-jurisdiction accountability
    python -m src.query said-vs-voted <person_id> # speeches vs votes

Everything prints citations. ``--json`` emits machine-readable output.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from src.query import lenses
from src.query.graph_rag import GraphRagAnswerer
from src.query.graph_store import build_store


def _print_citations(citations: Sequence[dict[str, str | None]]) -> None:
    if not citations:
        print("  (no source-anchored citations)")
        return
    print("\nCitations:")
    for i, citation in enumerate(citations, start=1):
        url = citation.get("source_url") or "(no url)"
        known = citation.get("known_at") or "?"
        print(f"  [{i}] {url}  @ {known}")


def _cmd_ask(args: argparse.Namespace) -> int:
    store = build_store(load_embeddings=True)
    answerer = GraphRagAnswerer.from_environment(store, top_k=args.top_k)
    result = answerer.answer(args.question)
    if args.json:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return 0
    mode = "live LLM" if result.used_llm else "grounded stub (no live backend)"
    print(f"Question: {result.question}")
    print(f"Mode: {mode}")
    print(f"Served by: {result.served_by}\n")
    print(result.answer)
    _print_citations(result.citations())
    if not result.retrieved:
        print(
            "\n(No entities carried embeddings — the store may lack dossier_embedding; "
            "retrieval returned nothing to ground on.)",
            file=sys.stderr,
        )
    return 0


def _cmd_lens(args: argparse.Namespace) -> int:
    if args.which == "copied-bills":
        store = build_store(edge_paths=()) if args.cite else None
        pairs = lenses.copied_bill_clusters(
            store=store, limit=args.scan, threshold=args.threshold, max_pairs=args.limit
        )
        if args.json:
            print(json.dumps([_copied_dict(p) for p in pairs], ensure_ascii=False, indent=2))
            return 0
        print(f"Similar BILLSTATUS-dossier pairs (jaccard >= {args.threshold}):\n")
        for p in pairs:
            tag = "CROSS-JURISDICTION " if p.cross_jurisdiction else ""
            title_a = str(p.a.get("title", ""))[:55]
            title_b = str(p.b.get("title", ""))[:55]
            print(f"  {p.jaccard:.3f} {tag}{title_a!r}")
            print(f"        <-> {title_b!r}")
            print(f"        src A: {_cite_url(p.a)}")
            print(f"        src B: {_cite_url(p.b)}")
        return 0
    if args.which == "accountability":
        rows = lenses.official_accountability(build_store(), limit=args.limit)
        if args.json:
            print(json.dumps([_acct_dict(r) for r in rows], ensure_ascii=False, indent=2))
            return 0
        print("Official accountability ranking (most observable surface first):\n")
        for r in rows:
            print(
                f"  {r.score:.3f}  {r.display_name[:32]:32}  "
                f"[{r.jurisdiction or 'unknown'}]  votes={r.observations['votes']} "
                f"cited={r.observations['cited_votes']} speeches={r.observations['floor_speeches']}"
            )
            print(f"          source: {r.citation['source_url']}")
        return 0
    if args.which == "jurisdictions":
        juris = lenses.jurisdiction_accountability(build_store(), limit=args.limit)
        if args.json:
            print(json.dumps([j.__dict__ for j in juris], ensure_ascii=False, indent=2))
            return 0
        print("Per-jurisdiction accountability surface:\n")
        for j in juris:
            print(
                f"  {j.jurisdiction:24}  officials={j.officials:5}  "
                f"mean_score={j.mean_score:.3f}  mean_source_coverage={j.mean_source_coverage:.3f}"
                f"  with_voice={j.officials_with_voice}"
            )
        return 0
    return 1


def _cite_url(brief: dict[str, object]) -> str | None:
    citation = brief.get("citation")
    if isinstance(citation, dict):
        return citation.get("source_url")
    return None


def _cmd_said_vs_voted(args: argparse.Namespace) -> int:
    # Include both speech feeds so this lens lights up fully: the per-issue
    # member -> Congressional Record feed (SPEECH_EDGES, broad coverage) and the
    # typed member -> bill feed (SPEECH_BILL_EDGES, ties a speech to the specific
    # bills debated). News mentions add the optional voice signal.
    from src.query.graph_store import (
        BILL_EDGES,
        MUNICIPAL_VOTES,
        NEWS_EDGES,
        SENATE_VOTES,
        SPEECH_BILL_EDGES,
        SPEECH_EDGES,
    )

    store = build_store(
        edge_paths=(
            MUNICIPAL_VOTES,
            SENATE_VOTES,
            BILL_EDGES,
            SPEECH_EDGES,
            SPEECH_BILL_EDGES,
            NEWS_EDGES,
        )
    )
    result = lenses.said_vs_voted(store, args.person_id)
    if result is None:
        print(f"No official with id {args.person_id!r}", file=sys.stderr)
        return 1
    if args.json:
        print(
            json.dumps(
                {
                    "official": result.official,
                    "speeches": list(result.speeches),
                    "votes": list(result.votes),
                    "note": result.note,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    print(f"Said vs voted — {result.official.get('display_name')}\n")
    print(f"Floor speeches ({len(result.speeches)}):")
    for s in result.speeches:
        print(f"  {s.get('venue')} [{s.get('chamber')}]  source: {_cite_url(s)}")
    if result.note:
        print(f"  {result.note}")
    print(f"\nRoll-call votes ({len(result.votes)}):")
    for v in result.votes:
        label = v.get("bill_name") or v.get("bill_id")
        print(f"  {str(v.get('choice')):>4} on {label}  source: {_cite_url(v)}")
    return 0


def _copied_dict(p: lenses.CopiedBillPair) -> dict[str, object]:
    return {
        "jaccard": round(p.jaccard, 4),
        "cross_jurisdiction": p.cross_jurisdiction,
        "a": p.a,
        "b": p.b,
    }


def _acct_dict(r: lenses.AccountabilityScore) -> dict[str, object]:
    return {
        "entity_id": r.entity_id,
        "display_name": r.display_name,
        "jurisdiction": r.jurisdiction,
        "score": r.score,
        "components": r.components,
        "observations": r.observations,
        "citation": r.citation,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.query",
        description="Ask cited questions of the Psephos America connected graph and run analytical lenses.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="Ask a natural-language question (cited GraphRAG answer).")
    ask.add_argument("question", help="The question to answer.")
    ask.add_argument("--top-k", type=int, default=5, help="How many entities to retrieve.")
    ask.add_argument("--json", action="store_true", help="Emit JSON.")
    ask.set_defaults(func=_cmd_ask)

    lens = sub.add_parser("lens", help="Run an analytical lens.")
    lens.add_argument(
        "which",
        choices=["copied-bills", "accountability", "jurisdictions"],
        help="Which lens to run.",
    )
    lens.add_argument("--limit", type=int, default=25, help="Max rows.")
    lens.add_argument("--scan", type=int, default=20000, help="Bills to scan (copied-bills).")
    lens.add_argument("--threshold", type=float, default=0.7, help="Jaccard cutoff (copied-bills).")
    lens.add_argument(
        "--cite",
        action="store_true",
        help="Enrich copied-bill citations from canonical bill records (builds the store).",
    )
    lens.add_argument("--json", action="store_true", help="Emit JSON.")
    lens.set_defaults(func=_cmd_lens)

    svv = sub.add_parser("said-vs-voted", help="Compare an official's speeches vs votes.")
    svv.add_argument("person_id", help="Canonical person id (e.g. ce-...).")
    svv.add_argument("--json", action="store_true", help="Emit JSON.")
    svv.set_defaults(func=_cmd_said_vs_voted)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
