"""Ordre de démarrage des identifiants (spec §2.7) et canal local d'enrôlement.

1. VDS_AGENT_TOKEN (prioritaire, canal local ignoré) ; 2. jeton chiffré du Store
(`core/agent_token`) ; 3. VDS_ENROLL_CODE (échangé, rangé, oublié) ; 4. VDS_AGENT_LOCAL_DIR :
sommeil, surveillance de `enroll.json` ; 5. sinon `agent.credentials_missing` (sortie 2).
Aucun port entrant : seulement des connexions sortantes.
"""

import asyncio
import contextlib
import dataclasses
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID

import httpx

from .enroll import EnrollError, exchange, take_handoff, write_presence
from .runtime import Agent
from .settings import AgentSettings
from .store import Store

log = logging.getLogger("vds.agent")

STATE_MODULE = "core"
STATE_KEY = "agent_token"
WATCH_S = 2.0
PRESENCE_S = 10.0

AgentFactory = Callable[[AgentSettings, Store], Agent]


class CredentialsMissingError(Exception):
    code = "agent.credentials_missing"


class Supervisor:
    def __init__(
        self,
        settings: AgentSettings,
        store: Store,
        *,
        agent_factory: AgentFactory | None = None,
        http: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        watch_s: float = WATCH_S,
        presence_s: float = PRESENCE_S,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.s = settings
        self.store = store
        self._factory = agent_factory or (lambda s, st: Agent(s, st))
        self._http = http or httpx.AsyncClient(
            verify=settings.ssl_context(), timeout=15, follow_redirects=False
        )
        self._sleep = sleep
        self._watch_s = watch_s
        self._presence_s = presence_s
        self._clock = clock
        self.state = "dormant"
        self.error: str | None = None
        self.agent_id: UUID | None = None
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    # --- présence ----------------------------------------------------------------
    def _write_presence(self) -> None:
        if self.s.local_dir is None:
            return
        try:
            write_presence(self.s.local_dir, self.state, self._clock(), self.agent_id, self.error)
        except OSError:
            log.warning("agent.presence_write_failed")

    def _set(self, state: str, error: str | None = None) -> None:
        self.state, self.error = state, error
        self._write_presence()

    async def _presence_loop(self) -> None:
        while True:
            self._write_presence()
            await self._sleep(self._presence_s)

    # --- jeton rangé ----------------------------------------------------------------
    async def _stored(self) -> str | None:
        tok = await self.store.state_get(STATE_MODULE, STATE_KEY)
        return str(tok) if tok else None

    async def _remember(self, token: str) -> None:
        await self.store.state_set(STATE_MODULE, STATE_KEY, token)

    async def _forget(self) -> None:
        await self.store.state_delete(STATE_MODULE, STATE_KEY)

    async def _enroll(self, code: str, previous: str | None = None) -> str:
        """Échange le code, range le jeton chiffré, l'oublie ensuite (code : variable locale)."""
        res = await exchange(self._http, self.s.http_base, code, previous, sleep=self._sleep)
        token = res.token.get_secret_value()
        await self._remember(token)
        self.agent_id = res.agent_id
        return token

    # --- boucle principale -------------------------------------------------------
    async def run(self) -> int:
        presence = None
        if self.s.local_dir is not None:
            presence = asyncio.create_task(self._presence_loop())
        try:
            return await self._run()
        finally:
            if presence:
                presence.cancel()
            await self._http.aclose()

    async def _run(self) -> int:
        s = self.s
        if s.token:  # 1. variable : prioritaire, canal local ignoré
            self.state = "active"
            return await self._serve(s.token, watch_local=False)
        token = await self._stored()  # 2. jeton chiffré
        if token:
            if s.enroll_code:
                log.warning("agent.enroll_code_ignored")
            self._set("active")
            return await self._serve(token, watch_local=s.local_dir is not None)
        if s.enroll_code:  # 3. code en variable
            self._set("enrolling")
            try:
                token = await self._enroll(s.enroll_code)
            except EnrollError as e:
                log.error("agent.enroll_failed", extra={"code": e.code})
                if s.local_dir is None:
                    return 2
                self._set("error", e.code)
            else:
                self._set("active")
                return await self._serve(token, watch_local=s.local_dir is not None)
        if s.local_dir is None:
            raise CredentialsMissingError
        return await self._dormant()  # 4. sommeil

    async def _dormant(self) -> int:
        """Aucune connexion tant qu'aucun dépôt n'arrive ; renvoie la sortie du service actif."""
        if self.state != "error":
            self._set("dormant")
        while not self._stop.is_set():
            token = await self._poll_handoff(None)
            if token:
                self._set("active")
                return await self._serve(token, watch_local=True)
            await self._sleep(self._watch_s)
        return 0

    async def _poll_handoff(self, previous: str | None) -> str | None:
        """Consomme `enroll.json` s'il existe ; None si absent ou si l'échange échoue."""
        assert self.s.local_dir is not None  # noqa: S101
        handoff = take_handoff(self.s.local_dir)
        if handoff is None:
            return None
        if isinstance(handoff, EnrollError):
            self._set("error" if previous is None else "active", handoff.code)
            return None
        keep = self.state
        self._set("enrolling")
        try:
            return await self._enroll(handoff.code.get_secret_value(), previous)
        except EnrollError as e:
            log.error("agent.enroll_failed", extra={"code": e.code})
            self._set("error" if previous is None else keep, e.code)
            return None

    async def _serve(self, token: str, *, watch_local: bool) -> int:
        """Fait tourner l'agent ; réenrôle sur dépôt ; revient au sommeil si révoqué (local)."""
        while not self._stop.is_set():
            agent = self._factory(dataclasses.replace(self.s, token=token), self.store)
            run = asyncio.create_task(agent.run())
            new_token: str | None = None
            try:
                while not run.done() and not self._stop.is_set():
                    if watch_local:
                        new_token = await self._poll_handoff(token)
                        if new_token:
                            break
                    await asyncio.wait({run}, timeout=self._watch_s)
            finally:
                if not run.done():
                    agent.stop()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(run, 30)
                if not run.done():
                    run.cancel()
            if new_token:  # réenrôlé (déplacement) : relance avec le nouveau jeton
                token = new_token
                # l'ancien agent a pu effacer ses secrets en s'arrêtant (révocation 4410)
                await self._remember(token)
                self._set("active")
                continue
            if self._stop.is_set():
                return 0
            if agent.end_reason == "revoked" and self.s.local_dir is not None and watch_local:
                await self._forget()
                self.agent_id = None
                self._set("dormant")
                return await self._dormant()
            return 0
        return 0
