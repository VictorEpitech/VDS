"""Lecture des instantanés publiés pour le résolveur (seule source de la diffusion).

Un instantané n'est lu que si la copie de travail existe encore : supprimer une programmation,
une playlist ou une mise en page retire immédiatement son contenu de la diffusion, comme avant
la validation de publication.
"""

from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Text, and_, cast, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.db.models import Playlist, Schedule
from vds_server.db.models_layout import Layout
from vds_server.db.models_publication import Publication
from vds_server.diffusion.layout_compose import LayoutSpec, ZoneSpec
from vds_server.diffusion.resolver import Rule, ScheduleSpec
from vds_server.publication import snapshots as snap


async def playlist_snapshots(
    session: AsyncSession, ids: Collection[UUID]
) -> dict[UUID, snap.PlaylistSnapshotV1]:
    """Instantanés publiés des playlists demandées (celles qui existent encore)."""
    if not ids:
        return {}
    rows = await session.execute(
        select(Publication.entity_id, Publication.snapshot)
        .join(Playlist, Playlist.id == Publication.entity_id)
        .where(
            Publication.entity_type == "playlist",
            Publication.snapshot.is_not(None),
            Publication.entity_id.in_(set(ids)),
        )
    )
    return {
        eid: snap.PlaylistSnapshotV1.model_validate(raw) for eid, raw in rows if raw is not None
    }


async def playlist_expiries(
    session: AsyncSession, ids: Collection[UUID]
) -> dict[UUID, datetime | None]:
    return {pid: s.expires_at for pid, s in (await playlist_snapshots(session, ids)).items()}


def _chain_conds(chains: Sequence[dict[str, UUID]]) -> list[Any]:
    pairs = sorted({(lvl, tid) for c in chains for lvl, tid in c.items()}, key=str)
    return [
        and_(
            Publication.snapshot["target_type"].astext == lvl,
            Publication.snapshot["target_id"].astext == str(tid),
        )
        for lvl, tid in pairs
    ]


async def schedule_specs(
    session: AsyncSession, chains: Sequence[dict[str, UUID]]
) -> list[ScheduleSpec]:
    """Programmations publiées et activées ciblant l'une des chaînes (2 requêtes en tout)."""
    if not chains:
        return []
    rows = (
        await session.execute(
            select(Publication.entity_id, Publication.snapshot)
            .join(Schedule, Schedule.id == Publication.entity_id)
            .where(
                Publication.entity_type == "schedule",
                Publication.snapshot.is_not(None),
                Publication.snapshot["enabled"].astext == "true",
                or_(*_chain_conds(chains)),
            )
        )
    ).all()
    loaded = [(eid, snap.ScheduleSnapshotV1.model_validate(raw)) for eid, raw in rows]
    expiries = await playlist_expiries(
        session, {s.playlist_id for _, s in loaded if s.playlist_id is not None}
    )
    return [
        ScheduleSpec(
            id=eid,
            playlist_id=s.playlist_id,
            target_type=s.target_type,
            target_id=s.target_id,
            priority=s.priority,
            updated_at=s.updated_at,
            starts_on=s.starts_on,
            ends_on=s.ends_on,
            enabled=s.enabled,
            rules=tuple(Rule(r.weekdays, r.start_minute, r.end_minute) for r in s.rules),
            until=None if s.playlist_id is None else expiries.get(s.playlist_id),
            layout_id=s.layout_id,
        )
        for eid, s in loaded
    ]


async def layout_specs(
    session: AsyncSession, ids: Collection[UUID], *, org_id: UUID | None = None
) -> dict[UUID, LayoutSpec]:
    """Mises en page publiées (zones incluses) ; hors organisation ou supprimées = absentes."""
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(Publication.entity_id, Publication.snapshot)
            .join(Layout, Layout.id == Publication.entity_id)
            .where(
                Publication.entity_type == "layout",
                Publication.snapshot.is_not(None),
                Publication.entity_id.in_(set(ids)),
            )
        )
    ).all()
    layouts = [(eid, snap.LayoutSnapshotV1.model_validate(raw)) for eid, raw in rows]
    if org_id is not None:
        layouts = [(eid, s) for eid, s in layouts if s.org_id == org_id]
    playlists = await playlist_snapshots(
        session, {z.playlist_id for _, s in layouts for z in s.zones if z.playlist_id is not None}
    )
    return {
        eid: LayoutSpec(
            id=eid,
            revision=s.revision,
            orientation=s.orientation,
            background_color=s.background_color,
            background_media_id=s.background_media_id,
            zones=tuple(
                ZoneSpec(
                    key=z.key,
                    name=z.name,
                    x=z.x,
                    y=z.y,
                    w=z.w,
                    h=z.h,
                    z=z.z,
                    fit=z.fit,
                    playlist_id=z.playlist_id if z.playlist_id in playlists else None,
                    default_item_duration_ms=z.default_item_duration_ms,
                    active_from=z.active_from,
                    active_until=z.active_until,
                    playlist_expires_at=(
                        playlists[z.playlist_id].expires_at if z.playlist_id in playlists else None
                    ),
                )
                for z in s.zones
            ),
        )
        for eid, s in layouts
    }


async def published_schedules_of_playlist(session: AsyncSession, playlist_id: UUID) -> list[UUID]:
    """Programmations dont l'instantané publié diffuse la playlist (suppression refusée)."""
    rows = await session.scalars(
        select(Publication.entity_id)
        .join(Schedule, Schedule.id == Publication.entity_id)
        .where(
            Publication.entity_type == "schedule",
            Publication.snapshot["playlist_id"].astext == str(playlist_id),
        )
        .order_by(Publication.entity_id)
    )
    return list(rows)


async def published_layouts_of_playlist(session: AsyncSession, playlist_id: UUID) -> list[UUID]:
    """Mises en page dont l'instantané publié diffuse la playlist dans une zone."""
    rows = await session.scalars(
        select(Publication.entity_id)
        .join(Layout, Layout.id == Publication.entity_id)
        .where(
            Publication.entity_type == "layout",
            Publication.snapshot["zones"].contains([{"playlist_id": str(playlist_id)}]),
        )
        .order_by(Publication.entity_id)
    )
    return list(rows)


async def published_schedules_of_layout(session: AsyncSession, layout_id: UUID) -> list[UUID]:
    rows = await session.scalars(
        select(Publication.entity_id)
        .join(Schedule, Schedule.id == Publication.entity_id)
        .where(
            Publication.entity_type == "schedule",
            Publication.snapshot["layout_id"].astext == str(layout_id),
        )
        .order_by(Publication.entity_id)
    )
    return list(rows)


async def playlists_publishing_media(session: AsyncSession, media_id: UUID) -> list[UUID]:
    """Playlists dont l'instantané publié référence le média (compté dans son usage)."""
    rows = await session.scalars(
        select(Publication.entity_id)
        .join(Playlist, Playlist.id == Publication.entity_id)
        .where(
            Publication.entity_type == "playlist",
            Publication.snapshot["media_ids"].contains([str(media_id)]),
        )
        .order_by(Publication.entity_id)
    )
    return list(rows)


async def layouts_publishing_media(session: AsyncSession, media_id: UUID) -> list[UUID]:
    """Mises en page dont l'instantané publié utilise le média en fond."""
    rows = await session.scalars(
        select(Publication.entity_id)
        .join(Layout, Layout.id == Publication.entity_id)
        .where(
            Publication.entity_type == "layout",
            Publication.snapshot["background_media_id"].astext == str(media_id),
        )
        .order_by(Publication.entity_id)
    )
    return list(rows)


def published_media_clause() -> ColumnElement[bool]:
    """Le média (table `media`) n'est référencé que par un instantané publié (playlist ou fond).

    Les références de la copie de travail (éléments de playlist) sont traitées à part : la purge
    les retire avec le média. Faux dès que la copie de travail référence aussi le média."""
    from vds_server.db.models import Media, PlaylistItemMedia

    in_playlist = exists().where(
        Publication.entity_type == "playlist",
        Publication.entity_id == Playlist.id,
        Publication.snapshot["media_ids"].has_key(cast(Media.id, Text)),
    )
    as_background = exists().where(
        Publication.entity_type == "layout",
        Publication.entity_id == Layout.id,
        Publication.snapshot["background_media_id"].astext == cast(Media.id, Text),
    )
    in_working_copy = exists().where(PlaylistItemMedia.media_id == Media.id)
    return (in_playlist | as_background) & ~in_working_copy


async def strip_media(session: AsyncSession, media_id: UUID) -> None:
    """Retire des instantanés de playlist les éléments qui utilisent un média purgé.

    L'instantané ne change pas autrement (ni état, ni révision) : ces éléments ne pouvaient déjà
    plus jouer (média expiré)."""
    wanted = str(media_id)
    rows = await session.scalars(
        select(Publication)
        .where(
            Publication.entity_type == "playlist",
            Publication.snapshot["media_ids"].contains([wanted]),
        )
        .with_for_update()
    )
    for row in rows:
        raw = dict(row.snapshot or {})
        kept = [i for i in raw.get("items", []) if wanted not in i.get("media_ids", [])]
        raw["items"] = kept
        raw["media_ids"] = sorted(
            {m for i in kept if i.get("enabled", True) for m in i.get("media_ids", [])}
        )
        row.snapshot = raw


async def is_published(session: AsyncSession, entity_type: str, entity_id: UUID) -> bool:
    """L'objet a un instantané publié (donc peut être joué)."""
    return (
        await session.scalar(
            select(Publication.entity_id).where(
                Publication.entity_type == entity_type,
                Publication.entity_id == entity_id,
                Publication.snapshot.is_not(None),
            )
        )
    ) is not None
