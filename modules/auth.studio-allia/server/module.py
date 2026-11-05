"""auth.studio-allia : authentification déléguée au Studio Allia (mode hébergé).

VDS transmet le cookie `allia_espace` à l'endpoint interne du Studio (`STUDIO_SESSION_URL`),
avec l'en-tête de secret partagé `x-vds-secret`.
Le Studio valide la session et renvoie { id, nom, email, role }.

Règles :
- Aucun accès à `espace-db` : isolation complète des données Studio.
- Rôles : president, direction -> admin ; membre -> editor ; financeur -> 403.
- Inactif ou mot de passe provisoire -> aucun accès (401/403).
- Cache en mémoire 60 s maximum indexé par SHA-256 du cookie.
- Le cookie n'est jamais journalisé en clair.
- Contrôle de même origine sur toute écriture (POST, PUT, DELETE, PATCH).
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from urllib.parse import quote, urlparse

import httpx
from fastapi import APIRouter
from vds_sdk import AccessDenied, ModuleContext
from vds_sdk.server import AuthProvider, AuthRequest, Identity, ShellLink

from vds_server.core.errors import ApiError

PROVIDER = "auth.studio-allia"
LOG = logging.getLogger("vds.auth.studio-allia")
COOKIE_NAME = "allia_espace"


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class StudioAlliaAuth(AuthProvider):
    ctx: ModuleContext | None = None
    _cache: dict[str, tuple[float, Identity | Exception]]

    def __init__(self) -> None:
        self._cache = {}

    async def setup(self, ctx: ModuleContext) -> None:
        self.ctx = ctx
        LOG.info("auth.studio-allia: module initialisé (mode hébergé)")

    # --- interface AuthProvider -----------------------------------------
    async def authenticate(self, request: AuthRequest) -> Identity | None:
        cookie = request.cookies.get(COOKIE_NAME)
        if not cookie:
            return None

        # Contrôle de même origine sur les écritures (CSRF)
        if request.method.upper() in ("POST", "PUT", "DELETE", "PATCH"):
            self._verify_same_origin(request)

        token_hash = _digest(cookie)
        now = time.monotonic()

        # Cache en mémoire borné (60 s)
        cached = self._cache.get(token_hash)
        if cached is not None:
            expires_at, entry = cached
            if now < expires_at:
                if isinstance(entry, Exception):
                    raise entry
                return entry
            del self._cache[token_hash]

        # Interrogation du Studio
        identity = await self._check_session(cookie, token_hash, now)
        return identity

    def login_url(self, return_to: str) -> str:
        login_base = os.environ.get("STUDIO_LOGIN_URL", "/espace/connexion")
        return f"{login_base}?retour={quote(return_to, safe='')}"

    def logout_url(self) -> str:
        return os.environ.get("STUDIO_LOGOUT_URL", "/espace/deconnexion")

    def shell_links(self) -> list[ShellLink]:
        return [
            ShellLink(
                id="back_to_studio",
                label_key="nav.links.back_to_studio",
                url="/espace",
            )
        ]

    def routes(self) -> APIRouter:
        return APIRouter()

    # --- logique interne -------------------------------------------------
    def _verify_same_origin(self, request: AuthRequest) -> None:
        """Vérifie l'origine sur toute mutation pour contrer les requêtes inter-sites."""
        sec_fetch_site = request.headers.get("sec-fetch-site")
        if sec_fetch_site == "cross-site":
            raise AccessDenied("errors.auth.cross_site_forbidden")

        origin = request.headers.get("origin")
        if not origin:
            return

        origin_host = urlparse(origin).netloc.lower()
        allowed_hosts: set[str] = set()

        host_header = request.headers.get("host")
        if host_header:
            allowed_hosts.add(host_header.lower())

        http_facade = getattr(self.ctx.core, "http", None) if self.ctx and hasattr(self.ctx, "core") else None
        public_origin = getattr(http_facade, "public_origin", None) if http_facade else None
        if public_origin:
            allowed_hosts.add(urlparse(public_origin).netloc.lower())

        env_origin = os.environ.get("VDS_PUBLIC_ORIGIN")
        if env_origin:
            allowed_hosts.add(urlparse(env_origin).netloc.lower())

        if allowed_hosts and origin_host not in allowed_hosts:
            LOG.warning(
                "auth.studio-allia: origine rejetée: %s (attendus: %s)",
                origin_host,
                allowed_hosts,
            )
            raise AccessDenied("errors.auth.origin_mismatch")

    async def _check_session(
        self, cookie: str, token_hash: str, now: float
    ) -> Identity | None:
        cfg = await self.ctx.config.resolve() if self.ctx and hasattr(self.ctx, "config") else {}
        session_url = (
            os.environ.get("STUDIO_SESSION_URL")
            or cfg.get("session_url")
            or "http://website:3000/api/espace/vds/session"
        )
        shared_secret = (
            os.environ.get("STUDIO_SHARED_SECRET")
            or cfg.get("shared_secret")
            or ""
        )
        cache_ttl = int(cfg.get("cache_ttl_seconds", 60))
        timeout_s = float(cfg.get("timeout_seconds", 2.0))

        headers = {
            "Cookie": f"{COOKIE_NAME}={cookie}",
            "x-vds-secret": shared_secret,
        }

        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.get(session_url, headers=headers)
        except Exception as exc:
            LOG.warning(
                "auth.studio-allia: échec réseau vers STUDIO_SESSION_URL: %s",
                type(exc).__name__,
            )
            raise ApiError("auth.provider_unavailable", 503) from exc

        if resp.status_code == 401:
            # Session absente, expirée ou invalide côté Studio
            return None

        if resp.status_code == 403:
            LOG.warning("auth.studio-allia: Studio a renvoyé 403 (secret invalide ou refus)")
            raise AccessDenied("errors.auth.studio_secret_mismatch")

        if resp.status_code != 200:
            LOG.warning("auth.studio-allia: Studio a renvoyé HTTP %d", resp.status_code)
            raise ApiError("auth.provider_unavailable", 503)

        try:
            data = resp.json()
        except Exception as exc:
            LOG.warning("auth.studio-allia: payload JSON invalide renvoyé par Studio")
            raise ApiError("auth.provider_unavailable", 503) from exc

        user_id = data.get("id")
        if not user_id:
            LOG.warning("auth.studio-allia: champ 'id' manquant dans le payload Studio")
            raise ApiError("auth.provider_unavailable", 503)

        role = str(data.get("role", "")).strip().lower()

        # Correspondance des rôles Studio -> VDS
        if role in ("president", "direction"):
            mapped_role = "admin"
        elif role == "membre":
            mapped_role = "editor"
        elif role == "financeur":
            exc = AccessDenied("errors.auth.studio_financeur_forbidden")
            self._cache[token_hash] = (now + cache_ttl, exc)
            raise exc
        else:
            LOG.warning("auth.studio-allia: rôle Studio non autorisé: %s", role)
            exc = AccessDenied("errors.auth.studio_role_unauthorized")
            self._cache[token_hash] = (now + cache_ttl, exc)
            raise exc

        identity = Identity(
            provider=PROVIDER,
            external_id=str(user_id),
            email=data.get("email"),
            display_name=str(data.get("nom") or user_id),
            role=mapped_role,
        )

        self._cache[token_hash] = (now + cache_ttl, identity)
        return identity
