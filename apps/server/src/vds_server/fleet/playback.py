"""Programmation active par écran, en lecture seule (réutilise le résolveur du player)."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.db.models import Playlist, Schedule, Screen, Site
from vds_server.db.models_layout import Layout
from vds_server.db.models_override import PlaybackOverride
from vds_server.diffusion import resolver
from vds_server.player.schedule import _zone, load_overrides, load_specs, screen_chain

TimezoneOf = Callable[[UUID], Awaitable[object]]
RespectOf = Callable[[UUID], Awaitable[object]]


class ScreenPlaybackOut(BaseModel):
    screen_id: UUID
    source: Literal["schedule", "override", "none"]  # "override" : vds-core 1.7.0 (additif)
    override_id: UUID | None = None
    override_name: str | None = None
    schedule_id: UUID | None = None
    schedule_name: str | None = None
    playlist_id: UUID | None = None
    playlist_name: str | None = None
    layout_id: UUID | None = None
    layout_name: str | None = None
    until: datetime | None = None
    next_change_at: datetime | None = None


async def fleet_playback(
    session: AsyncSession,
    site_id: UUID | None,
    now: datetime,
    timezone_of: TimezoneOf,
    respect_of: RespectOf | None = None,
    screen_filter: ColumnElement[bool] | None = None,
) -> list[ScreenPlaybackOut]:
    q = (
        select(Screen)
        .join(Site, Site.id == Screen.site_id)
        .where(Screen.archived_at.is_(None), Site.archived_at.is_(None))
        .order_by(Screen.name, Screen.id)
    )
    if site_id is not None:
        q = q.where(Screen.site_id == site_id)
    if screen_filter is not None:
        q = q.where(screen_filter)
    out: list[ScreenPlaybackOut] = []
    for screen in (await session.execute(q)).scalars():
        tz = _zone(await timezone_of(screen.id))
        chain = await screen_chain(session, screen)
        respect = bool(await respect_of(screen.id)) if respect_of else False
        overrides = await load_overrides(session, chain, now, respect)
        slots = resolver.resolve(
            chain, await load_specs(session, chain), tz, now.astimezone(tz).date(), overrides
        )
        idx = next((i for i, s in enumerate(slots) if s.start <= now < s.end), None)
        if idx is None:
            out.append(ScreenPlaybackOut(screen_id=screen.id, source="none"))
            continue
        cur = slots[idx]
        nxt = slots[idx + 1].start.astimezone(UTC) if idx + 1 < len(slots) else None
        until = cur.end.astimezone(UTC)
        if cur.is_off:
            out.append(
                ScreenPlaybackOut(
                    screen_id=screen.id,
                    source="none" if cur.override_id is None else "override",
                    override_id=cur.override_id,
                    until=until,
                    next_change_at=nxt,
                )
            )
            continue
        sched_name = (
            None
            if cur.schedule_id is None
            else (
                await session.execute(select(Schedule.name).where(Schedule.id == cur.schedule_id))
            ).scalar_one_or_none()
        )
        ov_name = (
            None
            if cur.override_id is None
            else (
                await session.execute(
                    select(PlaybackOverride.name).where(PlaybackOverride.id == cur.override_id)
                )
            ).scalar_one_or_none()
        )
        pl_name = (
            None
            if cur.playlist_id is None
            else (
                await session.execute(select(Playlist.name).where(Playlist.id == cur.playlist_id))
            ).scalar_one_or_none()
        )
        lay_name = (
            None
            if cur.layout_id is None
            else (
                await session.execute(select(Layout.name).where(Layout.id == cur.layout_id))
            ).scalar_one_or_none()
        )
        out.append(
            ScreenPlaybackOut(
                screen_id=screen.id,
                source=cur.source if cur.source != "off" else "none",
                override_id=cur.override_id,
                override_name=ov_name,
                schedule_id=cur.schedule_id,
                schedule_name=sched_name,
                playlist_id=cur.playlist_id,
                playlist_name=pl_name,
                layout_id=cur.layout_id,
                layout_name=lay_name,
                until=until,
                next_change_at=nxt,
            )
        )
    return out
