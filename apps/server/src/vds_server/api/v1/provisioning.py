"""Assistant de provisioning d'écran (admin) et service d'APK à l'agent de site."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.agents.auth import AgentPrincipal
from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.core.runtime import Runtime
from vds_server.provisioning import schemas as sc
from vds_server.provisioning.service import ProvisionService, to_out

APK_MIME = "application/vnd.android.package-archive"


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["provisioning"])
    Session = Annotated[AsyncSession, Depends(rt.session)]
    Admin = Annotated[Principal, Depends(rt.pipeline.require("admin"))]

    def svc(session: Session, actor: Admin) -> ProvisionService:
        return ProvisionService(
            session,
            rt.bus,
            actor,
            rt.command_queue,
            rt.settings,
            rt.apk_catalog,
            rt.ticket_box,
            rt.registry.provision_modules,
        )

    Svc = Annotated[ProvisionService, Depends(svc)]

    @router.get("/provisioning/capabilities", response_model=sc.CapabilitiesOut)
    async def capabilities(site_id: Annotated[UUID, Query()], s: Svc) -> sc.CapabilitiesOut:
        return await s.capabilities(site_id)

    @router.post("/screens/{screen_id}/provision", response_model=sc.ProvisionOut, status_code=201)
    async def create(screen_id: UUID, body: sc.ProvisionCreate, s: Svc) -> sc.ProvisionOut:
        return to_out(await s.create(screen_id, body))

    @router.get("/provisioning/{pid}", response_model=sc.ProvisionOut)
    async def read(pid: UUID, s: Svc) -> sc.ProvisionOut:
        return to_out(await s.get(pid))

    @router.post("/provisioning/{pid}/retry", response_model=sc.ProvisionOut)
    async def retry(pid: UUID, body: sc.ProvisionRetry, s: Svc) -> sc.ProvisionOut:
        return to_out(await s.retry(pid, body))

    @router.post("/provisioning/{pid}/cancel", response_model=sc.ProvisionOut)
    async def cancel(pid: UUID, s: Svc) -> sc.ProvisionOut:
        return to_out(await s.cancel(pid))

    @router.get(
        "/agent/apk/android/{version_code}",
        response_class=FileResponse,
        responses={200: {"content": {APK_MIME: {}}}, 304: {"description": "Inchangé"}},
    )
    async def apk(
        request: Request,
        version_code: Annotated[int, Path(ge=1, le=2**31 - 1)],
        _agent: Annotated[AgentPrincipal, Depends(rt.agent_auth)],
    ) -> Response:
        entry = rt.apk_catalog.get(version_code)
        if entry is None:
            raise ApiError("provision.apk_unavailable", 404)
        etag = f'"{entry.sha256}"'
        headers = {"ETag": etag, "Cache-Control": "private, no-store"}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return FileResponse(entry.path, media_type=APK_MIME, headers=headers)

    return router
