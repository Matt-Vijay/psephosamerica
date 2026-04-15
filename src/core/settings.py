from __future__ import annotations

from urllib.parse import unquote, urlparse

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "OPENPACT_"}

    environment: str = Field(default="development", description="Runtime environment name")
    postgres_dsn: str = Field(
        default="postgresql://localhost:5432/openpact",
        description="Postgres connection string",
    )
    r2_bucket: str = Field(default="openpact-artifacts", description="Cloudflare R2 bucket name")
    snapshot_prefix: str = Field(
        default="snapshots/", description="Key prefix for published snapshots in R2"
    )
    congress_api_key: str = Field(default="", description="Congress.gov API key")
    fec_api_key: str = Field(default="", description="FEC API key")

    @property
    def db_host(self) -> str:
        parsed = urlparse(self.postgres_dsn)
        return parsed.hostname or "localhost"

    @property
    def db_port(self) -> int:
        parsed = urlparse(self.postgres_dsn)
        return parsed.port or 5432

    @property
    def db_name(self) -> str:
        parsed = urlparse(self.postgres_dsn)
        path = parsed.path.lstrip("/")
        return path or "openpact"

    @property
    def db_user(self) -> str:
        parsed = urlparse(self.postgres_dsn)
        return unquote(parsed.username) if parsed.username else ""

    @property
    def db_password(self) -> str:
        parsed = urlparse(self.postgres_dsn)
        return unquote(parsed.password) if parsed.password else ""
