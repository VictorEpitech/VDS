"""Routes du planning admin : lecture editor/admin, simulation sans écriture."""

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.core.auth import Principal
from vds_server.core.runtime import Runtime
from vds_server.diffusion import planning_schemas as ps
from vds_server.diffusion.planning import PlanningService
from vds_server.diffusion.schedules import ScheduleService
from vds_server.diffusion.schemas import TargetType


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1/planning", tags=["planning"])
    Session = Annotated[AsyncSession, Depends(rt.session)]
    Editor = Annotated[Principal, Depends(rt.pipeline.require("editor"))]

    def service(session: Session, actor: Editor) -> PlanningService:
        return PlanningService(
            session, rt.schemas, ScheduleService(session, None, actor), actor=actor
        )

    Svc = Annotated[PlanningService, Depends(service)]
    Limit = Annotated[int, Query(ge=1, le=ps.MAX_SCREENS_PER_PAGE)]

    @router.get("/timeline", response_model=ps.TimelineOut)
    async def timeline(
        s: Svc,
        response: Response,
        target_type: TargetType,
        target_id: UUID,
        from_: Annotated[date, Query(alias="from")],
        to: date,
        explain: bool | None = None,
        cursor: Annotated[str | None, Query(max_length=400)] = None,
        limit: Limit = ps.DEFAULT_SCREENS_PER_PAGE,
        if_none_match: Annotated[str | None, Header(max_length=1000)] = None,
    ) -> ps.TimelineOut | Response:
        etag, out = await s.timeline(
            target_type=target_type, target_id=target_id, start=from_, end=to, explain=explain,
            cursor=cursor, limit=limit, if_none_match=if_none_match,
        )  # fmt: skip
        headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
        if out is None:
            return Response(status_code=304, headers=headers)
        response.headers.update(headers)
        return out

    @router.get("/at", response_model=ps.AtOut)
    async def at(
        s: Svc,
        target_type: TargetType,
        target_id: UUID,
        at: datetime | None = None,
        cursor: Annotated[str | None, Query(max_length=400)] = None,
        limit: Limit = ps.DEFAULT_SCREENS_PER_PAGE,
    ) -> ps.AtOut:
        return await s.at(
            target_type=target_type, target_id=target_id, at=at, cursor=cursor, limit=limit
        )

    @router.post("/simulate", response_model=ps.SimulateOut)
    async def simulate(body: ps.SimulateIn, s: Svc) -> ps.SimulateOut:
        return await s.simulate(body)

    return router
