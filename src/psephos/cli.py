"""A small acquisition/inspection CLI; stdio MCP is the LLM-facing interface."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .retrieve import Reader
from .store import Store


def main() -> None:
    parser = argparse.ArgumentParser(description="Source-grounded US legal information")
    parser.add_argument("--data", type=Path, default=Path("data"), help="Local evidence store")
    commands = parser.add_subparsers(dest="command", required=True)
    sync = commands.add_parser("sync", help="Acquire and index supported publisher collections")
    sync.add_argument("collections", nargs="+")
    sync.add_argument(
        "--refresh", action="store_true", help="Conditionally revalidate mutable sources"
    )
    sync.add_argument(
        "--limit", type=int, help="Bound documents for development; coverage stays partial"
    )
    sync.add_argument(
        "--as-of", help="Acquire a source-supported historical snapshot when available"
    )
    status = commands.add_parser(
        "status", help="Browse bounded acquired-source coverage, clocks, and inventory status"
    )
    status.add_argument(
        "--view",
        choices=["jurisdictions", "collections", "documents", "inventory"],
        help="Default: jurisdictions without scope filters, otherwise collections",
    )
    status.add_argument("--jurisdiction", help="Exact jurisdiction ID, not inferred applicability")
    status.add_argument(
        "--collection", help="Exact collection ID; required for documents and inventory views"
    )
    status.add_argument("--status", help="Exact inventory status; inventory view only")
    status.add_argument("--offset", type=int, default=0, help="Zero-based page offset")
    status.add_argument("--limit", type=int, default=10, help="Page size (default 10, maximum 20)")
    reindex = commands.add_parser(
        "reindex", help="Reproject retained source bytes offline; preserve old IDs"
    )
    reindex.add_argument("collection", choices=["nyc"])
    commands.add_parser(
        "audit", help="Rehash source bytes and check catalog/key/geometry integrity"
    )
    search = commands.add_parser("search", help="Lexical legal search")
    search.add_argument("query")
    search.add_argument("--collection")
    search.add_argument("--jurisdiction")
    search.add_argument("--limit", type=int, default=10)
    read = commands.add_parser("read", help="Read a source key or a search result ID")
    read.add_argument("key")
    read.add_argument("--offset", type=int, default=0)
    read.add_argument("--length", type=int, default=10000)
    read.add_argument("--markup", action="store_true")
    read.add_argument(
        "--media-offset", type=int, default=0, help="Page source-media descriptors separately"
    )
    find = commands.add_parser("find", help="Jump to literal text in an exact source provision")
    find.add_argument("key")
    find.add_argument("query")
    find.add_argument("--start", type=int, default=0)
    find.add_argument("--limit", type=int, default=10)
    for command in (search, read, find):
        command.add_argument("--as-of")
        command.add_argument("--observed-before")
    versions = commands.add_parser("versions", help="List acquired source versions")
    versions.add_argument("key")
    receipt = commands.add_parser("receipt", help="Inspect a retained source acquisition")
    receipt.add_argument("acquisition_id", type=int)
    geo = commands.add_parser(
        "zoning", help="Intersect a coordinate with supported publisher GIS polygons"
    )
    geo.add_argument("longitude", type=float)
    geo.add_argument("latitude", type=float)
    geo.add_argument("--collection")
    geo.add_argument("--as-of")
    geo.add_argument("--observed-before")
    commands.add_parser("serve", help="Run the read-only stdio MCP server")
    args = parser.parse_args()
    try:
        if args.command == "serve":
            from .server import create_server

            create_server(args.data).run(transport="stdio")
            return
        if args.command == "sync":
            from .sources import sync_collections

            result = sync_collections(
                args.data,
                args.collections,
                refresh=args.refresh,
                limit=args.limit,
                as_of=args.as_of,
            )
        elif args.command == "reindex":
            from .municipal import reindex_nyc

            store = Store(args.data)
            try:
                result = reindex_nyc(store)
            finally:
                store.close()
        else:
            store = Store(args.data, readonly=True)
            try:
                reader = Reader(store)
                if args.command == "audit":
                    from .audit import audit

                    result = audit(store)
                elif args.command == "status":
                    result = reader.coverage(
                        view=args.view,
                        jurisdiction=args.jurisdiction,
                        collection=args.collection,
                        status=args.status,
                        offset=args.offset,
                        limit=args.limit,
                    )
                elif args.command == "search":
                    result = reader.search(
                        args.query,
                        collection=args.collection,
                        jurisdiction=args.jurisdiction,
                        as_of=args.as_of,
                        observation_cutoff=args.observed_before,
                        limit=args.limit,
                    )
                elif args.command == "read":
                    result = reader.read(
                        args.key,
                        as_of=args.as_of,
                        observation_cutoff=args.observed_before,
                        offset=args.offset,
                        length=args.length,
                        include_markup=args.markup,
                        media_offset=args.media_offset,
                    )
                elif args.command == "find":
                    result = reader.find(
                        args.key,
                        args.query,
                        as_of=args.as_of,
                        observation_cutoff=args.observed_before,
                        start=args.start,
                        limit=args.limit,
                    )
                elif args.command == "zoning":
                    from .geography import zoning_at

                    result = zoning_at(
                        store,
                        args.longitude,
                        args.latitude,
                        collection=args.collection,
                        as_of=args.as_of,
                        observation_cutoff=args.observed_before,
                    )
                elif args.command == "receipt":
                    result = reader.receipt(args.acquisition_id)
                else:
                    result = reader.versions(args.key)
            finally:
                store.close()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
        print(f"psephos: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
