"""Résumé de parc : agrégats en lecture seule."""

from collections import Counter
from datetime import datetime
from typing import get_args
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.core.auth import Principal
from vds_server.db.models import Screen
from vds_server.db.models_power import Incident, SiteAgent
from vds_server.diffusion.scope import PerimeterScope
from vds_server.fleet.health import evaluate
from vds_server.fleet.schemas import (
    AgentsSummaryOut,
    FleetSummaryOut,
    IncidentsSummaryOut,
    ScreensSummaryOut,
    ToHandleOut,
)
from vds_server.supervision.status import ScreenStatus, Thresholds
from vds_server.supervision.visibility import incident_filter

REASONS = ("offline", "revoked", "power_failed")


async def summary(
    session: AsyncSession,
    site_id: UUID | None,
    now: datetime,
    th: Thresholds,
    actor: Principal | None = None,
) -> FleetSummaryOut:
    scope = PerimeterScope(session)
    q = select(Screen).where(Screen.archived_at.is_(None))
    if actor is not None:
        q = q.where(scope.screen_filter(actor))
    if site_id:
        q = q.where(Screen.site_id == site_id)
    health = await evaluate(session, list((await session.execute(q)).scalars()), now, th)
    by_status = Counter(h.status for h in health.values())
    by_reason = Counter(r for h in health.values() for r in h.reasons)

    iq = select(Incident).where(Incident.resolved_at.is_(None))
    if actor is not None:
        iq = iq.where(incident_filter(actor, scope))
    if site_id:
        iq = iq.where(Incident.site_id == site_id)
    incidents = list((await session.execute(iq)).scalars())

    aq = select(SiteAgent).where(SiteAgent.revoked_at.is_(None))
    if actor is not None and not actor.perimeter.unrestricted:
        aq = aq.where(SiteAgent.site_id.in_(actor.perimeter.sites))
    if site_id:
        aq = aq.where(SiteAgent.site_id == site_id)
    agents = list((await session.execute(aq)).scalars())
    offline = sum(
        1
        for a in agents
        if a.last_seen_at is not None
        and (now - a.last_seen_at).total_seconds() > th.agent_offline_after_s
    )
    return FleetSummaryOut(
        site_id=site_id,
        generated_at=now,
        screens=ScreensSummaryOut(
            total=len(health),
            by_status={s: by_status.get(s, 0) for s in get_args(ScreenStatus)},
        ),
        to_handle=ToHandleOut(
            screens=sum(1 for h in health.values() if h.to_handle),
            by_reason={r: by_reason.get(r, 0) for r in REASONS},
        ),
        incidents=IncidentsSummaryOut(
            open=len(incidents),
            unacknowledged=sum(1 for i in incidents if i.acknowledged_at is None),
            by_severity=dict(Counter(i.severity for i in incidents)),
            by_kind=dict(Counter(i.kind for i in incidents)),
        ),
        agents=AgentsSummaryOut(total=len(agents), online=len(agents) - offline, offline=offline),
    )
