"""Session WebSocket d'un agent (protocole vds.agent.v1) : hello/welcome, commandes, reprise."""

import asyncio
import contextlib
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from fastapi import WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from vds_sdk import Event, EventBus
from vds_sdk.wire import (
    CLOSE_BAD_MESSAGE,
    CLOSE_HELLO_TIMEOUT,
    CLOSE_RATE_LIMITED,
    CLOSE_REPLACED,
    CLOSE_REVOKED,
    MAX_UNACKED,
    AgentHeartbeat,
    AgentHello,
    AgentWelcome,
    CacheInfo,
    CmdResult,
    EventData,
    Frame,
    ModuleEnabled,
    SeqReceiver,
    WireError,
    decode_frame,
    encode_frame,
    parse_data,
)

from vds_server.agents.bridge import EventBridge
from vds_server.agents.hub import AgentHub
from vds_server.agents.queue import CommandQueue
from vds_server.agents.service import command_frame_data
from vds_server.db.models_power import SiteAgent

HELLO_TIMEOUT_S = 5.0
POLL_S = 5.0
IDLE_TIMEOUT_S = 180.0
MAX_FRAMES_PER_S = 50
CLOSE_GOING_AWAY = 1001
SEQ_KEY = "agent_seq"  # dernier seq agent acquitté, conservé dans last_hello


class Socket(Protocol):
    async def receive_text(self) -> str: ...
    async def send_text(self, data: str) -> None: ...
    async def close(self, code: int = 1000) -> None: ...


class _Closed(Exception):
    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


class AgentSession:
    def __init__(
        self,
        ws: Socket,
        agent: SiteAgent,
        *,
        maker: async_sessionmaker[AsyncSession],
        bus: EventBus,
        hub: AgentHub,
        queue: CommandQueue,
        bridge: EventBridge,
        modules_enabled: Callable[[], list[ModuleEnabled]] = list,
        cache_info: Callable[[SiteAgent], CacheInfo] = lambda _row: CacheInfo(),
        hello_timeout_s: float | None = None,
        poll_s: float | None = None,
        idle_timeout_s: float | None = None,
    ) -> None:
        self._ws, self._maker, self._bus, self._hub = ws, maker, bus, hub
        self._queue, self._bridge, self._modules = queue, bridge, modules_enabled
        self._cache_info = cache_info
        self._hello_timeout = HELLO_TIMEOUT_S if hello_timeout_s is None else hello_timeout_s
        self._poll = POLL_S if poll_s is None else poll_s
        self._idle = IDLE_TIMEOUT_S if idle_timeout_s is None else idle_timeout_s
        self.agent_id, self.site_id = agent.id, agent.site_id
        self._token_hash = agent.token_hash
        self._version = ""
        self._wake = asyncio.Event()
        self._recv = SeqReceiver()
        self._last_sent = 0
        self._acked = 0
        self._window: list[float] = []
        self._closed = False
        self._reason = "closed"

    # --- Wakeable (hub) ----------------------------------------------------
    def wake(self) -> None:
        self._wake.set()

    async def replaced(self) -> None:
        self._reason = "replaced"
        await self._close(CLOSE_REPLACED)

    # --- bas niveau ---------------------------------------------------------
    async def _close(self, code: int) -> None:
        if not self._closed:
            self._closed = True
            with contextlib.suppress(Exception):
                await self._ws.close(code)

    async def _send(self, frame: Frame) -> None:
        if not self._closed:
            await self._ws.send_text(encode_frame(frame))

    async def _read(self, timeout: float) -> Frame:  # noqa: ASYNC109
        try:
            raw = await asyncio.wait_for(self._ws.receive_text(), timeout)
        except TimeoutError as exc:
            raise _Closed(CLOSE_GOING_AWAY, "idle_timeout") from exc
        except WebSocketDisconnect as exc:  # fermeture par le pair ou arrêt du serveur
            raise _Closed(exc.code, "peer_closed") from exc
        now = time.monotonic()
        self._window = [t for t in self._window if now - t < 1.0]
        self._window.append(now)
        if len(self._window) > MAX_FRAMES_PER_S:
            raise _Closed(CLOSE_RATE_LIMITED, "rate_limited")
        try:
            return decode_frame(raw)
        except WireError as exc:
            raise _Closed(exc.close_code, exc.code) from exc

    def _parse(self, frame: Frame) -> Any:
        try:
            return parse_data(frame, "agent", "to_server")
        except WireError as exc:
            raise _Closed(exc.close_code, exc.code) from exc

    # --- base --------------------------------------------------------------
    async def _row(self) -> SiteAgent | None:
        async with self._maker() as s:
            return await s.get(SiteAgent, self.agent_id)

    async def _update(self, **changes: Any) -> None:
        async with self._maker.begin() as s:
            row = await s.get(SiteAgent, self.agent_id)
            if row is not None:
                for k, v in changes.items():
                    setattr(row, k, v)

    async def _persist_seq(self, seq: int) -> None:
        async with self._maker.begin() as s:
            row = await s.get(SiteAgent, self.agent_id)
            if row is not None:
                row.last_hello = {**(row.last_hello or {}), SEQ_KEY: seq}
                row.last_seen_at = datetime.now(UTC)

    async def _still_valid(self) -> int | None:
        """Code de fermeture si le jeton a été révoqué ou changé, sinon None."""
        row = await self._row()
        if row is None or row.revoked_at is not None or row.token_hash != self._token_hash:
            return CLOSE_REVOKED
        return None

    # --- cycle ---------------------------------------------------------------
    async def run(self) -> None:
        try:
            await self._handshake()
            await self._publish("agent.connected", version=self._version)
            await self._loop()
        except _Closed as exc:
            self._reason = exc.reason
            await self._close(exc.code)
        except Exception:
            self._reason = "error"
            await self._close(1011)
            raise
        finally:
            self._hub.unregister(self.agent_id, self)
            if self._version:
                with contextlib.suppress(Exception):
                    await self._publish("agent.disconnected", reason=self._reason)

    async def _publish(self, name: str, **payload: Any) -> None:
        await self._bus.publish(
            Event(
                name=name,
                payload={"agent_id": str(self.agent_id), "site_id": str(self.site_id), **payload},
                source="core",
            )
        )

    async def _handshake(self) -> None:
        try:
            frame = await self._read(self._hello_timeout)
        except _Closed as exc:
            if exc.reason == "idle_timeout":
                raise _Closed(CLOSE_HELLO_TIMEOUT, "hello_timeout") from exc
            raise
        if frame.type != "hello":
            raise _Closed(CLOSE_BAD_MESSAGE, "hello_expected")
        hello: AgentHello = self._parse(frame)
        row = await self._row()
        if row is None or row.revoked_at is not None or row.token_hash != self._token_hash:
            raise _Closed(CLOSE_REVOKED, "revoked")
        agent_seq = int((row.last_hello or {}).get(SEQ_KEY, 0))
        self._recv = SeqReceiver(agent_seq)
        self._last_sent = self._acked = hello.last_server_seq
        self._version = hello.agent_version
        await self._update(
            version=hello.agent_version,
            last_seen_at=datetime.now(UTC),
            last_hello={**hello.model_dump(mode="json"), SEQ_KEY: agent_seq},
        )
        await self._hub.register(self.agent_id, self)
        welcome = AgentWelcome(
            agent_id=self.agent_id,
            site_id=self.site_id,
            session_id=uuid4(),
            server_time=datetime.now(UTC),
            resume_from=agent_seq,
            modules_enabled=self._modules(),
            cache=self._cache_info(row),
        )
        await self._send(Frame(type="welcome", data=welcome.model_dump(mode="json")))

    async def _loop(self) -> None:
        reader = asyncio.create_task(self._reader())
        writer = asyncio.create_task(self._writer())
        done, pending = await asyncio.wait({reader, writer}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        for t in pending:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        for t in done:
            t.result()  # propage _Closed

    async def _writer(self) -> None:
        while not self._closed:
            if (code := await self._still_valid()) is not None:
                raise _Closed(code, "revoked")
            await self._flush()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self._poll)
            self._wake.clear()

    async def _flush(self) -> None:
        room = MAX_UNACKED - (self._last_sent - self._acked)
        if room <= 0:
            return
        cmds = await self._queue.pending("agent", self.agent_id, self._last_sent, limit=room)
        # `sent` avant l'envoi : queued et sent sont rejoués à la reprise, rien n'est perdu
        await self._queue.mark_sent([c.id for c in cmds])
        for cmd in cmds:
            await self._send(
                Frame(id=cmd.id, type="cmd", seq=cmd.seq, data=command_frame_data(cmd))
            )
            self._last_sent = cmd.seq

    async def _reader(self) -> None:
        while not self._closed:
            frame = await self._read(self._idle)
            if frame.type == "hello":
                raise _Closed(CLOSE_BAD_MESSAGE, "unexpected_type")
            data = self._parse(frame)
            if frame.ack is not None:
                if frame.ack > self._last_sent:
                    raise _Closed(CLOSE_BAD_MESSAGE, "ack_ahead")
                self._acked = max(self._acked, frame.ack)
            if frame.type == "heartbeat":
                await self._on_heartbeat(data)
            elif frame.reliable:
                await self._on_reliable(frame, data)

    async def _on_heartbeat(self, hb: AgentHeartbeat) -> None:
        await self._update(
            last_seen_at=datetime.now(UTC), last_heartbeat=hb.model_dump(mode="json")
        )

    async def _on_reliable(self, frame: Frame, data: Any) -> None:
        try:
            fresh = self._recv.accept(frame)
        except WireError as exc:
            raise _Closed(exc.close_code, exc.code) from exc
        if fresh:
            if isinstance(data, CmdResult):
                await self._queue.complete(
                    "agent", self.agent_id, data.cmd_id, data.status, data.code, data.data
                )
            elif isinstance(data, EventData):
                try:
                    await self._bridge.republish(self.site_id, data)
                except WireError as exc:
                    raise _Closed(exc.close_code, exc.code) from exc
            await self._persist_seq(self._recv.last_seq)
        await self._send(Frame(type="ack", ack=self._recv.last_seq))
