"""`<base>/ws/player` : canal facultatif de réactivité (docs/architecture-lot3.md § 1.3).

Jeton d'appareil dans le premier message (5 s), `welcome`, puis push de `schedule.changed`
uniquement. Le polling HTTP reste la voie nominale : aucune donnée de programmation ne transite
ici. Pas de cookie, donc pas de CSWSH : l'`Origin` n'est pas imposé.
"""

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from vds_sdk.wire import (
    CLOSE_BAD_MESSAGE,
    CLOSE_HELLO_TIMEOUT,
    CLOSE_RATE_LIMITED,
    CLOSE_REVOKED,
    CLOSE_UNAUTHORIZED,
    CLOSE_VERSION_UNSUPPORTED,
    SUBPROTOCOL_PLAYER,
    Frame,
    PlayerAuth,
    PlayerWelcome,
    ScheduleChanged,
    WireError,
    decode_frame,
    encode_frame,
    parse_data,
)

from vds_server.core.runtime import Runtime
from vds_server.db.models import ScreenDevice
from vds_server.player.device_auth import TOKEN_PREFIX, hash_secret
from vds_server.player.factory import schedule_builder
from vds_server.player.schemas import Heartbeat
from vds_server.player.service import HEARTBEAT_INTERVAL_S, heartbeat

log = logging.getLogger("vds.ws.player")

AUTH_TIMEOUT_S = 5.0
CHECK_S = 10.0  # intervalle de recalcul de la révision (et de revérification du jeton)
MAX_FRAMES_PER_S = 20
HEARTBEAT_MIN_S = 10.0  # au plus une écriture de heartbeat toutes les 10 s par connexion


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter()

    async def device_state(token: str) -> tuple[str, ScreenDevice | None]:
        if not token.startswith(TOKEN_PREFIX):
            return "unauthorized", None
        async with rt.maker() as s:
            row = (
                await s.execute(
                    select(ScreenDevice).where(ScreenDevice.token_hash == hash_secret(token))
                )
            ).scalar_one_or_none()
        if row is None:
            return "unauthorized", None
        if row.revoked_at is not None or (
            row.expires_at is not None and row.expires_at <= datetime.now(UTC)
        ):
            return "revoked", row
        return "ok", row

    async def revision(screen_id: UUID) -> str | None:
        async with rt.maker() as s:
            body = await schedule_builder(rt, s).build(screen_id)
        return str(body["revision"])

    @router.websocket("/ws/player")
    async def ws_player(ws: WebSocket) -> None:
        offered = [p.strip() for p in ws.headers.get("sec-websocket-protocol", "").split(",")]
        ok = SUBPROTOCOL_PLAYER in offered
        await ws.accept(subprotocol=SUBPROTOCOL_PLAYER if ok else None)
        if not ok:
            await ws.close(code=CLOSE_VERSION_UNSUPPORTED)
            return
        try:
            raw = await asyncio.wait_for(ws.receive_text(), AUTH_TIMEOUT_S)
            frame = decode_frame(raw)
            auth = parse_data(frame, "player", "to_server")
            if frame.type != "auth" or not isinstance(auth, PlayerAuth):
                raise WireError("auth_required")
        except TimeoutError:
            await ws.close(code=CLOSE_HELLO_TIMEOUT)
            return
        except (WireError, WebSocketDisconnect):
            with contextlib.suppress(Exception):
                await ws.close(code=CLOSE_BAD_MESSAGE)
            return
        state, device = await device_state(auth.token)
        if device is None or state == "unauthorized":
            await ws.close(code=CLOSE_UNAUTHORIZED)
            return
        if state == "revoked":
            await ws.close(code=CLOSE_REVOKED)
            return
        screen_id = device.screen_id
        device_id = device.id
        try:
            current = await revision(screen_id)
        except Exception:
            log.warning("ws/player: revision unavailable")
            await ws.close(code=1011)
            return
        welcome = PlayerWelcome(
            device_id=device_id,
            screen_id=screen_id,
            heartbeat_s=HEARTBEAT_INTERVAL_S,
            server_time=datetime.now(UTC),
            revision=current,
        )
        await ws.send_text(
            encode_frame(Frame(type="welcome", data=welcome.model_dump(mode="json")))
        )
        await _serve(ws, auth.token, device_id, screen_id, current)

    async def _serve(
        ws: WebSocket, token: str, device_id: UUID, screen_id: UUID, last: str | None
    ) -> None:
        async def pusher() -> None:
            sent = last
            while True:
                await asyncio.sleep(CHECK_S)
                st, _ = await device_state(token)
                if st == "revoked":
                    await ws.close(code=CLOSE_REVOKED)
                    return
                if st != "ok":
                    await ws.close(code=CLOSE_UNAUTHORIZED)
                    return
                try:
                    rev = await revision(screen_id)
                except Exception:
                    log.warning("ws/player: revision unavailable")
                    continue
                if rev != sent:
                    sent = rev
                    data = ScheduleChanged(revision=str(rev)).model_dump()
                    await ws.send_text(encode_frame(Frame(type="schedule.changed", data=data)))

        async def reader() -> None:
            window_start, count = asyncio.get_event_loop().time(), 0
            last_hb = float("-inf")
            while True:
                raw = await ws.receive_text()
                now = asyncio.get_event_loop().time()
                if now - window_start >= 1:
                    window_start, count = now, 0
                count += 1
                if count > MAX_FRAMES_PER_S:
                    await ws.close(code=CLOSE_RATE_LIMITED)
                    return
                try:
                    f = decode_frame(raw)
                    data = parse_data(f, "player", "to_server")
                except WireError:
                    await ws.close(code=CLOSE_BAD_MESSAGE)
                    return
                if f.type == "heartbeat":
                    try:
                        hb = Heartbeat.model_validate(data)
                    except ValueError:
                        await ws.close(code=CLOSE_BAD_MESSAGE)
                        return
                    if now - last_hb < HEARTBEAT_MIN_S:
                        continue  # trop fréquent : ignoré, aucune écriture
                    last_hb = now
                    async with rt.maker() as s:
                        await heartbeat(s, device_id, hb.model_dump(mode="json"), hb.app_version)

        tasks = [asyncio.create_task(pusher()), asyncio.create_task(reader())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, Exception):
                    await t

    return router
