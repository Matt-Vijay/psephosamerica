"""End-to-end demo flows for the conflict and publish products (no live deps)."""

from src.demo.conflict_demo import DemoResult, run_conflict_demo
from src.demo.publish_demo import PublishDemoResult, run_publish_demo

__all__ = [
    "DemoResult",
    "PublishDemoResult",
    "run_conflict_demo",
    "run_publish_demo",
]
