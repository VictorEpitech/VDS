"""Maintenance temporaire du kiosque d'un écran (admin, contrôle de même origine global)."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.core.auth import Principal
from vds_server.core.runtime import Runtime
from vds_server.player.kiosk import MAX_MAINTENANCE_MIN, MIN_MAINTENANCE_MIN, KioskService


class MaintenanceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    duration_min: int = Field(ge=MIN_MAINTENANCE_MIN, le=MAX_MAINTENANCE_MIN)
    reason: str | None = Field(default=None, max_length=200)


class MaintenanceOut(BaseModel):
    until: datetime


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1/screens", tags=["kiosk"])
    Session = Annotated[AsyncSession, Depends(rt.session)]
    Admin = Annotated[Principal, Depends(rt.pipeline.require("admin"))]

    @router.post("/{screen_id}/kiosk/maintenance", response_model=MaintenanceOut)
    async def start(
        screen_id: UUID, body: MaintenanceIn, actor: Admin, session: Session
    ) -> MaintenanceOut:
        until = await KioskService(session, rt.bus).start_maintenance(
            actor, screen_id, body.duration_min, body.reason
        )
        return MaintenanceOut(until=until)

    @router.delete("/{screen_id}/kiosk/maintenance", status_code=204)
    async def end(screen_id: UUID, actor: Admin, session: Session) -> Response:
        await KioskService(session, rt.bus).end_maintenance(actor, screen_id)
        return Response(status_code=204)

    return router
