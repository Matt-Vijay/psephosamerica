import asyncio
import json
import sys

import pytest
from conftest import retain
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from psephos.geography import publish_arcgis_layer, zoning_at
from psephos.store import Provision


def page(ids):
    return json.dumps(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"OBJECTID": i, "ZONE": "CX"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                    },
                }
                for i in ids
            ],
        }
    ).encode()


def test_layer_membership_shrink_and_failed_refresh_are_atomic(store):
    store.collection(
        "portland-zoning-gis",
        ("us", "United States", "federal", None),
        name="Fixture layer",
        authority="Publisher",
        kind="gis",
        homepage="https://example.test/",
        source_status="Fixture",
        access="Fixture",
    )

    def publish(ids, page_ids, accepted, flag=None):
        inventory = retain(store, json.dumps(ids).encode())
        payloads = [json.loads(page(p)) for p in page_ids]
        if flag:
            target = payloads[-1] if flag == "root" else payloads[-1].setdefault("properties", {})
            target["exceededTransferLimit"] = True
        return publish_arcgis_layer(
            store,
            inventory,
            [retain(store, json.dumps(p).encode()) for p in payloads],
            "OBJECTID",
            ids,
            {"accepted_at": accepted},
        )

    publish([1, 2, 3], [[1, 2], [3]], "2026-02-01T00:00:00Z")
    assert len(zoning_at(store, 0.5, 0.5)["matches"]) == 3
    publish([1, 2], [[1, 2]], "2026-03-01T00:00:00Z")
    assert len(zoning_at(store, 0.5, 0.5)["matches"]) == 2
    historic = zoning_at(store, 0.5, 0.5, observation_cutoff="2026-02-15T00:00:00Z")
    assert len(historic["matches"]) == 3
    with pytest.raises(ValueError, match="membership"):
        publish([1, 2, 4], [[1, 2]], "2026-04-01T00:00:00Z")
    with pytest.raises(ValueError, match="Duplicate"):
        publish([1, 2, 4], [[1, 2], [2, 4]], "2026-04-01T00:00:00Z")
    with pytest.raises(ValueError, match="nonempty"):
        publish([], [], "2026-04-01T00:00:00Z")
    with pytest.raises(ValueError, match="unique"):
        publish([1, 1], [[1]], "2026-04-01T00:00:00Z")
    for flag in ("root", "properties"):
        with pytest.raises(ValueError, match="exceededTransferLimit"):
            publish([1, 2, 4], [[1, 2], [4]], "2026-04-01T00:00:00Z", flag)
    assert len(zoning_at(store, 0.5, 0.5)["matches"]) == 2
    assert all(m["on_boundary"] for m in zoning_at(store, 0, 0.5)["matches"])
    assert not zoning_at(store, 0.5, 0.5, as_of="2026-01-01")["matches"]
    assert not zoning_at(store, 5, 5)["matches"]
    with pytest.raises(ValueError):
        zoning_at(store, float("nan"), 1)


def test_real_stdio_mcp_protocol_in_fresh_store(store):
    source = retain(store, b"fixture")
    store.ingest(
        collection="test",
        document="one",
        title="Example",
        url=source.url,
        acquisition=source.id,
        snapshot_basis="fixture",
        parser="test",
        provisions=[
            Provision("test:1", "Example 1", "Definition", "Preserved exception.", "", source.url)
        ],
    )

    async def run():
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "psephos.cli", "--data", str(store.root), "serve"]
        )
        async with stdio_client(params) as (rd, wr), ClientSession(rd, wr) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            assert tools == {
                "legal_coverage",
                "legal_find",
                "legal_search",
                "legal_read",
                "legal_versions",
                "legal_references",
                "zoning_at",
                "source_receipt",
            }
            result = await session.call_tool("legal_read", {"key_or_id": "test:1"})
            assert not result.isError
            assert result.structuredContent["text"] == "Preserved exception."
            found = await session.call_tool(
                "legal_find", {"key_or_id": "test:1", "query": "exception"}
            )
            assert not found.isError
            assert found.structuredContent["matches"][0]["match_offset"] == 10
            assert not (
                await session.call_tool("source_receipt", {"acquisition_id": source.id})
            ).isError

    asyncio.run(run())


def test_invalid_acquired_geometry_is_warned_not_repaired(store):
    store.collection(
        "portland-zoning-gis",
        ("us", "United States", "federal", None),
        name="Fixture",
        authority="Publisher",
        kind="gis",
        homepage="https://example.test/",
        source_status="Fixture",
        access="Fixture",
    )
    data = json.loads(page([1]))
    data["features"][0]["geometry"]["coordinates"] = [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]
    publish_arcgis_layer(
        store,
        retain(store, b"[1]"),
        [retain(store, json.dumps(data).encode())],
        "OBJECTID",
        [1],
        {"accepted_at": "2026-02-01T00:00:00Z"},
    )
    result = zoning_at(store, 0.5, 0.5)
    assert not result["matches"] and len(result["geometry_warnings"]) == 1
