"""Lecture historique (avant la validation de publication) des entrées du résolveur.

Copie fidèle des requêtes qui lisaient la copie de travail (`schedule`, `playlist_item`,
`layout_zone`). Sert d'oracle au test d'équivalence : tant qu'aucun éditeur ne change rien, le
document player lu depuis les instantanés doit être identique octet pour octet.
"""

from collections.abc import Collection, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from vds_server.db.models import (
    Media,
    Playlist,
    PlaylistItem,
    PlaylistItemMedia,
    Schedule,
    ScheduleRule,
    Screen,
)
from vds_server.db.models_layout import Layout, LayoutZone
from vds_server.db.models_override import PlaybackOverride
from vds_server.diffusion import resolver
from vds_server.diffusion.layout_compose import LayoutSpec, ZoneSpec
from vds_server.diffusion.resolver import ScheduleSpec
from vds_server.player import schedule as ps


async def fetch_specs(
    session: AsyncSession, chains: Sequence[dict[str, UUID]]
) -> list[ScheduleSpec]:
    if not chains:
        return []
    found_rows = (
        await session.execute(
            select(Schedule, Playlist.expires_at)
            .outerjoin(Playlist, Playlist.id == Schedule.playlist_id)
            .where(Schedule.enabled.is_(True), or_(*ps._chain_conds(chains, Schedule)))
        )
    ).all()
    rows = [r for r, _ in found_rows]
    until = {r.id: exp for r, exp in found_rows}
    rules: dict[UUID, list[resolver.Rule]] = {r.id: [] for r in rows}
    if rows:
        found = await session.execute(
            select(ScheduleRule)
            .where(ScheduleRule.schedule_id.in_(list(rules)))
            .order_by(ScheduleRule.start_minute, ScheduleRule.end_minute, ScheduleRule.weekdays)
        )
        for r in found.scalars():
            rules[r.schedule_id].append(resolver.Rule(r.weekdays, r.start_minute, r.end_minute))
    return [
        ScheduleSpec(
            id=r.id,
            playlist_id=r.playlist_id,
            target_type=r.target_type,
            target_id=r.target_id,
            priority=r.priority,
            updated_at=r.updated_at,
            starts_on=r.starts_on,
            ends_on=r.ends_on,
            enabled=r.enabled,
            rules=tuple(rules[r.id]),
            until=until[r.id],
            layout_id=r.layout_id,
        )
        for r in rows
    ]


async def fetch_override_rows(
    session: AsyncSession,
    chains: Sequence[dict[str, UUID]],
    now: datetime,
    *,
    zones: bool = False,
) -> list[tuple[PlaybackOverride, datetime | None]]:
    if not chains:
        return []
    rows = (
        await session.execute(
            select(PlaybackOverride, Playlist.expires_at)
            .outerjoin(Playlist, Playlist.id == PlaybackOverride.playlist_id)
            .where(
                PlaybackOverride.ends_at > now - ps.OVERRIDE_LOOKBACK,
                PlaybackOverride.zone_ids.is_not(None)
                if zones
                else PlaybackOverride.zone_ids.is_(None),
                or_(*ps._chain_conds(chains, PlaybackOverride)),
            )
        )
    ).all()
    return [(r, e) for r, e in rows]


async def load_layouts(
    session: AsyncSession, ids: Collection[UUID], *, org_id: UUID | None = None
) -> dict[UUID, LayoutSpec]:
    if not ids:
        return {}
    q = select(Layout).where(Layout.id.in_(set(ids)))
    if org_id is not None:
        q = q.where(Layout.org_id == org_id)
    layouts = list((await session.execute(q)).scalars())
    zones: dict[UUID, list[ZoneSpec]] = {lo.id: [] for lo in layouts}
    if layouts:
        rows = await session.execute(
            select(LayoutZone, Playlist.expires_at)
            .outerjoin(Playlist, Playlist.id == LayoutZone.playlist_id)
            .where(LayoutZone.layout_id.in_(list(zones)))
            .order_by(LayoutZone.z, LayoutZone.key)
        )
        for z, expires_at in rows:
            zones[z.layout_id].append(
                ZoneSpec(
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
                    playlist_expires_at=expires_at,
                )
            )
    return {
        lo.id: LayoutSpec(
            id=lo.id,
            revision=lo.revision,
            orientation=lo.orientation,
            background_color=lo.background_color,
            background_media_id=lo.background_media_id,
            zones=tuple(zones[lo.id]),
        )
        for lo in layouts
    }


async def playlists(
    self: ps.ScheduleBuilder, ids: set[UUID], now: datetime
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    out_playlists: dict[str, Any] = {}
    assets: dict[str, Any] = {}
    types: dict[str, Any] = {}
    for pid in sorted(ids):
        items_q = (
            select(PlaylistItem)
            .where(PlaylistItem.playlist_id == pid, PlaylistItem.enabled.is_(True))
            .order_by(PlaylistItem.position)
        )
        out: list[dict[str, Any]] = []
        for item in (await self._s.execute(items_q)).scalars():
            built = await self._item(item, now)  # type: ignore[arg-type]
            if built is None:
                continue
            entry, item_assets = built
            out.append(entry)
            assets.update(item_assets)
            ctype = item.content_type
            if ctype not in types and (info := self._entry(ctype)):
                types[ctype] = {k: info[k] for k in ("version", "element", "script")}
        out_playlists[ps._pl(pid)] = {"items": out}
    return out_playlists, assets, types


async def screen_media_ids(
    session: AsyncSession, screen_id: UUID, *, include_expired: bool = False
) -> set[UUID]:
    screen = await session.get(Screen, screen_id)
    if screen is None or screen.archived_at is not None:
        return set()
    specs = await ps.load_specs(session, await ps.screen_chain(session, screen))
    now = datetime.now(UTC)
    from datetime import timedelta

    horizon = now.date() - timedelta(days=1)
    pls = {
        s.playlist_id
        for s in specs
        if s.playlist_id is not None
        and (s.ends_on is None or s.ends_on >= horizon)
        and (include_expired or s.until is None or s.until > now)
    }
    chain = await ps.screen_chain(session, screen)
    layout_ids = {
        s.layout_id
        for s in specs
        if s.layout_id is not None and (s.ends_on is None or s.ends_on >= horizon)
    }
    overrides = [o for o in await ps.load_overrides(session, chain, now) if o.ends_at > now]
    pls |= {o.playlist_id for o in overrides if o.playlist_id is not None}
    layout_ids |= {o.layout_id for o in overrides if o.layout_id is not None}
    zone_ovs = await ps.select_zone_overrides(
        session, screen_id=screen_id, window_start=now, window_end=now + timedelta(days=3)
    )
    pls |= {z.playlist_id for z in zone_ovs}
    media: set[UUID] = set()
    if layout_ids:
        zone_playlists = await session.execute(
            select(LayoutZone.playlist_id).where(
                LayoutZone.layout_id.in_(layout_ids), LayoutZone.playlist_id.is_not(None)
            )
        )
        pls |= {p for p in zone_playlists.scalars() if p is not None}
        backgrounds = await session.execute(
            select(Layout.background_media_id).where(
                Layout.id.in_(layout_ids), Layout.background_media_id.is_not(None)
            )
        )
        media |= {m for m in backgrounds.scalars() if m is not None}
    if not pls:
        return media
    q = (
        select(PlaylistItemMedia.media_id)
        .join(PlaylistItem, PlaylistItem.id == PlaylistItemMedia.item_id)
        .where(PlaylistItem.playlist_id.in_(pls), PlaylistItem.enabled.is_(True))
    )
    if not include_expired:
        q = q.join(Media, Media.id == PlaylistItemMedia.media_id).where(
            Media.expired_at.is_(None), or_(Media.expires_at.is_(None), Media.expires_at > now)
        )
    return media | set((await session.execute(q)).scalars())
