"""Appairage, révocation et heartbeat des appareils."""

import hashlib
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.commands.service import CommandCore, ref_of
from vds_server.core import audit
from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.core.runtime import Runtime
from vds_server.feeds import schemas as fsc
from vds_server.feeds.access import feed_ids_of
from vds_server.inventory import schemas as isc
from vds_server.inventory.devices import screens_out
from vds_server.player import schemas as sc
from vds_server.player.device_auth import DevicePrincipal, bearer
from vds_server.player.enroll import pair_new_screen
from vds_server.player.factory import schedule_builder
from vds_server.player.ratelimit import FailureLimiter, RateLimiter
from vds_server.player.schedule import SCHEDULE_FORMAT, ScheduleBuilder
from vds_server.player.service import (
    HEARTBEAT_INTERVAL_S,
    POLL_INTERVAL_S,
    PairingService,
    format_code,
    heartbeat,
)
from vds_server.security.client_ip import client_ip

FEED_ID_RE = re.compile(r"[0-9a-f]{32}")
CLAIM_FAILURES = frozenset({404, 409, 410})


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["player"])
    Session = Annotated[AsyncSession, Depends(rt.session)]
    Admin = Annotated[Principal, Depends(rt.pipeline.require("admin"))]
    Device = Annotated[DevicePrincipal, Depends(rt.device_auth)]
    start_limiter = RateLimiter(limit=10, window_s=60)
    ticket_limiter = RateLimiter(limit=10, window_s=60)
    poll_limiter = RateLimiter(limit=40, window_s=60)
    # échecs (404/409/410) par utilisateur, partagé claim + screens/pair ; par processus
    claim_limiter = FailureLimiter(limit=30, window_s=60)

    async def guarded_claim[T](actor: Principal, via: str, call: Callable[[], Awaitable[T]]) -> T:
        res = claim_limiter.reserve(f"user:{actor.actor_id}")
        counted = False
        try:
            return await call()
        except ApiError as exc:
            counted = exc.status in CLAIM_FAILURES
            if counted:  # session séparée : persiste malgré le rollback ; jamais le code
                async with rt.maker() as audit_session:
                    audit.record(
                        audit_session,
                        actor_kind="user",
                        actor_id=actor.actor_id,
                        action="pairing.claim_failed",
                        target_type="pairing",
                        details={"via": via, "status": exc.status, "code": exc.code},
                    )
                    await audit_session.commit()
            raise
        finally:
            if not counted:
                claim_limiter.release(res)

    def svc(session: Session) -> PairingService:
        return PairingService(session, rt.bus)

    Pairing = Annotated[PairingService, Depends(svc)]

    def builder(session: Session) -> ScheduleBuilder:
        return schedule_builder(rt, session)

    Builder = Annotated[ScheduleBuilder, Depends(builder)]

    @router.post("/player/pairings", response_model=sc.PairingStarted, status_code=201)
    async def start_pairing(
        body: sc.PairingStart, request: Request, s: Pairing
    ) -> sc.PairingStarted:
        start_limiter.check(client_ip(request))
        row, secret = await s.start(body.device)
        return sc.PairingStarted(
            pairing_id=row.id,
            code=format_code(row.code),
            poll_secret=secret,
            expires_at=row.expires_at,
            poll_interval_s=POLL_INTERVAL_S,
        )

    @router.get(
        "/player/pairings/{pairing_id}",
        response_model=sc.PairingDone | sc.PairingPending,
        responses={202: {"model": sc.PairingPending}},
    )
    async def poll_pairing(
        pairing_id: UUID, request: Request, response: Response, s: Pairing
    ) -> sc.PairingDone | sc.PairingPending:
        poll_limiter.check(f"{client_ip(request)}:{pairing_id}")
        state, token, screen_id = await s.poll(pairing_id, bearer(request))
        response.headers["Cache-Control"] = "no-store"
        if state == "pending" or token is None or screen_id is None:
            response.status_code = 202
            return sc.PairingPending()
        return sc.PairingDone(
            device_token=token, screen_id=screen_id, server_time=datetime.now(UTC)
        )

    @router.post("/player/pair", response_model=sc.PairingDone)
    async def pair_with_ticket(
        body: sc.TicketPair, request: Request, response: Response, s: Pairing
    ) -> sc.PairingDone:
        ticket_limiter.check(client_ip(request))
        token, screen_id = await s.pair_with_ticket(
            body.pairing_ticket,
            body.device,
            client_ip(request),
            lan_check=rt.settings.provision_lan_check,
        )
        response.headers["Cache-Control"] = "no-store"
        return sc.PairingDone(
            device_token=token, screen_id=screen_id, server_time=datetime.now(UTC)
        )

    @router.post("/pairings/claim", response_model=sc.PairingClaimed)
    async def claim(body: sc.PairingClaim, actor: Admin, s: Pairing) -> sc.PairingClaimed:
        screen_id = await guarded_claim(
            actor, "pairings.claim", lambda: s.claim(actor, body.code, body.screen_id)
        )
        return sc.PairingClaimed(screen_id=screen_id)

    @router.post(
        "/screens/pair",
        response_model=isc.ScreenPaired,
        status_code=201,
        responses={200: {"model": isc.ScreenPaired}},
        tags=["inventory"],
    )
    async def pair_screen(
        body: isc.ScreenPair, actor: Admin, session: Session, response: Response
    ) -> isc.ScreenPaired:
        screen, row, replayed = await guarded_claim(
            actor, "screens.pair", lambda: pair_new_screen(session, rt.bus, actor, body)
        )
        if replayed:
            response.status_code = 200
        out = (await screens_out(session, [screen]))[0]
        return isc.ScreenPaired(
            screen=out,
            pairing=isc.PairingStateOut(
                status="consumed" if row.status == "consumed" else "claimed",
                expires_at=row.expires_at,
            ),
            replayed=replayed,
        )

    @router.delete("/screens/{screen_id}/device", status_code=204)
    async def revoke_device(screen_id: UUID, actor: Admin, s: Pairing) -> Response:
        await s.revoke(actor, screen_id)
        return Response(status_code=204)

    @router.post("/player/heartbeat", response_model=sc.HeartbeatAck)
    async def player_heartbeat(
        body: sc.Heartbeat, device: Device, session: Session, b: Builder
    ) -> sc.HeartbeatAck:
        await heartbeat(
            session,
            device.device_id,
            body.model_dump(mode="json", exclude={"command_results"}),
            body.app_version,
            rt.bus,
            body.app_version_code,
        )
        core = CommandCore(session, rt.bus)
        acked = await core.apply_results(device.device_id, body.command_results)
        pending = await core.pending(device.device_id)  # repli HTTP de la trame `cmd`
        await core.mark_sent(pending)
        current = await b.build(device.screen_id)
        return sc.HeartbeatAck(
            revision=str(current["revision"]),
            server_time=datetime.now(UTC),
            poll_interval_s=HEARTBEAT_INTERVAL_S,
            commands=[ref_of(r) for r in pending],
            results_acked=acked,
        )

    @router.get(
        "/player/schedule",
        response_model=sc.ScheduleDocument,
        responses={304: {"description": "Programmation inchangée (If-None-Match)"}},
    )
    async def player_schedule(
        request: Request,
        device: Device,
        b: Builder,
        format: Annotated[int, Query(ge=1)] = SCHEDULE_FORMAT,  # noqa: A002
    ) -> Response:
        if format != SCHEDULE_FORMAT:
            raise ApiError("errors.schedule_format_unsupported", 400, supported=[SCHEDULE_FORMAT])
        body = await b.build(device.screen_id)
        rt.feed_access.put(device.screen_id, feed_ids_of(body))  # alimente le cache des flux
        etag = f'"{body["revision"]}"'
        headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
        if _etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers=headers)
        return JSONResponse(body, headers=headers)

    @router.get(
        "/player/feeds/{feed_id}",
        response_model=fsc.FeedOut,
        responses={
            204: {"description": "Flux connu mais jamais récupéré avec succès"},
            304: {"description": "Données inchangées (If-None-Match)"},
            404: {"description": "feeds.unknown : flux absent de la programmation de l'écran"},
        },
    )
    async def player_feed(
        feed_id: str,
        request: Request,
        device: Device,
        b: Builder,
    ) -> Response:
        """Dernier état connu d'un flux, lu par le player sur son propre serveur."""
        row = await rt.feeds.get(feed_id) if FEED_ID_RE.fullmatch(feed_id) else None
        if row is None:
            raise ApiError("feeds.unknown", 404)
        # le flux doit figurer dans la programmation résolue de CET écran (journée + lendemain)
        allowed = rt.feed_access.get(device.screen_id)
        if allowed is None:  # absent ou périmé : une seule reconstruction, puis cache
            allowed = frozenset(feed_ids_of(await b.build(device.screen_id)))
            rt.feed_access.put(device.screen_id, allowed)
        if feed_id not in allowed:
            raise ApiError("feeds.unknown", 404)
        if row.data is None or row.fetched_at is None:
            return Response(status_code=204, headers={"Cache-Control": "private, no-cache"})
        stamp = row.fetched_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        etag = '"' + hashlib.sha256(f"{feed_id}:{stamp}".encode()).hexdigest()[:24] + '"'
        headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
        if _etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers=headers)
        out = fsc.FeedOut(feed_id=feed_id, fetched_at=row.fetched_at, data=row.data)
        return JSONResponse(out.model_dump(mode="json"), headers=headers)

    return router


def _etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    tags = [t.strip().removeprefix("W/") for t in header.split(",")]
    return "*" in tags or etag in tags
