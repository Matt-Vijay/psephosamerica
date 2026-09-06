"""End-to-end tests for `run(argv)` over the DB-backed publish roundtrip path.

These tests keep CLI parsing, `commands.verify_publish_roundtrip_local`,
and the real roundtrip verifier in play. The only patched boundaries are:

- `open_connection` in `commands.py` so no real database is required
- the irreducible DB fetch helpers used by the roundtrip stages
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.api.read_service import get_homepage_bootstrap, get_zip_entry
from src.export.filesystem import write_planned_files
from src.export.writer import (
    PlannedFile,
    homepage_bootstrap_path,
    serialize_payload,
    zip_entry_path,
)
from src.runtime.main import run
from src.runtime.publish_roundtrip_types import PublishRoundtripStageResult
from tests.support.published_roundtrip_fixtures import (
    PublishedRoundtrip,
    assemble_from_homepage_feed_row_set,
    make_evidence_card_row_set,
    make_member_row_set,
    make_roundtrip,
    make_zip_feed_row_set,
)

_OPEN_CONNECTION = "src.runtime.commands.core.open_connection"
_FETCH_MEMBER_BY_SLUG = "src.runtime.publish_roundtrip_profiles.fetch_member_row_by_slug"
_FETCH_SCORE_ROWS = "src.runtime.publish_roundtrip_profiles.fetch_member_score_snapshot_rows"
_FETCH_RULE_FIRES = "src.runtime.publish_roundtrip_profiles.fetch_member_rule_fire_rows"
_FETCH_COMMITTEES = "src.runtime.publish_roundtrip_profiles.fetch_member_committee_rows"
_FETCH_PROFILE_CARDS = "src.runtime.publish_roundtrip_profiles.fetch_all_evidence_card_rows"
_FETCH_ALL_CARDS = "src.runtime.publish_roundtrip_evidence.fetch_all_evidence_card_rows"
_FETCH_ONTOLOGY_ROWS = "src.runtime.publish_roundtrip_ontology.fetch_all_ontology_edge_rows"
_VERIFY_PREDICTION = "src.runtime.publish_roundtrip.verify_published_prediction_roundtrip"
_FETCH_ZIP_ROWS = "src.runtime.publish_roundtrip_zip.fetch_zip_member_summary_rows"
_FETCH_ZIP_EVIDENCE = "src.runtime.publish_roundtrip_zip.fetch_recent_evidence_ids_by_bioguide"
_FETCH_HOMEPAGE_ROWS = "src.runtime.publish_roundtrip_homepage.fetch_homepage_feed_rows"


def _argv(publish_root: Path) -> list[str]:
    return ["verify-publish-roundtrip", "--publish-root", str(publish_root)]


def _write_homepage_feed(root: Path, rt: PublishedRoundtrip) -> None:
    payload = assemble_from_homepage_feed_row_set(
        rt.homepage_feed_row_set,
        snapshot_date=rt.snapshot_date,
    )
    feed_file = root / "homepage" / "feed.json"
    feed_file.parent.mkdir(parents=True, exist_ok=True)
    feed_file.write_bytes(serialize_payload(payload))
    homepage_bootstrap = get_homepage_bootstrap(snapshot_root=root)
    assert not isinstance(homepage_bootstrap, dict)
    assert getattr(homepage_bootstrap, "ok", False) is True
    planned = [
        PlannedFile.from_bytes(
            homepage_bootstrap_path(),
            serialize_payload(homepage_bootstrap.data),
        )
    ]
    for zip_feed in rt.zip_feed_row_sets:
        zip_entry = get_zip_entry(zip_feed.zip_code, snapshot_root=root)
        assert not isinstance(zip_entry, dict)
        assert getattr(zip_entry, "ok", False) is True
        planned.append(
            PlannedFile.from_bytes(
                zip_entry_path(zip_feed.zip_code),
                serialize_payload(zip_entry.data),
            )
        )
    write_planned_files(planned, root)


def _make_profile_side_effects(rt: PublishedRoundtrip):
    slug_to_rs = {rs.member_row["slug"]: rs for rs in rt.member_row_sets}
    id_to_rs = {rs.member_row["id"]: rs for rs in rt.member_row_sets}

    def _by_slug(conn, slug: str):
        rs = slug_to_rs.get(slug)
        return rs.member_row if rs else None

    def _score_rows(conn, member_id: int):
        rs = id_to_rs.get(member_id)
        return rs.score_snapshot_rows if rs else []

    def _rule_fires(conn, member_id: int):
        rs = id_to_rs.get(member_id)
        return rs.rule_fire_rows if rs else []

    def _committees(conn, member_id: int):
        rs = id_to_rs.get(member_id)
        return rs.committee_rows if rs else []

    return _by_slug, _score_rows, _rule_fires, _committees


def _zip_rows(rt: PublishedRoundtrip) -> list[dict]:
    rows: list[dict] = []
    for rs in rt.zip_feed_row_sets:
        for member_row in rs.member_rows:
            for score in member_row["scores"]:
                rows.append(
                    {
                        "bioguide_id": member_row["bioguide_id"],
                        "dimension": score["dimension"],
                        "current_score": score["current_score"],
                        "rule_fire_count": score["rule_fire_count"],
                    }
                )
    return rows


def _zip_evidence(rt: PublishedRoundtrip) -> dict[str, list[str]]:
    evidence_by_member: dict[str, list[str]] = {}
    for rs in rt.zip_feed_row_sets:
        for row in rs.member_rows:
            evidence_by_member[row["bioguide_id"]] = list(row["top_evidence_card_ids"])
    return evidence_by_member


def _prediction_stub(conn, root, manifest):
    return PublishRoundtripStageResult(stage="prediction", checked=0, issues=())


def _run_and_parse(
    publish_root: Path,
    capsys,
    *,
    roundtrip: PublishedRoundtrip | None = None,
) -> tuple[int, dict]:
    conn = MagicMock(name="conn")

    if roundtrip is None:

        def by_slug(conn, slug: str):
            return None

        def score_rows(conn, member_id: int):
            return []

        def rule_fires(conn, member_id: int):
            return []

        def committees(conn, member_id: int):
            return []

        all_cards: list[dict] = []
        zip_rows: list[dict] = []
        zip_evidence: dict[str, list[str]] = {}
        homepage_rows: list[dict] = []
    else:
        by_slug, score_rows, rule_fires, committees = _make_profile_side_effects(roundtrip)
        all_cards = [rs.row for rs in roundtrip.evidence_card_row_sets]
        zip_rows = _zip_rows(roundtrip)
        zip_evidence = _zip_evidence(roundtrip)
        homepage_rows = roundtrip.homepage_feed_row_set.rows

    with (
        patch(_OPEN_CONNECTION, return_value=conn),
        patch(_FETCH_MEMBER_BY_SLUG, side_effect=by_slug),
        patch(_FETCH_SCORE_ROWS, side_effect=score_rows),
        patch(_FETCH_RULE_FIRES, side_effect=rule_fires),
        patch(_FETCH_COMMITTEES, side_effect=committees),
        patch(_FETCH_PROFILE_CARDS, return_value=all_cards),
        patch(_FETCH_ALL_CARDS, return_value=all_cards),
        patch(_FETCH_ONTOLOGY_ROWS, return_value=[]),
        patch(_VERIFY_PREDICTION, side_effect=_prediction_stub),
        patch(_FETCH_ZIP_ROWS, return_value=zip_rows),
        patch(_FETCH_ZIP_EVIDENCE, return_value=zip_evidence),
        patch(_FETCH_HOMEPAGE_ROWS, return_value=homepage_rows),
    ):
        code = run(_argv(publish_root))

    out = json.loads(capsys.readouterr().out)
    return code, out


def _make_valid_tree(tmp_path: Path) -> PublishedRoundtrip:
    rt = make_roundtrip(tmp_path)
    _write_homepage_feed(tmp_path, rt)
    return rt


class TestRunVerifyPublishRoundtripCommand:
    def test_exits_zero_for_valid_tree(self, tmp_path: Path, capsys) -> None:
        rt = _make_valid_tree(tmp_path)
        code, _ = _run_and_parse(tmp_path, capsys, roundtrip=rt)
        assert code == 0

    def test_output_has_command_field(self, tmp_path: Path, capsys) -> None:
        rt = _make_valid_tree(tmp_path)
        _, out = _run_and_parse(tmp_path, capsys, roundtrip=rt)
        assert out["command"] == "verify-publish-roundtrip"

    def test_output_has_roundtrip_key(self, tmp_path: Path, capsys) -> None:
        rt = _make_valid_tree(tmp_path)
        _, out = _run_and_parse(tmp_path, capsys, roundtrip=rt)
        assert "roundtrip" in out


class TestRunVerifyPublishRoundtripValidTree:
    def test_ok_true_for_default_roundtrip_tree(self, tmp_path: Path, capsys) -> None:
        rt = _make_valid_tree(tmp_path)
        _, out = _run_and_parse(tmp_path, capsys, roundtrip=rt)
        assert out["ok"] is True

    def test_roundtrip_summary_has_expected_stage_names(self, tmp_path: Path, capsys) -> None:
        rt = _make_valid_tree(tmp_path)
        _, out = _run_and_parse(tmp_path, capsys, roundtrip=rt)
        names = [stage["stage"] for stage in out["roundtrip"]["stages"]]
        assert names == [
            "snapshot",
            "profiles",
            "evidence",
            "ontology",
            "prediction",
            "zip",
            "homepage",
            "lookup",
        ]

    def test_multiple_members_and_cards_ok(self, tmp_path: Path, capsys) -> None:
        member_sets = [
            make_member_row_set(bioguide_id="A000001", slug="alice-smith", full_name="Alice Smith"),
            make_member_row_set(bioguide_id="B000002", slug="bob-jones", full_name="Bob Jones"),
        ]
        evidence_sets = [
            make_evidence_card_row_set(
                public_id="ec-a001", member_slug="alice-smith", bioguide_id="A000001"
            ),
            make_evidence_card_row_set(
                public_id="ec-b002", member_slug="bob-jones", bioguide_id="B000002"
            ),
        ]
        zip_sets = [
            make_zip_feed_row_set(zip_code="94102"),
            make_zip_feed_row_set(
                zip_code="10001",
                district="NY-12",
                bioguide_id="B000002",
                member_name="Bob Jones",
                member_slug="bob-jones",
                chamber="senate",
                party="Democrat",
                top_evidence_card_ids=["ec-b002"],
            ),
        ]
        rt = make_roundtrip(
            tmp_path,
            member_row_sets=member_sets,
            evidence_card_row_sets=evidence_sets,
            zip_feed_row_sets=zip_sets,
        )
        _write_homepage_feed(tmp_path, rt)
        _, out = _run_and_parse(tmp_path, capsys, roundtrip=rt)
        assert out["ok"] is True

    def test_missing_lookup_file_sets_ok_false(self, tmp_path: Path, capsys) -> None:
        rt = _make_valid_tree(tmp_path)
        lookup_files = list((tmp_path / "identity").glob("current-member-lookup.json"))
        assert lookup_files
        lookup_files[0].unlink()
        code, out = _run_and_parse(tmp_path, capsys, roundtrip=rt)
        assert code == 1
        assert out["ok"] is False


class TestRunVerifyPublishRoundtripBrokenTree:
    def test_missing_member_profile_sets_ok_false(self, tmp_path: Path, capsys) -> None:
        rt = _make_valid_tree(tmp_path)
        member_files = list((tmp_path / "members").glob("*.json"))
        assert member_files
        member_files[0].unlink()
        code, out = _run_and_parse(tmp_path, capsys, roundtrip=rt)
        assert code == 1
        assert out["ok"] is False

    def test_missing_manifest_sets_ok_false(self, tmp_path: Path, capsys) -> None:
        rt = _make_valid_tree(tmp_path)
        manifest_files = list((tmp_path / "snapshots").glob("*/manifest.json"))
        assert manifest_files
        manifest_files[0].unlink()
        code, out = _run_and_parse(tmp_path, capsys, roundtrip=rt)
        assert code == 1
        assert out["ok"] is False

    def test_empty_root_sets_ok_false(self, tmp_path: Path, capsys) -> None:
        empty_root = tmp_path / "empty"
        empty_root.mkdir()
        code, out = _run_and_parse(empty_root, capsys)
        assert code == 1
        assert out["ok"] is False


class TestRunVerifyPublishRoundtripCliArgs:
    def test_missing_publish_root_arg_exits_nonzero(self) -> None:
        code = run(["verify-publish-roundtrip"])
        assert code != 0

    def test_nonexistent_root_returns_ok_false(self, tmp_path: Path, capsys) -> None:
        missing = tmp_path / "does-not-exist"
        code, out = _run_and_parse(missing, capsys)
        assert code == 1
        assert out["ok"] is False


class TestRunVerifyPublishRoundtripOutputShape:
    def test_roundtrip_summary_has_expected_keys(self, tmp_path: Path, capsys) -> None:
        rt = _make_valid_tree(tmp_path)
        _, out = _run_and_parse(tmp_path, capsys, roundtrip=rt)
        assert {
            "ok",
            "total_checked",
            "total_errors",
            "total_warnings",
            "stages",
            "issues",
            "issues_truncated",
        }.issubset(out["roundtrip"])
        assert isinstance(out["roundtrip"]["issues"], list)
        assert out["roundtrip"]["issues_truncated"] is False

    def test_each_stage_has_stable_keys(self, tmp_path: Path, capsys) -> None:
        rt = _make_valid_tree(tmp_path)
        _, out = _run_and_parse(tmp_path, capsys, roundtrip=rt)
        for stage in out["roundtrip"]["stages"]:
            assert set(stage) == {"stage", "checked", "ok", "errors", "warnings"}
