"""Canal local : dépôt du code d'enrôlement dans un dossier partagé avec l'agent local.

Le serveur n'écrit que `enroll.json` (0600, atomique) et ne lit que `presence.json` (borné,
sans lien symbolique, donnée non fiable : affichage seul, jamais une autorisation).
"""

import asyncio
import contextlib
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


class LocalChannel:
    def __init__(self, directory: Path | None) -> None:
        self._dir = directory

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
