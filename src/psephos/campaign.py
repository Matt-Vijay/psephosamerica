"""Persistent byte reservations and pacing for bounded source continuations."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from .acquire import Acquirer, AcquisitionError
from .store import Store


def save(path: Path, value: Any) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class CampaignAcquirer(Acquirer):
    """Reuse Acquirer receipts/cache/robots, with a non-resetting local allowance.

    The caller holds a single-writer lock and restricts source URLs. A lower
    session ceiling supports metadata preflight without resetting the total cap.
    """

    @classmethod
    def budget_file(cls, store: Store, directory: Path) -> Path:
        return directory / "budget.json"

    def __init__(
        self,
        store: Store,
        directory: Path,
        initial_bytes: int = 0,
        *,
        cap: int = 512 * 1024**2,
        file_cap: int = 128 * 1024**2,
        ceiling: int | None = None,
        delay: float = 2.1,
    ):
        self.budget_path = self.budget_file(store, directory)
        self.file_cap = file_cap
        self.budget = (
            json.loads(self.budget_path.read_bytes())
            if self.budget_path.exists()
            else {"consumed_bytes": initial_bytes, "last_request_unix": 0.0, "cap_bytes": cap}
        )
        if self.budget["cap_bytes"] != cap:
            raise AcquisitionError("Campaign cap differs from the retained budget")
        abandoned = self.budget.pop("reserved_bytes", 0)
        self.budget["consumed_bytes"] += abandoned
        self.budget["uncertain_reserved_bytes"] = (
            self.budget.get("uncertain_reserved_bytes", 0) + abandoned
        )
        self._initializing = True
        super().__init__(
            store, max_bytes=min(cap, ceiling if ceiling is not None else cap), delay=delay
        )
        self._initializing = False
        self.client.headers["Accept-Encoding"] = "identity"
        self.client.event_hooks["response"] = [self._bounded_response]
        save(self.budget_path, self.budget)

    @property
    def downloaded(self) -> int:
        return int(self.budget["consumed_bytes"])

    @downloaded.setter
    def downloaded(self, value: int) -> None:
        if not self._initializing:
            self.budget["consumed_bytes"] = value
            self.budget["reserved_bytes"] = 0
            save(self.budget_path, self.budget)

    def _pause(self, url: str, delay: float | None = None) -> None:
        elapsed = max(0.0, time.time() - self.budget["last_request_unix"])
        self.last_request[urlsplit(url).netloc] = time.monotonic() - elapsed
        super()._pause(url, delay)
        self.budget["last_request_unix"] = time.time()
        save(self.budget_path, self.budget)

    def _bounded_response(self, response: httpx.Response) -> None:
        if self.budget.get("reserved_bytes"):
            raise AcquisitionError(
                "Unresolved response reservation; restart before acquiring more bytes"
            )
        if response.status_code != 200:
            return
        if response.headers.get("content-encoding", "identity").lower() not in ("", "identity"):
            raise AcquisitionError("Unexpected encoding; response not decoded")
        length = response.headers.get("content-length", "")
        if length.isdigit() and int(length) > min(self.file_cap, self.max_bytes - self.downloaded):
            raise AcquisitionError("Declared source exceeds campaign/file cap; body unread")

        def chunks(chunk_size: int | None = None) -> Any:
            iterator = response.iter_raw(chunk_size=65536)
            while True:
                if self.downloaded + 65536 > self.max_bytes:
                    raise AcquisitionError("Hard lifetime cap before next response chunk")
                self.budget["reserved_bytes"] = 65536
                save(self.budget_path, self.budget)
                try:
                    yield next(iterator)
                except StopIteration:
                    self.budget["reserved_bytes"] = 0
                    save(self.budget_path, self.budget)
                    return

        response.iter_bytes = chunks  # type: ignore[method-assign]
