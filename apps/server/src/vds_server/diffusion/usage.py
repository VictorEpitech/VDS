"""« Où est-il utilisé » : playlists, programmations et forçages qui portent un média/une playlist.

Les lignes sont filtrées par le `ContentScope` (pas de fuite de noms hors périmètre).
"""

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.db.models import (
    Media,
    Playlist,
    PlaylistItem,
    PlaylistItemMedia,
    Schedule,
    Screen,
    Site,
)
from vds_server.db.models_override import PlaybackOverride
from vds_server.diffusion import schemas as sc
from vds_server.diffusion.expiry import is_expired
from vds_server.diffusion.scope import ContentScope, PerimeterScope
from vds_server.diffusion.targets import TARGET_MODELS


class UsageService:
    def __init__(
        self, session: AsyncSession, actor: Principal, scope: ContentScope | None = None
    ) -> None:
        self._s = session
        self._actor = actor
        self._scope: ContentScope = scope or PerimeterScope(session)

    async def _target_names(self, rows: list[Schedule]) -> dict[tuple[str, UUID], str]:
        names: dict[tuple[str, UUID], str] = {}
        for kind in TARGET_MODELS:
            ids = {r.target_id for r in rows if r.target_type == kind}
            if not ids:
                continue
            model = TARGET_MODELS[kind]
            q = select(model.id, model.name).where(model.id.in_(ids))
            for target_id, name in (await self._s.execute(q)).all():
                names[(kind, target_id)] = name
        return names

    async def _schedules_and_overrides(
        self, playlist_ids: list[UUID], now: datetime
    ) -> tuple[list[sc.UsageSchedule], list[sc.UsageOverride], int, int]:
        """(programmations, forçages, écrans, nombre de lignes hors périmètre)."""
        if not playlist_ids:
            return [], [], 0, 0
        sched_vis = self._scope.target_filter(
            self._actor, Schedule.target_type, Schedule.target_id, "read"
        )
        ov_vis = self._scope.target_filter(
            self._actor, PlaybackOverride.target_type, PlaybackOverride.target_id, "read"
        )
        hidden = int(
            await self._s.scalar(
                select(func.count())
                .select_from(Schedule)
                .where(Schedule.playlist_id.in_(playlist_ids), ~sched_vis)
            )
            or 0
        ) + int(
            await self._s.scalar(
                select(func.count())
                .select_from(PlaybackOverride)
                .where(PlaybackOverride.playlist_id.in_(playlist_ids), ~ov_vis)
            )
            or 0
        )
        today: date = now.date()
        sched = list(
            (
                await self._s.execute(
                    select(Schedule)
                    .where(Schedule.playlist_id.in_(playlist_ids), sched_vis)
                    .order_by(Schedule.name, Schedule.id)
                )
            ).scalars()
        )
        names = await self._target_names(sched)
        schedules = [
            sc.UsageSchedule(
                id=r.id,
                name=r.name,
                playlist_id=r.playlist_id,
                layout_id=r.layout_id,
                target_type=r.target_type,
                target_id=r.target_id,
                target_name=names.get((r.target_type, r.target_id), ""),
                active=r.enabled and (r.ends_on is None or r.ends_on >= today),
            )
            for r in sched
        ]
        ovs = list(
            (
                await self._s.execute(
                    select(PlaybackOverride)
                    .where(PlaybackOverride.playlist_id.in_(playlist_ids), ov_vis)
                    .order_by(PlaybackOverride.ends_at.desc(), PlaybackOverride.id)
                )
            ).scalars()
        )
        overrides = [
            sc.UsageOverride(
                id=o.id,
                name=o.name,
                playlist_id=o.playlist_id,  # type: ignore[arg-type]
                ends_at=o.ends_at,
                active=o.ended_at is None and o.starts_at <= now < o.ends_at,
            )
            for o in ovs
        ]
        targets = [(s.target_type, s.target_id) for s in schedules if s.active]
        targets += [
            (o.target_type, o.target_id)
            for o in ovs
            if o.ended_at is None and o.starts_at <= now < o.ends_at
        ]
        return schedules, overrides, await self._screens(set(targets)), hidden

    async def _screens(self, targets: set[tuple[str, UUID]]) -> int:
        """Écrans actifs couverts par au moins une cible (borne supérieure, indicatif)."""
        if not targets:
            return 0
        conds: list[ColumnElement[bool]] = []
        for kind, target_id in targets:
            if kind == "organization":
                conds = [true()]
                break
            column: Any = {
                "site": Screen.site_id,
                "group": Screen.group_id,
                "screen": Screen.id,
            }[kind]
            conds.append(column == target_id)
        q = (
            select(Screen.id)
            .join(Site, Site.id == Screen.site_id)
            .where(
                Screen.archived_at.is_(None),
                Site.archived_at.is_(None),
                or_(*conds),
                self._scope.screen_filter(self._actor),
            )
        )
        return len((await self._s.execute(q)).all())

    async def media(self, media_id: UUID) -> sc.MediaUsageOut:
        if (
            await self._s.scalar(
                select(Media.id).where(Media.id == media_id, self._scope.media_filter(self._actor))
            )
            is None
        ):
            raise ApiError("media.not_found", 404)
        now = datetime.now(UTC)
        refs = (
            select(PlaylistItem.playlist_id)
            .join(PlaylistItemMedia, PlaylistItemMedia.item_id == PlaylistItem.id)
            .where(PlaylistItemMedia.media_id == media_id)
        )
        hidden_playlists = int(
            await self._s.scalar(
                select(func.count())
                .select_from(Playlist)
                .where(
                    Playlist.id.in_(refs),
                    Playlist.owner_override_id.is_(None),
                    ~self._scope.playlist_filter(self._actor),
                )
            )
            or 0
        )
        rows = list(
            (
                await self._s.execute(
                    select(Playlist)
                    .where(Playlist.id.in_(refs), self._scope.playlist_filter(self._actor))
                    .order_by(Playlist.name, Playlist.id)
                )
            ).scalars()
        )
        visible = [p.id for p in rows]
        schedules, overrides, screens, hidden = await self._schedules_and_overrides(visible, now)
        return sc.MediaUsageOut(
            playlists=[
                sc.UsagePlaylist(
                    id=p.id, name=p.name, expires_at=p.expires_at, expired=is_expired(p, now)
                )
                for p in rows
                if p.owner_override_id is None
            ],
            schedules=schedules,
            overrides=overrides,
            screens_now=screens,
            hidden_count=hidden + hidden_playlists,
        )

    async def playlist(self, playlist_id: UUID) -> sc.PlaylistUsageOut:
        found = await self._s.scalar(
            select(Playlist.id).where(
                Playlist.id == playlist_id, self._scope.playlist_filter(self._actor)
            )
        )
        if found is None:
            raise ApiError("playlist.not_found", 404)
        schedules, overrides, _, hidden = await self._schedules_and_overrides(
            [playlist_id], datetime.now(UTC)
        )
        return sc.PlaylistUsageOut(schedules=schedules, overrides=overrides, hidden_count=hidden)
