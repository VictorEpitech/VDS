"""Découverte LAN des écrans (admin) : capacités, lancement, lecture du dernier scan."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.core.auth import Principal
from vds_server.core.runtime import Runtime
from vds_server.discovery import schemas as sc
from vds_server.discovery.service import DiscoveryService


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1/sites/{site_id}/discovery", tags=["discovery"])
    Session = Annotated[AsyncSession, Depends(rt.session)]
    Admin = Annotated[Principal, Depends(rt.pipeline.require("admin"))]

    def svc(session: Session, actor: Admin) -> DiscoveryService:
        return DiscoveryService(
            session,
            rt.bus,
            actor,
            rt.command_queue,
            rt.registry.discovery_modules,
            rt.schemas,
            rt.registry.device_address_fields,
        )

    Svc = Annotated[DiscoveryService, Depends(svc)]

    @router.get("/capabilities", response_model=sc.CapabilitiesOut)
    async def capabilities(site_id: UUID, s: Svc) -> sc.CapabilitiesOut:
        return await s.capabilities(site_id)

    @router.post("/scans", response_model=sc.ScanCreated, status_code=202)
    async def create(site_id: UUID, s: Svc, body: sc.ScanCreate | None = None) -> sc.ScanCreated:
        row = await s.create(site_id)
        return sc.ScanCreated(scan_id=row.id, status=row.status)  # type: ignore[arg-type]

    @router.get("/scans/latest", response_model=sc.ScanOut)
    async def latest(site_id: UUID, s: Svc) -> sc.ScanOut:
        return await s.latest(site_id)

    @router.get("/scans/{scan_id}", response_model=sc.ScanOut)
    async def read(site_id: UUID, scan_id: UUID, s: Svc) -> sc.ScanOut:
        return await s.get(site_id, scan_id)

    return router
