"""Configuration du serveur (variables VDS_*)."""

import re
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BASE_RE = re.compile(r"^(/[a-z0-9_-]+)+$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VDS_", extra="ignore")

    base_path: str = "/"
    database_url: str = "postgresql+asyncpg://vds:vds@db:5432/vds"
    public_origin: str | None = None
    cookie_secure: Literal["auto", "true", "false"] = "auto"  # VDS_COOKIE_SECURE
    default_locale: str = "fr"
    auth_provider: str = "auth.local"
    theme: str = "theme.vtech"
    storage: str = "storage.local"
    version: str = "0.0.0"
    admin_dir: Path | None = None
    player_dir: Path | None = None
    modules_dir: Path = Path("modules")
    organization_name: str = "VDS"
    quarantine_failures: int = 5
    quarantine_window_s: int = 600
    secret_key: str | None = None  # VDS_SECRET_KEY : dérive les clés du service LAN de cache

    @field_validator("secret_key")
    @classmethod
    def _secret_key_length(cls, value: str | None) -> str | None:
        if value is not None and len(value.encode()) < 32:
            raise ValueError("errors.secret_key_too_short")
        return value

    @field_validator("base_path")
    @classmethod
    def _normalize_base_path(cls, value: str) -> str:
        stripped = value.strip()
        if stripped in ("", "/"):
            return "/"
        if stripped.endswith("/") or not _BASE_RE.match(stripped):
            raise ValueError("errors.invalid_base_path")
        return stripped

    @property
    def prefix(self) -> str:
        """Préfixe sans slash final : '' pour la racine."""
        return "" if self.base_path == "/" else self.base_path

    def url(self, path: str = "/") -> str:
        """URL absolue-chemin sous le préfixe (path commence par '/')."""
        return f"{self.prefix}{path}"
