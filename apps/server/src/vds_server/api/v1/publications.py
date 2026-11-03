"""Routes de validation de publication (`/api/v1/publications`, vds-core 1.13.0).

Soumettre / retirer : éditeur dans son périmètre (ou admin). Approuver / refuser : admin.
Le serveur décide ; l'admin masque seulement les boutons.
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.core.runtime import Runtime
from vds_server.db.models import Playlist, Schedule
from vds_server.db.models_layout import Layout
from vds_server.db.models_publication import Publication
from vds_server.diffusion.scope import PerimeterScope
from vds_server.publication import schemas as ps
from vds_server.publication.service import PublicationService

EntityPath = Literal["playlist", "layout", "schedule"]
NOT_FOUND = {"playlist": "playlist.not_found", "layout": "layout.not_found"}


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1/publications", tags=["publications"])
    Session = Annotated[AsyncSession, Depends(rt.session)]
    Editor = Annotated[Principal, Depends(rt.pipeline.require("editor"))]
    Admin = Annotated[Principal, Depends(rt.pipeline.require("admin"))]

    def service(session: Session, actor: Editor) -> PublicationService:
        return PublicationService(session, actor, rt.schemas)

    def admin_service(session: Session, actor: Admin) -> PublicationService:
        return PublicationService(session, actor, rt.schemas)

    Svc = Annotated[PublicationService, Depends(service)]
    AdminSvc = Annotated[PublicationService, Depends(admin_service)]

    async def check(
        session: AsyncSession, actor: Principal, entity_type: str, entity_id: UUID, *, write: bool
    ) -> None:
        """404 hors périmètre de lecture (aucune fuite d'existence), 403 si non modifiable."""
        scope = PerimeterScope(session)
        code = NOT_FOUND.get(entity_type, "schedule.not_found")
        if entity_type == "playlist":
            visible = await session.scalar(
                select(Playlist.id).where(
                    Playlist.id == entity_id,
                    Playlist.owner_override_id.is_(None),
                    scope.playlist_filter(actor),
                )
            )
            allowed = visible is not None and (
                not write or await scope.can_write_playlist(actor, entity_id)
            )
        elif entity_type == "layout":
            visible = await session.scalar(
                select(Layout.id).where(Layout.id == entity_id, scope.layout_filter(actor))
            )
            allowed = visible is not None and (
                not write or await scope.can_write_layout(actor, entity_id)
            )
        else:
            row = (
                await session.execute(
                    select(Schedule.target_type, Schedule.target_id).where(
                        Schedule.id == entity_id,
                        scope.target_filter(
                            actor, Schedule.target_type, Schedule.target_id, "read"
                        ),
                    )
                )
            ).first()
            visible = None if row is None else entity_id
            allowed = row is not None and (
                not write or await scope.can_target(actor, row[0], row[1])
            )
        if visible is None:
            raise ApiError(code, 404)
        if not allowed:
            raise ApiError("scope.forbidden", 403)

    async def finish(
        session: AsyncSession, svc: PublicationService, row: Publication
    ) -> ps.PublicationOut:
        out = await svc.out(row)
        await session.commit()
        for event in svc.drain():
            await rt.bus.publish(event)
        return out

    @router.get("", response_model=list[ps.PublicationOut])
    async def list_publications(
        session: Session,
        actor: Editor,
        s: Svc,
        state: ps.StateLit | None = None,
        entity_type: ps.EntityTypeLit | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
    ) -> list[ps.PublicationOut]:
        scope = PerimeterScope(session)
        visible = {
            "playlist": select(Playlist.id).where(
                Playlist.owner_override_id.is_(None), scope.playlist_filter(actor)
            ),
            "layout": select(Layout.id).where(scope.layout_filter(actor)),
            "schedule": select(Schedule.id).where(
                scope.target_filter(actor, Schedule.target_type, Schedule.target_id, "read")
            ),
        }
        q = select(Publication).where(
            or_(
                *(
                    and_(Publication.entity_type == t, Publication.entity_id.in_(sub))
                    for t, sub in visible.items()
                )
            )
        )
        if state is not None:
            q = q.where(Publication.state == state)
        if entity_type is not None:
            q = q.where(Publication.entity_type == entity_type)
        rows = list(
            (
                await session.scalars(
                    q.order_by(Publication.updated_at.desc(), Publication.entity_id).limit(limit)
                )
            ).all()
        )
        return await s.list_outs(rows)

    @router.get("/{entity_type}/{entity_id}", response_model=ps.PublicationOut)
    async def get_publication(
        entity_type: EntityPath, entity_id: UUID, session: Session, actor: Editor, s: Svc
    ) -> ps.PublicationOut:
        await check(session, actor, entity_type, entity_id, write=False)
        return await s.out_for(entity_type, entity_id)

    @router.post("/{entity_type}/{entity_id}/submit", response_model=ps.PublicationOut)
    async def submit(
        entity_type: EntityPath,
        entity_id: UUID,
        body: ps.SubmitIn,
        session: Session,
        actor: Editor,
        s: Svc,
    ) -> ps.PublicationOut:
        await check(session, actor, entity_type, entity_id, write=True)
        row = await s.submit(entity_type, entity_id, body.comment)
        return await finish(session, s, row)

    @router.post("/{entity_type}/{entity_id}/withdraw", response_model=ps.PublicationOut)
    async def withdraw(
        entity_type: EntityPath,
        entity_id: UUID,
        body: ps.WithdrawIn,
        session: Session,
        actor: Editor,
        s: Svc,
    ) -> ps.PublicationOut:
        await check(session, actor, entity_type, entity_id, write=True)
        row = await s.withdraw(entity_type, entity_id)
        return await finish(session, s, row)

    @router.post("/{entity_type}/{entity_id}/approve", response_model=ps.PublicationOut)
    async def approve(
        entity_type: EntityPath,
        entity_id: UUID,
        body: ps.ApproveIn,
        session: Session,
        actor: Admin,
        s: AdminSvc,
    ) -> ps.PublicationOut:
        await check(session, actor, entity_type, entity_id, write=False)
        row = await s.approve(entity_type, entity_id, body.expected_rev, body.include_dependencies)
        return await finish(session, s, row)

    @router.post("/{entity_type}/{entity_id}/reject", response_model=ps.PublicationOut)
    async def reject(
        entity_type: EntityPath,
        entity_id: UUID,
        body: ps.RejectIn,
        session: Session,
        actor: Admin,
        s: AdminSvc,
    ) -> ps.PublicationOut:
        await check(session, actor, entity_type, entity_id, write=False)
        row = await s.reject(entity_type, entity_id, body.comment)
        return await finish(session, s, row)

    return router
