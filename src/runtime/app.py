from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.settings import Settings
from src.normalize.taxonomy_runtime import load_taxonomy_runtime
from src.runtime.context import ConnectFn, RuntimeContext, build_runtime_context, open_connection
from src.runtime.paths import local_publish_root, repo_root


@dataclass(frozen=True)
class OpenPactRuntime:
    context: RuntimeContext
    repo_root: Path
    publish_root: Path


def build_runtime(
    settings: Settings | None = None,
    *,
    data_root: Path | None = None,
    publish_root: Path | None = None,
    connect_fn: ConnectFn | None = None,
) -> OpenPactRuntime:
    resolved_settings = settings if settings is not None else Settings()
    resolved_repo_root = repo_root()
    resolved_data_root = data_root if data_root is not None else resolved_repo_root / "data"
    resolved_publish_root = publish_root if publish_root is not None else local_publish_root()

    taxonomy = load_taxonomy_runtime(resolved_data_root)
    context = build_runtime_context(resolved_settings, taxonomy, connect_fn=connect_fn)

    return OpenPactRuntime(
        context=context,
        repo_root=resolved_repo_root,
        publish_root=resolved_publish_root,
    )


def open_runtime_connection(runtime: OpenPactRuntime) -> Any:
    return open_connection(runtime.context)
