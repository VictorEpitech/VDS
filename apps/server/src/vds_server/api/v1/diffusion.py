"""Routes médias (métadonnées), playlists, programmation : lecture et écriture éditeur."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.core.auth import Principal
from vds_server.core.runtime import Runtime
from vds_server.diffusion import schemas as sc
from vds_server.diffusion.media import MediaService
from vds_server.diffusion.playlists import PlaylistService, to_out
from vds_server.diffusion.schedules import ScheduleService
from vds_server.diffusion.tags import MAX_TAG_LEN, normalize_tags
from vds_server.diffusion.usage import UsageService


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["diffusion"])
    Session = Annotated[AsyncSession, Depends(rt.session)]
    Editor = Annotated[Principal, Depends(rt.pipeline.require("editor"))]

    def media_svc(session: Session, actor: Editor) -> MediaService:
        return MediaService(session, rt.bus, actor, rt.schemas)

    def playlist_svc(session: Session, actor: Editor) -> PlaylistService:
        return PlaylistService(session, rt.bus, actor, rt.registry.content_item_schema, rt.schemas)

    def schedule_svc(session: Session, actor: Editor) -> ScheduleService:
        return ScheduleService(session, rt.bus, actor)

    def usage_svc(session: Session, actor: Editor) -> UsageService:
        return UsageService(session, actor)

    Usage = Annotated[UsageService, Depends(usage_svc)]
    Media = Annotated[MediaService, Depends(media_svc)]
    Playlists = Annotated[PlaylistService, Depends(playlist_svc)]
    Schedules = Annotated[ScheduleService, Depends(schedule_svc)]

    # --- médias ----------------------------------------------------------
    @router.get("/media", response_model=sc.MediaPage)
    async def list_media(
        s: Media,
        kind: sc.MediaKind | None = None,
        status: sc.MediaStatus | None = None,
        q: Annotated[str | None, Query(max_length=120)] = None,
        tags: Annotated[list[str] | None, Query(max_length=20)] = None,
        state: sc.MediaState = "all",
        unused: bool | None = None,
        sort: sc.MediaSort = "-created_at",
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> sc.MediaPage:
        return await s.list_page(
            kind=kind,
            status=status,
            search=q,
            tags=_split_tags(tags),
            state=state,
            unused=unused,
            sort=sort,
            limit=limit,
            offset=offset,
        )

    @router.get("/media/tags", response_model=list[str])
    async def media_tags(
        s: Media, prefix: Annotated[str, Query(max_length=MAX_TAG_LEN)] = ""
    ) -> list[str]:
        return await s.tag_suggestions(prefix)

    @router.post("/media/bulk", response_model=sc.MediaBulkOut)
    async def bulk_media(body: sc.MediaBulkIn, s: Media) -> sc.MediaBulkOut:
        return await s.bulk(body)

    @router.get("/media/{media_id}", response_model=sc.MediaDetailOut)
    async def get_media(media_id: UUID, s: Media) -> sc.MediaDetailOut:
        row = await s.get(media_id)
        return sc.MediaDetailOut(
            **(await s.out(row)).model_dump(),
            variants=[sc.MediaVariantOut.model_validate(v) for v in await s.variants(media_id)],
            used_by=await s.used_by(media_id),
        )

    @router.patch("/media/{media_id}", response_model=sc.MediaOut)
    async def patch_media(media_id: UUID, body: sc.MediaPatch, s: Media) -> sc.MediaOut:
        return await s.out(await s.patch(media_id, body))

    @router.get("/media/{media_id}/usage", response_model=sc.MediaUsageOut)
    async def media_usage(media_id: UUID, u: Usage) -> sc.MediaUsageOut:
        return await u.media(media_id)

    @router.delete("/media/{media_id}", status_code=204)
    async def delete_media(media_id: UUID, s: Media) -> Response:
        await s.delete(media_id)
        return Response(status_code=204)

    # --- playlists -------------------------------------------------------
    @router.get("/playlists", response_model=sc.PlaylistPage)
    async def list_playlists(
        s: Playlists,
        q: Annotated[str | None, Query(max_length=120)] = None,
        tags: Annotated[list[str] | None, Query(max_length=20)] = None,
        state: sc.MediaState = "all",
        sort: sc.PlaylistSort = "name",
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> sc.PlaylistPage:
        return await s.list_page(
            search=q, tags=_split_tags(tags), state=state, sort=sort, limit=limit, offset=offset
        )

    @router.get("/playlists/tags", response_model=list[str])
    async def playlist_tags(
        s: Playlists, prefix: Annotated[str, Query(max_length=MAX_TAG_LEN)] = ""
    ) -> list[str]:
        return await s.tag_suggestions(prefix)

    @router.get("/playlists/{playlist_id}/usage", response_model=sc.PlaylistUsageOut)
    async def playlist_usage(playlist_id: UUID, u: Usage) -> sc.PlaylistUsageOut:
        return await u.playlist(playlist_id)

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


def _split_tags(raw: list[str] | None) -> list[str]:
    """`tags=a,b` ou `tags=a&tags=b` ; la normalisation et les bornes sont celles du service."""
    parts = [t for value in raw or [] for t in value.split(",") if t.strip()]
    return normalize_tags(parts) if parts else []


async def _detail(s: PlaylistService, playlist_id: UUID) -> sc.PlaylistDetailOut:
    playlist, items = await s.detail(playlist_id)
    return sc.PlaylistDetailOut(
        **to_out(playlist, len(items), datetime.now(UTC)).model_dump(), items=items
    )
