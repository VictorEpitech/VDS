"""Connexions d'agents tenues par ce processus + écoute LISTEN/NOTIFY (multi-workers)."""

import asyncio
import contextlib
import logging
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine

from vds_server.agents.queue import CHANNEL

log = logging.getLogger("vds.agents")


class Wakeable(Protocol):
    def wake(self) -> None: ...
    async def replaced(self) -> None: ...
    async def refresh(self) -> None: ...


class AgentHub:
    def __init__(self) -> None:
        self._sessions: dict[UUID, Wakeable] = {}
        self._listener: asyncio.Task[None] | None = None

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
            await session.refresh()

    def dispatch(self, payload: str) -> None:
        kind, _, raw = payload.partition(":")
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
