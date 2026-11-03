"""Routes des mises en page (`/api/v1/layouts`) : rôle editor (`layouts.read` / `layouts.write`)."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.core.auth import Principal
from vds_server.core.runtime import Runtime
from vds_server.diffusion import layout_schemas as ls
from vds_server.diffusion.layouts import LayoutService, revision_header
from vds_server.player.factory import schedule_builder


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1/layouts", tags=["layouts"])
    Session = Annotated[AsyncSession, Depends(rt.session)]
    Editor = Annotated[Principal, Depends(rt.pipeline.require("editor"))]

    def service(session: Session, actor: Editor) -> LayoutService:
        return LayoutService(session, rt.bus, actor, rt.schemas)

    Svc = Annotated[LayoutService, Depends(service)]

    @router.get("", response_model=ls.LayoutPage)
    async def list_layouts(
        s: Svc,
        q: Annotated[str | None, Query(max_length=120)] = None,
        tag: Annotated[str | None, Query(max_length=32)] = None,
        orientation: ls.Orientation | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    ) -> ls.LayoutPage:
        return await s.list_page(
            search=q, tag=tag, orientation=orientation, limit=limit, offset=offset
        )

    @router.post("", response_model=ls.LayoutOut, status_code=201)
    async def create_layout(body: ls.LayoutIn, s: Svc) -> ls.LayoutOut:
        return await s.create(body)

    @router.get("/{layout_id}", response_model=ls.LayoutOut)
    async def get_layout(layout_id: UUID, s: Svc, response: Response) -> ls.LayoutOut:
        out = await s.get(layout_id)
        response.headers["ETag"] = f'"{out.revision}"'
        return out

    @router.put("/{layout_id}", response_model=ls.LayoutOut)
    async def replace_layout(
        layout_id: UUID,
        body: ls.LayoutIn,
        s: Svc,
        response: Response,
        if_match: Annotated[str | None, Header()] = None,
    ) -> ls.LayoutOut:
        out = await s.update(layout_id, body, revision_header(if_match))
        response.headers["ETag"] = f'"{out.revision}"'
        return out

    @router.post("/{layout_id}/duplicate", response_model=ls.LayoutOut, status_code=201)
    async def duplicate_layout(layout_id: UUID, s: Svc) -> ls.LayoutOut:
        return await s.duplicate(layout_id)

    @router.delete("/{layout_id}", status_code=204)
    async def delete_layout(layout_id: UUID, s: Svc) -> Response:
        await s.delete(layout_id)
        return Response(status_code=204)

    @router.get("/{layout_id}/usage", response_model=ls.LayoutUsageOut)
    async def layout_usage(layout_id: UUID, s: Svc) -> ls.LayoutUsageOut:
        return await s.usage(layout_id)

    @router.get("/{layout_id}/preview")
    async def layout_preview(layout_id: UUID, session: Session, s: Svc) -> dict[str, object]:
        """ScheduleDocument à un créneau d'1 h pour l'aperçu admin (lecture seule, session)."""
        layout = await s.get(layout_id)  # périmètre + 404
        return await schedule_builder(rt, session).preview_layout(layout.id, datetime.now(UTC))

    return router
