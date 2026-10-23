"""Ouverture/fermeture/acquittement des incidents ; un seul incident ouvert par sujet et kind."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from vds_sdk import Event, EventBus

from vds_server.db.models import Screen, Site
from vds_server.db.models_power import Incident, SiteAgent

Severity = str


def _event(name: str, row: Incident) -> Event:
    return Event(
        name=name,
        payload={
            "incident_id": str(row.id),
            "kind": row.kind,
            "subject_type": row.subject_type,
            "subject_id": row.subject_id,
            "site_id": str(row.site_id) if row.site_id else None,
            "severity": row.severity,
        },
        source="core",
    )


class IncidentStore:
    def __init__(self, maker: async_sessionmaker[AsyncSession], bus: EventBus) -> None:
        self._maker = maker
        self._bus = bus

    async def open(
        self,
        kind: str,
        severity: Severity,
        subject_type: str,
        subject_id: str,
        *,
        site_id: UUID | None = None,
        details: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> UUID | None:
        """Ouvre l'incident ; None s'il est déjà ouvert (aucun événement)."""
        stmt = (
            pg_insert(Incident)
            .values(
                kind=kind,
                severity=severity,
                subject_type=subject_type,
                subject_id=subject_id,
                site_id=site_id,
                details=details or {},
                opened_at=now or datetime.now(UTC),
            )
            .on_conflict_do_nothing(
                index_elements=["kind", "subject_type", "subject_id"],
                index_where=text("resolved_at IS NULL"),
            )
            .returning(Incident.id)
        )
        async with self._maker.begin() as s:
            new_id = (await s.execute(stmt)).scalar_one_or_none()
            row = await s.get(Incident, new_id) if new_id else None
        if row is None:
            return None
        await self._bus.publish(_event("incident.opened", row))
        return row.id

    async def resolve(
        self, kind: str, subject_type: str, subject_id: str, *, now: datetime | None = None
    ) -> UUID | None:
        """Ferme l'incident ouvert du sujet ; None s'il n'y en avait pas."""
        stmt = (
            update(Incident)
            .where(
                Incident.kind == kind,
                Incident.subject_type == subject_type,
                Incident.subject_id == subject_id,
                Incident.resolved_at.is_(None),
            )
            .values(resolved_at=now or datetime.now(UTC))
            .returning(Incident.id)
        )
        async with self._maker.begin() as s:
            found = (await s.execute(stmt)).scalar_one_or_none()
            row = await s.get(Incident, found) if found else None
        if row is None:
            return None
        await self._bus.publish(_event("incident.resolved", row))
        return row.id


async def site_of_screen(session: AsyncSession, screen_id: UUID) -> UUID | None:
    return (await session.execute(select(Screen.site_id).where(Screen.id == screen_id))).scalar()


async def view(session: AsyncSession, row: Incident) -> dict[str, Any]:
    """Incident + noms (jamais d'identifiant technique en clair pour les notifications)."""
    subject: dict[str, str] = {}
    site_name: str | None = None
    tz = "Europe/Paris"
    site_id = row.site_id
    if row.subject_type == "screen":
        try:
            sid = UUID(row.subject_id)
        except ValueError:
            sid = None
        screen = await session.get(Screen, sid) if sid else None
        if screen is not None:
            subject["screen"] = screen.name
            site_id = site_id or screen.site_id
    elif row.subject_type == "agent":
        try:
            aid = UUID(row.subject_id)
        except ValueError:
            aid = None
        agent = await session.get(SiteAgent, aid) if aid else None
        if agent is not None:
            subject["agent"] = agent.name
            site_id = site_id or agent.site_id
    elif row.subject_type == "module":
        subject["module"] = row.subject_id
    site = await session.get(Site, site_id) if site_id else None
    if site is not None:
        site_name, tz = site.name, site.timezone
        subject["site"] = site.name
    return {
        "id": row.id,
        "kind": row.kind,
        "severity": row.severity,
        "subject_type": row.subject_type,
        "subject_id": row.subject_id,
        "subject": subject,
        "site_id": site_id,
        "site_name": site_name,
        "timezone": tz,
        "details": row.details,
        "opened_at": row.opened_at,
        "resolved_at": row.resolved_at,
        "acknowledged_at": row.acknowledged_at,
        "acknowledged_by": row.acknowledged_by,
    }
