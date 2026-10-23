"""Routes du parc : organisation, sites, groupes, écrans (écriture admin, lecture éditeur)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.core.auth import Principal
from vds_server.core.runtime import Runtime
from vds_server.inventory import schemas as sc
from vds_server.inventory.service import InventoryService


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["inventory"])
    Session = Annotated[AsyncSession, Depends(rt.session)]
    Reader = Annotated[Principal, Depends(rt.pipeline.require("editor"))]
    Admin = Annotated[Principal, Depends(rt.pipeline.require("admin"))]

    def svc(session: Session, actor: Reader) -> InventoryService:
        return InventoryService(session, rt.bus, actor)

    def admin_svc(session: Session, actor: Admin) -> InventoryService:
        return InventoryService(session, rt.bus, actor)

    Read = Annotated[InventoryService, Depends(svc)]
    Write = Annotated[InventoryService, Depends(admin_svc)]

    @router.get("/organization", response_model=sc.OrganizationOut)
    async def get_organization(s: Read) -> sc.OrganizationOut:
        return sc.OrganizationOut.model_validate(await s.organization())

    @router.patch("/organization", response_model=sc.OrganizationOut)
    async def patch_organization(body: sc.OrganizationPatch, s: Write) -> sc.OrganizationOut:
        return sc.OrganizationOut.model_validate(await s.rename_organization(body))

    @router.get("/sites", response_model=list[sc.SiteOut])
    async def list_sites(s: Read, include_archived: bool = False) -> list[sc.SiteOut]:
        rows = await s.list_sites(include_archived=include_archived)
        return [sc.SiteOut.model_validate(r) for r in rows]

    @router.post("/sites", response_model=sc.SiteOut, status_code=201)
    async def create_site(body: sc.SiteCreate, s: Write) -> sc.SiteOut:
        return sc.SiteOut.model_validate(await s.create_site(body))

    @router.get("/sites/{site_id}", response_model=sc.SiteOut)
    async def get_site(site_id: UUID, s: Read) -> sc.SiteOut:
        return sc.SiteOut.model_validate(await s.get_site(site_id))

    @router.patch("/sites/{site_id}", response_model=sc.SiteOut)
    async def patch_site(site_id: UUID, body: sc.SitePatch, s: Write) -> sc.SiteOut:
        return sc.SiteOut.model_validate(await s.update_site(site_id, body))

    @router.delete("/sites/{site_id}", status_code=204)
    async def delete_site(site_id: UUID, s: Write) -> Response:
        await s.delete_site(site_id)
        return Response(status_code=204)

    @router.get("/sites/{site_id}/groups", response_model=list[sc.GroupOut])
    async def list_groups(site_id: UUID, s: Read) -> list[sc.GroupOut]:
        return [sc.GroupOut.model_validate(r) for r in await s.list_groups(site_id)]

    @router.post("/sites/{site_id}/groups", response_model=sc.GroupOut, status_code=201)
    async def create_group(site_id: UUID, body: sc.GroupCreate, s: Write) -> sc.GroupOut:
        return sc.GroupOut.model_validate(await s.create_group(site_id, body))

    @router.get("/groups/{group_id}", response_model=sc.GroupOut)
    async def get_group(group_id: UUID, s: Read) -> sc.GroupOut:
        return sc.GroupOut.model_validate(await s.get_group(group_id))

    @router.patch("/groups/{group_id}", response_model=sc.GroupOut)
    async def patch_group(group_id: UUID, body: sc.GroupPatch, s: Write) -> sc.GroupOut:
        return sc.GroupOut.model_validate(await s.update_group(group_id, body))

    @router.delete("/groups/{group_id}", status_code=204)
    async def delete_group(group_id: UUID, s: Write) -> Response:
        await s.delete_group(group_id)
        return Response(status_code=204)

    @router.get("/screens", response_model=list[sc.ScreenOut])
    async def list_screens(
        s: Read,
        site_id: UUID | None = None,
        group_id: UUID | None = None,
        include_archived: bool = False,
    ) -> list[sc.ScreenOut]:
        rows = await s.list_screens(
            site_id=site_id, group_id=group_id, include_archived=include_archived
        )
        return [sc.ScreenOut.model_validate(r) for r in rows]

    @router.post("/screens", response_model=sc.ScreenOut, status_code=201)
    async def create_screen(body: sc.ScreenCreate, s: Write) -> sc.ScreenOut:
        return sc.ScreenOut.model_validate(await s.create_screen(body))

    @router.get("/screens/{screen_id}", response_model=sc.ScreenOut)
    async def get_screen(screen_id: UUID, s: Read) -> sc.ScreenOut:
        return sc.ScreenOut.model_validate(await s.get_screen(screen_id))

    @router.patch("/screens/{screen_id}", response_model=sc.ScreenOut)
    async def patch_screen(screen_id: UUID, body: sc.ScreenPatch, s: Write) -> sc.ScreenOut:
        return sc.ScreenOut.model_validate(await s.update_screen(screen_id, body))

    @router.delete("/screens/{screen_id}", status_code=204)
    async def delete_screen(screen_id: UUID, s: Write) -> Response:
        await s.delete_screen(screen_id)
        return Response(status_code=204)

    return router
