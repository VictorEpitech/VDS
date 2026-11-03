"""Médias : métadonnées, vues paginées, tags, expiration, usage, actions groupées.

Les octets et leur traitement sont dans `media_files.py`.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Text, cast, exists, func, or_, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from vds_sdk import EventBus

from vds_server.config.schemas import ConfigSchemaProvider
from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.core.perimeter import node_of_owner, owner_ref
from vds_server.db.models import Media, MediaVariant, PlaylistItem, PlaylistItemMedia
from vds_server.db.models_layout import Layout
from vds_server.db.models_publication import Publication
from vds_server.diffusion import schemas as sc
from vds_server.diffusion.base import ServiceBase
from vds_server.diffusion.expiry import (
    apply_expiry,
    aware,
    expiring_soon_days,
    is_expired,
    state_clause,
)
from vds_server.diffusion.layouts import touch_layouts
from vds_server.diffusion.ownership import check_owner_change, require_ownable, resolve_node
from vds_server.diffusion.scope import ContentScope, PerimeterScope
from vds_server.diffusion.tags import (
    AUTOCOMPLETE_MAX,
    escape_like,
    normalize_tag,
    normalize_tags,
)
from vds_server.diffusion.tombstones import add_tombstones
from vds_server.publication.reader import playlists_publishing_media

THUMB = "thumb"
MAX_PAGE = 200

_SORTS: dict[str, tuple[Any, bool]] = {  # clé -> (colonne, décroissant)
    "-created_at": (Media.created_at, True),
    "created_at": (Media.created_at, False),
    "title": (Media.title, False),
    "-title": (Media.title, True),
    "-bytes": (Media.bytes, True),
    "expires_at": (Media.expires_at, False),
    "-updated_at": (Media.updated_at, True),
}


def usage_count_expr() -> ColumnElement[int]:
    return (
        select(func.count(func.distinct(PlaylistItem.playlist_id)))
        .select_from(PlaylistItemMedia)
        .join(PlaylistItem, PlaylistItem.id == PlaylistItemMedia.item_id)
        .where(PlaylistItemMedia.media_id == Media.id)
        .correlate(Media)
        .scalar_subquery()
    )


def has_thumb_expr() -> ColumnElement[bool]:
    return exists().where(MediaVariant.media_id == Media.id, MediaVariant.name == THUMB)


def to_out(row: Media, usage_count: int, has_thumb: bool, now: datetime) -> sc.MediaOut:
    return sc.MediaOut(
        id=row.id,
        kind=row.kind,  # type: ignore[arg-type]
        title=row.title,
        original_filename=row.original_filename,
        mime=row.mime,
        bytes=row.bytes,
        sha256=row.sha256,
        width=row.width,
        height=row.height,
        duration_ms=row.duration_ms,
        page_count=row.page_count,
        status=row.status,  # type: ignore[arg-type]
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
        expires_at=row.expires_at,
        expired=is_expired(row, now),
        revision=row.revision,
        tags=list(row.tags or []),
        thumb=f"media/{row.id}/{THUMB}" if has_thumb else None,
        usage_count=usage_count,
        owner=owner_ref(row.owner_site_id, row.owner_group_id),
    )


class MediaService(ServiceBase):
    def __init__(
        self,
        session: AsyncSession,
        bus: EventBus | None,
        actor: Principal,
        schemas: ConfigSchemaProvider | None = None,
        scope: ContentScope | None = None,
    ) -> None:
        super().__init__(session, bus, actor)
        self._schemas = schemas
        self._scope: ContentScope = scope or PerimeterScope(session)

    # --- lecture ---------------------------------------------------------
    async def list_page(
        self,
        *,
        kind: str | None,
        status: str | None,
        search: str | None,
        tags: list[str],
        state: str,
        unused: bool | None,
        sort: str,
        limit: int,
        offset: int,
    ) -> sc.MediaPage:
        now = datetime.now(UTC)
        where: list[ColumnElement[bool]] = [self._scope.media_filter(self._actor)]
        if kind:
            where.append(Media.kind == kind)
        if status:
            where.append(Media.status == status)
        if search:
            like = f"%{escape_like(search)}%"
            where.append(
                or_(
                    Media.title.ilike(like, escape="\\"),
                    Media.original_filename.ilike(like, escape="\\"),
                )
            )
        if tags:
            where.append(Media.tags.contains(normalize_tags(tags)))
        if state != "all":
            where.append(state_clause(Media, state, now, await self._soon_days()))
        if unused is not None:
            used = exists().where(PlaylistItemMedia.media_id == Media.id) | exists().where(
                Publication.entity_type == "playlist",
                Publication.snapshot["media_ids"].has_key(cast(Media.id, Text)),
            )
            where.append(~used if unused else used)
        column, desc = _SORTS[sort]
        order = column.desc() if desc else column.asc()
        if sort == "expires_at":
            order = column.asc().nulls_last()
        q = (
            select(Media, usage_count_expr(), has_thumb_expr())
            .where(*where)
            .order_by(order, Media.id)
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._s.execute(q)).all()
        total = await self._s.scalar(select(func.count()).select_from(Media).where(*where))
        return sc.MediaPage(
            items=[to_out(m, int(n), bool(t), now) for m, n, t in rows], total=int(total or 0)
        )

    async def tag_suggestions(self, prefix: str) -> list[str]:
        try:
            needle = normalize_tag(prefix) if prefix.strip() else ""
        except ApiError:  # préfixe qui ne peut être le début d'aucun tag
            return []
        unnested = func.unnest(Media.tags).table_valued("tag").render_derived(name="u")
        tag = unnested.c.tag
        q = (
            select(tag)
            .select_from(Media)
            .join(unnested, true())
            .where(
                self._scope.media_filter(self._actor),
                tag.like(f"{escape_like(needle)}%", escape="\\"),
            )
            .distinct()
            .order_by(tag)
            .limit(AUTOCOMPLETE_MAX)
        )
        return list((await self._s.execute(q)).scalars())

    async def _soon_days(self) -> int:
        return await expiring_soon_days(self._s, self._schemas)

    async def get(self, media_id: UUID) -> Media:
        row = await self._s.get(Media, media_id)
        if row is None:
            raise ApiError("media.not_found", 404)
        visible = await self._s.scalar(
            select(exists().where(Media.id == media_id, self._scope.media_filter(self._actor)))
        )
        if not visible:
            raise ApiError("media.not_found", 404)  # hors périmètre : pas de révélation (J-D6)
        return row

    async def visible(self, media_id: UUID) -> bool:
        return bool(
            await self._s.scalar(
                select(exists().where(Media.id == media_id, self._scope.media_filter(self._actor)))
            )
        )

    async def out(self, row: Media) -> sc.MediaOut:
        usage = await self._s.scalar(select(usage_count_expr()).where(Media.id == row.id))
        thumb = bool(
            await self._s.scalar(
                select(exists().where(MediaVariant.media_id == row.id, MediaVariant.name == THUMB))
            )
        )
        return to_out(row, int(usage or 0), thumb, datetime.now(UTC))

    async def variants(self, media_id: UUID) -> list[MediaVariant]:
        await self.get(media_id)
        q = (
            select(MediaVariant)
            .where(MediaVariant.media_id == media_id)
            .order_by(MediaVariant.name)
        )
        return list((await self._s.execute(q)).scalars())

    async def used_by(self, media_id: UUID) -> list[UUID]:
        await self.get(media_id)
        return await self._used_by(media_id)

    async def _used_by(self, media_id: UUID) -> list[UUID]:
        q = (
            select(PlaylistItem.playlist_id)
            .join(PlaylistItemMedia, PlaylistItemMedia.item_id == PlaylistItem.id)
            .where(PlaylistItemMedia.media_id == media_id)
            .distinct()
            .order_by(PlaylistItem.playlist_id)
        )
        working = list((await self._s.execute(q)).scalars())
        # un instantané publié compte comme une référence (purge, suppression, expiration)
        published = await playlists_publishing_media(self._s, media_id)
        return sorted({*working, *published}, key=str)

    # --- écriture --------------------------------------------------------
    async def _writable(self, media_id: UUID) -> Media:
        media = await self.get(media_id)
        if not await self._scope.can_write_media(self._actor, media_id):
            raise ApiError("scope.forbidden", 403)
        return media

    def _apply_patch(
        self, media: Media, fields: set[str], title: str | None, tags: list[str] | None,
        expires_at: datetime | None, now: datetime,
    ) -> None:  # fmt: skip
        if "title" in fields and title is not None:
            media.title = title
        if "tags" in fields:
            media.tags = normalize_tags(tags or [])
        restored = False
        if "expires_at" in fields:
            restored = apply_expiry(media, aware(expires_at), now)
        media.updated_at = now
        self._audit("media.updated", "media", media.id, "media_id", fields=sorted(fields))
        if restored:
            self._audit("media.restored", "media", media.id, "media_id")

    async def patch(self, media_id: UUID, body: sc.MediaPatch) -> Media:
        media = await self._writable(media_id)
        if "owner" in body.model_fields_set:
            node, (site_id, group_id) = await resolve_node(self._s, body.owner)
            require_ownable(self._scope, self._actor, node)
            if node != node_of_owner(media.owner_site_id, media.owner_group_id):
                await check_owner_change(self._s, "media", media.id, node)
                media.owner_site_id, media.owner_group_id = site_id, group_id
                self._audit("media.owner_changed", "media", media.id, "media_id", emit=False)
        self._apply_patch(
            media, set(body.model_fields_set), body.title, body.tags, body.expires_at,
            datetime.now(UTC),
        )  # fmt: skip
        await self._commit("media.conflict")
        return media

    async def _delete_one(self, media: Media) -> None:
        used = await self._used_by(media.id)
        if used:
            raise ApiError("media.in_use", 409, playlists=[str(p) for p in used])
        backgrounds = list(
            (
                await self._s.scalars(
                    select(Layout.id).where(Layout.background_media_id == media.id)
                )
            ).all()
        )
        keys = set(
            (
                await self._s.scalars(
                    select(MediaVariant.storage_key).where(MediaVariant.media_id == media.id)
                )
            ).all()
        )
        if media.storage_key:
            keys.add(media.storage_key)
        backend = media.storage_backend or "storage.local"
        try:
            async with self._s.begin_nested():
                await self._s.delete(media)
                await self._s.flush()
        except IntegrityError as exc:  # course : utilisé entre-temps
            raise ApiError("media.in_use", 409) from exc
        await add_tombstones(self._s, backend, keys, reason="deleted", media_id=media.id)
        for layout_id, revision in await touch_layouts(self._s, backgrounds):
            self._audit("layout.updated", "layout", layout_id, "layout_id", revision=revision)
            await self._pub.touch("layout", layout_id)
        self._audit("media.deleted", "media", media.id, "media_id", kind=media.kind)

    async def delete(self, media_id: UUID) -> None:
        media = await self._writable(media_id)
        await self._delete_one(media)
        await self._commit("media.in_use")

    async def bulk(self, body: sc.MediaBulkIn) -> sc.MediaBulkOut:
        now = datetime.now(UTC)
        wanted = normalize_tags(body.tags) if body.tags else []
        expires_at = aware(body.expires_at)
        done: list[UUID] = []
        failed: list[dict[str, str]] = []
        for media_id in dict.fromkeys(body.ids):
            try:
                media = await self._writable(media_id)
                if body.action == "delete":
                    await self._delete_one(media)
                elif body.action == "set_expiry":
                    self._apply_patch(media, {"expires_at"}, None, None, expires_at, now)
                else:
                    current = set(media.tags or [])
                    merged = (
                        current | set(wanted)
                        if body.action == "add_tags"
                        else current - set(wanted)
                    )
                    self._apply_patch(media, {"tags"}, None, sorted(merged), None, now)
                done.append(media_id)
            except ApiError as exc:
                failed.append({"id": str(media_id), "code": exc.code})
        await self._commit("media.conflict")
        return sc.MediaBulkOut(done=done, failed=failed)
