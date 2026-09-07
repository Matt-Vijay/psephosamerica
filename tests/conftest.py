from pathlib import Path

import pytest

from psephos.acquire import Receipt
from psephos.store import Store, digest


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "evidence")
    s.collection(
        "test",
        ("us", "United States", "federal", None),
        name="Fixture",
        authority="Fixture publisher",
        kind="code",
        homepage="https://example.test/",
        source_status="Test data",
        access="Offline fixture",
    )
    yield s
    s.close()


def retain(store: Store, data: bytes, observed="2026-01-01T00:00:00Z") -> Receipt:
    sha = digest(data)
    path: Path = store.object_path(sha)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    url = "https://example.test/" + sha
    with store.db:
        store.db.execute("INSERT OR IGNORE INTO artifacts VALUES (?,?)", (sha, len(data)))
        cursor = store.db.execute(
            "INSERT INTO acquisitions(url,final_url,observed_at,status,sha256,headers) VALUES (?,?,?,200,?,'{}')",
            (url, url, observed, sha),
        )
    return Receipt(cursor.lastrowid, url, sha, observed, len(data))
