"""Enrôlement par code à usage unique : échange HTTP, dépôt local, présence (spec §2.7).

Le code et le jeton ne sont jamais journalisés. `enroll.json` est supprimé dès lu, avant tout
appel réseau. La présence est une donnée non fiable, bornée à 4 Kio, sans secret.
"""

import asyncio
import contextlib
import logging
import os
import platform
import re
import socket
import stat
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from uuid import UUID

import httpx
from pydantic import SecretStr, ValidationError
from vds_sdk.agent.enroll import (
    PRESENCE_MAX_BYTES,
    EnrollRequest,
    EnrollResponse,
    LocalHandoff,
    LocalPresence,
)

from .settings import AGENT_VERSION
from .trust import AUTH_HEADER, ServerUnverified

log = logging.getLogger("vds.agent.enroll")

HANDOFF_FILE = "enroll.json"
PRESENCE_FILE = "presence.json"
HANDOFF_MAX_BYTES = 4096
ATTEMPTS = 3
RETRY_DELAY_S = 2.0

_HTTP_ERRORS = {
    401: "enroll.invalid",
    403: "enroll.forbidden",
    409: "enroll.consumed",
    410: "enroll.expired",
    429: "enroll.rate_limited",
}


class EnrollError(Exception):
    """`code` est un code i18n (jamais un message brut ni un secret)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def local_hostname() -> str:
    name = re.sub(r"[^A-Za-z0-9._-]", "-", socket.gethostname())[:253]
    return name or "unknown"


def local_platform() -> str:
    arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())
    return f"{platform.system().lower()}/{arch}"[:32]


async def exchange(
    http: httpx.AsyncClient,
    http_base: str,
    code: str,
    previous_token: str | None = None,
    *,
    local_auth: str | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> EnrollResponse:
    """POST /api/v1/agent/enroll. Réseau ou 5xx : 3 tentatives ; 4xx : abandon immédiat."""
    try:
        body = EnrollRequest(
            code=code,
            hostname=local_hostname(),
            agent_version=AGENT_VERSION,
            platform=local_platform(),
            previous_token=SecretStr(previous_token) if previous_token else None,
        ).model_dump(mode="json")
    except ValidationError as e:
        raise EnrollError("enroll.invalid") from e
    if previous_token:
        body["previous_token"] = previous_token
    for attempt in range(ATTEMPTS):
        try:
            # Aucun cookie, aucun Origin : l'endpoint public refuse toute requête avec Origin.
            r = await http.post(
                f"{http_base}/api/v1/agent/enroll",
                json=body,
                headers={AUTH_HEADER: local_auth} if local_auth else None,
            )
        except ServerUnverified as e:  # rien n'est parti : le code n'a pas quitté l'agent
            raise EnrollError("enroll.server_unverified") from e
        except httpx.HTTPError:
            r = None
        if r is not None and r.status_code < 500:
            if r.status_code == 200:
                try:
                    return EnrollResponse.model_validate(r.json())
                except (ValueError, ValidationError) as e:
                    raise EnrollError("enroll.bad_response") from e
            raise EnrollError(_HTTP_ERRORS.get(r.status_code, "enroll.failed"))
        if attempt + 1 < ATTEMPTS:
            await sleep(RETRY_DELAY_S)
    raise EnrollError("enroll.unreachable")


def take_handoff(directory: Path) -> LocalHandoff | None | EnrollError:
    """Lit puis supprime `enroll.json` (avant tout appel réseau).

    None si absent ; EnrollError si présent mais invalide (déjà supprimé).
    """
    path = directory / HANDOFF_FILE
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError:  # lien symbolique, droits : on retire ce qu'on peut et on signale
        with contextlib.suppress(OSError):
            path.unlink()
        return EnrollError("enroll.handoff_invalid")
    try:
        st = os.fstat(fd)
        raw = os.read(fd, HANDOFF_MAX_BYTES + 1) if stat.S_ISREG(st.st_mode) else b""
    finally:
        os.close(fd)
    with contextlib.suppress(OSError):
        path.unlink()
    if not raw or len(raw) > HANDOFF_MAX_BYTES:
        return EnrollError("enroll.handoff_invalid")
    try:
        return LocalHandoff.model_validate_json(raw)
    except ValidationError:
        return EnrollError("enroll.handoff_invalid")


def write_presence(
    directory: Path,
    state: str,
    now: datetime,
    agent_id: UUID | None = None,
    error: str | None = None,
) -> None:
    """Écriture atomique (0644 : donnée non secrète, lisible quel que soit l'UID du serveur)."""
    presence = LocalPresence(
        hostname=local_hostname(),
        agent_version=AGENT_VERSION,
        platform=local_platform(),
        state=state,  # type: ignore[arg-type]
        agent_id=agent_id,
        error=error,
        ts=now,
    )
    raw = presence.model_dump_json(exclude_none=True).encode()
    assert len(raw) <= PRESENCE_MAX_BYTES  # noqa: S101 - champs bornés par le modèle
    tmp = directory / f".{PRESENCE_FILE}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
        os.chmod(tmp, 0o644)
        os.replace(tmp, directory / PRESENCE_FILE)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
