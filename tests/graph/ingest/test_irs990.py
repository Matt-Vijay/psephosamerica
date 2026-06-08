from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.ingest.irs990 import normalize_ein, parse_propublica_org
from src.graph.provenance import ProvenanceEnvelope

_PROV = ProvenanceEnvelope(
    source_url="https://projects.propublica.org/nonprofits/organizations/475374786",
    content_sha256="a" * 64,
    first_observed_at=datetime(2024, 1, 10, tzinfo=UTC),
    valid_from=date(2024, 1, 1),
    known_at=datetime(2024, 1, 5, tzinfo=UTC),
)


def test_normalize_ein() -> None:
    assert normalize_ein("47-5374786") == "47-5374786"
    assert normalize_ein(475374786) == "47-5374786"
    assert normalize_ein(" 12-3456789 ") == "12-3456789"


def test_normalize_ein_rejects_bad() -> None:
    with pytest.raises(ValueError, match="EIN"):
        normalize_ein("not-an-ein")
    with pytest.raises(ValueError, match="EIN"):
        normalize_ein("123")


def test_parse_propublica_org() -> None:
    rec = parse_propublica_org(
        {
            "ein": 475374786,
            "strein": "47-5374786",
            "name": "Sierra Club Foundation",
            "city": "Oakland",
            "state": "CA",
            "ntee_code": "C99",
        },
        provenance=_PROV,
    )
    assert rec.entity_type == "org"
    assert rec.source_system == "irs_990"
    assert rec.display_name == "Sierra Club Foundation"
    assert rec.region == "CA"
    assert rec.jurisdiction == "us"
    assert rec.external_id_keys == frozenset({"ein:47-5374786"})


def test_parse_uses_ein_when_strein_absent() -> None:
    rec = parse_propublica_org({"ein": 123456789, "name": "Acme Fund"}, provenance=_PROV)
    assert rec.external_id_keys == frozenset({"ein:12-3456789"})


def test_missing_ein_rejected() -> None:
    with pytest.raises(ValueError, match="EIN"):
        parse_propublica_org({"name": "No EIN Org"}, provenance=_PROV)


def test_missing_name_rejected() -> None:
    with pytest.raises(ValueError, match="name"):
        parse_propublica_org({"ein": 475374786, "name": "  "}, provenance=_PROV)


def test_two_filings_same_ein_resolve_to_one() -> None:
    from src.graph.entity_resolution.linker import resolve

    a = parse_propublica_org(
        {"strein": "47-5374786", "name": "Sierra Club Foundation"}, provenance=_PROV
    )
    b = parse_propublica_org(
        {"strein": "47-5374786", "name": "The Sierra Club Fdn"}, provenance=_PROV
    )
    # Same EIN -> one canonical org despite the name variation.
    assert len(resolve([a, b]).clusters) == 1
