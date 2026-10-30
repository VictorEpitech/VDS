"""Authentification du serveur sur la boucle locale, avant tout envoi de secret (spec I1).

Le clair vers 127.0.0.1 n'authentifie pas son interlocuteur : un utilisateur local de l'hôte
peut écouter le port pendant un redémarrage du conteneur serveur. L'agent local partage donc
un secret aléatoire avec le serveur (`proof.key`, 0600, volume `local-agent`) et exige, avant
d'envoyer code ou jeton, la preuve HMAC d'un nonce frais. Le secret ne circule jamais.
Le canal distant (TLS vérifié) n'est pas concerné.

Limite connue : un attaquant qui relaie le défi vers le vrai serveur par une autre route
(IP du conteneur) obtient une preuve valide ; l'authentification de chaque requête n'est
pas liée au flux. Le TLS reste la protection pour tout hôte multi-utilisateurs.
"""

import contextlib
import hashlib
import hmac
import os
import secrets
import stat
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from .settings import AgentSettings

KEY_FILE = "proof.key"
KEY_BYTES = 32
PROOF_CONTEXT = b"vds-local-proof\0"
ENROLL_CONTEXT = b"vds-local-enroll\0"
PROOF_PATH = "/api/v1/agent/local/proof"
AUTH_HEADER = "X-VDS-Local-Auth"
TTL_S = 5.0  # une preuve vaut quelques secondes (pas une session)


class ServerUnverified(httpx.TransportError):
    """Le serveur n'a pas prouvé sa légitimité : rien n'est envoyé."""


def read_key(directory: Path) -> bytes | None:
    try:
        fd = os.open(directory / KEY_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        raw = os.read(fd, 4 * KEY_BYTES).strip()
    finally:
        os.close(fd)
    try:
        key = bytes.fromhex(raw.decode())
    except ValueError:
        return None
    return key if len(key) == KEY_BYTES else None


def enroll_auth(directory: Path, code: str) -> str | None:
    """En-tête prouvant au serveur que la requête vient de l'agent local (seau distinct)."""
    key = read_key(directory)
    if key is None:
        return None
    return hmac.new(key, ENROLL_CONTEXT + code.encode(), hashlib.sha256).hexdigest()


def required(settings: AgentSettings) -> bool:
    """Preuve exigée : agent local (dossier partagé) joignant le serveur en clair."""
    return settings.local_dir is not None and not settings.is_tls


class ServerGuard:
    def __init__(
        self,
        settings: AgentSettings,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._s = settings
        self._client = client
        self._clock = clock
        self._ok_until = 0.0

    async def verify(self, *, fresh: bool = False) -> None:
        if not required(self._s):
            return
        now = self._clock()
        if not fresh and now < self._ok_until:
            return
        assert self._s.local_dir is not None  # noqa: S101
        nonce = secrets.token_hex(16)
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=5, follow_redirects=False)
        try:
            r = await self._client.get(f"{self._s.http_base}{PROOF_PATH}", params={"nonce": nonce})
            proof = str(r.json()["proof"]) if r.status_code == 200 else ""
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            raise ServerUnverified("agent.server_unverified") from e
        # Clé lue APRÈS le défi : au tout premier appel, c'est ce défi qui la fait créer.
        key = read_key(self._s.local_dir)
        if key is None:
            raise ServerUnverified("agent.server_unverified")
        want = hmac.new(key, PROOF_CONTEXT + nonce.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(want.encode(), proof.encode()):
            raise ServerUnverified("agent.server_unverified")
        self._ok_until = now + TTL_S

    async def hook(self, request: httpx.Request) -> None:
        """Hook httpx : aucune requête (donc aucun secret) ne part vers un serveur non prouvé."""
        await self.verify()

    async def aclose(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.aclose()


def new_http(settings: AgentSettings) -> tuple[httpx.AsyncClient, ServerGuard]:
    guard = ServerGuard(settings)
    client = httpx.AsyncClient(
        verify=settings.ssl_context(),
        timeout=15,
        follow_redirects=False,
        event_hooks={"request": [guard.hook]},
    )
    return client, guard
