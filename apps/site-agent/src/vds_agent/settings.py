"""Configuration de l'agent (variables d'environnement, aucun secret en dépôt)."""

import os
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

AGENT_VERSION = "0.1.0"


class SettingsError(ValueError):
    pass


def ws_url(server_url: str) -> str:
    """Dérive l'URL WSS depuis l'URL serveur (préfixe compris), jamais depuis la racine."""
    parts = urlsplit(server_url)
    if parts.scheme not in ("https", "wss") or not parts.netloc:
        raise SettingsError("agent.server_url_must_be_https")
    path = parts.path.rstrip("/") + "/ws/agent"
    return urlunsplit(("wss", parts.netloc, path, "", ""))


def http_base(server_url: str) -> str:
    parts = urlsplit(server_url)
    if parts.scheme not in ("https", "wss") or not parts.netloc:
        raise SettingsError("agent.server_url_must_be_https")
    return urlunsplit(("https", parts.netloc, parts.path.rstrip("/"), "", ""))


def _read_secret(env: Mapping[str, str], name: str) -> str | None:
    file = env.get(f"{name}_FILE")
    if file:
        return Path(file).read_text(encoding="utf-8").strip() or None
    return env.get(name) or None


@dataclass(frozen=True)
class AgentSettings:
    server_url: str
    token: str = ""
    data_dir: Path = Path("/data/agent")
    modules_dir: Path = Path("/app/modules")
    ca_file: str | None = None
    state_key: str | None = None

    @property
    def ws_url(self) -> str:
        return ws_url(self.server_url)

    @property
    def http_base(self) -> str:
        return http_base(self.server_url)

    def ssl_context(self) -> ssl.SSLContext:
        """TLS vérifié obligatoire ; seule une AC privée peut être ajoutée."""
        return ssl.create_default_context(cafile=self.ca_file)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AgentSettings":
        e = os.environ if env is None else env
        url = e.get("VDS_SERVER_URL")
        if not url:
            raise SettingsError("agent.server_url_missing")
        token = _read_secret(e, "VDS_AGENT_TOKEN")
        if not token:
            raise SettingsError("agent.token_missing")
        ws_url(url)
        return cls(
            server_url=url,
            token=token,
            data_dir=Path(e.get("VDS_AGENT_DATA_DIR", "/data/agent")),
            modules_dir=Path(e.get("VDS_MODULES_DIR", "/app/modules")),
            ca_file=e.get("VDS_AGENT_CA_FILE") or None,
            state_key=_read_secret(e, "VDS_AGENT_STATE_KEY"),
        )
