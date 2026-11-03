"""Chargement des mises en page et résolution du contenu d'un créneau `play` (vds-core 1.9.0).

`resolve_play_content` est l'unique point qui transforme la cible d'un créneau (playlist ou mise
en page) en `PlaySlot.layout` ; la composition elle-même est pure (`layout_compose`).
"""

from collections.abc import Collection, Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.diffusion.layout_compose import (
    ActiveZoneOverride,
    LayoutSpec,
    ResolvedDefaults,
    ResolvedLayout,
    compose_play_content,
)
from vds_server.publication.reader import layout_specs


async def load_layouts(
    session: AsyncSession, ids: Collection[UUID], *, org_id: UUID | None = None
) -> dict[UUID, LayoutSpec]:
    """Mises en page publiées (instantanés, zones incluses) ; hors organisation = absentes."""
    return await layout_specs(session, ids, org_id=org_id)


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
