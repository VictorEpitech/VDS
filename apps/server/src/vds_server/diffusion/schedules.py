"""Programmation : playlist + cible + période + règles hebdomadaires."""

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select

from vds_server.core.errors import ApiError
from vds_server.db.models import (
    Organization,
    Playlist,
    Schedule,
    ScheduleRule,
    Screen,
    ScreenGroup,
    Site,
)
from vds_server.diffusion import schemas as sc
from vds_server.diffusion.base import ServiceBase

TARGET_MODELS: dict[str, Any] = {
    "organization": Organization,
    "site": Site,
    "group": ScreenGroup,
    "screen": Screen,
}


class ScheduleService(ServiceBase):
    async def _target_exists(self, target_type: str, target_id: UUID) -> None:
        if await self._s.get(TARGET_MODELS[target_type], target_id) is None:
            raise ApiError("schedule.target_not_found", 422, target_type=target_type)

    async def _playlist_exists(self, playlist_id: UUID) -> None:
        if await self._s.get(Playlist, playlist_id) is None:
            raise ApiError("schedule.playlist_not_found", 422)

    async def _rules(self, ids: list[UUID]) -> dict[UUID, list[sc.RuleOut]]:
        out: dict[UUID, list[sc.RuleOut]] = {i: [] for i in ids}
        if ids:
            q = (
                select(ScheduleRule)
                .where(ScheduleRule.schedule_id.in_(ids))
                .order_by(ScheduleRule.start_minute, ScheduleRule.end_minute, ScheduleRule.weekdays)
            )
            for r in (await self._s.execute(q)).scalars():
                out[r.schedule_id].append(
                    sc.RuleOut(
                        weekdays=r.weekdays,
                        start=sc.to_hhmm(r.start_minute),
                        end=sc.to_hhmm(r.end_minute),
                    )
                )
        return out

    def _out(self, s: Schedule, rules: list[sc.RuleOut]) -> sc.ScheduleOut:
        return sc.ScheduleOut(
            id=s.id,
            name=s.name,
            playlist_id=s.playlist_id,
            target_type=s.target_type,  # type: ignore[arg-type]
            target_id=s.target_id,
            priority=s.priority,
            starts_on=s.starts_on,
            ends_on=s.ends_on,
            enabled=s.enabled,
            rules=rules,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )

    async def list_all(
        self, *, target_type: str | None, target_id: UUID | None, playlist_id: UUID | None
    ) -> list[sc.ScheduleOut]:
        q = select(Schedule).order_by(Schedule.name, Schedule.id)
        if target_type:
            q = q.where(Schedule.target_type == target_type)
        if target_id:
            q = q.where(Schedule.target_id == target_id)
        if playlist_id:
            q = q.where(Schedule.playlist_id == playlist_id)
        rows = list((await self._s.execute(q)).scalars())
        rules = await self._rules([r.id for r in rows])
        return [self._out(r, rules[r.id]) for r in rows]

    async def _get(self, schedule_id: UUID) -> Schedule:
        row = await self._s.get(Schedule, schedule_id)
        if row is None:
            raise ApiError("schedule.not_found", 404)
        return row

    async def get(self, schedule_id: UUID) -> sc.ScheduleOut:
        row = await self._get(schedule_id)
        return self._out(row, (await self._rules([row.id]))[row.id])

    def _add_rules(self, schedule_id: UUID, rules: list[sc.RuleIn]) -> None:
        for r in rules:
            self._s.add(
                ScheduleRule(
                    schedule_id=schedule_id,
                    weekdays=r.weekdays,
                    start_minute=sc.to_minutes(r.start),
                    end_minute=sc.to_minutes(r.end),
                )
            )

    async def create(self, body: sc.ScheduleCreate) -> sc.ScheduleOut:
        await self._playlist_exists(body.playlist_id)
        await self._target_exists(body.target_type, body.target_id)
        row = Schedule(
            name=body.name,
            playlist_id=body.playlist_id,
            target_type=body.target_type,
            target_id=body.target_id,
            priority=body.priority,
            starts_on=body.starts_on,
            ends_on=body.ends_on,
            enabled=body.enabled,
            created_by=self._actor.user_id,
            updated_by=self._actor.user_id,
        )
        self._s.add(row)
        await self._s.flush()
        self._add_rules(row.id, body.rules)
        self._audit(
            "schedule.created",
            "schedule",
            row.id,
            "schedule_id",
            target_type=body.target_type,
            target_id=str(body.target_id),
        )
        await self._commit("schedule.conflict")
        return await self.get(row.id)

    async def update(self, schedule_id: UUID, body: sc.SchedulePatch) -> sc.ScheduleOut:
        row = await self._get(schedule_id)
        fields = body.model_fields_set
        if body.playlist_id is not None:
            await self._playlist_exists(body.playlist_id)
            row.playlist_id = body.playlist_id
        if body.target_type is not None or body.target_id is not None:
            if body.target_type is None or body.target_id is None:
                raise ApiError("schedule.target_incomplete", 422)
            await self._target_exists(body.target_type, body.target_id)
            row.target_type, row.target_id = body.target_type, body.target_id
        if body.name is not None:
            row.name = body.name
        if body.priority is not None:
            row.priority = body.priority
        if body.enabled is not None:
            row.enabled = body.enabled
        starts: date | None = body.starts_on if "starts_on" in fields else row.starts_on
        ends: date | None = body.ends_on if "ends_on" in fields else row.ends_on
        if starts and ends and starts > ends:
            raise ApiError("schedule.period_invalid", 422)
        row.starts_on, row.ends_on = starts, ends
        if body.rules is not None:
            q = select(ScheduleRule).where(ScheduleRule.schedule_id == row.id)
            for old in (await self._s.execute(q)).scalars():
                await self._s.delete(old)
            await self._s.flush()
            self._add_rules(row.id, body.rules)
        row.updated_at = datetime.now(UTC)
        row.updated_by = self._actor.user_id
        self._audit("schedule.updated", "schedule", row.id, "schedule_id", fields=sorted(fields))
        await self._commit("schedule.conflict")
        return await self.get(row.id)

    async def delete(self, schedule_id: UUID) -> None:
        row = await self._get(schedule_id)
        await self._s.delete(row)
        self._audit("schedule.deleted", "schedule", schedule_id, "schedule_id")
        await self._commit("schedule.conflict")
