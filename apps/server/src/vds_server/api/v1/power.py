"""Routes du planning d'alimentation : plans, exceptions, calendriers, plan résolu d'un écran."""

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.config.api import OriginOut
from vds_server.core.auth import Principal
from vds_server.core.runtime import Runtime
from vds_server.diffusion.schemas import TargetType
from vds_server.power import resolver
from vds_server.power import schemas as sc
from vds_server.power.plan import PowerPlanBuilder
from vds_server.power.service import PowerService


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1/power", tags=["power"])
    Session = Annotated[AsyncSession, Depends(rt.session)]
    Editor = Annotated[Principal, Depends(rt.pipeline.require("editor"))]
    Admin = Annotated[Principal, Depends(rt.pipeline.require("admin"))]

    def svc(session: Session, actor: Editor) -> PowerService:
        return PowerService(session, rt.bus, actor)

    def admin_svc(session: Session, actor: Admin) -> PowerService:
        return PowerService(session, rt.bus, actor)

    def builder(session: Session, _: Editor) -> PowerPlanBuilder:
        return PowerPlanBuilder(session, rt.schemas)

    Svc = Annotated[PowerService, Depends(svc)]
    AdminSvc = Annotated[PowerService, Depends(admin_svc)]
    Builder = Annotated[PowerPlanBuilder, Depends(builder)]

    # --- plans -----------------------------------------------------------
    @router.get("/plans", response_model=list[sc.PlanOut])
    async def list_plans(s: Svc) -> list[sc.PlanOut]:
        return await s.list_plans()

    @router.post("/plans", response_model=sc.PlanOut, status_code=201)
    async def create_plan(body: sc.PlanCreate, s: Svc) -> sc.PlanOut:
        return await s.create_plan(body)

    @router.get("/plans/{plan_id}", response_model=sc.PlanOut)
    async def get_plan(plan_id: UUID, s: Svc) -> sc.PlanOut:
        return await s.get_plan(plan_id)

    @router.patch("/plans/{plan_id}", response_model=sc.PlanOut)
    async def patch_plan(plan_id: UUID, body: sc.PlanPatch, s: Svc) -> sc.PlanOut:
        return await s.update_plan(plan_id, body)

    @router.delete("/plans/{plan_id}", status_code=204)
    async def delete_plan(plan_id: UUID, s: Svc) -> Response:
        await s.delete_plan(plan_id)
        return Response(status_code=204)

    # --- exceptions ------------------------------------------------------
    @router.get("/exceptions", response_model=list[sc.ExceptionOut])
    async def list_exceptions(
        s: Svc,
        scope_level: TargetType | None = None,
        scope_id: UUID | None = None,
        on_or_after: date | None = None,
        on_or_before: date | None = None,
    ) -> list[sc.ExceptionOut]:
        return await s.list_exceptions(
            scope_level=scope_level,
            scope_id=scope_id,
            on_or_after=on_or_after,
            on_or_before=on_or_before,
        )

    @router.post("/exceptions", response_model=sc.ExceptionOut, status_code=201)
    async def create_exception(body: sc.ExceptionCreate, s: Svc) -> sc.ExceptionOut:
        return await s.create_exception(body)

    @router.get("/exceptions/{exception_id}", response_model=sc.ExceptionOut)
    async def get_exception(exception_id: UUID, s: Svc) -> sc.ExceptionOut:
        return await s.get_exception(exception_id)

    @router.put("/exceptions/{exception_id}", response_model=sc.ExceptionOut)
    async def replace_exception(
        exception_id: UUID, body: sc.ExceptionReplace, s: Svc
    ) -> sc.ExceptionOut:
        return await s.replace_exception(exception_id, body)

    @router.delete("/exceptions/{exception_id}", status_code=204)
    async def delete_exception(exception_id: UUID, s: Svc) -> Response:
        await s.delete_exception(exception_id)
        return Response(status_code=204)

    # --- calendriers (lecture éditeur, écriture admin) --------------------
    @router.get("/calendars", response_model=list[sc.CalendarOut])
    async def list_calendars(s: Svc) -> list[sc.CalendarOut]:
        return await s.list_calendars()

    @router.post("/calendars", response_model=sc.CalendarOut, status_code=201)
    async def create_calendar(body: sc.CalendarCreate, s: AdminSvc) -> sc.CalendarOut:
        return await s.create_calendar(body)

    @router.delete("/calendars/{key}", status_code=204)
    async def delete_calendar(key: str, s: AdminSvc) -> Response:
        await s.delete_calendar(key)
        return Response(status_code=204)

    @router.get("/calendars/{key}/days", response_model=list[sc.CalendarDayOut])
    async def list_days(
        key: str, s: Svc, start: date | None = None, end: date | None = None
    ) -> list[sc.CalendarDayOut]:
        return await s.list_days(key, start, end)

    @router.put("/calendars/{key}/days/{day}", response_model=sc.CalendarDayOut)
    async def put_day(
        key: str, day: date, body: sc.CalendarDayIn, s: AdminSvc
    ) -> sc.CalendarDayOut:
        return await s.put_day(key, day, body)

    @router.delete("/calendars/{key}/days/{day}", status_code=204)
    async def delete_day(key: str, day: date, s: AdminSvc) -> Response:
        await s.delete_day(key, day)
        return Response(status_code=204)

    # --- plan résolu d'un écran (le rattachement passe par /config, clés power.*) ---
    @router.get("/screens/{screen_id}", response_model=sc.ScreenPowerOut)
    async def screen_power(
        screen_id: UUID, b: Builder, at: Annotated[datetime | None, Query()] = None
    ) -> sc.ScreenPowerOut:
        r = await b.for_screen(screen_id, at)
        wire = resolver.to_wire(r.slots)
        return sc.ScreenPowerOut(
            screen_id=r.screen_id,
            timezone=r.timezone,
            plan_id=r.plan_id,
            plan_id_origin=_o(r.plan_id_origin),
            calendars=r.calendars,
            calendars_origin=_o(r.calendars_origin),
            slots=[sc.SlotOut.model_validate(w) for w in wire],
        )

    return router


def _o(o: object) -> OriginOut:
    return OriginOut(level=o.level, id=o.id, name=o.name)  # type: ignore[attr-defined]
