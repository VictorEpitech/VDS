"""Playlists et éléments ; extraction des références de média (`x-vds-media`)."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from jsonschema import Draft202012Validator
from sqlalchemy import ColumnElement, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from vds_sdk import EventBus

from vds_server.config.schemas import ConfigSchemaProvider
from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.db.models import Media, Playlist, PlaylistItem, PlaylistItemMedia, Schedule
from vds_server.db.models_layout import LayoutZone
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
from vds_server.diffusion.scope import ContentScope, OrganizationScope
from vds_server.diffusion.tags import AUTOCOMPLETE_MAX, escape_like, normalize_tag, normalize_tags

ItemSchemaLookup = Callable[[str], dict[str, Any] | None]


def media_refs(schema: dict[str, Any], props: dict[str, Any]) -> list[tuple[str, UUID, str]]:
    """(propriété, media_id, kind attendu) pour chaque propriété `x-vds-media` renseignée."""
    refs: list[tuple[str, UUID, str]] = []
    for name, spec in (schema.get("properties") or {}).items():
        kind = spec.get("x-vds-media") if isinstance(spec, dict) else None
        value = props.get(name)
        if kind is None or value is None:
            continue
        try:
            refs.append((name, UUID(str(value)), str(kind)))
        except ValueError as exc:
            raise ApiError("playlist.media_ref_invalid", 422, field=name) from exc
    return refs


_SORTS: dict[str, tuple[Any, bool]] = {
    "name": (Playlist.name, False),
    "-name": (Playlist.name, True),
    "-created_at": (Playlist.created_at, True),
    "created_at": (Playlist.created_at, False),
    "expires_at": (Playlist.expires_at, False),
    "-updated_at": (Playlist.updated_at, True),
}


def to_out(p: Playlist, item_count: int, now: datetime) -> sc.PlaylistOut:
    return sc.PlaylistOut(
        id=p.id,
        name=p.name,
        description=p.description,
        item_count=item_count,
        created_at=p.created_at,
        updated_at=p.updated_at,
        expires_at=p.expires_at,
        expired=is_expired(p, now),
        tags=list(p.tags or []),
    )


class PlaylistService(ServiceBase):
    def __init__(
        self,
        session: AsyncSession,
        bus: EventBus | None,
        actor: Principal,
        item_schema: ItemSchemaLookup,
        schemas: ConfigSchemaProvider | None = None,
        scope: ContentScope | None = None,
    ) -> None:
        super().__init__(session, bus, actor)
        self._item_schema = item_schema
        self._schemas = schemas
        self._scope: ContentScope = scope or OrganizationScope()

    # --- playlists -------------------------------------------------------
    async def list_page(
        self,
        *,
        search: str | None,
        tags: list[str],
        state: str,
        sort: str,
        limit: int,
        offset: int,
    ) -> sc.PlaylistPage:
        now = datetime.now(UTC)
        where: list[ColumnElement[bool]] = [
            Playlist.owner_override_id.is_(None),
            self._scope.playlist_filter(self._actor),
        ]
        if search:
            where.append(Playlist.name.ilike(f"%{escape_like(search)}%", escape="\\"))
        if tags:
            where.append(Playlist.tags.contains(normalize_tags(tags)))
        if state != "all":
            where.append(
                state_clause(Playlist, state, now, await expiring_soon_days(self._s, self._schemas))
            )
        column, desc = _SORTS[sort]
        order = column.desc() if desc else column.asc()
        if sort == "expires_at":
            order = column.asc().nulls_last()
        count = func.count(PlaylistItem.id)
        q = (
            select(Playlist, count)
            .outerjoin(PlaylistItem, PlaylistItem.playlist_id == Playlist.id)
            .where(*where)
            .group_by(Playlist.id)
            .order_by(order, Playlist.id)
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._s.execute(q)).all()
        total = await self._s.scalar(select(func.count()).select_from(Playlist).where(*where))
        return sc.PlaylistPage(
            items=[to_out(p, int(n), now) for p, n in rows], total=int(total or 0)
        )

    async def tag_suggestions(self, prefix: str) -> list[str]:
        try:
            needle = normalize_tag(prefix) if prefix.strip() else ""
        except ApiError:
            return []
        unnested = func.unnest(Playlist.tags).table_valued("tag").render_derived(name="u")
        tag = unnested.c.tag
        q = (
            select(tag)
            .select_from(Playlist)
            .join(unnested, true())
            .where(
                self._scope.playlist_filter(self._actor),
                Playlist.owner_override_id.is_(None),
                tag.like(f"{escape_like(needle)}%", escape="\\"),
            )
            .distinct()
            .order_by(tag)
            .limit(AUTOCOMPLETE_MAX)
        )
        return list((await self._s.execute(q)).scalars())

    async def _writable(self, playlist_id: UUID) -> Playlist:
        playlist = await self.get(playlist_id)
        if not await self._scope.can_write_playlist(self._actor, playlist_id):
            raise ApiError("playlist.forbidden", 403)
        return playlist

    async def get(self, playlist_id: UUID) -> Playlist:
        row = await self._s.get(Playlist, playlist_id)
        if row is None:
            raise ApiError("playlist.not_found", 404)
        return row

    async def items(self, playlist_id: UUID) -> list[PlaylistItem]:
        q = (
            select(PlaylistItem)
            .where(PlaylistItem.playlist_id == playlist_id)
            .order_by(PlaylistItem.position)
        )
        return list((await self._s.execute(q)).scalars())

    async def media_ids(self, item_ids: list[UUID]) -> dict[UUID, list[UUID]]:
        out: dict[UUID, list[UUID]] = {i: [] for i in item_ids}
        if item_ids:
            q = (
                select(PlaylistItemMedia.item_id, PlaylistItemMedia.media_id)
                .where(PlaylistItemMedia.item_id.in_(item_ids))
                .order_by(PlaylistItemMedia.media_id)
            )
            for item_id, media_id in (await self._s.execute(q)).all():
                out[item_id].append(media_id)
        return out

    async def detail(self, playlist_id: UUID) -> tuple[Playlist, list[sc.ItemOut]]:
        playlist = await self.get(playlist_id)
        items = await self.items(playlist_id)
        media = await self.media_ids([i.id for i in items])
        return playlist, [self._out(i, media[i.id]) for i in items]

    @staticmethod
    def _out(item: PlaylistItem, media_ids: list[UUID]) -> sc.ItemOut:
        return sc.ItemOut(
            id=item.id,
            position=item.position,
            content_type=item.content_type,
            props=item.props,
            duration_ms=item.duration_ms,
            enabled=item.enabled,
            media_ids=media_ids,
        )

    async def create(self, body: sc.PlaylistCreate) -> Playlist:
        playlist = Playlist(
            name=body.name,
            description=body.description,
            created_by=self._actor.user_id,
            updated_by=self._actor.user_id,
        )
        self._s.add(playlist)
        await self._s.flush()
        self._audit("playlist.created", "playlist", playlist.id, "playlist_id")
        await self._commit("playlist.conflict")
        return playlist

    def _touch(self, playlist: Playlist) -> None:
        playlist.updated_at = datetime.now(UTC)
        playlist.updated_by = self._actor.user_id

    async def update(self, playlist_id: UUID, body: sc.PlaylistPatch) -> Playlist:
        playlist = await self._writable(playlist_id)
        if body.name is not None:
            playlist.name = body.name
        if "description" in body.model_fields_set:
            playlist.description = body.description
        if body.tags is not None:
            playlist.tags = normalize_tags(body.tags)
        restored = False
        if "expires_at" in body.model_fields_set:
            new = aware(body.expires_at)
            if new is not None and playlist.owner_override_id is not None:
                raise ApiError("playlist.expiry_unsupported", 422)
            restored = apply_expiry(playlist, new, datetime.now(UTC))
        self._touch(playlist)
        self._audit(
            "playlist.updated",
            "playlist",
            playlist.id,
            "playlist_id",
            fields=sorted(body.model_fields_set),
        )
        if restored:
            self._audit("playlist.restored", "playlist", playlist.id, "playlist_id")
        await self._commit("playlist.conflict")
        return playlist

    async def delete(self, playlist_id: UUID) -> None:
        playlist = await self._writable(playlist_id)
        schedules = (
            await self._s.execute(
                select(Schedule.id).where(Schedule.playlist_id == playlist_id).order_by(Schedule.id)
            )
        ).scalars()
        used = [str(s) for s in schedules]
        if used:
            raise ApiError("playlist.in_use", 409, schedules=used)
        zone_layouts = sorted(
            (
                await self._s.scalars(
                    select(LayoutZone.layout_id)
                    .where(LayoutZone.playlist_id == playlist_id)
                    .distinct()
                )
            ).all(),
            key=str,
        )
        # ON DELETE SET NULL vide les zones ; les révisions sont incrémentées avant la suppression
        for layout_id, revision in await touch_layouts(self._s, zone_layouts):
            self._audit("layout.updated", "layout", layout_id, "layout_id", revision=revision)
        await self._s.delete(playlist)
        self._audit("playlist.deleted", "playlist", playlist_id, "playlist_id")
        await self._commit("playlist.in_use")

    # --- éléments --------------------------------------------------------
    async def _validate_props(
        self, content_type: str, props: dict[str, Any], keep: frozenset[UUID] = frozenset()
    ) -> list[UUID]:
        """`keep` : médias déjà présents dans l'élément (un média expiré y reste toléré)."""
        schema = self._item_schema(content_type)
        if schema is None:
            raise ApiError("playlist.content_type_unknown", 422, content_type=content_type)
        errors = sorted(
            Draft202012Validator(schema).iter_errors(props), key=lambda e: list(e.absolute_path)
        )
        if errors:
            first = errors[0]
            raise ApiError(
                "playlist.item_props_invalid",
                422,
                path="/".join(str(p) for p in first.absolute_path),
                keyword=first.validator,
            )
        ids: list[UUID] = []
        for name, media_id, kind in media_refs(schema, props):
            media = await self._s.get(Media, media_id)
            if media is None:
                raise ApiError("playlist.media_not_found", 422, field=name)
            if media.kind != kind:
                raise ApiError("playlist.media_kind_mismatch", 422, field=name, expected=kind)
            if media_id not in keep and is_expired(media, datetime.now(UTC)):
                raise ApiError("media.expired", 422, field=name)
            if media_id not in ids:
                ids.append(media_id)
        return ids

    async def _set_media(self, item_id: UUID, media_ids: list[UUID]) -> None:
        current = set(
            (
                await self._s.execute(
                    select(PlaylistItemMedia.media_id).where(PlaylistItemMedia.item_id == item_id)
                )
            ).scalars()
        )
        wanted = set(media_ids)
        for gone in current - wanted:
            await self._s.delete(await self._s.get_one(PlaylistItemMedia, (item_id, gone)))
        for new in wanted - current:
            self._s.add(PlaylistItemMedia(item_id=item_id, media_id=new))

    async def _item(self, playlist_id: UUID, item_id: UUID) -> PlaylistItem:
        item = await self._s.get(PlaylistItem, item_id)
        if item is None or item.playlist_id != playlist_id:
            raise ApiError("playlist.item_not_found", 404)
        return item

    def _renumber(self, items: list[PlaylistItem]) -> None:
        for index, item in enumerate(items):
            if item.position != index:
                item.position = index

    async def add_item_uncommitted(
        self, playlist_id: UUID, content_type: str, props: dict[str, Any], duration_ms: int | None
    ) -> PlaylistItem:
        """Ajoute un élément (validé) en fin de playlist sans commit ni audit : l'appelant porte
        la transaction (forçage de contenu isolé)."""
        media_ids = await self._validate_props(content_type, props)
        position = len(await self.items(playlist_id))
        item = PlaylistItem(
            playlist_id=playlist_id,
            position=position,
            content_type=content_type,
            props=props,
            duration_ms=duration_ms,
            enabled=True,
        )
        self._s.add(item)
        await self._s.flush()
        await self._set_media(item.id, media_ids)
        return item

    async def add_item(self, playlist_id: UUID, body: sc.ItemCreate) -> sc.ItemOut:
        playlist = await self._writable(playlist_id)
        media_ids = await self._validate_props(body.content_type, body.props)
        items = await self.items(playlist_id)
        item = PlaylistItem(
            playlist_id=playlist_id,
            position=len(items),
            content_type=body.content_type,
            props=body.props,
            duration_ms=body.duration_ms,
            enabled=body.enabled,
        )
        self._s.add(item)
        await self._s.flush()
        at = len(items) if body.position is None else min(body.position, len(items))
        items.insert(at, item)
        self._renumber(items)
        await self._set_media(item.id, media_ids)
        self._touch(playlist)
        self._audit(
            "playlist.updated",
            "playlist",
            playlist_id,
            "playlist_id",
            change="item_added",
            item_id=str(item.id),
        )
        await self._commit("playlist.conflict")
        return self._out(item, sorted(media_ids))

    async def update_item(self, playlist_id: UUID, item_id: UUID, body: sc.ItemPatch) -> sc.ItemOut:
        playlist = await self._writable(playlist_id)
        item = await self._item(playlist_id, item_id)
        if body.props is not None:
            current = frozenset((await self.media_ids([item_id]))[item_id])
            media_ids = await self._validate_props(item.content_type, body.props, current)
            item.props = body.props
            await self._set_media(item.id, media_ids)
        if "duration_ms" in body.model_fields_set:
            item.duration_ms = body.duration_ms
        if body.enabled is not None:
            item.enabled = body.enabled
        item.updated_at = datetime.now(UTC)
        self._touch(playlist)
        self._audit(
            "playlist.updated",
            "playlist",
            playlist_id,
            "playlist_id",
            change="item_updated",
            item_id=str(item_id),
        )
        await self._commit("playlist.conflict")
        media = await self.media_ids([item_id])
        return self._out(item, media[item_id])

    async def delete_item(self, playlist_id: UUID, item_id: UUID) -> None:
        playlist = await self._writable(playlist_id)
        item = await self._item(playlist_id, item_id)
        await self._s.delete(item)
        await self._s.flush()
        self._renumber(await self.items(playlist_id))
        self._touch(playlist)
        self._audit(
            "playlist.updated",
            "playlist",
            playlist_id,
            "playlist_id",
            change="item_removed",
            item_id=str(item_id),
        )
        await self._commit("playlist.conflict")

    async def reorder(self, playlist_id: UUID, body: sc.ItemOrder) -> None:
        playlist = await self._writable(playlist_id)
        items = await self.items(playlist_id)
        by_id = {i.id: i for i in items}
        if len(body.item_ids) != len(by_id) or set(body.item_ids) != set(by_id):
            raise ApiError("playlist.order_mismatch", 422)
        self._renumber([by_id[i] for i in body.item_ids])
        self._touch(playlist)
        self._audit(
            "playlist.updated", "playlist", playlist_id, "playlist_id", change="items_reordered"
        )
        await self._commit("playlist.conflict")
