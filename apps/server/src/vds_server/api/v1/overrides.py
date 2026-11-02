"""Routes du forçage (`/api/v1/overrides`) : rôle editor ; extinction/organisation : admin."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.core.auth import Principal
from vds_server.core.runtime import Runtime
from vds_server.diffusion.schemas import TargetType
from vds_server.overrides import schemas as sc
from vds_server.overrides.service import OverrideService


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1/overrides", tags=["overrides"])
    Session = Annotated[AsyncSession, Depends(rt.session)]
    Editor = Annotated[Principal, Depends(rt.pipeline.require("editor"))]

    def service(session: Session, actor: Editor) -> OverrideService:
        return OverrideService(session, rt.bus, actor, rt.schemas, rt.registry.content_item_schema)

    Svc = Annotated[OverrideService, Depends(service)]

    @router.post("", response_model=sc.OverrideOut, status_code=201)
    async def create_override(body: sc.OverrideCreate, s: Svc) -> sc.OverrideOut:
        return await s.create(body)

    @router.post("/preview", response_model=sc.OverridePreviewOut)
    async def preview_override(body: sc.OverrideCreate, s: Svc) -> sc.OverridePreviewOut:
        return await s.preview(body)

    @router.get("", response_model=list[sc.OverrideOut])
    async def list_overrides(
        s: Svc,
        active: bool | None = None,
        site_id: UUID | None = None,
        target_type: TargetType | None = None,
        target_id: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[sc.OverrideOut]:
        return await s.list_all(
            active=active,
            site_id=site_id,
            target_type=target_type,
            target_id=target_id,
            limit=limit,
            offset=offset,
        )

    @router.get("/{override_id}", response_model=sc.OverrideOut)
    async def get_override(override_id: UUID, s: Svc) -> sc.OverrideOut:
        return await s.get(override_id)

    @router.patch("/{override_id}", response_model=sc.OverrideOut)
    async def patch_override(override_id: UUID, body: sc.OverridePatch, s: Svc) -> sc.OverrideOut:
        return await s.update(override_id, body)

    @router.post("/{override_id}/stop", response_model=sc.OverrideOut)
    async def stop_override(override_id: UUID, s: Svc) -> sc.OverrideOut:
        return await s.stop(override_id)

    return router
