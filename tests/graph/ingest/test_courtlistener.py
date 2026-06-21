from __future__ import annotations

from datetime import UTC, datetime

from src.graph.ingest.courtlistener import (
    parse_court,
    parse_court_opinion,
    parse_judge,
    statute_refs_in,
)

_OBSERVED = datetime(2026, 6, 21, tzinfo=UTC)


def test_parse_court_requires_id_and_name() -> None:
    court = parse_court(
        {
            "id": "scotus",
            "full_name": "Supreme Court of the United States",
            "citation_string": "SCOTUS",
            "jurisdiction": "F",
            "start_date": "1789-09-24",
        }
    )
    assert court is not None
    assert court.court_id == "scotus"
    assert court.start_date is not None and court.start_date.year == 1789
    # Missing id / name -> skipped.
    assert parse_court({"full_name": "Nameless"}) is None
    assert parse_court({"id": "x"}) is None


def test_parse_judge_uses_fjc_and_person_keys() -> None:
    judge = parse_judge(
        {
            "id": 3045,
            "name_first": "Sonia",
            "name_middle": "",
            "name_last": "Sotomayor",
            "name_suffix": "",
            "fjc_id": 2243,
            "slug": "sonia-sotomayor",
            "dob_state": "NY",
        }
    )
    assert judge is not None
    assert judge.person_id == 3045
    assert judge.fjc_id == "2243"
    assert judge.display_name == "Sonia Sotomayor"
    # No id or no name -> skipped.
    assert parse_judge({"name_first": "No", "name_last": "Id"}) is None
    assert parse_judge({"id": 9}) is None


def test_parse_judge_without_fjc_still_resolves_on_person_id() -> None:
    judge = parse_judge({"id": 16242, "name_first": "Angel", "name_last": "Kelley"})
    assert judge is not None
    assert judge.fjc_id is None


def test_parse_court_opinion_reads_authorship_and_citations() -> None:
    opinion = parse_court_opinion(
        {
            "cluster_id": 12345,
            "court_id": "scotus",
            "dateFiled": "2020-06-15",
            "caseName": "Bostock v. Clayton County",
            "docketNumber": "17-1618",
            "citation": ["590 U.S. 644"],
            "judge": "Neil Gorsuch",
            "panel_ids": [1, 2, 3],
            "non_participating_judge_ids": [],
            "opinions": [
                {
                    "id": 5001,
                    "author_id": 1,
                    "type": "lead",
                    "joined_by_ids": [2, 3],
                    "cites": [7001, 7002],
                }
            ],
        }
    )
    assert opinion is not None
    assert opinion.cluster_id == 12345
    assert opinion.court_id == "scotus"
    assert opinion.date_filed.isoformat() == "2020-06-15"
    assert opinion.docket_number == "17-1618"
    assert opinion.reporter_citations == ("590 U.S. 644",)
    assert len(opinion.opinions) == 1
    auth = opinion.opinions[0]
    assert auth.author_id == 1
    assert auth.joined_by_ids == (2, 3)
    assert auth.cited_opinion_ids == (7001, 7002)


def test_parse_court_opinion_skips_malformed() -> None:
    # No cluster id.
    assert parse_court_opinion({"court_id": "scotus", "dateFiled": "2020-01-01"}) is None
    # No court id.
    assert parse_court_opinion({"cluster_id": 1, "dateFiled": "2020-01-01"}) is None
    # Unparseable date.
    assert parse_court_opinion({"cluster_id": 1, "court_id": "scotus", "dateFiled": "n/a"}) is None


def test_statute_refs_parse_usc_and_public_law() -> None:
    refs = statute_refs_in("see 42 U.S.C. § 1983 and Pub. L. No. 111-148")
    ids = {r.identifier for r in refs}
    assert "usc-42-1983" in ids
    assert "pl-111-148" in ids
    # USC sub-section suffix is preserved.
    refs2 = statute_refs_in("18 U.S.C. 924(c) and 42 U. S. C. §2000e-2")
    ids2 = {r.identifier for r in refs2}
    assert "usc-18-924" in ids2
    assert "usc-42-2000e-2" in ids2
    # No statute -> empty (the keyless metadata gate).
    assert statute_refs_in("just a case name") == ()
