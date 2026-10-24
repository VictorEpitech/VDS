"""Cookies : toujours Path=<base>/ (ou /) ; Secure selon le schéma effectif."""

import logging
from typing import Literal

from fastapi import Request, Response

from vds_server.settings import Settings

log = logging.getLogger("vds.security")


def cookie_path(settings: Settings) -> str:
    return f"{settings.prefix}/"


def cookie_secure(settings: Settings, request: Request) -> bool:
    """Secure si le schéma effectif est HTTPS.

    `request.url.scheme` reflète X-Forwarded-Proto uniquement si le proxy est de confiance
    (uvicorn `--forwarded-allow-ips`) : l'en-tête n'est jamais lu ici (forgeable).
    """
    if settings.cookie_secure == "true":
        return True
    if settings.cookie_secure == "false":
        return False
    if (settings.public_origin or "").lower().startswith("https://"):
        return True
    return request.url.scheme == "https"


def warn_if_insecure_http(settings: Settings) -> None:
    """Avertissement de démarrage : sans HTTPS, les cookies voyagent en clair."""
    https_origin = (settings.public_origin or "").lower().startswith("https://")
    if settings.cookie_secure == "false":
        log.warning("security.cookie_secure_disabled")
    elif settings.cookie_secure == "true" and settings.public_origin and not https_origin:
        log.warning("security.cookie_secure_forced_on_http_origin")
    elif settings.cookie_secure == "auto" and not https_origin:
        log.warning("security.http_cookies_not_secure_unless_https_request")


def set_cookie(
    response: Response,
    settings: Settings,
    request: Request,
    key: str,
    value: str,
    *,
    max_age: int | None = None,
    samesite: Literal["lax", "strict"] = "lax",
) -> None:
    response.set_cookie(
        key,
        value,
        max_age=max_age,
        path=cookie_path(settings),
        httponly=True,
        secure=cookie_secure(settings, request),
        samesite=samesite,
    )


def delete_cookie(response: Response, settings: Settings, request: Request, key: str) -> None:
    response.delete_cookie(
        key,
        path=cookie_path(settings),
        httponly=True,
        secure=cookie_secure(settings, request),
        samesite="lax",
    )
