from __future__ import annotations

from urllib.parse import unquote, urlparse

from pydantic import Field
from pydantic_settings import BaseSettings, EnvSettingsSource, PydanticBaseSettingsSource

# Legacy prefix kept as a read-time fallback so pre-rename environments and
# .env files (``OPENPACT_*``) keep working after the Psephos America rename.
_LEGACY_ENV_PREFIX = "OPENPACT_"


class Settings(BaseSettings):
    model_config = {"env_prefix": "PSEPHOS_"}

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Primary ``PSEPHOS_`` sources take precedence; the legacy ``OPENPACT_``
        # env source is appended last so a new name always wins when both exist.
        legacy_env = EnvSettingsSource(
            settings_cls,
            env_prefix=_LEGACY_ENV_PREFIX,
            case_sensitive=cls.model_config.get("case_sensitive", False),
        )
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
            legacy_env,
        )

    environment: str = Field(default="development", description="Runtime environment name")
    postgres_dsn: str = Field(
        default="postgresql://localhost:5432/psephosamerica",
        description="Postgres connection string",
    )
    r2_bucket: str = Field(
        default="psephosamerica-artifacts", description="Cloudflare R2 bucket name"
    )
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
        return path or "psephosamerica"

    @property
    def db_user(self) -> str:
        parsed = urlparse(self.postgres_dsn)
        return unquote(parsed.username) if parsed.username else ""

    @property
    def db_password(self) -> str:
        parsed = urlparse(self.postgres_dsn)
        return unquote(parsed.password) if parsed.password else ""
