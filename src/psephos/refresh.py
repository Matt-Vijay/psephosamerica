"""Scheduled, bounded source refresh. Operational clocks never become legal dates."""

from __future__ import annotations

import json
import re
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import getproxies_environment

from .acquire import Acquirer, AcquisitionError, Receipt
from .campaign import save
from .store import Store, utc_now, writer_lock

SOURCES = {
    "uscode": ("uscode",),
    "ecfr": ("ecfr",),
    "dc": ("dc-code", "dc-laws"),
    "portland-guides": ("portland-zoning-guides",),
}
MIB = 1024**2
DENIED = (401, 403, 407, 451)
NOTE = "Publisher check only, not legal effectiveness, complete coverage or a new snapshot date."


def _time(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Refresh timestamps must include a timezone")
    return result


def _path(root: Path) -> Path:
    return root / "refresh" / "state.json"


def _load(root: Path) -> dict[str, Any] | None:
    path = _path(root)
    if not path.exists():
        return None
    if path.stat().st_size > 1024**2:
        raise ValueError("Refresh state exceeds 1 MiB; inspect it before resuming")
    state = json.loads(path.read_bytes())
    if not isinstance(state, dict) or state.get("version") != 1:
        raise ValueError("Unsupported refresh state")
    if state.get("store") != str(root.resolve()):
        raise ValueError("Refresh state belongs to another store; do not copy a live schedule")
    if type(state.get("enabled")) is not bool or not re.fullmatch(
        r"\d{4}-(0[1-9]|1[0-2])", str(state.get("period", ""))
    ):
        raise ValueError("Invalid refresh enablement or budget period")
    for key in ("interval_hours", "max_run_bytes", "monthly_bytes", "used_bytes"):
        if type(state.get(key)) is not int or state[key] < (0 if key == "used_bytes" else 1):
            raise ValueError("Invalid refresh setting: " + key)
    if not isinstance(state.get("sources"), dict) or not state["sources"]:
        raise ValueError("Refresh sources are missing")
    if set(state["sources"]) - SOURCES.keys():
        raise ValueError("Unsupported automatic source; source-specific review required")
    for entry in state["sources"].values():
        if not isinstance(entry, dict):
            raise ValueError("Invalid source refresh state")
        if type(entry.get("failures", 0)) is not int or entry.get("failures", 0) < 0:
            raise ValueError("Invalid refresh failure count")
        for key in ("next_due", "started_at", "finished_at", "last_success", "cycle_started"):
            if entry.get(key):
                _time(entry[key])
    return state


def _save(root: Path, state: dict[str, Any]) -> None:
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    save(path, state)
    path.chmod(0o600)


def configure(
    root: Path,
    sources: list[str],
    *,
    interval_hours: int = 168,
    max_mib: int = 512,
    monthly_mib: int = 8192,
) -> dict[str, Any]:
    if not sources or set(sources) - SOURCES.keys():
        raise ValueError("Automatic refresh supports: " + ", ".join(SOURCES))
    if not 1 <= interval_hours <= 24 * 365 or not 1 <= max_mib <= monthly_mib <= 102400:
        raise ValueError("Use interval 1..8760 hours and 1 <= max MiB <= monthly MiB <= 102400")
    with writer_lock(root):
        store = Store(root, readonly=True)
        try:
            for source in sources:
                for collection in SOURCES[source]:
                    if not store.db.execute(
                        "SELECT 1 FROM collections WHERE id=?", (collection,)
                    ).fetchone():
                        raise ValueError(
                            "Acquire the collection before scheduling it: " + collection
                        )
        finally:
            store.close()
        old = _load(root) or {}
        state = {
            **old,
            "version": 1,
            "store": str(root.resolve()),
            "enabled": True,
            "interval_hours": interval_hours,
            "max_run_bytes": max_mib * MIB,
            "monthly_bytes": monthly_mib * MIB,
            "period": old.get("period", utc_now()[:7]),
            "used_bytes": old.get("used_bytes", 0),
            "https_proxy": getproxies_environment().get("https"),
            "sources": {
                name: old.get("sources", {}).get(name, {"status": "never_checked"})
                for name in dict.fromkeys(sources)
            },
        }
        for entry in state["sources"].values():
            if entry.get("status") == "checked" and entry.get("last_success"):
                entry["next_due"] = (
                    _time(entry["last_success"]) + timedelta(hours=interval_hours)
                ).isoformat()
        _save(root, state)
    return status(root)


def pause(root: Path) -> dict[str, Any]:
    with writer_lock(root):
        state = _load(root)
        if state is not None:
            state["enabled"] = False
            _save(root, state)
    return status(root)


def retry(root: Path, source: str) -> dict[str, Any]:
    """Operator retry preserves both spend and last success; HTTP policy still applies."""
    with writer_lock(root):
        state = _load(root)
        if state is None or source not in state["sources"]:
            raise ValueError("Source is not configured")
        entry = state["sources"][source]
        entry.update(status="retry_requested", next_due=utc_now(), failures=0)
        _save(root, state)
    return status(root)


def status(root: Path) -> dict[str, Any]:
    state = _load(root)
    if state is None:
        return {"enabled": False, "sources": {}, "meaning": NOTE}
    now = datetime.now(UTC)
    result = {
        key: value for key, value in state.items() if key not in {"store", "sources", "https_proxy"}
    }
    result["sources"] = {}
    for source, value in state["sources"].items():
        last = value.get("last_success")
        age = (now - _time(last)).total_seconds() if last else None
        result["sources"][source] = {
            **{key: item for key, item in value.items() if key != "inventory_items"},
            "collections": list(SOURCES[source]),
            "check_overdue": age is None or age > state["interval_hours"] * 3600,
        }
    result["meaning"] = NOTE
    return result


def collection_status(root: Path, collection: str) -> dict[str, Any]:
    try:
        report = status(root)
    except (ValueError, OSError, TypeError, KeyError):
        return {"status": "unreadable_refresh_state", "check_overdue": True, "meaning": NOTE}
    entries = [v for v in report["sources"].values() if collection in v["collections"]]
    if not entries:
        return {"status": "not_scheduled", "last_success": None, "meaning": NOTE}
    entry = entries[0]
    return {
        "enabled": report["enabled"],
        "worker_error": report.get("worker_error"),
        **{
            key: entry.get(key)
            for key in (
                "status",
                "last_success",
                "started_at",
                "finished_at",
                "next_due",
                "check_overdue",
            )
        },
        "error": str(entry.get("error") or "")[:300] or None,
        "meaning": NOTE,
    }


class RefreshAcquirer(Acquirer):
    def __init__(self, store: Store, *, max_bytes: int):
        super().__init__(store, refresh=True, max_bytes=max_bytes)
        self.requests = 0
        self.deadline = time.monotonic() + 15 * 60
        self.blocked = False

    def fetch(self, url: str, **options: Any) -> Receipt:
        self._check_denial(url)
        return super().fetch(url, **options)

    def _check_denial(self, url: str) -> None:
        if self.blocked:
            raise AcquisitionError("Source access denied; no further requests in this sweep")
        previous = self.store.db.execute(
            "SELECT status,error FROM acquisitions WHERE url=? OR final_url=? ORDER BY id DESC LIMIT 1",
            (url, url),
        ).fetchone()
        if previous and _denied(previous[0], previous[1]):
            self.blocked = True
            raise AcquisitionError("Retained access denial; review publisher/runtime permissions")

    def _pause(self, url: str, delay: float | None = None) -> None:
        if self.requests >= 100 or time.monotonic() >= self.deadline:
            raise AcquisitionError("Refresh request/time allowance exhausted")
        self._check_denial(url)
        super()._pause(url, delay)
        self.requests += 1

    def _record(
        self,
        url: str,
        final_url: str,
        observed: str,
        status: int,
        headers: dict[str, str],
        sha: str | None,
        error: str | None,
    ) -> int:
        if _denied(status, error):
            self.blocked = True
        return super()._record(url, final_url, observed, status, headers, sha, error)

    def _allowed(self, url: str) -> None:
        self._check_denial(url)
        try:
            super()._allowed(url)
        except AcquisitionError as exc:
            if str(exc).startswith("Disallowed by publisher robots policy:"):
                self.blocked = True
            raise


def _denied(status: int, error: str | None) -> bool:
    # CONNECT denials arrive as ProxyError text, without an HTTP response status.
    return status in DENIED or (
        status == 0 and bool(re.match(r"^(401|403|407|451)\b", error or ""))
    )


def _identities(source: str, items: list[str]) -> set[str]:
    return {item.split("@", 1)[0] if source == "ecfr" else item for item in items}


def _inventory(store: Store, source: str, report: Any) -> dict[str, list[str]]:
    if not isinstance(report, dict) or not isinstance(report.get("inventory_items"), dict):
        raise ValueError("Adapter did not certify the current inventory it attempted")
    inventory = report["inventory_items"]
    if set(inventory) != set(SOURCES[source]):
        raise ValueError("Adapter inventory does not match its named collections")
    for collection, items in inventory.items():
        if not isinstance(items, list) or not items or not all(isinstance(v, str) for v in items):
            raise ValueError("Empty or invalid native source inventory")
        if len(set(items)) != len(items):
            raise ValueError("Duplicated native source inventory")
        for item in items:
            row = store.db.execute(
                "SELECT status,error FROM inventories WHERE collection_id=? AND item=?",
                (collection, item),
            ).fetchone()
            if row is None or row[0] not in {"indexed", "metadata", "reserved"} or row[1]:
                raise AcquisitionError("Current source inventory is incomplete: " + item)
    return dict(inventory)


def run(root: Path) -> dict[str, Any]:
    from .sources import _sources

    with writer_lock(root):
        state = _load(root)
        if state is None or not state["enabled"]:
            return {"status": "paused", **status(root)}
        log = root / "refresh" / "service.log"
        if log.exists() and log.stat().st_size > 2 * MIB:
            log.replace(log.with_name("service.previous.log"))
        if state.get("https_proxy") != getproxies_environment().get("https"):
            state["worker_error"] = (
                "Refresh network environment changed; no direct fallback. Review the configuration."
            )
            _save(root, state)
            return status(root)
        state.pop("worker_error", None)
        now = datetime.now(UTC)
        period = now.strftime("%Y-%m")
        if period > state["period"]:
            state.update(period=period, used_bytes=0)
        elif period < state["period"]:
            raise ValueError("Clock moved before budget period; refusing to reset allowance")
        store = Store(root)
        remaining = min(state["max_run_bytes"], state["monthly_bytes"] - state["used_bytes"])
        try:
            for source, entry in sorted(
                state["sources"].items(), key=lambda pair: pair[1].get("next_due", "")
            ):
                if entry.get("status") == "running":
                    entry.update(
                        status="interrupted",
                        error="Prior run interrupted; reserved bytes stay charged",
                    )
                if entry.get("status") == "blocked":
                    continue
                if entry.get("next_due") and _time(entry["next_due"]) > now:
                    continue
                if remaining <= 512 * 1024:
                    entry.update(
                        status="budget_exhausted", error="Run or monthly byte allowance exhausted"
                    )
                    continue
                if shutil.disk_usage(root).free < 8 * 1024**3:
                    entry.update(status="disk_space_low", error="At least 8 GiB free is required")
                    continue
                # Reserve before HTTP; a killed process cannot refund unknown transfers.
                allowance = remaining
                state["used_bytes"] += allowance
                cycle = entry.get("cycle_started") or (
                    entry.get("started_at")
                    if entry["status"] in {"failed_retryable", "retry_requested", "interrupted"}
                    else None
                )
                if (
                    not cycle
                    or datetime.now(UTC) - _time(cycle) >= timedelta(hours=24)
                    or (entry.get("last_success") and _time(entry["last_success"]) >= _time(cycle))
                ):
                    cycle = utc_now()
                entry.update(
                    status="running",
                    started_at=utc_now(),
                    finished_at=None,
                    cycle_started=cycle,
                    error=None,
                )
                _save(root, state)
                acquisition_start = store.db.execute(
                    "SELECT coalesce(max(id),0) FROM acquisitions"
                ).fetchone()[0]
                versions_before = store.db.execute("SELECT count(*) FROM versions").fetchone()[0]
                a = None
                try:
                    # Account for request headers/error responses and one rejected decoded chunk.
                    a = RefreshAcquirer(store, max_bytes=allowance - 100 * 4096 - 64 * 1024)
                    a.resume_after = cycle
                    report = _sources()[source].sync(store, a, None, None)
                    inventory = _inventory(store, source, report)
                    attempts = store.db.execute(
                        "SELECT url,status,error FROM acquisitions WHERE id IN "
                        "(SELECT max(id) FROM acquisitions WHERE id>? GROUP BY url)",
                        (acquisition_start,),
                    ).fetchall()
                    if any(
                        row["error"]
                        and not (
                            row["status"] in (404, 410)
                            and urlsplit(row["url"]).path == "/robots.txt"
                        )
                        for row in attempts
                    ):
                        raise AcquisitionError(
                            "Source transfer reported a failure; inspect the retained receipts"
                        )
                    previous = entry.get("inventory_items", {}) if entry.get("last_success") else {}

                    removed = sum(
                        len(
                            _identities(source, items) - _identities(source, inventory.get(cid, []))
                        )
                        for cid, items in previous.items()
                    )
                    if removed:
                        raise ValueError(
                            "Publisher removed inventory entries; review retained historical documents"
                        )
                    entry.update(
                        status="checked",
                        last_success=utc_now(),
                        next_due=(
                            datetime.now(UTC) + timedelta(hours=state["interval_hours"])
                        ).isoformat(),
                        inventory_items=inventory,
                        inventory_comparison="compared_to_last_success"
                        if previous
                        else "baseline_created",
                        failures=0,
                    )
                except Exception as exc:
                    # One malformed publisher response must not starve unrelated sources.
                    denied = any(
                        _denied(row[0], row[1])
                        for row in store.db.execute(
                            "SELECT status,error FROM acquisitions WHERE id>?",
                            (acquisition_start,),
                        )
                    )
                    failures = entry.get("failures", 0) + 1
                    entry.update(
                        status="blocked"
                        if (a and a.blocked)
                        or denied
                        or not isinstance(exc, (AcquisitionError, OSError))
                        else "failed_retryable",
                        error=str(exc)[:1000],
                        failures=failures,
                        next_due=(
                            datetime.now(UTC) + timedelta(hours=min(24, 2 ** min(failures - 1, 5)))
                        ).isoformat(),
                    )
                finally:
                    if a is not None:
                        a.close()
                    downloaded = a.downloaded if a else 0
                    requests = a.requests if a else 0
                    charged = (
                        allowance if entry["status"] == "running" else downloaded + requests * 4096
                    )
                    state["used_bytes"] += charged - allowance
                    remaining -= charged
                    entry.update(
                        finished_at=utc_now(),
                        downloaded_bytes=downloaded,
                        charged_bytes=charged,
                        requests=requests,
                        added_versions=store.db.execute("SELECT count(*) FROM versions").fetchone()[
                            0
                        ]
                        - versions_before,
                    )
                    _save(root, state)
            _save(root, state)
        finally:
            store.close()
    return status(root)
