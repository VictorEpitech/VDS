"""Routes médias (métadonnées), playlists, programmation : lecture et écriture éditeur."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.core.auth import Principal
from vds_server.core.runtime import Runtime
from vds_server.diffusion import schemas as sc
from vds_server.diffusion.media import MediaService
from vds_server.diffusion.playlists import PlaylistService
from vds_server.diffusion.schedules import ScheduleService


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["diffusion"])
    Session = Annotated[AsyncSession, Depends(rt.session)]
    Editor = Annotated[Principal, Depends(rt.pipeline.require("editor"))]

    def media_svc(session: Session, actor: Editor) -> MediaService:
        return MediaService(session, rt.bus, actor)

    def playlist_svc(session: Session, actor: Editor) -> PlaylistService:
        return PlaylistService(session, rt.bus, actor, rt.registry.content_item_schema)

    def schedule_svc(session: Session, actor: Editor) -> ScheduleService:
        return ScheduleService(session, rt.bus, actor)

    Media = Annotated[MediaService, Depends(media_svc)]
    Playlists = Annotated[PlaylistService, Depends(playlist_svc)]
    Schedules = Annotated[ScheduleService, Depends(schedule_svc)]

    # --- médias ----------------------------------------------------------
    @router.get("/media", response_model=list[sc.MediaOut])
    async def list_media(
        s: Media,
        kind: sc.MediaKind | None = None,
        status: sc.MediaStatus | None = None,
        q: Annotated[str | None, Query(max_length=120)] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[sc.MediaOut]:
        rows = await s.list_all(kind=kind, status=status, search=q, limit=limit, offset=offset)
        return [sc.MediaOut.model_validate(r) for r in rows]

    @router.get("/media/{media_id}", response_model=sc.MediaDetailOut)
    async def get_media(media_id: UUID, s: Media) -> sc.MediaDetailOut:
        row = await s.get(media_id)
        return sc.MediaDetailOut(
            **sc.MediaOut.model_validate(row).model_dump(),
            variants=[sc.MediaVariantOut.model_validate(v) for v in await s.variants(media_id)],
            used_by=await s.used_by(media_id),
        )

    @router.patch("/media/{media_id}", response_model=sc.MediaOut)
    async def patch_media(media_id: UUID, body: sc.MediaPatch, s: Media) -> sc.MediaOut:
        return sc.MediaOut.model_validate(await s.rename(media_id, body.title))

    @router.delete("/media/{media_id}", status_code=204)
    async def delete_media(media_id: UUID, s: Media) -> Response:
        await s.delete(media_id)
        return Response(status_code=204)

    # --- playlists -------------------------------------------------------
    @router.get("/playlists", response_model=list[sc.PlaylistOut])
    async def list_playlists(s: Playlists) -> list[sc.PlaylistOut]:
        rows = await s.list_all()
        return [sc.PlaylistOut.model_validate({**_attrs(p), "item_count": n}) for p, n in rows]

    @router.post("/playlists", response_model=sc.PlaylistDetailOut, status_code=201)
    async def create_playlist(body: sc.PlaylistCreate, s: Playlists) -> sc.PlaylistDetailOut:
        created = await s.create(body)
        return await _detail(s, created.id)

    @router.get("/playlists/{playlist_id}", response_model=sc.PlaylistDetailOut)
    async def get_playlist(playlist_id: UUID, s: Playlists) -> sc.PlaylistDetailOut:
        return await _detail(s, playlist_id)

    @router.patch("/playlists/{playlist_id}", response_model=sc.PlaylistDetailOut)
    async def patch_playlist(
        playlist_id: UUID, body: sc.PlaylistPatch, s: Playlists
    ) -> sc.PlaylistDetailOut:
        await s.update(playlist_id, body)
        return await _detail(s, playlist_id)

    @router.delete("/playlists/{playlist_id}", status_code=204)
    async def delete_playlist(playlist_id: UUID, s: Playlists) -> Response:
        await s.delete(playlist_id)
        return Response(status_code=204)

    @router.post("/playlists/{playlist_id}/items", response_model=sc.ItemOut, status_code=201)
    async def add_item(playlist_id: UUID, body: sc.ItemCreate, s: Playlists) -> sc.ItemOut:
        return await s.add_item(playlist_id, body)

    @router.put("/playlists/{playlist_id}/items/order", response_model=sc.PlaylistDetailOut)
    async def reorder_items(
        playlist_id: UUID, body: sc.ItemOrder, s: Playlists
    ) -> sc.PlaylistDetailOut:
        await s.reorder(playlist_id, body)
        return await _detail(s, playlist_id)

    @router.patch("/playlists/{playlist_id}/items/{item_id}", response_model=sc.ItemOut)
    async def patch_item(
        playlist_id: UUID, item_id: UUID, body: sc.ItemPatch, s: Playlists
    ) -> sc.ItemOut:
        return await s.update_item(playlist_id, item_id, body)

    @router.delete("/playlists/{playlist_id}/items/{item_id}", status_code=204)
    async def delete_item(playlist_id: UUID, item_id: UUID, s: Playlists) -> Response:
        await s.delete_item(playlist_id, item_id)
        return Response(status_code=204)

    # --- programmation ---------------------------------------------------
    @router.get("/schedules", response_model=list[sc.ScheduleOut])
    async def list_schedules(
        s: Schedules,
        target_type: sc.TargetType | None = None,
        target_id: UUID | None = None,
        playlist_id: UUID | None = None,
    ) -> list[sc.ScheduleOut]:
        return await s.list_all(
            target_type=target_type, target_id=target_id, playlist_id=playlist_id
        )

    @router.post("/schedules", response_model=sc.ScheduleOut, status_code=201)
    async def create_schedule(body: sc.ScheduleCreate, s: Schedules) -> sc.ScheduleOut:
        return await s.create(body)

    @router.get("/schedules/{schedule_id}", response_model=sc.ScheduleOut)
    async def get_schedule(schedule_id: UUID, s: Schedules) -> sc.ScheduleOut:
        return await s.get(schedule_id)

    @router.patch("/schedules/{schedule_id}", response_model=sc.ScheduleOut)
    async def patch_schedule(
        schedule_id: UUID, body: sc.SchedulePatch, s: Schedules
    ) -> sc.ScheduleOut:
        return await s.update(schedule_id, body)

    @router.delete("/schedules/{schedule_id}", status_code=204)
    async def delete_schedule(schedule_id: UUID, s: Schedules) -> Response:
        await s.delete(schedule_id)
        return Response(status_code=204)

    return router


def _attrs(p: object) -> dict[str, object]:
    return {k: getattr(p, k) for k in ("id", "name", "description", "created_at", "updated_at")}


async def _detail(s: PlaylistService, playlist_id: UUID) -> sc.PlaylistDetailOut:
    playlist, items = await s.detail(playlist_id)
    return sc.PlaylistDetailOut.model_validate(
        {**_attrs(playlist), "item_count": len(items), "items": items}
    )
