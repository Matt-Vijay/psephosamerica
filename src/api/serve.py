"""One-command launcher for the Psephos America explorer + graph query API.

Run:

    python -m src.api.serve            # serves http://127.0.0.1:8000
    python -m src.api.serve --port 9000 --host 0.0.0.0

This boots the framework-neutral :class:`~src.api.wsgi_app.PredictionWsgiApp`
on Python's stdlib :mod:`wsgiref` server (no gunicorn/uvicorn needed for local
use). The connected-graph store is built lazily on the first graph/explorer
request, so startup is instant and the first hit takes a few seconds to index.

Landing page: ``http://<host>:<port>/`` — search box, ask box, jurisdiction
links. The prediction read API is mounted too but starts with an empty served
snapshot (the frozen prediction pins are published separately); the graph,
GraphRAG, lenses, and explorer are the point of this launcher.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from wsgiref.simple_server import make_server

from src.api.wsgi_app import PredictionWsgiApp
from src.prediction.benchmark_gate import BenchmarkSliceMetrics
from src.prediction.calibration_dashboard import build_calibration_dashboard
from src.prediction.read_api import (
    PredictionReadService,
    TokenBucketRateLimiter,
    build_prediction_snapshot,
)


def build_app() -> PredictionWsgiApp:
    """Assemble the WSGI app with a minimal (empty) prediction snapshot.

    The graph/GraphRAG/lens/explorer routes are the focus here; the prediction
    read API is wired with an empty served snapshot so its routes return 404
    rather than erroring, without depending on a published prediction artifact.
    """
    snapshot = build_prediction_snapshot([], generated_at=datetime.now(timezone.utc))
    read_service = PredictionReadService(
        snapshot=snapshot,
        rate_limiter=TokenBucketRateLimiter(capacity=120, refill_per_second=1.0),
    )
    dashboard = build_calibration_dashboard(
        "per_member_signal_model",
        [
            BenchmarkSliceMetrics(
                slice_name="overall", brier_score=0.0, log_loss=0.0, sample_count=0
            )
        ],
    )
    return PredictionWsgiApp(read_service=read_service, dashboard=dashboard)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.api.serve", description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default 8000).")
    args = parser.parse_args(argv)

    app = build_app()
    with make_server(args.host, args.port, app) as server:
        print(f"Psephos America explorer running at http://{args.host}:{args.port}/")
        print("  landing page (search + ask):   /")
        print("  query API:                     /v1/graph/entities?q=...")
        print("  ask (GraphRAG):                /v1/graph/ask?q=...")
        print("Press Ctrl+C to stop.")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nshutting down")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
