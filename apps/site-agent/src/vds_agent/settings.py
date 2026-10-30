"""Configuration de l'agent (variables d'environnement, aucun secret en dépôt)."""

import ipaddress
import logging
import os
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import SplitResult, urlsplit, urlunsplit

AGENT_VERSION = "0.1.0"


class SettingsError(ValueError):
    pass


def _check_insecure_host(host: str | None) -> None:
    """Le mode dev clair n'est toléré que vers une IP privée/loopback (jamais un nom public)."""
    try:
        ip = ipaddress.ip_address(host or "")
    except ValueError:
        ip = None
    if ip is None or not (ip.is_private or ip.is_loopback):
        raise SettingsError("agent.insecure_dev_private_ip_only")


def _is_loopback(host: str | None) -> bool:
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def _scheme_ok(parts: SplitResult, insecure_dev: bool) -> bool:
    if not parts.netloc:
        return False
    if parts.scheme in ("https", "wss"):
        return True
    # Agent local : le clair est accepté vers la boucle locale seule (IP littérale 127/8, ::1),
    # sans drapeau ; toute autre URL http exige VDS_AGENT_INSECURE_DEV=1 vers une IP privée.
    if parts.scheme in ("http", "ws") and _is_loopback(parts.hostname):
        return True
    if insecure_dev and parts.scheme in ("http", "ws"):
        _check_insecure_host(parts.hostname)
        return True
    return False


def ws_url(server_url: str, insecure_dev: bool = False) -> str:
    """Dérive l'URL WSS depuis l'URL serveur (préfixe compris), jamais depuis la racine."""
    parts = urlsplit(server_url)
    if not _scheme_ok(parts, insecure_dev):
        raise SettingsError("agent.server_url_must_be_https")
    path = parts.path.rstrip("/") + "/ws/agent"
    scheme = "wss" if parts.scheme in ("https", "wss") else "ws"
    return urlunsplit((scheme, parts.netloc, path, "", ""))


def http_base(server_url: str, insecure_dev: bool = False) -> str:
    parts = urlsplit(server_url)
    if not _scheme_ok(parts, insecure_dev):
        raise SettingsError("agent.server_url_must_be_https")
    scheme = "https" if parts.scheme in ("https", "wss") else "http"
    return urlunsplit((scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


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
    cache_max_gb: float = 20.0
    cache_lan_listen: str | None = None  # opt-in explicite : `ip:port`, désactivé par défaut
    # Opt-in DEV uniquement (VDS_AGENT_INSECURE_DEV=1) : http/ws clair vers une IP privée.
    # Le jeton d'agent circule alors en clair sur le LAN. Défaut : False (TLS obligatoire).
    insecure_dev: bool = False
    # Enrôlement (spec §2.7) : code à usage unique en variable, canal local (dossier partagé).
    enroll_code: str | None = None
    local_dir: Path | None = None

    @property
    def ws_url(self) -> str:
        return ws_url(self.server_url, self.insecure_dev)

    @property
    def http_base(self) -> str:
        return http_base(self.server_url, self.insecure_dev)

    @property
    def is_tls(self) -> bool:
        return self.ws_url.startswith("wss://")

    def ssl_context(self) -> ssl.SSLContext:
        """TLS vérifié obligatoire ; seule une AC privée peut être ajoutée."""
        return ssl.create_default_context(cafile=self.ca_file)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AgentSettings":
        e = os.environ if env is None else env
        local_dir = e.get("VDS_AGENT_LOCAL_DIR") or None
        url = e.get("VDS_SERVER_URL")
        if not url and local_dir:
            # Agent local (réseau hôte) : port publié du serveur, joint par la boucle locale.
            base = (e.get("VDS_BASE_PATH") or "/").strip("/")
            url = f"http://127.0.0.1:{e.get('VDS_PORT') or '8000'}" + (f"/{base}" if base else "")
        if not url:
            raise SettingsError("agent.server_url_missing")
        token = _read_secret(e, "VDS_AGENT_TOKEN") or ""
        insecure = e.get("VDS_AGENT_INSECURE_DEV") == "1"
        ws_url(url, insecure)
        if insecure and not url.startswith(("wss", "https")):
            logging.getLogger("vds.agent").warning(
                "agent.insecure_dev_enabled: connexion en clair, jeton exposé sur le réseau. "
                "NE PAS UTILISER EN PRODUCTION."
            )
        return cls(
            server_url=url,
            token=token,
            data_dir=Path(e.get("VDS_AGENT_DATA_DIR", "/data/agent")),
            modules_dir=Path(e.get("VDS_MODULES_DIR", "/app/modules")),
            ca_file=e.get("VDS_AGENT_CA_FILE") or None,
            state_key=_read_secret(e, "VDS_AGENT_STATE_KEY"),
            cache_max_gb=float(e.get("VDS_AGENT_CACHE_MAX_GB", "20")),
            cache_lan_listen=e.get("VDS_AGENT_CACHE_LAN_LISTEN") or None,
            insecure_dev=insecure,
            enroll_code=_read_secret(e, "VDS_ENROLL_CODE"),
            local_dir=Path(local_dir) if local_dir else None,
        )
