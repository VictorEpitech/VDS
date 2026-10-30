"""Prédicat « à traiter » unique, partagé par `GET /screens?issues=true` et `/fleet/summary`.

Le statut est recalculé à la requête par la fonction pure `screen_status` (pas de cache mémoire).
"""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.db.models import Screen, ScreenDevice
from vds_server.db.models_power import Incident, PowerState
from vds_server.supervision.status import ScreenStatus, Thresholds, screen_status

SCREEN_INCIDENT_REASONS = {"screen.offline": "offline", "power.failed": "power_failed"}


@dataclass(frozen=True)
class ScreenHealth:
    status: ScreenStatus
    reasons: frozenset[str] = field(default_factory=frozenset)

    @property
    def to_handle(self) -> bool:
        return bool(self.reasons)


async def evaluate(
    session: AsyncSession, screens: list[Screen], now: datetime, th: Thresholds
) -> dict[UUID, ScreenHealth]:
    """Santé des écrans non archivés de `screens` (les archivés sont ignorés)."""
    live = [s for s in screens if s.archived_at is None]
    ids = [s.id for s in live]
    if not ids:
        return {}
    devices: dict[UUID, list[ScreenDevice]] = {}
    for d in (
        await session.execute(select(ScreenDevice).where(ScreenDevice.screen_id.in_(ids)))
    ).scalars():
        devices.setdefault(d.screen_id, []).append(d)
    power = {
        p.screen_id: p
        for p in (
            await session.execute(select(PowerState).where(PowerState.screen_id.in_(ids)))
        ).scalars()
    }
    incident_reasons: dict[str, set[str]] = {}
    for kind, subject_id in (
        await session.execute(
            select(Incident.kind, Incident.subject_id).where(
                Incident.resolved_at.is_(None),
                Incident.subject_type == "screen",
                Incident.kind.in_(list(SCREEN_INCIDENT_REASONS)),
                Incident.subject_id.in_([str(i) for i in ids]),
            )
        )
    ).all():
        incident_reasons.setdefault(subject_id, set()).add(SCREEN_INCIDENT_REASONS[kind])
    out: dict[UUID, ScreenHealth] = {}
    for sc in live:
        devs = devices.get(sc.id, [])
        active = [d for d in devs if d.revoked_at is None]
        seen = [d.last_seen_at for d in active if d.last_seen_at is not None]
        ps = power.get(sc.id)
        status = screen_status(
            has_active_device=bool(active),
            has_revoked_device=bool(devs) and not active,
            last_seen_at=max(seen) if seen else None,
            desired=ps.desired if ps else None,
            desired_since=ps.desired_since if ps else None,
            now=now,
            thresholds=th,
        )
        reasons = set(incident_reasons.get(str(sc.id), set()))
        if status in ("offline", "revoked"):
            reasons.add(status)
        out[sc.id] = ScreenHealth(status, frozenset(reasons))
    return out
