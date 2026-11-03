"""Aides de test : republier la copie de travail modifiée en SQL direct (hors services)."""

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from vds_server.db.models import Playlist, Schedule
from vds_server.db.models_layout import Layout
from vds_server.publication.service import PublicationService


async def republish_all(maker: async_sessionmaker[AsyncSession]) -> None:
    """Publie d'office tous les objets (équivalent d'un enregistrement admin)."""
    async with maker() as session:
        svc = PublicationService(session, None)
        for kind, model in (("playlist", Playlist), ("layout", Layout), ("schedule", Schedule)):
            for entity_id in (await session.scalars(select(model.id))).all():
                await svc.publish_system(kind, entity_id)
        await session.commit()


async def disable_review(admin: AsyncClient, prefix: str) -> None:
    """Désactive la validation (`diffusion.review_required`) : l'éditeur publie d'office."""
    org = (await admin.get(f"{prefix}/api/v1/organization")).json()["id"]
    r = await admin.put(
        f"{prefix}/api/v1/config/organization/{org}/core/diffusion.review_required",
        json={"value": False},
        headers={"Origin": "https://vds.test"},
    )
    assert r.status_code in (200, 204), r.text
