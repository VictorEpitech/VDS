"""Connexions d'agents tenues par ce processus + écoute LISTEN/NOTIFY (multi-workers)."""

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncEngine

from vds_server.agents.queue import CHANNEL, DEVICE_KIND, MODULES_KIND

log = logging.getLogger("vds.agents")


class Wakeable(Protocol):
    def wake(self) -> None: ...
    async def replaced(self) -> None: ...
    async def refresh(self) -> None: ...


class AgentHub:
    def __init__(self) -> None:
        self._sessions: dict[UUID, Wakeable] = {}
        self._listener: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Future[None]] = set()
        self._devices: dict[UUID, tuple[str, Callable[[], Awaitable[None]]]] = {}

    def is_connected(self, agent_id: UUID) -> bool:
        return agent_id in self._sessions

    async def register(self, agent_id: UUID, session: Wakeable) -> None:
        """Une seule connexion active par agent : l'ancienne est fermée (4409)."""
        previous = self._sessions.get(agent_id)
        self._sessions[agent_id] = session
        if previous is not None and previous is not session:
            await previous.replaced()

    def unregister(self, agent_id: UUID, session: Wakeable) -> None:
        if self._sessions.get(agent_id) is session:
            del self._sessions[agent_id]

    async def refresh_all(self) -> None:
        """Modules activés/désactivés : les agents se reconnectent et reçoivent la liste à jour."""
        for session in list(self._sessions.values()):
            with contextlib.suppress(Exception):
                await session.refresh()

    # --- une seule connexion /ws/player par appareil (tous workers) -----------
    def register_device(self, device_id: UUID, closer: Callable[[], Awaitable[None]]) -> str:
        """Enregistre la connexion ; l'ancienne du même worker est fermée (4409) par l'appelant."""
        nonce = f"{time.time_ns():020d}-{uuid4().hex}"
        previous = self._devices.get(device_id)
        self._devices[device_id] = (nonce, closer)
        if previous is not None:
            self._spawn(previous[1]())
        return nonce

    def unregister_device(self, device_id: UUID, nonce: str) -> None:
        current = self._devices.get(device_id)
        if current is not None and current[0] == nonce:
            del self._devices[device_id]

    def _device_announced(self, raw: str) -> None:
        raw_id, _, nonce = raw.partition(":")
        try:
            current = self._devices.get(UUID(raw_id))
        except ValueError:
            return
        # la plus récente gagne : on ne ferme que les connexions enregistrées avant l'annonce
        if current is not None and current[0] < nonce:
            self._spawn(current[1]())

    def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def dispatch(self, payload: str) -> None:
        kind, _, raw = payload.partition(":")
        if kind == MODULES_KIND:
            # signal émis par n'importe quel worker : chacun ferme ses propres sessions (1012)
            self._spawn(self.refresh_all())
            return
        if kind == DEVICE_KIND:
            self._device_announced(raw)
            return
        if kind != "agent":
            return
        try:
            session = self._sessions.get(UUID(raw))
        except ValueError:
            return
        if session is not None:
            session.wake()

    # --- LISTEN vds_command ------------------------------------------------
    def start(self, engine: AsyncEngine) -> None:
        self._listener = asyncio.create_task(self._listen(engine))

    async def stop(self) -> None:
        if self._listener is not None:
            self._listener.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listener
            self._listener = None

    async def _listen(self, engine: AsyncEngine) -> None:
        # Si LISTEN échoue, les sessions restent réveillées par leur sondage périodique.
        while True:
            try:
                # `engine.connect()` : la connexion est rendue au pool dans le contexte asyncio
                # (fermer un `raw_connection()` hors greenlet lève MissingGreenlet à l'arrêt).
                async with engine.connect() as conn:
                    raw = await conn.get_raw_connection()
                    driver: Any = raw.driver_connection
                    await driver.add_listener(
                        CHANNEL, lambda _c, _pid, _ch, payload: self.dispatch(payload)
                    )
                    try:
                        while True:
                            await asyncio.sleep(30)
                            await driver.execute("SELECT 1")
                    finally:
                        # connexion dédiée au LISTEN : fermée, jamais rendue au pool
                        with contextlib.suppress(Exception):
                            await conn.invalidate()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("command listener lost, retrying")
                await asyncio.sleep(5)
