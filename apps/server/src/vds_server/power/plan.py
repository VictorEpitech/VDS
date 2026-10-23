"""Plan d'alimentation résolu d'un écran et contenu servi à l'agent de site et au player."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.config.resolver import Origin, ResolvedKey
from vds_server.config.schemas import ConfigSchemaProvider
from vds_server.config.service import ConfigError, ConfigService
from vds_server.core.errors import ApiError
from vds_server.db.models import Screen
from vds_server.db.models_power import (
    PowerCalendar,
    PowerCalendarDay,
    PowerException,
    PowerPlanRule,
)
from vds_server.player.schedule import revision_of
from vds_server.power import resolver
from vds_server.power.resolver import ExceptionSpec, PlanRule, PowerSlot

DEFAULT_TZ = "Europe/Paris"


@dataclass(frozen=True)
class ScreenPower:
    screen_id: UUID
    timezone: str
    plan_id: UUID | None
    plan_id_origin: Origin
    calendars: list[str]
    calendars_origin: Origin
    slots: list[PowerSlot]
    cascade: list[str]
    retries: int
    verify_delay_s: int


def _zone(name: object) -> ZoneInfo:
    try:
        return ZoneInfo(str(name))
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TZ)


class PowerPlanBuilder:
    def __init__(self, session: AsyncSession, schemas: ConfigSchemaProvider) -> None:
        self._s = session
        self._cfg = ConfigService(session, schemas)
        self._schemas = schemas

    async def for_screen(self, screen_id: UUID, now: datetime | None = None) -> ScreenPower:
        now = now or datetime.now(UTC)
        screen = await self._s.get(Screen, screen_id)
        if screen is None or screen.archived_at is not None:
            raise ApiError("errors.screen_not_found", 404)
        res = (await self._cfg.resolve("screen", screen_id, "core")).values
        tz = _zone(res["timezone"].value)
        day: date = now.astimezone(tz).date()
        window = (day, day + timedelta(days=1))
        plan_id = _uuid(res["power.plan_id"].value)
        calendars = [str(k) for k in res["power.calendars"].value or []]
        rules = await self._rules(plan_id) if plan_id else None
        days = await self._calendar_days(calendars, window)
        exceptions = await self._exceptions(await self._cfg.chain("screen", screen_id), window)
        slots = resolver.resolve(rules, days, exceptions, tz, day)
        return ScreenPower(
            screen_id=screen_id,
            timezone=tz.key,
            plan_id=plan_id,
            plan_id_origin=res["power.plan_id"].origin,
            calendars=calendars,
            calendars_origin=res["power.calendars"].origin,
            slots=slots,
            cascade=[str(x) for x in res["power.cascade"].value],
            retries=int(res["power.retries"].value),
            verify_delay_s=int(res["power.verify_delay_s"].value),
        )

    async def _rules(self, plan_id: UUID) -> list[PlanRule] | None:
        rows = (
            await self._s.execute(select(PowerPlanRule).where(PowerPlanRule.plan_id == plan_id))
        ).scalars()
        return [PlanRule(r.weekdays, r.start_minute, r.end_minute) for r in rows]

    async def _calendar_days(self, keys: list[str], window: tuple[date, date]) -> set[date]:
        if not keys:
            return set()
        q = (
            select(PowerCalendarDay.day)
            .join(PowerCalendar, PowerCalendar.id == PowerCalendarDay.calendar_id)
            .where(PowerCalendar.key.in_(keys), PowerCalendarDay.day.between(*window))
        )
        return set((await self._s.execute(q)).scalars())

    async def _exceptions(self, chain: list[Any], window: tuple[date, date]) -> list[ExceptionSpec]:
        conds = [
            and_(PowerException.scope_level == n.level, PowerException.scope_id == n.id)
            for n in chain
        ]
        conds.append(
            and_(PowerException.scope_level == "organization", PowerException.scope_id.is_(None))
        )
        q = select(PowerException).where(
            or_(*conds), PowerException.starts_on <= window[1], PowerException.ends_on >= window[0]
        )
        return [
            ExceptionSpec(
                e.scope_level,
                e.starts_on,
                e.ends_on,
                e.state,  # type: ignore[arg-type]
                e.start_minute,
                e.end_minute,
            )
            for e in (await self._s.execute(q)).scalars()
        ]

    async def player_slots(
        self, screen_id: UUID, now: datetime | None = None
    ) -> list[dict[str, str]]:
        return resolver.to_wire((await self.for_screen(screen_id, now)).slots)

    async def agent_plan(self, site_id: UUID, now: datetime | None = None) -> dict[str, Any]:
        """`GET agent/plan` : écrans actifs du site, créneaux J + J+1, cascade, pilotes."""
        ids = (
            await self._s.execute(
                select(Screen.id)
                .where(Screen.site_id == site_id, Screen.archived_at.is_(None))
                .order_by(Screen.id)
            )
        ).scalars()
        screens: list[dict[str, Any]] = []
        for sid in list(ids):
            sp = await self.for_screen(sid, now)
            screens.append(
                {
                    "screen_id": str(sid),
                    "timezone": sp.timezone,
                    "power": resolver.to_wire(sp.slots),
                    "cascade": sp.cascade,
                    "retries": sp.retries,
                    "verify_delay_s": sp.verify_delay_s,
                    "drivers": await self._drivers(sid, sp.cascade),
                }
            )
        body: dict[str, Any] = {"screens": screens}
        body["revision"] = revision_of(body)
        return body

    async def _drivers(self, screen_id: UUID, cascade: list[str]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for driver in cascade:
            if self._schemas.get_schema(driver) is None:
                continue
            try:
                values = (await self._cfg.resolve("screen", screen_id, driver)).values
            except ConfigError:
                continue
            out[driver] = {k: _plain(v) for k, v in values.items() if not v.secret}
        return out


def _plain(v: ResolvedKey) -> Any:
    return v.value


def _uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except ValueError:
        return None
