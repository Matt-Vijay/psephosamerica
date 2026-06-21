from __future__ import annotations

from src.graph.unified_jurisdiction import (
    build_jurisdiction_bridges,
    legistar_client_by_jurisdiction,
    officials_per_client,
    summarize_bridges,
)


def test_legistar_index_keys_on_jurisdiction_code() -> None:
    index = legistar_client_by_jurisdiction()
    assert index["us-ca-city-oakland"].client == "oakland"
    assert index["us-wa-county-king_county"].client == "kingcounty"


def test_officials_per_client_counts_legistar_keys() -> None:
    ext = [
        "legistar:chicago:1",
        "legistar:chicago:2",
        "legistar:oakland:5",
        "bioguide:A000001",  # ignored
        "legistar:oakland",  # too few parts, ignored
    ]
    counts = officials_per_client(ext)
    assert counts == {"chicago": 2, "oakland": 1}


def test_connected_bridge_links_ordinances_to_officials() -> None:
    bridges = build_jurisdiction_bridges(
        locus_jurisdiction_counts={
            "us-ca-city-oakland": 120,  # a Legistar client
            "us-ak-city-nome": 40,  # not a Legistar client -> minted, unconnected
        },
        municipal_external_ids=["legistar:oakland:1", "legistar:oakland:2"],
    )
    by_code = {b.canonical_code: b for b in bridges}
    oakland = by_code["us-ca-city-oakland"]
    assert oakland.legistar_client == "oakland"
    assert oakland.locus_ordinances == 120
    assert oakland.officials == 2
    assert oakland.is_connected
    nome = by_code["us-ak-city-nome"]
    assert nome.legistar_client is None
    assert not nome.is_connected


def test_folded_jurisdiction_connects() -> None:
    # LOCUS slug "pimacounty" folds to canonical us-az-county-pima_county (Legistar).
    bridges = build_jurisdiction_bridges(
        locus_jurisdiction_counts={"us-az-county-pimacounty": 7},
        municipal_external_ids=["legistar:pima:1"],
    )
    assert len(bridges) == 1
    bridge = bridges[0]
    assert bridge.canonical_code == "us-az-county-pima_county"
    assert bridge.legistar_client == "pima"
    assert bridge.officials == 1
    assert bridge.is_connected


def test_summary_counts_only_connected() -> None:
    bridges = build_jurisdiction_bridges(
        locus_jurisdiction_counts={
            "us-ca-city-oakland": 100,
            "us-ak-city-nome": 50,
        },
        municipal_external_ids=["legistar:oakland:1"],
    )
    summary = summarize_bridges(bridges)
    assert summary.total_jurisdictions == 2
    assert summary.connected_jurisdictions == 1
    assert summary.connected_ordinances == 100  # nome excluded (unconnected)
    assert summary.connected_officials == 1
