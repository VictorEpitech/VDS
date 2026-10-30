"""Configuration du serveur (variables VDS_*)."""

import re
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ORIGIN_RE = re.compile(r"^https?://([A-Za-z0-9.\-]+|\[[0-9a-fA-F:]+\])(:\d{1,5})?$")
_BASE_RE = re.compile(r"^(/[a-z0-9_-]+)+$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VDS_", extra="ignore", hide_input_in_errors=True)

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
    apk_dir: Path = Path("/opt/vds/apk")  # VDS_APK_DIR : apk.json + fichiers APK signés par la CI
    provision_lan_check: bool = True  # VDS_PROVISION_LAN_CHECK
    secret_key: str | None = None  # VDS_SECRET_KEY : dérive les clés du service LAN de cache
    local_agent_dir: Path | None = None  # VDS_LOCAL_AGENT_DIR : canal local (absent = indisponible)
    agent_image: str | None = None  # VDS_AGENT_IMAGE : image affichée dans la commande générée
    agent_pull_user: str | None = None  # VDS_AGENT_PULL_USER : registre privé (lecture seule)
    agent_pull_token: SecretStr | None = None  # VDS_AGENT_PULL_TOKEN : read:packages seul

    @field_validator("public_origin")
    @classmethod
    def _public_origin_shape(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            return None
        if value is not None and not _ORIGIN_RE.match(value):
            raise ValueError("errors.public_origin_invalid")  # M8 : origine nue, sans chemin ni /
        return value

    @field_validator(
        "local_agent_dir", "agent_image", "agent_pull_user", "agent_pull_token", mode="before"
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("secret_key")
    @classmethod
    def _secret_key_length(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            return None  # variable vide transmise par docker compose = non définie
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
