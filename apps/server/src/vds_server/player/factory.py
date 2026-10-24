"""Fabrique du ScheduleBuilder, partagée par `GET player/schedule` et `/ws/player`."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from vds_sdk import ScopeRef

from vds_server.config.reader import ModuleConfigReader
from vds_server.config.schemas import CORE_NAMESPACE
from vds_server.core.runtime import Runtime
from vds_server.db.models import Screen
from vds_server.player import kiosk
from vds_server.player.schedule import ScheduleBuilder
from vds_server.power.plan import PowerPlanBuilder


def schedule_builder(rt: Runtime, session: AsyncSession) -> ScheduleBuilder:
    async def timezone_of(screen_id: UUID) -> object:
        cfg = await ModuleConfigReader(rt.maker, rt.schemas, CORE_NAMESPACE).resolve(
            ScopeRef(level="screen", id=str(screen_id))
        )
        return cfg.get("timezone")

    async def power_of(screen_id: UUID, now: datetime) -> list[dict[str, str]]:
        return await PowerPlanBuilder(session, rt.schemas).player_slots(screen_id, now)

    async def kiosk_of(screen: Screen, now: datetime) -> dict[str, Any]:
        request = (await kiosk.load_requests(session, [screen]))[screen.id]
        if request.until is not None and request.until <= now:
            await kiosk.KioskService(session, rt.bus).expire(screen.id, request.until)
        return kiosk.served(request, now)

    return ScheduleBuilder(
        session,
        rt.registry.content_item_schema,
        rt.registry.content_entry,
        timezone_of,
        rt.registry.instance,
        power_of,
        kiosk_of,
    )
