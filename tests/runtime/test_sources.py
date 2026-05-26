"""Tests for src/runtime/sources.py.

No network calls. No DB. Pure unit tests over immutable SourceSpec values.
"""

from __future__ import annotations

import pytest

from src.runtime.sources import (
    CONFLICT_RECOMPUTE,
    CONGRESS_CORE,
    DISCLOSURE_LOAD,
    FEC_BULK,
    HOUSE_DISCLOSURES,
    MEMBER_FEC_CROSSWALK,
    SENATE_DISCLOSURES,
    SNAPSHOT_PUBLISH,
    all_sources,
    source_by_slug,
)


# ---------------------------------------------------------------------------
# SourceSpec shape
# ---------------------------------------------------------------------------


def test_source_spec_is_frozen():
    spec = CONGRESS_CORE
    with pytest.raises((AttributeError, TypeError)):
        spec.slug = "other"  # type: ignore[misc]


def test_all_specs_have_required_fields():
    for spec in all_sources():
        assert spec.slug and isinstance(spec.slug, str)
        assert spec.name and isinstance(spec.name, str)
        assert spec.source_kind in ("official", "supporting", "artifact", "internal")
        assert spec.base_url is None or isinstance(spec.base_url, str)


# ---------------------------------------------------------------------------
# Canonical slug values (interface contract for provenance store callers)
# ---------------------------------------------------------------------------


def test_congress_core_slug():
    assert CONGRESS_CORE.slug == "congress-gov-api"


def test_disclosure_load_slug():
    assert DISCLOSURE_LOAD.slug == "financial-disclosures"


def test_house_disclosures_slug():
    assert HOUSE_DISCLOSURES.slug == "house-disclosures"


def test_senate_disclosures_slug():
    assert SENATE_DISCLOSURES.slug == "senate-disclosures"


def test_conflict_recompute_slug():
    assert CONFLICT_RECOMPUTE.slug == "conflict-recompute"


def test_snapshot_publish_slug():
    assert SNAPSHOT_PUBLISH.slug == "snapshot-publish"


def test_fec_bulk_slug():
    assert FEC_BULK.slug == "fec-bulk"


def test_member_fec_crosswalk_slug():
    assert MEMBER_FEC_CROSSWALK.slug == "member-fec-crosswalk"


# ---------------------------------------------------------------------------
# source_kind constraints match db/schema.sql CHECK
# ---------------------------------------------------------------------------


def test_congress_core_is_official():
    assert CONGRESS_CORE.source_kind == "official"


def test_house_disclosures_is_official():
    assert HOUSE_DISCLOSURES.source_kind == "official"


def test_senate_disclosures_is_official():
    assert SENATE_DISCLOSURES.source_kind == "official"


def test_fec_bulk_is_official():
    assert FEC_BULK.source_kind == "official"


def test_member_fec_crosswalk_is_supporting():
    assert MEMBER_FEC_CROSSWALK.source_kind == "supporting"


def test_disclosure_load_is_internal():
    assert DISCLOSURE_LOAD.source_kind == "internal"


def test_conflict_recompute_is_internal():
    assert CONFLICT_RECOMPUTE.source_kind == "internal"


def test_snapshot_publish_is_artifact():
    assert SNAPSHOT_PUBLISH.source_kind == "artifact"


# ---------------------------------------------------------------------------
# base_url presence rules
# ---------------------------------------------------------------------------


def test_external_sources_have_base_url():
    assert CONGRESS_CORE.base_url is not None
    assert HOUSE_DISCLOSURES.base_url is not None
    assert SENATE_DISCLOSURES.base_url is not None
    assert FEC_BULK.base_url is not None
    assert MEMBER_FEC_CROSSWALK.base_url is not None


def test_internal_sources_have_no_base_url():
    assert DISCLOSURE_LOAD.base_url is None
    assert CONFLICT_RECOMPUTE.base_url is None
    assert SNAPSHOT_PUBLISH.base_url is None


# ---------------------------------------------------------------------------
# all_sources
# ---------------------------------------------------------------------------


def test_all_sources_returns_eight_specs():
    assert len(all_sources()) == 8


def test_all_sources_slugs_are_unique():
    slugs = [s.slug for s in all_sources()]
    assert len(slugs) == len(set(slugs))


def test_all_sources_contains_each_canonical():
    slugs = {s.slug for s in all_sources()}
    assert "congress-gov-api" in slugs
    assert "house-disclosures" in slugs
    assert "senate-disclosures" in slugs
    assert "fec-bulk" in slugs
    assert "member-fec-crosswalk" in slugs
    assert "financial-disclosures" in slugs
    assert "conflict-recompute" in slugs
    assert "snapshot-publish" in slugs


# ---------------------------------------------------------------------------
# source_by_slug
# ---------------------------------------------------------------------------


def test_source_by_slug_returns_correct_spec():
    assert source_by_slug("congress-gov-api") is CONGRESS_CORE
    assert source_by_slug("house-disclosures") is HOUSE_DISCLOSURES
    assert source_by_slug("senate-disclosures") is SENATE_DISCLOSURES
    assert source_by_slug("fec-bulk") is FEC_BULK
    assert source_by_slug("member-fec-crosswalk") is MEMBER_FEC_CROSSWALK
    assert source_by_slug("financial-disclosures") is DISCLOSURE_LOAD
    assert source_by_slug("conflict-recompute") is CONFLICT_RECOMPUTE
    assert source_by_slug("snapshot-publish") is SNAPSHOT_PUBLISH


def test_source_by_slug_raises_key_error_for_unknown():
    with pytest.raises(KeyError, match="unknown source slug"):
        source_by_slug("does-not-exist")


def test_source_by_slug_round_trips_all():
    for spec in all_sources():
        assert source_by_slug(spec.slug) is spec
