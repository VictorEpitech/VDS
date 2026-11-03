"""Chargement des mises en page et résolution du contenu d'un créneau `play` (vds-core 1.9.0).

`resolve_play_content` est l'unique point qui transforme la cible d'un créneau (playlist ou mise
en page) en `PlaySlot.layout` ; la composition elle-même est pure (`layout_compose`).
"""

from collections.abc import Collection, Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.db.models import Playlist
from vds_server.db.models_layout import Layout, LayoutZone
from vds_server.diffusion.layout_compose import (
    ActiveZoneOverride,
    LayoutSpec,
    ResolvedDefaults,
    ResolvedLayout,
    ZoneSpec,
    compose_play_content,
)


async def load_layouts(
    session: AsyncSession, ids: Collection[UUID], *, org_id: UUID | None = None
) -> dict[UUID, LayoutSpec]:
    """Mises en page demandées (zones incluses), 2 requêtes ; hors organisation = absentes."""
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
                )  # fmt: skip
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


async def resolve_play_content(
    session: AsyncSession,
    *,
    org_id: UUID,
    playlist_id: UUID | None,
    layout_id: UUID | None,
    slot_start: datetime,
    slot_end: datetime,
    defaults: ResolvedDefaults,
    zone_overrides: Sequence[ActiveZoneOverride] = (),
    layouts: dict[UUID, LayoutSpec] | None = None,
) -> ResolvedLayout:
    """Playlist -> zone `main` plein écran (document 1.8) ; mise en page -> zones filtrées.

    `layouts` : cache de mises en page déjà chargées (évite une requête par créneau). Une mise en
    page introuvable (supprimée entre-temps) donne une scène vide, jamais une erreur."""
    spec: LayoutSpec | None = None
    if layout_id is not None:
        found = layouts
        if found is None:
            found = await load_layouts(session, [layout_id], org_id=org_id)
        spec = found.get(layout_id) or LayoutSpec(layout_id, 1, "landscape", None, None, ())
    return compose_play_content(
        playlist_id=playlist_id if layout_id is None else None,
        layout=spec,
        slot_start=slot_start,
        slot_end=slot_end,
        defaults=defaults,
        zone_overrides=zone_overrides,
    )
