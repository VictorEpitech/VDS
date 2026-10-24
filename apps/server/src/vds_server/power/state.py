"""État d'alimentation des écrans : `desired` (plan résolu), `observed` et journal des actions.

- `desired` : recalculé périodiquement et à chaque changement susceptible de modifier le plan ;
  n'est réécrit que lorsqu'il change (`desired_since` = début de l'état voulu).
- `observed` / `power_action` : alimentés par `power.changed` et `power.failed` (agent ou player),
  y compris les événements rejoués depuis l'outbox de l'agent.
"""

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_OID, UUID, uuid5

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from vds_sdk import Event

from vds_server.agents.plan import TRIGGERS
from vds_server.config.schemas import ConfigSchemaProvider
from vds_server.db.models import Screen
from vds_server.db.models_power import PowerAction, PowerState, RemoteCommand
from vds_server.power.plan import PowerPlanBuilder
from vds_server.power.resolver import PowerSlot

log = logging.getLogger("vds.power")

PERIOD_S = 15.0
REASONS = ("manual", "schedule", "exception", "alert")


def _uuid(raw: object) -> UUID | None:
    try:
        return UUID(str(raw)) if raw else None
    except ValueError:
        return None


def _current(slots: list[PowerSlot], now: datetime) -> tuple[str, datetime]:
    """État voulu à `now` et début du créneau en cours (`desired_since`)."""
    instant = now.astimezone(UTC)
    for s in slots:
        if s.start <= instant < s.end:
            return s.state, s.start.astimezone(UTC)
    return "on", now


class PowerStateTracker:
    def __init__(
        self, maker: async_sessionmaker[AsyncSession], schemas: ConfigSchemaProvider
    ) -> None:
        self._maker = maker
        self._schemas = schemas
        self._task: asyncio.Task[None] | None = None

    def register(self, subscribe: Any) -> None:
        subscribe("power.changed", self._on_changed)
        subscribe("power.failed", self._on_failed)
        for name in TRIGGERS:
            subscribe(name, self._on_plan_event)

    # --- desired --------------------------------------------------------------
    async def refresh(self, now: datetime | None = None, screen_id: UUID | None = None) -> None:
        """Recalcule `desired` des écrans actifs (ou d'un seul)."""
        now = now or datetime.now(UTC)
        async with self._maker() as s:
            q = select(Screen.id).where(Screen.archived_at.is_(None))
            if screen_id is not None:
                q = q.where(Screen.id == screen_id)
            ids = list((await s.execute(q)).scalars())
        for sid in ids:
            try:
                await self._refresh_one(sid, now)
            except Exception:
                log.warning("power.refresh_failed", exc_info=False)

    async def _refresh_one(self, screen_id: UUID, now: datetime) -> None:
        async with self._maker.begin() as s:
            # multi-workers : un seul recalcul à la fois par écran (idempotent, l'autre ignore)
            got = await s.scalar(
                text("SELECT pg_try_advisory_xact_lock(hashtextextended(:k, 0))"),
                {"k": f"power_desired:{screen_id}"},
            )
            if not got:
                return
            sp = await PowerPlanBuilder(s, self._schemas).for_screen(screen_id, now)
            desired, since = _current(sp.slots, now)
            row = await s.get(PowerState, screen_id)
            if row is None:
                s.add(
                    PowerState(
                        screen_id=screen_id,
                        desired=desired,
                        desired_reason="schedule",
                        desired_since=since,
                        observed="unknown",
                        updated_at=now,
                    )
                )
            elif row.desired != desired:
                row.desired, row.desired_reason = desired, "schedule"
                row.desired_since, row.updated_at = since, now

    async def _on_plan_event(self, event: Event) -> None:
        with contextlib.suppress(Exception):
            await self.refresh()

    # --- observed + journal ----------------------------------------------------
    async def _reason(self, s: AsyncSession, request_id: str | None) -> str:
        if request_id is None:
            return "schedule"
        args = (
            await s.execute(
                select(RemoteCommand.args).where(
                    RemoteCommand.name == "power.set",
                    RemoteCommand.args["request_id"].astext == request_id,
                )
            )
        ).scalar()
        reason = args.get("reason") if isinstance(args, dict) else None
        return reason if reason in REASONS else "schedule"

    async def _record(self, event: Event, *, ok: bool) -> None:
        p = event.payload
        screen_id = _uuid(p.get("screen_id"))
        state = p.get("state")
        if screen_id is None or state not in ("on", "off"):
            return
        raw_request = str(p["request_id"]) if p.get("request_id") else None
        # request_id est une chaîne libre côté agent : clé stable (UUID) pour la colonne et le rejeu
        request_id = None
        if raw_request is not None:
            request_id = _uuid(raw_request) or uuid5(NAMESPACE_OID, raw_request)
        now = datetime.now(UTC)
        await self.refresh(now, screen_id)  # garantit la ligne power_state
        driver = p.get("driver") if ok else None
        verified = bool(p.get("verified")) if ok else False
        code = None if ok else str(p.get("code") or "")
        async with self._maker.begin() as s:
            if request_id is not None:
                dup = await s.scalar(
                    select(PowerAction.id).where(
                        PowerAction.screen_id == screen_id,
                        PowerAction.request_id == request_id,
                        PowerAction.state == state,
                        PowerAction.ok == ok,
                    )
                )
                if dup is not None:
                    return  # rejeu d'outbox
            s.add(
                PowerAction(
                    screen_id=screen_id,
                    request_id=request_id,
                    state=state,
                    reason=await self._reason(s, raw_request),
                    origin=event.origin,
                    driver=driver,
                    ok=ok,
                    verified=verified,
                    code=code,
                    at=now,
                )
            )
            row = await s.get(PowerState, screen_id)
            if row is None:
                return
            row.updated_at = now
            if ok:
                row.observed, row.observed_at = state, now
                row.driver, row.verified, row.last_code = driver, verified, None
            else:
                row.last_code = code

    async def _on_changed(self, event: Event) -> None:
        await self._record(event, ok=True)

    async def _on_failed(self, event: Event) -> None:
        await self._record(event, ok=False)

    # --- tâche périodique ------------------------------------------------------
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
            with contextlib.suppress(Exception):
                await self.refresh()
            await asyncio.sleep(PERIOD_S)
