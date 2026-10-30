"""Canal local : dépôt du code d'enrôlement dans un dossier partagé avec l'agent local.

Le serveur n'écrit que `enroll.json` (0600, atomique) et ne lit que `presence.json` (borné,
sans lien symbolique, donnée non fiable : affichage seul, jamais une autorisation).
"""

import asyncio
import contextlib
import hashlib
import hmac
import json
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from pydantic import SecretStr, ValidationError
from vds_sdk.agent.enroll import PRESENCE_MAX_BYTES, LocalHandoff, LocalPresence, parse_presence

HANDOFF_FILE = "enroll.json"
PRESENCE_FILE = "presence.json"
PRESENCE_MAX_AGE = timedelta(seconds=30)
KEY_FILE = "proof.key"
KEY_BYTES = 32
PROOF_CONTEXT = b"vds-local-proof\0"
ENROLL_CONTEXT = b"vds-local-enroll\0"


class LocalChannel:
    def __init__(self, directory: Path | None) -> None:
        self._dir = directory
        self._key: bytes | None = None

    # --- secret partagé avec l'agent local (preuve HMAC, jamais transmis) ------------
    def _load_key(self) -> bytes:
        """Lit `proof.key` ou le crée (0600, sans écrasement : plusieurs workers sans course)."""
        assert self._dir is not None  # noqa: S101
        path = self._dir / KEY_FILE
        for _ in range(3):
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            except FileNotFoundError:
                fd = -1
            if fd >= 0:
                try:
                    raw = os.read(fd, 4 * KEY_BYTES) if stat.S_ISREG(os.fstat(fd).st_mode) else b""
                finally:
                    os.close(fd)
                key = raw.strip()
                if len(key) == 2 * KEY_BYTES:
                    return bytes.fromhex(key.decode())
                raise OSError("proof.key invalide")
            tmp = self._dir / f".{KEY_FILE}.{os.getpid()}.{os.urandom(4).hex()}.tmp"
            wfd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                with os.fdopen(wfd, "wb") as f:
                    f.write(os.urandom(KEY_BYTES).hex().encode())
                    f.flush()
                    os.fsync(f.fileno())
                try:
                    os.link(tmp, path)  # atomique, échoue si un autre worker a gagné
                except FileExistsError:
                    pass
            finally:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
        raise OSError("proof.key illisible")

    async def key(self) -> bytes | None:
        if self._dir is None:
            return None
        if self._key is None:
            self._key = await asyncio.to_thread(self._load_key)
        return self._key

    async def prove_server(self, nonce: str) -> str | None:
        """Preuve que ce serveur détient le secret du volume (défi de l'agent local)."""
        key = await self.key()
        if key is None:
            return None
        return hmac.new(key, PROOF_CONTEXT + nonce.encode(), hashlib.sha256).hexdigest()

    async def is_local_agent(self, auth: str | None, code: str) -> bool:
        """L'agent local prouve qu'il détient le secret : requête d'enrôlement authentifiée."""
        if not auth or self._dir is None:
            return False
        key = await self.key()
        assert key is not None  # noqa: S101
        want = hmac.new(key, ENROLL_CONTEXT + code.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(want.encode(), auth.encode())

    @property
    def available(self) -> bool:
        return self._dir is not None

    # --- écriture du dépôt ---------------------------------------------------
    def _write(self, payload: bytes) -> None:
        assert self._dir is not None  # noqa: S101
        target = self._dir / HANDOFF_FILE
        tmp = self._dir / f".{HANDOFF_FILE}.{os.getpid()}.{os.urandom(4).hex()}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, target)  # atomique : remplace un dépôt précédent
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        dir_fd = os.open(self._dir, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    async def write_handoff(self, code: str, enrollment_id: UUID, expires_at: datetime) -> None:
        handoff = LocalHandoff(
            code=SecretStr(code), enrollment_id=enrollment_id, expires_at=expires_at
        )
        data = handoff.model_dump(mode="json")
        data["code"] = handoff.code.get_secret_value()  # le fichier est le canal : 0600
        raw = json.dumps(data, separators=(",", ":")).encode()
        await asyncio.to_thread(self._write, raw)

    # --- lecture de la présence ----------------------------------------------
    def _read(self) -> bytes | None:
        assert self._dir is not None  # noqa: S101
        try:
            fd = os.open(self._dir / PRESENCE_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:  # absent, lien symbolique (ELOOP), droits
            return None
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_size > PRESENCE_MAX_BYTES:
                return None
            return os.read(fd, PRESENCE_MAX_BYTES + 1)
        finally:
            os.close(fd)

    async def read_presence(self, now: datetime | None = None) -> LocalPresence | None:
        """Présence valide et fraîche (≤ 30 s), sinon None."""
        if self._dir is None:
            return None
        raw = await asyncio.to_thread(self._read)
        if raw is None:
            return None
        try:
            presence = parse_presence(raw)
        except (ValueError, ValidationError):
            return None
        now = now or datetime.now(UTC)
        ts = presence.ts if presence.ts.tzinfo else presence.ts.replace(tzinfo=UTC)
        if abs(now - ts) > PRESENCE_MAX_AGE:  # trop vieille, ou horloge incohérente
            return None
        return presence.model_copy(update={"ts": ts})
