"""Appairage, révocation et heartbeat des appareils."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.core.runtime import Runtime
from vds_server.player import schemas as sc
from vds_server.player.device_auth import DevicePrincipal, bearer
from vds_server.player.factory import schedule_builder
from vds_server.player.ratelimit import RateLimiter
from vds_server.player.schedule import SCHEDULE_FORMAT, ScheduleBuilder
from vds_server.player.service import (
    HEARTBEAT_INTERVAL_S,
    POLL_INTERVAL_S,
    PairingService,
    format_code,
    heartbeat,
)
from vds_server.security.client_ip import client_ip


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["player"])
    Session = Annotated[AsyncSession, Depends(rt.session)]
    Admin = Annotated[Principal, Depends(rt.pipeline.require("admin"))]
    Device = Annotated[DevicePrincipal, Depends(rt.device_auth)]
    start_limiter = RateLimiter(limit=10, window_s=60)
    ticket_limiter = RateLimiter(limit=10, window_s=60)
    poll_limiter = RateLimiter(limit=40, window_s=60)

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
        token, screen_id = await s.pair_with_ticket(body.pairing_ticket, body.device)
        response.headers["Cache-Control"] = "no-store"
        return sc.PairingDone(
            device_token=token, screen_id=screen_id, server_time=datetime.now(UTC)
        )

    @router.post("/pairings/claim", response_model=sc.PairingClaimed)
    async def claim(body: sc.PairingClaim, actor: Admin, s: Pairing) -> sc.PairingClaimed:
        return sc.PairingClaimed(screen_id=await s.claim(actor, body.code, body.screen_id))

    @router.delete("/screens/{screen_id}/device", status_code=204)
    async def revoke_device(screen_id: UUID, actor: Admin, s: Pairing) -> Response:
        await s.revoke(actor, screen_id)
        return Response(status_code=204)

    @router.post("/player/heartbeat", response_model=sc.HeartbeatAck)
    async def player_heartbeat(
        body: sc.Heartbeat, device: Device, session: Session, b: Builder
    ) -> sc.HeartbeatAck:
        await heartbeat(
            session, device.device_id, body.model_dump(mode="json"), body.app_version, rt.bus
        )
        current = await b.build(device.screen_id)
        return sc.HeartbeatAck(
            revision=str(current["revision"]),
            server_time=datetime.now(UTC),
            poll_interval_s=HEARTBEAT_INTERVAL_S,
            commands=[],
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
        etag = f'"{body["revision"]}"'
        headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
        if _etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers=headers)
        return JSONResponse(body, headers=headers)

    return router


def _etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    tags = [t.strip().removeprefix("W/") for t in header.split(",")]
    return "*" in tags or etag in tags
