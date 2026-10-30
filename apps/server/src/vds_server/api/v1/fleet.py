"""Résumé de parc (Accueil) : compteurs en lecture seule."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.core.runtime import Runtime
from vds_server.db.models import Site
from vds_server.fleet import service
from vds_server.fleet.schemas import FleetSummaryOut
from vds_server.supervision.status import Thresholds


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1/fleet", tags=["fleet"])
    Session = Annotated[AsyncSession, Depends(rt.session)]
    Reader = Annotated[Principal, Depends(rt.pipeline.require("editor"))]

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

    return router
