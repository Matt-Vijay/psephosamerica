"""Weekly marginal-votes report: the top-N flippable votes with evidence (v5 #5).

The product, in one artifact. For a target window it ranks the most flippable
(member, bill) pairs by predicted defection probability -- the votes a campaign
would work -- and attaches, for each: the ex-ante factors that drove the call
(cited, with signed contribution), the counterfactual that would flip it, and the
venue's historical P(pass) on the bill's policy area (from venue-score). It
composes defection-watch (who to persuade) with venue-score (where it matters)
into the marginal-vote brief.

Pure roll-call data; strict cutoff (model trained only on votes <= cutoff). Emits
JSON + a server-rendered HTML brief.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from html import escape
from pathlib import Path
from typing import Any

from src.prediction.defection import build_party_profiles, defection_features
from src.prediction.defection_head import train_defection_head
from src.prediction.vote_record import VoteRecord
from src.runtime.flat_corpus import build_linked_flat_votes

_FACTOR_LABEL = {
    "loyalty_gap": "breaks with party overall",
    "sector_divergence": "breaks with party on this policy area",
}


@dataclass(frozen=True)
class MarginalVote:
    rank: int
    member: str
    party: str
    state: str
    bill_id: str
    target_vote_date: str
    p_defect: float
    factors: list[str]
    counterfactual: str
    venue_p_pass: float
    citations: list[dict[str, str]] = field(default_factory=list)


def generate(
    votes: list[tuple[VoteRecord, str]],
    *,
    cutoff: date,
    target_end: date,
    top_n: int = 20,
) -> dict[str, Any]:
    """Rank the top-N flippable (member, bill) pairs in (cutoff, target_end]."""
    train_pairs = [(r, b) for r, b in votes if r.vote_date <= cutoff]
    train = [r for r, _b in train_pairs]
    target = [(r, b) for r, b in votes if cutoff < r.vote_date <= target_end]
    profiles = build_party_profiles(train)
    head = train_defection_head(train, profiles)

    # venue P(pass): historical pass rate per bill from pre-cutoff votes (a flat
    # corpus has no sectors, so this is the bill/venue-level rate).
    pass_by_bill: dict[str, tuple[int, int]] = {}
    for rec, b in train_pairs:
        y, n = pass_by_bill.get(b, (0, 0))
        pass_by_bill[b] = (y + int(rec.is_yea), n + 1)
    overall_pass = sum(1 for rec in train if rec.is_yea) / len(train) if train else 0.5

    scored = []
    for r, bill_id in target:
        features = defection_features(r, profiles)
        scored.append((head.probability(features), r, bill_id, features))
    scored.sort(key=lambda t: t[0], reverse=True)

    rows: list[MarginalVote] = []
    for i, (p, r, bill_id, features) in enumerate(scored[:top_n], start=1):
        contributions = head.contributions(features)
        ordered = sorted(contributions.items(), key=lambda kv: abs(kv[1]), reverse=True)
        factors = [f"{_FACTOR_LABEL.get(n, n)} ({v:+.2f})" for n, v in ordered if abs(v) > 1e-9]
        top = max(contributions.items(), key=lambda kv: kv[1]) if contributions else ("", 0.0)
        cf = (
            f"if the member no longer {_FACTOR_LABEL.get(top[0], top[0])}, P(defect) drops most"
            if top[1] > 0
            else "already at the party line"
        )
        yy, nn = pass_by_bill.get(bill_id, (0, 0))
        venue_pass = yy / nn if nn else overall_pass
        rows.append(
            MarginalVote(
                rank=i,
                member=r.member,
                party=r.party,
                state=r.state,
                bill_id=bill_id,
                target_vote_date=r.vote_date.isoformat(),
                p_defect=p,
                factors=factors,
                counterfactual=cf,
                venue_p_pass=venue_pass,
                citations=[
                    {"kind": "rollcall_bill", "ref": bill_id},
                    {
                        "kind": "member_history",
                        "ref": r.member,
                        "detail": f"loyalty_gap={features.get('loyalty_gap', 0.0):.3f}",
                    },
                ],
            )
        )
    return {
        "generated_for_window": f"{cutoff.isoformat()}..{target_end.isoformat()}",
        "model_name": "defection_head_logreg",
        "top_n": top_n,
        "marginal_votes": [asdict(m) for m in rows],
    }


def render_html(report: dict[str, Any]) -> str:
    style = (
        "body{font:14px system-ui,sans-serif;margin:2rem;color:#111}h1{font-size:1.4rem}"
        "table{border-collapse:collapse;width:100%}th,td{border:1px solid #ddd;padding:.4rem .6rem;"
        "text-align:left}th{background:#f6f6f6}.prob{font-weight:700;color:#c0392b}.muted{color:#666}"
    )
    rows = "".join(
        "<tr>"
        f"<td>{m['rank']}</td>"
        f"<td><strong>{escape(m['member'])}</strong> "
        f"<span class='muted'>({escape(m['party'])}-{escape(m['state'])})</span></td>"
        f"<td>{escape(m['bill_id'])}</td>"
        f"<td class='prob'>{m['p_defect'] * 100:.0f}%</td>"
        f"<td>{m['venue_p_pass'] * 100:.0f}%</td>"
        f"<td class='muted'>{escape('; '.join(m['factors']))}</td>"
        f"<td class='muted'>{escape(m['counterfactual'])}</td>"
        "</tr>"
        for m in report["marginal_votes"]
    )
    body = rows or "<tr><td colspan='7' class='muted'>No marginal votes.</td></tr>"
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<title>OpenPact — Marginal Votes Brief</title>"
        f"<style>{style}</style></head><body>"
        "<h1>OpenPact — Weekly Marginal-Votes Brief</h1>"
        f"<p class='muted'>Top {report['top_n']} flippable votes for "
        f"{escape(report['generated_for_window'])} — who to persuade, why, and the venue's "
        "pass rate. Model trained strictly on pre-window votes.</p>"
        "<table><thead><tr><th>#</th><th>Member</th><th>Bill</th><th>P(defect)</th>"
        "<th>Venue P(pass)</th><th>Why (factor, contribution)</th><th>What flips it</th>"
        f"</tr></thead><tbody>{body}</tbody></table></body></html>"
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Weekly marginal-votes report")
    parser.add_argument("--corpus", default="data/real/house_119.jsonl")
    parser.add_argument("--cutoff", default="2025-09-30")
    parser.add_argument("--target-end", default="2025-12-18")
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--out", default="benchmarks/marginal_votes_report.json")
    parser.add_argument("--html", default="benchmarks/marginal_votes_report.html")
    args = parser.parse_args(argv)

    votes = build_linked_flat_votes(Path(args.corpus))
    report = generate(
        votes,
        cutoff=date.fromisoformat(args.cutoff),
        target_end=date.fromisoformat(args.target_end),
        top_n=args.top_n,
    )
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    Path(args.html).write_text(render_html(report), encoding="utf-8")
    print(f"top-{args.top_n} marginal votes; wrote {args.out} + {args.html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
