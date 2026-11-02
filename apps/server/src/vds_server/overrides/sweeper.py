"""Balayeur d'expiration et purge des forçages (aucune poussée : le player a déjà la fin)."""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from vds_sdk import Event, EventBus

from vds_server.config.schemas import CORE_NAMESPACE, ConfigSchemaProvider
from vds_server.config.service import ConfigService
from vds_server.core import audit
from vds_server.db.models import Organization
from vds_server.db.models_override import PlaybackOverride
from vds_server.overrides.schemas import OverrideExpiredV1

log = logging.getLogger("vds.overrides")

SWEEP_INTERVAL_S = 30.0
DEFAULT_RETENTION_DAYS = 90
AUDIT_IDS_MAX = 100


class OverrideSweeper:
    def __init__(
        self,
        maker: async_sessionmaker[AsyncSession],
        bus: EventBus,
        schemas: ConfigSchemaProvider,
    ) -> None:
        self._maker = maker
        self._bus = bus
        self._schemas = schemas

    async def sweep(self, now: datetime | None = None) -> list[Event]:
        """Marque les forçages échus (`expired`, audit système) puis publie `override.expired`."""
        now = now or datetime.now(UTC)
        events: list[Event] = []
        async with self._maker() as s:
            rows = (
                await s.execute(
                    select(PlaybackOverride)
                    .where(PlaybackOverride.ended_at.is_(None), PlaybackOverride.ends_at <= now)
                    .with_for_update(skip_locked=True)
                )
            ).scalars()
            for row in rows:
                row.ended_at, row.end_reason, row.updated_at = row.ends_at, "expired", now
                audit.record(
                    s,
                    actor_kind="system",
                    actor_id=None,
                    action="override.expire",
                    target_type="override",
                    target_id=str(row.id),
                    details={"target_type": row.target_type, "target_id": str(row.target_id)},
                )
                payload = OverrideExpiredV1(
                    override_id=row.id,
                    target_type=row.target_type,
                    target_id=row.target_id,
                    ended_at=row.ends_at,
                ).model_dump(mode="json")
                events.append(Event(name="override.expired", payload=payload, source="core"))
            await s.commit()
        for event in events:
            await self._bus.publish(event)
        return events

    async def retention_days(self, s: AsyncSession) -> int:
        org = await s.scalar(select(Organization.id))
        if org is None:
            return DEFAULT_RETENTION_DAYS
        res = await ConfigService(s, self._schemas).resolve("organization", org, CORE_NAMESPACE)
        return int(res.values["override.retention_days"].value)

    async def purge(self, now: datetime | None = None) -> int:
        """Supprime les forçages terminés depuis plus de `retention_days` (playlist éphémère en
        cascade). L'audit n'est pas purgé."""
        now = now or datetime.now(UTC)
        async with self._maker() as s:
            cutoff = now - timedelta(days=await self.retention_days(s))
            ids = list(
                (
                    await s.execute(
                        select(PlaybackOverride.id).where(PlaybackOverride.ended_at < cutoff)
                    )
                ).scalars()
            )
            if not ids:
                return 0
            await s.execute(delete(PlaybackOverride).where(PlaybackOverride.id.in_(ids)))
            audit.record(
                s,
                actor_kind="system",
                actor_id=None,
                action="override.purge",
                details={"count": len(ids), "ids": [str(i) for i in ids[:AUDIT_IDS_MAX]]},
            )
            await s.commit()
            return len(ids)

    async def run_once(self) -> None:
        try:
            await self.sweep()
            await self.purge()
        except Exception:
            log.exception("override.sweep_failed")
