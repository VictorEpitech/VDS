"""Liaison sortante WSS vers /ws/agent : hello/welcome, seq/ack/reprise, backoff, heartbeat."""

import asyncio
import contextlib
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from vds_sdk.wire import (
    CLOSE_REVOKED,
    CLOSE_TOKEN_ROTATED,
    MAX_FRAME_BYTES,
    SUBPROTOCOL_AGENT,
    AgentHello,
    AgentWelcome,
    Frame,
    SeqReceiver,
    WireError,
    decode_frame,
    encode_frame,
)
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from .settings import AgentSettings
from .store import Store

log = logging.getLogger("vds.agent.link")

BACKOFF_MIN = 1.0
BACKOFF_MAX = 60.0


def next_backoff(current: float, rng: random.Random | None = None) -> float:
    """Exponentiel 1 s -> 60 s, gigue +-20 %."""
    r = rng or random
    base = min(BACKOFF_MAX, max(BACKOFF_MIN, current))
    return float(base * r.uniform(0.8, 1.2))


def describe_link_error(e: BaseException) -> dict[str, Any]:
    """Cause lisible d'une erreur de liaison : type, code/raison de fermeture, message (sans secret)."""
    info: dict[str, Any] = {"err": type(e).__name__, "detail": str(e) or repr(e)}
    rcvd = getattr(e, "rcvd", None)
    sent = getattr(e, "sent", None)
    if rcvd is not None:
        info["close_code"], info["close_reason"] = rcvd.code, rcvd.reason
    elif sent is not None:
        info["close_code_sent"] = sent.code
    resp = getattr(e, "response", None)  # InvalidStatus : refus HTTP à la poignée de main
    if resp is not None:
        info["http_status"] = getattr(resp, "status_code", None)
    if e.__cause__ is not None:
        info["cause"] = f"{type(e.__cause__).__name__}: {e.__cause__}"
    return info


class Revoked(Exception):  # noqa: N818
    pass


class TokenRotated(Exception):  # noqa: N818
    pass


class ServerLink:
    def __init__(
        self,
        settings: AgentSettings,
        store: Store,
        *,
        build_hello: Callable[[int], Awaitable[AgentHello]],
        build_heartbeat: Callable[[], Awaitable[dict[str, Any]]],
        on_welcome: Callable[[AgentWelcome], Awaitable[None]],
        on_cmd: Callable[[Frame], Awaitable[None]],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        heartbeat_s: float = 60.0,
    ) -> None:
        self.s = settings
        self.store = store
        self._build_hello = build_hello
        self._build_hb = build_heartbeat
        self._on_welcome = on_welcome
        self._on_cmd = on_cmd
        self._sleep = sleep
        self._hb_s = heartbeat_s
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self.connected = asyncio.Event()
        self._ws: ClientConnection | None = None
        self.welcome: AgentWelcome | None = None

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    # --- émission fiable -------------------------------------------------------
    async def send_reliable(self, type_: str, data: dict[str, Any]) -> None:
        await self.store.outbox_add(Frame(type=type_, data=data))
        self._wake.set()

    async def emit(
        self, name: str, payload: dict[str, Any], *, version: int = 1, source: str = "agent"
    ) -> None:
        await self.send_reliable(
            "event",
            {
                "name": name,
                "version": version,
                "payload": payload,
                "occurred_at": datetime.now(UTC).isoformat(),
            },
        )

    async def _send(self, ws: ClientConnection, frame: Frame) -> None:
        await ws.send(encode_frame(frame))

    # --- boucle de reconnexion --------------------------------------------------
    async def run(self) -> None:
        delay = BACKOFF_MIN
        while not self._stop.is_set():
            try:
                welcomed = await self._session()
                if welcomed:
                    delay = BACKOFF_MIN
            except Revoked:
                log.error("agent.revoked")
                await self.store.wipe_secrets()
                self._stop.set()
                return
            except TokenRotated:
                # rotation : on conserve l'état (modules, appairages, seq) ; le nouveau jeton
                # s'installe par redémarrage de l'agent
                log.error("agent.token_rotated")
                self._stop.set()
                return
            except (OSError, ConnectionClosed, WireError, TimeoutError, InvalidHandshake) as e:
                log.warning("agent.link_error", extra=describe_link_error(e))
            except Exception:
                log.exception("agent.link_unexpected")
            self.connected.clear()
            if self._stop.is_set():
                return
            wait = next_backoff(delay)
            delay = min(BACKOFF_MAX, delay * 2)
            await self._sleep(wait)

    async def _session(self) -> bool:
        last_server_seq = int(await self.store.kv_get("in_seq", 0))
        async with connect(
            self.s.ws_url,
            ssl=self.s.ssl_context() if self.s.is_tls else None,
            subprotocols=[SUBPROTOCOL_AGENT],  # type: ignore[list-item]
            additional_headers={"Authorization": f"Bearer {self.s.token}"},
            max_size=MAX_FRAME_BYTES,
            ping_interval=30,
            ping_timeout=60,
            open_timeout=15,
        ) as ws:
            self._ws = ws
            try:
                await self._send(
                    ws,
                    Frame(
                        type="hello",
                        data=(await self._build_hello(last_server_seq)).model_dump(mode="json"),
                    ),
                )
                raw = await asyncio.wait_for(ws.recv(), 10)
                frame = decode_frame(raw)
                if frame.type != "welcome":
                    raise WireError("expected_welcome")
                welcome = AgentWelcome.model_validate(frame.data)
                self.welcome = welcome
                await self.store.outbox_ack(welcome.resume_from)
                await self._on_welcome(welcome)
                self.connected.set()
                receiver = SeqReceiver(last_server_seq)
                sent_upto = welcome.resume_from
                tasks = [
                    asyncio.create_task(self._reader(ws, receiver)),
                    asyncio.create_task(self._pump(ws, sent_upto)),
                    asyncio.create_task(self._heartbeats(ws)),
                ]
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
                for t in done:
                    t.result()
                return True
            except ConnectionClosed as e:
                code = e.rcvd.code if e.rcvd else None
                if code == CLOSE_REVOKED:
                    raise Revoked from e
                if code == CLOSE_TOKEN_ROTATED:
                    raise TokenRotated from e
                raise
            finally:
                self._ws = None

    async def _reader(self, ws: ClientConnection, receiver: SeqReceiver) -> None:
        async for raw in ws:
            frame = decode_frame(raw)
            if frame.ack is not None:
                await self.store.outbox_ack(frame.ack)
            if frame.type == "cmd":
                if receiver.accept(frame):
                    await self.store.kv_set("in_seq", receiver.last_seq)
                    await self._on_cmd(frame)
                await self._send(ws, Frame(type="ack", ack=receiver.last_seq))

    async def _pump(self, ws: ClientConnection, sent_upto: int) -> None:
        while True:
            self._wake.clear()
            for f in await self.store.outbox_after(sent_upto):
                await self._send(ws, f)
                sent_upto = f.seq or sent_upto
            if self._stop.is_set():
                return
            await self._wake.wait()

    async def _heartbeats(self, ws: ClientConnection) -> None:
        while True:
            await self._send(ws, Frame(type="heartbeat", data=await self._build_hb()))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), self._hb_s)
            if self._stop.is_set():
                return
