"""Commandes player et captures (lot I) : routes admin (périmètre) et routes du player."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.commands import schemas as sc
from vds_server.commands.access import CommandAdmin
from vds_server.commands.screenshots import ScreenshotIngest, read_body, read_image
from vds_server.core.auth import Principal
from vds_server.core.runtime import Runtime
from vds_server.player.device_auth import DevicePrincipal
from vds_server.player.factory import screenshot_interval

IMAGE_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
    "Content-Disposition": "inline",
    "Content-Security-Policy": "default-src 'none'",
}


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["commands"])
    Session = Annotated[AsyncSession, Depends(rt.session)]
    Editor = Annotated[Principal, Depends(rt.pipeline.require("editor"))]
    Device = Annotated[DevicePrincipal, Depends(rt.device_auth)]

    def admin_svc(session: Session, actor: Editor) -> CommandAdmin:
        return CommandAdmin(session, rt.bus, actor, rt.restart_context, rt.command_queue)

    Svc = Annotated[CommandAdmin, Depends(admin_svc)]

    @router.post(
        "/screens/{screen_id}/commands",
        response_model=sc.PlayerCommandOut,
        status_code=201,
        responses={200: {"model": sc.PlayerCommandOut}},
    )
    async def create_command(
        screen_id: UUID, body: sc.PlayerCommandCreate, s: Svc, response: Response
    ) -> sc.PlayerCommandOut:
        out, created = await s.create(screen_id, body)
        if not created:
            response.status_code = 200
        return out

    @router.get("/screens/{screen_id}/commands", response_model=list[sc.PlayerCommandOut])
    async def list_commands(
        screen_id: UUID, s: Svc, limit: Annotated[int, Query(ge=1, le=50)] = 20
    ) -> list[sc.PlayerCommandOut]:
        return await s.recent(screen_id, limit)

    @router.get("/player-commands", response_model=list[sc.PlayerCommandOut])
    async def poll_commands(
        s: Svc, ids: Annotated[str, Query(max_length=8000)]
    ) -> list[sc.PlayerCommandOut]:
        return await s.poll(ids)

    @router.post(
        "/screens/{screen_id}/commands/{command_id}/cancel", response_model=sc.PlayerCommandOut
    )
    async def cancel_command(screen_id: UUID, command_id: UUID, s: Svc) -> sc.PlayerCommandOut:
        return await s.cancel(screen_id, command_id)

    @router.get("/screens/{screen_id}/screenshots", response_model=list[sc.ScreenshotOut])
    async def list_screenshots(
        screen_id: UUID, s: Svc, limit: Annotated[int, Query(ge=1, le=50)] = 10
    ) -> list[sc.ScreenshotOut]:
        return await s.screenshots(screen_id, limit)

    @router.get(
        "/screenshots/{screenshot_id}/image",
        response_class=Response,
        responses={200: {"content": {"image/jpeg": {}}}},
    )
    async def screenshot_image(screenshot_id: UUID, s: Svc) -> Response:
        row = await s.screenshot(screenshot_id)  # périmètre : 404 sinon ; consultation non auditée
        data = await read_image(rt.storage_backend(), row)
        return Response(data, media_type="image/jpeg", headers=IMAGE_HEADERS)

    # --- player (jeton d'appareil) ---------------------------------------------------------
    def ingest(session: Session) -> ScreenshotIngest:
        async def interval_of(screen_id: UUID) -> int:
            return await screenshot_interval(rt, screen_id)

        return ScreenshotIngest(
            session, rt.bus, rt.storage_backend, interval_of, rt.screenshot_gate
        )

    Ingest = Annotated[ScreenshotIngest, Depends(ingest)]

    async def upload(
        svc: ScreenshotIngest, device: DevicePrincipal, request: Request, cmd_id: UUID | None,
        captured_at: str | None,
    ) -> sc.ScreenshotCreated:  # fmt: skip
        data = await read_body(request)
        shot = await svc.ingest(device.device_id, device.screen_id, cmd_id, data, captured_at)
        return sc.ScreenshotCreated(screenshot_id=shot)

    @router.post("/player/screenshots", response_model=sc.ScreenshotCreated, status_code=201)
    async def upload_periodic(
        request: Request,
        device: Device,
        svc: Ingest,
        x_vds_captured_at: Annotated[str | None, Header()] = None,
    ) -> sc.ScreenshotCreated:
        return await upload(svc, device, request, None, x_vds_captured_at)

    @router.post(
        "/player/screenshots/{cmd_id}", response_model=sc.ScreenshotCreated, status_code=201
    )
    async def upload_on_demand(
        cmd_id: UUID,
        request: Request,
        device: Device,
        svc: Ingest,
        x_vds_captured_at: Annotated[str | None, Header()] = None,
    ) -> sc.ScreenshotCreated:
        return await upload(svc, device, request, cmd_id, x_vds_captured_at)

    return router
