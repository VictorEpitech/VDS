"""Résumé de parc (Accueil) : compteurs en lecture seule."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from vds_sdk import ScopeRef

from vds_server.config.reader import ModuleConfigReader
from vds_server.config.schemas import CORE_NAMESPACE
from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.core.runtime import Runtime
from vds_server.db.models import Site
from vds_server.fleet import playback, service
from vds_server.fleet.schemas import FleetSummaryOut
from vds_server.provisioning.apk_update import ApkUpdateService, FleetApkOut
from vds_server.supervision.status import Thresholds


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1/fleet", tags=["fleet"])
    Session = Annotated[AsyncSession, Depends(rt.session)]
    Reader = Annotated[Principal, Depends(rt.pipeline.require("editor"))]
    Admin = Annotated[Principal, Depends(rt.pipeline.require("admin"))]

    @router.get("/summary", response_model=FleetSummaryOut)
    async def fleet_summary(
        session: Session, _: Reader, site_id: UUID | None = None
    ) -> FleetSummaryOut:
        if site_id is not None:
            site = await session.get(Site, site_id)
            if site is None or site.archived_at is not None:
                raise ApiError("site.not_found", 404)
        th = Thresholds.from_config(await rt.core_config())
        return await service.summary(session, site_id, datetime.now(UTC), th)

    @router.get("/playback", response_model=list[playback.ScreenPlaybackOut])
    async def fleet_playback(
        session: Session, _: Reader, site_id: UUID | None = None
    ) -> list[playback.ScreenPlaybackOut]:
        if site_id is not None:
            site = await session.get(Site, site_id)
            if site is None or site.archived_at is not None:
                raise ApiError("site.not_found", 404)

        async def timezone_of(screen_id: UUID) -> object:
            cfg = await ModuleConfigReader(rt.maker, rt.schemas, CORE_NAMESPACE).resolve(
                ScopeRef(level="screen", id=str(screen_id))
            )
            return cfg.get("timezone")

        return await playback.fleet_playback(session, site_id, datetime.now(UTC), timezone_of)

    @router.get("/apk", response_model=FleetApkOut)
    async def fleet_apk(session: Session, actor: Admin, site_id: UUID | None = None) -> FleetApkOut:
        svc = ApkUpdateService(
            session,
            rt.bus,
            actor,
            rt.command_queue,
            rt.apk_catalog,
            rt.registry.update_modules,
            rt.apk_update_host,
        )
        return await svc.fleet(site_id)

    return router
