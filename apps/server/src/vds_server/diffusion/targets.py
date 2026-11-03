"""Cibles de diffusion : écrans actifs couverts par une cible (org, site, groupe, écran)."""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.db.models import Organization, Screen, ScreenGroup, Site

TARGET_MODELS: dict[str, Any] = {
    "organization": Organization,
    "site": Site,
    "group": ScreenGroup,
    "screen": Screen,
}


async def screens_of(session: AsyncSession, target_type: str, target_id: UUID) -> list[Screen]:
    """Écrans actifs (non archivés, site non archivé) couverts par une cible."""
    q = (
        select(Screen)
        .join(Site, Site.id == Screen.site_id)
        .where(Screen.archived_at.is_(None), Site.archived_at.is_(None))
        .order_by(Screen.id)
    )
    if target_type == "site":
        q = q.where(Screen.site_id == target_id)
    elif target_type == "group":
        q = q.where(Screen.group_id == target_id)
    elif target_type == "screen":
        q = q.where(Screen.id == target_id)
    return list((await session.execute(q)).scalars())
