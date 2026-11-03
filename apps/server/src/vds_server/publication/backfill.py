"""Reprise de l'existant : un instantané publié pour chaque objet sans ligne `publication`.

Idempotente (INSERT ... ON CONFLICT DO NOTHING) et sûre en multi-workers. Exécutée au démarrage,
avant de servir les players : le résolveur ne lit jamais la copie de travail.
Retire aussi les lignes orphelines (objet supprimé hors des services).
"""

import logging
from typing import Any

from sqlalchemy import delete, exists, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vds_server.db.models import Playlist, Schedule
from vds_server.db.models_layout import Layout
from vds_server.db.models_publication import Publication
from vds_server.publication import snapshots as snap

log = logging.getLogger("vds.publication")
MODELS: dict[str, Any] = {"playlist": Playlist, "layout": Layout, "schedule": Schedule}


async def backfill_session(session: AsyncSession) -> int:
    """Crée les lignes manquantes (état `published`) ; retourne leur nombre. Ne commit pas."""
    created = 0
    for entity_type, model in MODELS.items():
        have = select(Publication.entity_id).where(
            Publication.entity_type == entity_type, Publication.entity_id == model.id
        )
        ids = list((await session.scalars(select(model.id).where(~exists(have)))).all())
        for entity_id in ids:
            built = await snap.build(session, entity_type, entity_id)
            if built is None:
                continue
            result = await session.execute(
                pg_insert(Publication)
                .values(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    state="published",
                    working_rev=1,
                    published_rev=1,
                    snapshot=snap.dump(built),
                )
                .on_conflict_do_nothing()
            )
            created += int(result.rowcount or 0)  # type: ignore[attr-defined]
    for entity_type, model in MODELS.items():
        alive = select(model.id).where(model.id == Publication.entity_id)
        await session.execute(
            delete(Publication).where(Publication.entity_type == entity_type, ~exists(alive))
        )
    return created


async def ensure_published_baseline(maker: async_sessionmaker[AsyncSession]) -> int:
    async with maker() as session:
        created = await backfill_session(session)
        await session.commit()
    if created:
        log.info("publication.baseline created=%d", created)
    return created
