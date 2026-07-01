from __future__ import annotations

from src.core.settings import Settings


def test_defaults():
    s = Settings()
    assert s.environment == "development"
    assert "psephosamerica" in s.postgres_dsn
    assert s.r2_bucket == "psephosamerica-artifacts"
    assert s.snapshot_prefix == "snapshots/"
    assert s.congress_api_key == ""
    assert s.fec_api_key == ""


def test_env_override(monkeypatch):
    monkeypatch.setenv("PSEPHOS_ENVIRONMENT", "production")
    monkeypatch.setenv("PSEPHOS_CONGRESS_API_KEY", "test-key-123")
    s = Settings()
    assert s.environment == "production"
    assert s.congress_api_key == "test-key-123"


def test_postgres_dsn_override(monkeypatch):
    dsn = "postgresql://user:pass@db:5432/psephosamerica_prod"
    monkeypatch.setenv("PSEPHOS_POSTGRES_DSN", dsn)
    s = Settings()
    assert s.postgres_dsn == dsn


def test_postgres_dsn_derived_db_fields(monkeypatch):
    monkeypatch.setenv(
        "PSEPHOS_POSTGRES_DSN",
        "postgresql://alice:secret@db.example.com:5433/psephosamerica_prod",
    )
    s = Settings()
    assert s.db_host == "db.example.com"
    assert s.db_port == 5433
    assert s.db_name == "psephosamerica_prod"
    assert s.db_user == "alice"
    assert s.db_password == "secret"
