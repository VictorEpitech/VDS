"""Médias : métadonnées seulement (octets et traitement : lot 2 T3)."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from vds_server.core.errors import ApiError
from vds_server.db.models import Media, MediaVariant, PlaylistItem, PlaylistItemMedia
from vds_server.diffusion.base import ServiceBase


class MediaService(ServiceBase):
    async def list_all(
        self,
        *,
        kind: str | None,
        status: str | None,
        search: str | None,
        limit: int,
        offset: int,
    ) -> list[Media]:
        q = select(Media).order_by(Media.created_at.desc(), Media.id)
        if kind:
            q = q.where(Media.kind == kind)
        if status:
            q = q.where(Media.status == status)
        if search:
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            q = q.where(Media.title.ilike(f"%{escaped}%", escape="\\"))
        return list((await self._s.execute(q.limit(limit).offset(offset))).scalars())

    async def get(self, media_id: UUID) -> Media:
        row = await self._s.get(Media, media_id)
        if row is None:
            raise ApiError("media.not_found", 404)
        return row

    async def variants(self, media_id: UUID) -> list[MediaVariant]:
        q = (
            select(MediaVariant)
            .where(MediaVariant.media_id == media_id)
            .order_by(MediaVariant.name)
        )
        return list((await self._s.execute(q)).scalars())

    async def used_by(self, media_id: UUID) -> list[UUID]:
        q = (
            select(PlaylistItem.playlist_id)
            .join(PlaylistItemMedia, PlaylistItemMedia.item_id == PlaylistItem.id)
            .where(PlaylistItemMedia.media_id == media_id)
            .distinct()
            .order_by(PlaylistItem.playlist_id)
        )
        return list((await self._s.execute(q)).scalars())

    async def rename(self, media_id: UUID, title: str) -> Media:
        media = await self.get(media_id)
        media.title = title
        media.updated_at = datetime.now(UTC)
        self._audit("media.updated", "media", media.id, "media_id", emit=False, fields=["title"])
        await self._commit("media.conflict")
        return media

    async def delete(self, media_id: UUID) -> None:
        media = await self.get(media_id)
        used = await self.used_by(media_id)
        if used:
            raise ApiError("media.in_use", 409, playlists=[str(p) for p in used])
        await self._s.delete(media)
        try:
            await self._s.flush()
        except IntegrityError as exc:  # course : utilisé entre-temps
            await self._s.rollback()
            raise ApiError("media.in_use", 409) from exc
        self._audit("media.deleted", "media", media_id, "media_id", kind=media.kind)
        await self._commit("media.in_use")
