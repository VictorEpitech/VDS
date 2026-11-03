"""DTO d'instantané publié (V1) : entrée unique du résolveur de programmation.

Chaque DTO est construit depuis la copie de travail (ORM) puis sérialisé dans
`publication.snapshot`. Le résolveur ne consomme que ces DTO (convertis en `ScheduleSpec`,
`LayoutSpec`, éléments de playlist par `publication.reader`).
"""

from datetime import date, datetime
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.db.models import (
    Playlist,
    PlaylistItem,
    PlaylistItemMedia,
    Schedule,
    ScheduleRule,
)
from vds_server.db.models_layout import Layout, LayoutZone

EntityType = Literal["playlist", "layout", "schedule"]
ENTITY_TYPES: tuple[EntityType, ...] = ("playlist", "layout", "schedule")


class _Snap(BaseModel):
    model_config = ConfigDict(frozen=True)


class RuleSnap(_Snap):
    weekdays: int
    start_minute: int
    end_minute: int


class ScheduleSnapshotV1(_Snap):
    v: Literal[1] = 1
    playlist_id: UUID | None
    layout_id: UUID | None
    target_type: str
    target_id: UUID
    priority: int
    updated_at: datetime
    starts_on: date | None
    ends_on: date | None
    enabled: bool
    rules: tuple[RuleSnap, ...]


class ItemSnap(_Snap):
    id: UUID
    position: int
    content_type: str
    props: Any
    duration_ms: int | None
    enabled: bool
    media_ids: tuple[UUID, ...] = ()


class PlaylistSnapshotV1(_Snap):
    v: Literal[1] = 1
    expires_at: datetime | None
    items: tuple[ItemSnap, ...]

    def media_ids(self) -> list[str]:
        """Médias référencés (éléments actifs), triés : usage, purge, autorisation de lecture."""
        return sorted({str(m) for i in self.items if i.enabled for m in i.media_ids})


class ZoneSnap(_Snap):
    key: str
    name: str
    x: int
    y: int
    w: int
    h: int
    z: int
    fit: str
    playlist_id: UUID | None
    default_item_duration_ms: int | None
    active_from: datetime | None
    active_until: datetime | None


class LayoutSnapshotV1(_Snap):
    v: Literal[1] = 1
    org_id: UUID
    revision: int
    orientation: str
    background_color: str | None
    background_media_id: UUID | None
    zones: tuple[ZoneSnap, ...]


Snapshot = ScheduleSnapshotV1 | PlaylistSnapshotV1 | LayoutSnapshotV1
_MODELS: dict[str, type[BaseModel]] = {
    "schedule": ScheduleSnapshotV1,
    "playlist": PlaylistSnapshotV1,
    "layout": LayoutSnapshotV1,
}


def dump(snapshot: Snapshot) -> dict[str, Any]:
    out: dict[str, Any] = snapshot.model_dump(mode="json")
    if isinstance(snapshot, PlaylistSnapshotV1):
        out["media_ids"] = snapshot.media_ids()  # filtre `@>` du suivi d'usage des médias
    return out


def load(entity_type: str, raw: dict[str, Any]) -> Snapshot:
    return cast(Snapshot, _MODELS[entity_type].model_validate(raw))


async def build_playlist(session: AsyncSession, playlist_id: UUID) -> PlaylistSnapshotV1 | None:
    row = await session.get(Playlist, playlist_id)
    if row is None:
        return None
    await session.refresh(row)
    items = list(
        (
            await session.execute(
                select(PlaylistItem)
                .where(PlaylistItem.playlist_id == playlist_id)
                .order_by(PlaylistItem.position)
            )
        ).scalars()
    )
    media: dict[UUID, list[UUID]] = {i.id: [] for i in items}
    if items:
        found = await session.execute(
            select(PlaylistItemMedia.item_id, PlaylistItemMedia.media_id)
            .where(PlaylistItemMedia.item_id.in_(list(media)))
            .order_by(PlaylistItemMedia.media_id)
        )
        for item_id, media_id in found:
            media[item_id].append(media_id)
    return PlaylistSnapshotV1(
        expires_at=row.expires_at,
        items=tuple(
            ItemSnap(
                id=i.id,
                position=i.position,
                content_type=i.content_type,
                props=dict(i.props or {}),
                duration_ms=i.duration_ms,
                enabled=i.enabled,
                media_ids=tuple(media[i.id]),
            )
            for i in items
        ),
    )


async def build_layout(session: AsyncSession, layout_id: UUID) -> LayoutSnapshotV1 | None:
    row = await session.get(Layout, layout_id)
    if row is None:
        return None
    await session.refresh(row)
    zones = (
        await session.execute(
            select(LayoutZone)
            .where(LayoutZone.layout_id == layout_id)
            .order_by(LayoutZone.z, LayoutZone.key)
        )
    ).scalars()
    return LayoutSnapshotV1(
        org_id=row.org_id,
        revision=row.revision,
        orientation=row.orientation,
        background_color=row.background_color,
        background_media_id=row.background_media_id,
        zones=tuple(
            ZoneSnap(
                key=z.key,
                name=z.name,
                x=z.x,
                y=z.y,
                w=z.w,
                h=z.h,
                z=z.z,
                fit=z.fit,
                playlist_id=z.playlist_id,
                default_item_duration_ms=z.default_item_duration_ms,
                active_from=z.active_from,
                active_until=z.active_until,
            )
            for z in zones
        ),
    )


async def build_schedule(session: AsyncSession, schedule_id: UUID) -> ScheduleSnapshotV1 | None:
    row = await session.get(Schedule, schedule_id)
    if row is None:
        return None
    await session.refresh(row)
    rules = (
        await session.execute(
            select(ScheduleRule)
            .where(ScheduleRule.schedule_id == schedule_id)
            .order_by(ScheduleRule.start_minute, ScheduleRule.end_minute, ScheduleRule.weekdays)
        )
    ).scalars()
    return ScheduleSnapshotV1(
        playlist_id=row.playlist_id,
        layout_id=row.layout_id,
        target_type=row.target_type,
        target_id=row.target_id,
        priority=row.priority,
        updated_at=row.updated_at,
        starts_on=row.starts_on,
        ends_on=row.ends_on,
        enabled=row.enabled,
        rules=tuple(
            RuleSnap(weekdays=r.weekdays, start_minute=r.start_minute, end_minute=r.end_minute)
            for r in rules
        ),
    )


async def build(session: AsyncSession, entity_type: str, entity_id: UUID) -> Snapshot | None:
    if entity_type == "playlist":
        return await build_playlist(session, entity_id)
    if entity_type == "layout":
        return await build_layout(session, entity_id)
    return await build_schedule(session, entity_id)


def dependencies(snapshot: Snapshot) -> list[tuple[str, UUID]]:
    """Objets qui doivent avoir un instantané publié pour que celui-ci ait un sens."""
    deps: set[tuple[str, UUID]] = set()
    if isinstance(snapshot, ScheduleSnapshotV1):
        if snapshot.playlist_id is not None:
            deps.add(("playlist", snapshot.playlist_id))
        if snapshot.layout_id is not None:
            deps.add(("layout", snapshot.layout_id))
    elif isinstance(snapshot, LayoutSnapshotV1):
        deps |= {("playlist", z.playlist_id) for z in snapshot.zones if z.playlist_id is not None}
    return sorted(deps, key=lambda d: (d[0], str(d[1])))
