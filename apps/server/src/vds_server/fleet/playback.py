"""Programmation active par écran, en lecture seule (réutilise le résolveur du player)."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.db.models import Playlist, Schedule, Screen, Site
from vds_server.diffusion import resolver
from vds_server.player.schedule import _zone, load_specs, screen_chain

TimezoneOf = Callable[[UUID], Awaitable[object]]


class ScreenPlaybackOut(BaseModel):
    screen_id: UUID
    source: Literal["schedule", "none"]  # "override" ajouté en lot H, additif
    schedule_id: UUID | None = None
    schedule_name: str | None = None
    playlist_id: UUID | None = None
    playlist_name: str | None = None
    until: datetime | None = None
    next_change_at: datetime | None = None


async def fleet_playback(
    session: AsyncSession, site_id: UUID | None, now: datetime, timezone_of: TimezoneOf
) -> list[ScreenPlaybackOut]:
    q = (
        select(Screen)
        .join(Site, Site.id == Screen.site_id)
        .where(Screen.archived_at.is_(None), Site.archived_at.is_(None))
        .order_by(Screen.name, Screen.id)
    )
    if site_id is not None:
        q = q.where(Screen.site_id == site_id)
    out: list[ScreenPlaybackOut] = []
    for screen in (await session.execute(q)).scalars():
        tz = _zone(await timezone_of(screen.id))
        chain = await screen_chain(session, screen)
        slots = resolver.resolve(
            chain, await load_specs(session, chain), tz, now.astimezone(tz).date()
        )
        idx = next((i for i, s in enumerate(slots) if s.start <= now < s.end), None)
        if idx is None:
            out.append(ScreenPlaybackOut(screen_id=screen.id, source="none"))
            continue
        cur = slots[idx]
        nxt = slots[idx + 1].start.astimezone(UTC) if idx + 1 < len(slots) else None
        until = cur.end.astimezone(UTC)
        if cur.schedule_id is None or cur.playlist_id is None:
            out.append(
                ScreenPlaybackOut(
                    screen_id=screen.id, source="none", until=until, next_change_at=nxt
                )
            )
            continue
        sched_name = (
            await session.execute(select(Schedule.name).where(Schedule.id == cur.schedule_id))
        ).scalar_one_or_none()
        pl_name = (
            await session.execute(select(Playlist.name).where(Playlist.id == cur.playlist_id))
        ).scalar_one_or_none()
        out.append(
            ScreenPlaybackOut(
                screen_id=screen.id,
                source="schedule",
                schedule_id=cur.schedule_id,
                schedule_name=sched_name,
                playlist_id=cur.playlist_id,
                playlist_name=pl_name,
                until=until,
                next_change_at=nxt,
            )
        )
    return out
