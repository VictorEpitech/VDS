"""Routes `/api/v1/users` (admin) et `/api/v1/scope-tree` (editor) : périmètres (lot J)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.core import user_admin as ua
from vds_server.core.auth import Principal
from vds_server.core.runtime import Runtime


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["users"])
    Session = Annotated[AsyncSession, Depends(rt.session)]
    Admin = Annotated[Principal, Depends(rt.pipeline.require("admin"))]
    Editor = Annotated[Principal, Depends(rt.pipeline.require("editor"))]

    def admin_service(session: Session, actor: Admin) -> ua.UserAdminService:
        return ua.UserAdminService(session, rt.bus, actor)

    def editor_service(session: Session, actor: Editor) -> ua.UserAdminService:
        return ua.UserAdminService(session, rt.bus, actor)

    AdminSvc = Annotated[ua.UserAdminService, Depends(admin_service)]
    EditorSvc = Annotated[ua.UserAdminService, Depends(editor_service)]

    @router.get("/users", response_model=ua.UserPage)
    async def list_users(
        s: AdminSvc,
        q: Annotated[str | None, Query(max_length=120)] = None,
        role: ua.Role | None = None,
        site_id: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    ) -> ua.UserPage:
        return await s.list_page(q=q, role=role, site_id=site_id, limit=limit, offset=offset)

    @router.get("/users/{user_id}", response_model=ua.UserOut)
    async def get_user(user_id: UUID, s: AdminSvc, response: Response) -> ua.UserOut:
        out = await s.get(user_id)
        response.headers["ETag"] = f'"{out.scopes_version}"'
        return out

    @router.patch("/users/{user_id}", response_model=ua.UserOut)
    async def patch_user(
        user_id: UUID, body: ua.UserPatch, s: AdminSvc, response: Response
    ) -> ua.UserOut:
        out = await s.patch(user_id, body)
        response.headers["ETag"] = f'"{out.scopes_version}"'
        return out

    @router.put("/users/{user_id}/scopes", response_model=ua.UserOut)
    async def put_scopes(
        user_id: UUID,
        body: ua.ScopesIn,
        s: AdminSvc,
        response: Response,
        if_match: Annotated[str | None, Header()] = None,
    ) -> ua.UserOut:
        out = await s.put_scopes(user_id, body, if_match)
        response.headers["ETag"] = f'"{out.scopes_version}"'
        return out

    @router.get("/scope-tree", response_model=ua.ScopeTree)
    async def scope_tree(s: EditorSvc) -> ua.ScopeTree:
        return await s.scope_tree()

    return router
