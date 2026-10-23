"""Supervision : tâche périodique (30 s) + abonnements du noyau aux événements de panne.

Le noyau n'envoie rien : il publie `screen.status_changed` et `incident.opened|resolved`.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from vds_sdk import Event, EventBus

from vds_server.db.models import Screen, ScreenDevice
from vds_server.db.models_power import Incident, PowerState, SiteAgent
from vds_server.supervision.incidents import IncidentStore, site_of_screen
from vds_server.supervision.status import ScreenStatus, Thresholds, screen_status

log = logging.getLogger("vds.supervision")

PERIOD_S = 30.0
ConfigLoader = Callable[[], Awaitable[dict[str, Any]]]
Subscribe = Callable[[str, Callable[[Event], Awaitable[None]]], Callable[[], None]]

SCREEN_OFFLINE = "screen.offline"
AGENT_OFFLINE = "agent.offline"
POWER_FAILED = "power.failed"
MODULE_QUARANTINED = "module.quarantined"


class Supervisor:
    def __init__(
        self,
        maker: async_sessionmaker[AsyncSession],
        bus: EventBus,
        load_config: ConfigLoader,
    ) -> None:
        self._maker = maker
        self._bus = bus
        self._config = load_config
        self.incidents = IncidentStore(maker, bus)
        self._status: dict[UUID, ScreenStatus] = {}
        self._task: asyncio.Task[None] | None = None

    # --- abonnements aux pannes signalées par événement -----------------------
    def register(self, subscribe: Subscribe) -> None:
        subscribe("power.failed", self._on_power_failed)
        subscribe("power.changed", self._on_power_changed)
        subscribe("module.quarantined", self._on_quarantined)
        subscribe("module.enabled", self._on_module_enabled)

    async def _on_power_failed(self, event: Event) -> None:
        screen_id = UUID(str(event.payload["screen_id"]))
        async with self._maker() as s:
            site_id = await site_of_screen(s, screen_id)
        await self.incidents.open(
            POWER_FAILED,
            "warning",
            "screen",
            str(screen_id),
            site_id=site_id,
            details={
                "state": event.payload.get("state"),
                "attempted": event.payload.get("attempted"),
                "code": event.payload.get("code"),
            },
        )

    async def _on_power_changed(self, event: Event) -> None:
        if event.payload.get("verified") is True:
            await self.incidents.resolve(POWER_FAILED, "screen", str(event.payload["screen_id"]))

    async def _on_quarantined(self, event: Event) -> None:
        await self.incidents.open(
            MODULE_QUARANTINED,
            "critical",
            "module",
            str(event.payload["module_id"]),
            details={"code": event.payload.get("code")},
        )

    async def _on_module_enabled(self, event: Event) -> None:
        await self.incidents.resolve(MODULE_QUARANTINED, "module", str(event.payload["module_id"]))

    # --- tâche périodique -----------------------------------------------------
    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(PERIOD_S)
            try:
                await self.tick()
            except Exception:
                log.warning("supervision.tick_failed", exc_info=False)

    async def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        thresholds = Thresholds.from_config(await self._config())
        async with self._maker() as s:
            open_rows = list(
                (
                    await s.execute(
                        select(Incident).where(
                            Incident.kind.in_([SCREEN_OFFLINE, AGENT_OFFLINE]),
                            Incident.resolved_at.is_(None),
                        )
                    )
                ).scalars()
            )
            screens = await self._screen_states(s, now, thresholds)
            agents = await self._agent_states(s, now, thresholds)
        want: set[tuple[str, str]] = set()
        for screen_id, site_id, status in screens:
            previous = self._status.get(screen_id)
            self._status[screen_id] = status
            if previous is not None and previous != status:
                await self._bus.publish(
                    Event(
                        name="screen.status_changed",
                        payload={
                            "screen_id": str(screen_id),
                            "status": status,
                            "previous": previous,
                        },
                        source="core",
                    )
                )
            if status == "offline":
                want.add((SCREEN_OFFLINE, str(screen_id)))
                await self.incidents.open(
                    SCREEN_OFFLINE, "warning", "screen", str(screen_id), site_id=site_id, now=now
                )
        for agent_id, site_id, offline in agents:
            if offline:
                want.add((AGENT_OFFLINE, str(agent_id)))
                await self.incidents.open(
                    AGENT_OFFLINE, "critical", "agent", str(agent_id), site_id=site_id, now=now
                )
        for row in open_rows:
            if (row.kind, row.subject_id) not in want:
                await self.incidents.resolve(row.kind, row.subject_type, row.subject_id, now=now)
        self._status = {k: v for k, v in self._status.items() if k in {x[0] for x in screens}}

    async def _screen_states(
        self, s: AsyncSession, now: datetime, th: Thresholds
    ) -> list[tuple[UUID, UUID, ScreenStatus]]:
        screens = (
            await s.execute(select(Screen.id, Screen.site_id).where(Screen.archived_at.is_(None)))
        ).all()
        devices: dict[UUID, list[ScreenDevice]] = {}
        for d in (await s.execute(select(ScreenDevice))).scalars():
            devices.setdefault(d.screen_id, []).append(d)
        power = {p.screen_id: p for p in (await s.execute(select(PowerState))).scalars()}
        out: list[tuple[UUID, UUID, ScreenStatus]] = []
        for screen_id, site_id in screens:
            devs = devices.get(screen_id, [])
            active = [d for d in devs if d.revoked_at is None]
            seen = [d.last_seen_at for d in active if d.last_seen_at is not None]
            ps = power.get(screen_id)
            status = screen_status(
                has_active_device=bool(active),
                has_revoked_device=bool(devs) and not active,
                last_seen_at=max(seen) if seen else None,
                desired=ps.desired if ps else None,
                desired_since=ps.desired_since if ps else None,
                now=now,
                thresholds=th,
            )
            out.append((screen_id, site_id, status))
        return out

    async def _agent_states(
        self, s: AsyncSession, now: datetime, th: Thresholds
    ) -> list[tuple[UUID, UUID, bool]]:
        rows = (await s.execute(select(SiteAgent).where(SiteAgent.revoked_at.is_(None)))).scalars()
        return [
            (
                a.id,
                a.site_id,
                a.last_seen_at is not None
                and (now - a.last_seen_at).total_seconds() > th.agent_offline_after_s,
            )
            for a in rows
        ]
