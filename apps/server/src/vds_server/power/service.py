"""CRUD du planning d'alimentation : plans (et règles), exceptions, calendriers (et jours)."""

import json
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import Text, cast, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from vds_sdk import Event

from vds_server.core.errors import ApiError
from vds_server.db.models import ConfigOverride
from vds_server.db.models_power import (
    PowerCalendar,
    PowerCalendarDay,
    PowerException,
    PowerPlan,
    PowerPlanRule,
)
from vds_server.diffusion.base import ServiceBase
from vds_server.diffusion.schedules import TARGET_MODELS
from vds_server.diffusion.schemas import RuleIn, RuleOut, to_hhmm, to_minutes
from vds_server.power import schemas as sc

MANUAL_MODULE = "core"


class PowerService(ServiceBase):
    def _plan_changed(self, level: str, scope_id: UUID | None) -> None:
        payload = {"scope_level": level}
        if scope_id is not None:
            payload["scope_id"] = str(scope_id)
        self._events.append(Event(name="power.plan_changed", payload=payload, source="core"))

    # --- plans -----------------------------------------------------------
    async def _rules(self, plan_id: UUID) -> list[RuleOut]:
        q = (
            select(PowerPlanRule)
            .where(PowerPlanRule.plan_id == plan_id)
            .order_by(PowerPlanRule.start_minute, PowerPlanRule.end_minute, PowerPlanRule.weekdays)
        )
        return [
            RuleOut(weekdays=r.weekdays, start=to_hhmm(r.start_minute), end=to_hhmm(r.end_minute))
            for r in (await self._s.execute(q)).scalars()
        ]

    async def _plan_out(self, p: PowerPlan) -> sc.PlanOut:
        return sc.PlanOut(
            id=p.id,
            name=p.name,
            description=p.description,
            rules=await self._rules(p.id),
            created_at=p.created_at,
            updated_at=p.updated_at,
        )

    async def _plan(self, plan_id: UUID) -> PowerPlan:
        row = await self._s.get(PowerPlan, plan_id)
        if row is None:
            raise ApiError("power.plan_not_found", 404)
        return row

    def _add_rules(self, plan_id: UUID, rules: list[RuleIn]) -> None:
        for r in rules:
            self._s.add(
                PowerPlanRule(
                    plan_id=plan_id,
                    weekdays=r.weekdays,
                    start_minute=to_minutes(r.start),
                    end_minute=to_minutes(r.end),
                )
            )

    async def list_plans(self) -> list[sc.PlanOut]:
        rows = (
            await self._s.execute(select(PowerPlan).order_by(PowerPlan.name, PowerPlan.id))
        ).scalars()
        return [await self._plan_out(p) for p in rows]

    async def get_plan(self, plan_id: UUID) -> sc.PlanOut:
        return await self._plan_out(await self._plan(plan_id))

    async def create_plan(self, body: sc.PlanCreate) -> sc.PlanOut:
        row = PowerPlan(
            name=body.name,
            description=body.description,
            created_by=self._actor.user_id,
            updated_by=self._actor.user_id,
        )
        self._s.add(row)
        await self._s.flush()
        self._add_rules(row.id, body.rules)
        self._audit("power.plan.created", "power_plan", row.id, "plan_id", emit=False)
        await self._commit("power.conflict")
        return await self.get_plan(row.id)

    async def update_plan(self, plan_id: UUID, body: sc.PlanPatch) -> sc.PlanOut:
        row = await self._plan(plan_id)
        fields = body.model_fields_set
        if body.name is not None:
            row.name = body.name
        if "description" in fields:
            row.description = body.description
        if body.rules is not None:
            for old in (
                await self._s.execute(select(PowerPlanRule).where(PowerPlanRule.plan_id == row.id))
            ).scalars():
                await self._s.delete(old)
            await self._s.flush()
            self._add_rules(row.id, body.rules)
        row.updated_at = datetime.now(UTC)
        row.updated_by = self._actor.user_id
        self._audit(
            "power.plan.updated", "power_plan", row.id, "plan_id", emit=False, fields=sorted(fields)
        )
        self._plan_changed(
            "organization", None
        )  # le plan peut être rattaché à n'importe quel niveau
        await self._commit("power.conflict")
        return await self.get_plan(row.id)

    async def delete_plan(self, plan_id: UUID) -> None:
        row = await self._plan(plan_id)
        used = await self._s.execute(
            select(func.count())
            .select_from(ConfigOverride)
            .where(
                ConfigOverride.namespace == "core",
                ConfigOverride.key == "power.plan_id",
                cast(ConfigOverride.value, Text) == json.dumps(str(plan_id)),
            )
        )
        if used.scalar_one():
            raise ApiError("power.plan_in_use", 409)
        await self._s.delete(row)
        self._audit("power.plan.deleted", "power_plan", plan_id, "plan_id", emit=False)
        await self._commit("power.conflict")

    # --- exceptions ------------------------------------------------------
    @staticmethod
    def _exc_out(e: PowerException) -> sc.ExceptionOut:
        return sc.ExceptionOut(
            id=e.id,
            scope_level=e.scope_level,  # type: ignore[arg-type]
            scope_id=e.scope_id,
            starts_on=e.starts_on,
            ends_on=e.ends_on,
            start=None if e.start_minute is None else to_hhmm(e.start_minute),
            end=None if e.end_minute is None else to_hhmm(e.end_minute),
            state=e.state,  # type: ignore[arg-type]
            label=e.label,
            created_at=e.created_at,
            updated_at=e.updated_at,
        )

    async def _exception(self, exception_id: UUID) -> PowerException:
        row = await self._s.get(PowerException, exception_id)
        if row is None:
            raise ApiError("power.exception_not_found", 404)
        return row

    async def _check_scope(self, level: str, scope_id: UUID | None) -> None:
        if scope_id is not None and await self._s.get(TARGET_MODELS[level], scope_id) is None:
            raise ApiError("power.scope_not_found", 422, scope_level=level)

    def _fill(self, row: PowerException, body: sc.ExceptionCreate | sc.ExceptionReplace) -> None:
        row.scope_level = body.scope_level
        row.scope_id = body.scope_id
        row.starts_on, row.ends_on = body.starts_on, body.ends_on
        row.start_minute = None if body.start is None else to_minutes(body.start)
        row.end_minute = None if body.end is None else to_minutes(body.end)
        row.state, row.label = body.state, body.label

    async def list_exceptions(
        self,
        *,
        scope_level: str | None,
        scope_id: UUID | None,
        on_or_after: date | None,
        on_or_before: date | None,
    ) -> list[sc.ExceptionOut]:
        q = select(PowerException).order_by(
            PowerException.starts_on, PowerException.created_at, PowerException.id
        )
        if scope_level:
            q = q.where(PowerException.scope_level == scope_level)
        if scope_id:
            q = q.where(PowerException.scope_id == scope_id)
        if on_or_after:
            q = q.where(PowerException.ends_on >= on_or_after)
        if on_or_before:
            q = q.where(PowerException.starts_on <= on_or_before)
        return [self._exc_out(e) for e in (await self._s.execute(q)).scalars()]

    async def get_exception(self, exception_id: UUID) -> sc.ExceptionOut:
        return self._exc_out(await self._exception(exception_id))

    async def create_exception(self, body: sc.ExceptionCreate) -> sc.ExceptionOut:
        await self._check_scope(body.scope_level, body.scope_id)
        row = PowerException(created_by=self._actor.user_id, updated_by=self._actor.user_id)
        self._fill(row, body)
        self._s.add(row)
        await self._s.flush()
        self._audit(
            "power.exception.created", "power_exception", row.id, "exception_id", emit=False
        )
        self._plan_changed(body.scope_level, body.scope_id)
        await self._commit("power.conflict")
        return self._exc_out(row)

    async def replace_exception(
        self, exception_id: UUID, body: sc.ExceptionReplace
    ) -> sc.ExceptionOut:
        row = await self._exception(exception_id)
        await self._check_scope(body.scope_level, body.scope_id)
        self._plan_changed(row.scope_level, row.scope_id)  # ancienne portée aussi
        self._fill(row, body)
        row.updated_at = datetime.now(UTC)
        row.updated_by = self._actor.user_id
        self._audit(
            "power.exception.updated", "power_exception", row.id, "exception_id", emit=False
        )
        self._plan_changed(body.scope_level, body.scope_id)
        await self._commit("power.conflict")
        return self._exc_out(row)

    async def delete_exception(self, exception_id: UUID) -> None:
        row = await self._exception(exception_id)
        level, scope = row.scope_level, row.scope_id
        await self._s.delete(row)
        self._audit(
            "power.exception.deleted", "power_exception", exception_id, "exception_id", emit=False
        )
        self._plan_changed(level, scope)
        await self._commit("power.conflict")

    # --- calendriers -----------------------------------------------------
    async def _calendar(self, key: str) -> PowerCalendar:
        row = (
            await self._s.execute(select(PowerCalendar).where(PowerCalendar.key == key))
        ).scalar_one_or_none()
        if row is None:
            raise ApiError("power.calendar_not_found", 404)
        return row

    async def list_calendars(self) -> list[sc.CalendarOut]:
        counts = (
            select(PowerCalendarDay.calendar_id, func.count().label("n"))
            .group_by(PowerCalendarDay.calendar_id)
            .subquery()
        )
        q = (
            select(PowerCalendar, func.coalesce(counts.c.n, 0))
            .outerjoin(counts, counts.c.calendar_id == PowerCalendar.id)
            .order_by(PowerCalendar.key)
        )
        return [
            sc.CalendarOut(
                key=c.key,
                module_id=c.module_id,
                name=c.name,
                refreshed_at=c.refreshed_at,
                day_count=int(n),
            )
            for c, n in (await self._s.execute(q)).all()
        ]

    async def create_calendar(self, body: sc.CalendarCreate) -> sc.CalendarOut:
        row = PowerCalendar(key=body.key, module_id=MANUAL_MODULE, name=body.name)
        self._s.add(row)
        try:
            await self._s.flush()
        except IntegrityError as exc:
            await self._s.rollback()
            raise ApiError("power.calendar_exists", 409) from exc
        self._audit("power.calendar.created", "power_calendar", row.id, "calendar", emit=False)
        await self._commit("power.conflict")
        return next(c for c in await self.list_calendars() if c.key == body.key)

    async def delete_calendar(self, key: str) -> None:
        row = await self._calendar(key)
        await self._s.delete(row)
        self._audit("power.calendar.deleted", "power_calendar", row.id, "calendar", emit=False)
        self._plan_changed("organization", None)
        await self._commit("power.conflict")

    async def list_days(
        self, key: str, start: date | None, end: date | None
    ) -> list[sc.CalendarDayOut]:
        cal = await self._calendar(key)
        q = (
            select(PowerCalendarDay)
            .where(PowerCalendarDay.calendar_id == cal.id)
            .order_by(PowerCalendarDay.day)
        )
        if start:
            q = q.where(PowerCalendarDay.day >= start)
        if end:
            q = q.where(PowerCalendarDay.day <= end)
        return [sc.CalendarDayOut.model_validate(d) for d in (await self._s.execute(q)).scalars()]

    async def put_day(self, key: str, day: date, body: sc.CalendarDayIn) -> sc.CalendarDayOut:
        cal = await self._calendar(key)
        stmt = insert(PowerCalendarDay).values(calendar_id=cal.id, day=day, label=body.label)
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=["calendar_id", "day"], set_={"label": stmt.excluded.label}
            )
        )
        self._audit("power.calendar.day_set", "power_calendar", cal.id, "calendar", emit=False)
        self._plan_changed("organization", None)
        await self._commit("power.conflict")
        return sc.CalendarDayOut(day=day, label=body.label)

    async def delete_day(self, key: str, day: date) -> None:
        cal = await self._calendar(key)
        res = await self._s.execute(
            delete(PowerCalendarDay).where(
                PowerCalendarDay.calendar_id == cal.id, PowerCalendarDay.day == day
            )
        )
        if not res.rowcount:  # type: ignore[attr-defined]
            raise ApiError("power.calendar_day_not_found", 404)
        self._audit("power.calendar.day_removed", "power_calendar", cal.id, "calendar", emit=False)
        self._plan_changed("organization", None)
        await self._commit("power.conflict")
